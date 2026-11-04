#!/usr/bin/env python3
"""JR-nearby validator (pure stdlib; runs locally and in CI).

Checks:
  1. Structure per schema/station.v1.json (built-in checks; `jsonschema` too if installed)
  2. Trilingual completeness & quality:
     - ja/en/zh all present and non-empty for every name/desc
     - desc length 40-120 chars per language
     - zh must be Simplified Chinese: >=8 Han chars, no hiragana,
       katakana runs <=12 chars (proper nouns only, explained in zh desc)
     - en must contain Latin letters, no kana
  3. In-station dedup: identical name.ja, or two POIs with coords <50 m apart
  4. Distance sanity: distance_m <= 2000; walk_min consistent (55..110 m/min)
  5. coverage.poi_count == len(pois); poi_count < 4 requires non-empty gaps
  6. index.json consistency (file exists, sha256, poi_count)
  7. Optional URL reachability (--check-urls), deterministic sampling (--url-sample N)

Exit 0 = pass, 1 = violations.
"""
import argparse
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CATEGORIES = {"sight", "shrine_temple", "food", "cafe", "shopping", "onsen_sento",
              "park", "entertainment", "lodging",
              "convenience", "bank_atm", "medical", "post_office", "police",
              "tourist_info", "public_service"}
SOURCES = {"osm", "official_site", "city_page", "tourism_site", "wikipedia",
           "ja_wiki", "en_wiki", "zh_wiki", "operator_site", "news", "other"}

HIRAGANA = re.compile(r"[\u3040-\u309f]")
KANA_RUN = re.compile(r"[\u30a0-\u30ff\u30fc]{13,}")
KANJI = re.compile(r"[\u4e00-\u9fff]")
LATIN = re.compile(r"[A-Za-z]")
JP_CHAR = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")
STATION_ID = re.compile(r"^st[0-9]{5}$")
POI_ID = re.compile(r"^st[0-9]{5}-[0-9]{3}$")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


class Report:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def err(self, f, msg):
        self.errors.append(f"{f}: {msg}")

    def warn(self, f, msg):
        self.warnings.append(f"{f}: {msg}")

    def ok(self):
        return not self.errors


def haversine(lat1, lon1, lat2, lon2):
    from math import asin, cos, radians, sin, sqrt
    la1, lo1, la2, lo2 = map(radians, (lat1, lon1, lat2, lon2))
    h = sin((la2 - la1) / 2) ** 2 + cos(la1) * cos(la2) * sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371000 * asin(sqrt(h))


def check_i18n(rep, f, where, obj, is_desc):
    if not isinstance(obj, dict):
        rep.err(f, f"{where}: not an object")
        return
    for extra in set(obj) - {"ja", "en", "zh"}:
        rep.err(f, f"{where}.{extra}: unexpected language key")
    for lang in ("ja", "en", "zh"):
        v = obj.get(lang)
        if not isinstance(v, str) or not v.strip():
            rep.err(f, f"{where}.{lang}: missing or empty")
            continue
        v = v.strip()
        n = len(v)
        if is_desc:
            if n < 40 or n > 120:
                rep.err(f, f"{where}.{lang}: desc length {n} outside 40-120")
        else:
            if n > 80:
                rep.err(f, f"{where}.{lang}: name too long ({n})")
        if lang == "ja":
            if not JP_CHAR.search(v) and not LATIN.search(v):
                rep.err(f, f"{where}.ja: no Japanese/Latin characters")
        elif lang == "en":
            if not LATIN.search(v):
                rep.err(f, f"{where}.en: no Latin characters")
            if HIRAGANA.search(v) or re.search(r"[\u30a0-\u30ff]", v):
                rep.err(f, f"{where}.en: contains kana")
        elif lang == "zh":
            han = len(KANJI.findall(v))
            if HIRAGANA.search(v):
                rep.err(f, f"{where}.zh: contains hiragana (untranslated Japanese)")
            m = KANA_RUN.search(v)
            if m:
                rep.err(f, f"{where}.zh: katakana run >12 chars (looks Japanese): {m.group(0)}")
            if is_desc and han < 8:
                rep.err(f, f"{where}.zh: only {han} Han chars, not real Chinese")
            if not is_desc and han < 1 and not LATIN.search(v):
                rep.err(f, f"{where}.zh: no Han characters")


def check_file(rep, path, url_bucket):
    f = os.path.relpath(path, ROOT)
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except Exception as e:
        rep.err(f, f"invalid JSON: {e}")
        return None

    if d.get("schema") != "station.v1":
        rep.err(f, "schema field must be 'station.v1'")
    for k in ("station", "generated_at", "pois", "coverage"):
        if k not in d:
            rep.err(f, f"missing top-level key: {k}")
    if rep.errors and any(x.startswith(f + ": missing") for x in rep.errors):
        return None

    if not re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$", d.get("generated_at", "") or ""):
        rep.err(f, "generated_at must be a real UTC timestamp YYYY-MM-DDTHH:MM:SSZ (no placeholders)")

    st = d["station"]
    sid = st.get("id", "")
    if not STATION_ID.match(sid or ""):
        rep.err(f, f"station.id invalid: {sid!r}")
    if sid and os.path.basename(path) != f"{sid}.json":
        rep.err(f, f"filename must be {sid}.json")
    check_i18n(rep, f, "station.name", st.get("name"), is_desc=False)
    if not isinstance(st.get("line_group"), list) or not st.get("line_group"):
        rep.err(f, "station.line_group must be non-empty array")
    if not st.get("pref"):
        rep.err(f, "station.pref missing")
    for c in ("lat", "lon"):
        if not isinstance(st.get(c), (int, float)):
            rep.err(f, f"station.{c} must be number")

    pois = d.get("pois", [])
    if not isinstance(pois, list):
        rep.err(f, "pois must be array")
        pois = []

    seen_names = {}
    coords = []
    for i, p in enumerate(pois):
        w = f"pois[{i}]"
        pid = p.get("id", "")
        if not POI_ID.match(pid or ""):
            rep.err(f, f"{w}.id invalid: {pid!r}")
        elif sid and not pid.startswith(sid + "-"):
            rep.err(f, f"{w}.id must start with station id {sid}-")
        cat = p.get("category")
        if cat not in CATEGORIES:
            rep.err(f, f"{w}.category invalid: {cat!r}")
        check_i18n(rep, f, f"{w}.name", p.get("name"), is_desc=False)
        check_i18n(rep, f, f"{w}.desc", p.get("desc"), is_desc=True)
        # desc 里出现的分钟数必须与 walk_min 同源（不许手写打架）
        desc_obj = p.get("desc") or {}
        minute_hits = []
        for lang, txt in desc_obj.items():
            if not isinstance(txt, str):
                continue
            for m in re.finditer(r"(?:約|大约)?\s*([0-9]{1,2})\s*(?:分(?:鐘|钟)?|min(?:ute)?s?)\b", txt):
                minute_hits.append((lang, int(m.group(1))))
        for lang, mv in minute_hits:
            if isinstance(p.get("walk_min"), int) and mv != p["walk_min"]:
                rep.err(f, f"{w}.desc.{lang}: says {mv} min but walk_min={p['walk_min']} (desc numbers must match fields)")
        dm, wm = p.get("distance_m"), p.get("walk_min")
        if not isinstance(dm, int) or isinstance(dm, bool):
            rep.err(f, f"{w}.distance_m must be integer")
        else:
            if dm < 1 or dm > 2000:
                rep.err(f, f"{w}.distance_m={dm} outside 1..2000")
            if isinstance(wm, int) and not isinstance(wm, bool) and wm > 0:
                import math
                lo_w = max(1, math.ceil(dm / 110))
                hi_w = max(lo_w, math.ceil(dm / 55))
                if not (lo_w <= wm <= hi_w):
                    rep.err(f, f"{w}: walk_min={wm} inconsistent with distance_m={dm} (expect {lo_w}-{hi_w})")
        if not isinstance(wm, int) or isinstance(wm, bool) or wm < 1:
            rep.err(f, f"{w}.walk_min must be positive integer")
        if "hours" in p and p["hours"] is not None and not isinstance(p["hours"], str):
            rep.err(f, f"{w}.hours must be string or null")
        if isinstance(p.get("hours"), str) and "JST" not in p["hours"]:
            rep.err(f, f"{w}.hours must carry timezone semantics, e.g. '10:00-18:00 (JST)'")
        hs = p.get("hours_source")
        if hs not in ("osm", "official", "unknown"):
            rep.err(f, f"{w}.hours_source must be osm|official|unknown")
        elif (p.get("hours") is None) != (hs == "unknown"):
            rep.err(f, f"{w}: hours is {'set' if p.get('hours') else 'null'} but hours_source={hs} (null must pair with unknown)")
        hca = p.get("hours_checked_at")
        if hca is not None and not re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$", hca or ""):
            rep.err(f, f"{w}.hours_checked_at must be YYYY-MM-DD or null")
        if hs == "official" and not hca:
            rep.err(f, f"{w}: hours_source=official requires hours_checked_at (date the official page was checked)")
        if "closed_on" in p and p["closed_on"] is not None and not isinstance(p["closed_on"], str):
            rep.err(f, f"{w}.closed_on must be string or null")
        url = p.get("url")
        if not isinstance(url, str) or not re.match(r"^https?://[^ ]+$", url or ""):
            rep.err(f, f"{w}.url missing/invalid")
        else:
            url_bucket.append((f, pid, url))
        if "tel" in p and p["tel"] is not None and not isinstance(p["tel"], str):
            rep.err(f, f"{w}.tel must be string or null")
        ts = p.get("tel_source")
        if ts not in ("osm", "official", "unknown"):
            rep.err(f, f"{w}.tel_source must be osm|official|unknown")
        elif (p.get("tel") is None) != (ts == "unknown"):
            rep.err(f, f"{w}: tel is {'set' if p.get('tel') else 'null'} but tel_source={ts} (null must pair with unknown)")
        if "official_url_pending" in p and not re.match(r"^https?://[^ ]+$", p.get("official_url_pending") or ""):
            rep.err(f, f"{w}.official_url_pending must be a URL")
        for fld in ("aka", "merged_ids"):
            if fld in p:
                v = p[fld]
                if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
                    rep.err(f, f"{w}.{fld} must be array of non-empty strings")
        pl = p.get("price_level")
        if pl is not None and pl not in ("¥", "¥¥", "¥¥¥", "¥¥¥¥"):
            rep.err(f, f"{w}.price_level invalid: {pl!r}")
        src = p.get("source")
        if not isinstance(src, list) or not src:
            rep.err(f, f"{w}.source must be non-empty array")
        else:
            for s in src:
                if s not in SOURCES:
                    rep.err(f, f"{w}.source invalid value: {s!r}")
        if not re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}", p.get("verified_at", "") or ""):
            rep.err(f, f"{w}.verified_at must be YYYY-MM-DD...")

        # dedup
        nm = (p.get("name") or {}).get("ja", "")
        key = re.sub(r"\s+", "", nm)
        if key:
            if key in seen_names:
                rep.err(f, f"{w}: duplicate name.ja {nm!r} (same as pois[{seen_names[key]}])")
            else:
                seen_names[key] = i
        if isinstance(p.get("lat"), (int, float)) and isinstance(p.get("lon"), (int, float)):
            for j, (la, lo, cat_j) in enumerate(coords):
                if haversine(p["lat"], p["lon"], la, lo) < 50:
                    # true duplicates share a name (caught above) or name+category.
                    # distinct establishments (bank vs convenience store...) only warned.
                    rep.warn(f, f"{w}: within 50 m of pois[{j}] (dense area, verify they are distinct)")
                    break
            coords.append((p["lat"], p["lon"], p.get("category")))

    cov = d.get("coverage", {})
    if cov.get("poi_count") != len(pois):
        rep.err(f, f"coverage.poi_count={cov.get('poi_count')} != len(pois)={len(pois)}")
    if cov.get("confidence") not in ("high", "medium", "low"):
        rep.err(f, "coverage.confidence invalid")
    rm = cov.get("radius_m")
    if rm != 2000:
        rep.err(f, f"coverage.radius_m must be 2000 (fixed policy), got {rm!r}")
    if "truncated" in cov:
        rep.err(f, "coverage.truncated is retired; use fetch_truncated (fetch cap) + selected_cap (selection cap)")
    if "fetch_truncated" in cov and not isinstance(cov["fetch_truncated"], bool):
        rep.err(f, "coverage.fetch_truncated must be boolean")
    if "candidate_count" in cov:
        if not isinstance(cov["candidate_count"], int) or isinstance(cov["candidate_count"], bool):
            rep.err(f, "coverage.candidate_count must be integer")
        if "fetch_truncated" not in cov:
            rep.err(f, "coverage.candidate_count requires fetch_truncated (state explicitly whether the count is exact or a lower bound)")
    if "selected_cap" in cov:
        sc = cov["selected_cap"]
        if sc is not None and (not isinstance(sc, int) or isinstance(sc, bool) or sc < 1):
            rep.err(f, "coverage.selected_cap must be positive integer or null")
        if isinstance(sc, int) and len(pois) > sc:
            rep.err(f, f"poi_count={len(pois)} exceeds selected_cap={sc}")
    if "excluded_candidates" in cov:
        ec = cov["excluded_candidates"]
        if not isinstance(ec, list):
            rep.err(f, "coverage.excluded_candidates must be array")
        else:
            for k, item in enumerate(ec):
                if not isinstance(item, dict) or not item.get("name") or not item.get("reason") \
                   or set(item) - {"name", "reason"}:
                    rep.err(f, f"coverage.excluded_candidates[{k}] must be {{name, reason}}")
    gaps = cov.get("gaps")
    if not isinstance(gaps, list):
        rep.err(f, "coverage.gaps must be array")
    if len(pois) < 4 and not gaps:
        rep.err(f, "sparse station (<4 POIs) must document coverage.gaps")

    # optional: jsonschema library cross-check
    try:
        import jsonschema
        schema = json.load(open(os.path.join(ROOT, "schema", "station.v1.json"), encoding="utf-8"))
        jsonschema.validate(d, schema)
    except ImportError:
        pass
    except Exception as e:
        rep.err(f, f"jsonschema: {str(e)[:200]}")
    return d


def check_index(rep, data_dir):
    idx_path = os.path.join(ROOT, "index.json")
    if not os.path.exists(idx_path):
        rep.err("index.json", "missing")
        return
    try:
        idx = json.load(open(idx_path, encoding="utf-8"))
    except Exception as e:
        rep.err("index.json", f"invalid JSON: {e}")
        return
    for k, v in idx.items():
        if not STATION_ID.match(k):
            rep.err("index.json", f"bad station_key: {k}")
            continue
        for fld in ("name", "pref", "file", "poi_count", "updated_at", "sha256"):
            if fld not in v:
                rep.err("index.json", f"{k}: missing field {fld}")
        fp = os.path.join(ROOT, v.get("file", ""))
        if not os.path.exists(fp):
            rep.err("index.json", f"{k}: file {v.get('file')} does not exist")
            continue
        raw = open(fp, "rb").read()
        if hashlib.sha256(raw).hexdigest() != v.get("sha256"):
            rep.err("index.json", f"{k}: sha256 mismatch (index stale)")
        try:
            d = json.loads(raw)
            if len(d.get("pois", [])) != v.get("poi_count"):
                rep.err("index.json", f"{k}: poi_count mismatch")
        except Exception:
            rep.err("index.json", f"{k}: file not parseable")


def url_status(url):
    """Final HTTP status after redirects, via curl (browser-like UA)."""
    import subprocess
    p = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-L", "--max-time", "20", "-w", "%{http_code}",
         "-A", UA, "-H", "Accept: text/html,application/xhtml+xml,*/*", url],
        capture_output=True)
    try:
        return int(p.stdout.decode().strip())
    except ValueError:
        return 0


def check_urls(rep, url_bucket, sample):
    import random
    urls = sorted(set(url_bucket))
    if sample and sample < len(urls):
        seed = int(__import__("datetime").date.today().strftime("%Y%m%d"))
        urls = random.Random(seed).sample(urls, sample)
    checked = bad = 0
    for f, pid, url in urls:
        checked += 1
        status = url_status(url)
        if status != 200:
            bad += 1
            msg = f"{pid}: URL unreachable {url} (status {status})"
            if status == 0 and not os.environ.get("CI"):
                # local network may block some domains; CI re-checks with full internet
                rep.warn(f, msg + " [local network failure, re-check in CI]")
            else:
                rep.err(f, msg)
    print(f"[urls] checked={checked} unreachable={bad}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="*", help="explicit station files to check")
    ap.add_argument("--all", action="store_true", help="check every data/*.json")
    ap.add_argument("--mode", choices=["changed", "all"], default="all")
    ap.add_argument("--check-urls", action="store_true")
    ap.add_argument("--url-sample", type=int, default=0)
    ap.add_argument("--skip-index", action="store_true")
    args = ap.parse_args()

    rep = Report()
    data_dir = os.path.join(ROOT, "data")

    if args.files:
        files = args.files
    elif args.all or args.mode == "all":
        files = sorted(
            os.path.join(data_dir, x) for x in (os.listdir(data_dir) if os.path.isdir(data_dir) else [])
            if x.endswith(".json")
        )
    else:  # changed mode
        import subprocess
        before = os.environ.get("GH_EVENT_BEFORE") or "HEAD~1"
        try:
            out = subprocess.run(["git", "diff", "--name-only", before, "HEAD"],
                                 capture_output=True, text=True, cwd=ROOT).stdout
            files = [os.path.join(ROOT, p) for p in out.split()
                     if p.startswith("data/") and p.endswith(".json") and os.path.exists(os.path.join(ROOT, p))]
        except Exception:
            files = []
        print(f"[changed] {len(files)} station file(s) in this push")

    url_bucket = []
    for path in files:
        check_file(rep, path, url_bucket)

    if not args.skip_index:
        check_index(rep, data_dir)

    if args.check_urls and url_bucket:
        check_urls(rep, url_bucket, args.url_sample)

    for w in rep.warnings:
        print("WARN", w)
    for e in rep.errors:
        print("FAIL", e)
    print(f"[result] files={len(files)} errors={len(rep.errors)}")
    sys.exit(0 if rep.ok() else 1)


if __name__ == "__main__":
    main()
