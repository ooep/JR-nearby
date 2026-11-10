#!/usr/bin/env python3
"""Consume runner-produced Overpass candidates and ship station files.

Per batch: candidate JSON -> t1cache -> t1_gen.build_station -> single-file
validate (skip index) -> index/manifest refresh -> full validate (jsonschema)
-> one commit containing every new data file + index + manifest.

The local workstation cannot reach official sites, so url_status is forced to
0: official URLs are recorded as official_url_pending (honest accounting, no
fabricated 200) and the CI upgrade-pending-urls job retests them from the
runner. Nothing is pushed unless the full validate reports 0 errors.

Usage:
  GITHUB_TOKEN=... python3 runner_batch.py <artifact_dir> [--push]
"""
import datetime
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
PYLIB = os.environ.get("JR_PYLIB", "/private/tmp/jrnearby_vcheck/pylib")


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_py(args, token=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = PYLIB
    if token:
        env["GITHUB_TOKEN"] = token
    return subprocess.run([sys.executable] + args, capture_output=True, text=True, env=env)


def validate_files(paths):
    r = run_py(["repo/scripts/validate.py", "--files", *paths, "--skip-index"])
    return r.returncode == 0, (r.stdout + r.stderr)


def validate_all():
    r = run_py(["repo/scripts/validate.py", "--all"])
    return r.returncode == 0, (r.stdout + r.stderr)


def remote_head(token):
    r = run_py(["t1_remote.py", "head"], token)
    return r.stdout.strip() if r.returncode == 0 else None


def make_cache(slug, cands, meta):
    m = meta[slug]
    st = {k: m[k] for k in ("slug", "name", "name_en", "lat", "lon", "pref", "lines", "riders")
          if k in m and m[k] is not None}
    doc = {"station": st, "fetch_truncated": False, "candidates": cands}
    with open(f"t1cache/{slug}.json", "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)


def generate(slug):
    import t1_gen
    t1_gen.url_status = lambda u: 0
    doc, review = t1_gen.build_station(slug, check_urls=True)
    with open(f"repo/data/{slug}.json", "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    return doc, review


def refresh_index_manifest(new_slugs):
    idx = json.load(open("repo/index.json", encoding="utf-8"))
    for slug in new_slugs:
        raw = open(f"repo/data/{slug}.json", "rb").read()
        doc = json.loads(raw)
        idx[slug] = {
            "file": f"data/{slug}.json",
            "name": doc["station"]["name"]["ja"],
            "poi_count": len(doc["pois"]),
            "pref": doc["station"]["pref"],
            "sha256": hashlib.sha256(raw).hexdigest(),
            "updated_at": now(),
        }
    with open("repo/index.json", "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=2)
        f.write("\n")

    man = json.load(open("repo/manifest.json", encoding="utf-8"))
    man["done"] = len(idx)
    man["updated_at"] = now()
    by_pref = {}
    for v in idx.values():
        by_pref[v["pref"]] = by_pref.get(v["pref"], 0) + 1
    man["by_pref"] = dict(sorted(by_pref.items()))
    with open("repo/manifest.json", "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return len(idx)


def merge_remote_index(token, accepted):
    """Adopt remote index entries for stations not generated in this batch.

    The URL backfill job may commit data/*.json together with a resynced
    index.json while this job is fetching (pull_index ran up to ~15 min
    earlier). Rebuilding entries from our stale checkout would resurrect
    sha256 mismatches, so every already-published entry is taken verbatim
    from the remote right before pushing; only this batch's own entries
    stay locally computed.
    """
    import base64 as _b64
    keep = set(accepted)
    try:
        p = subprocess.run(
            ["curl", "-s", "-m", "30",
             "-H", "Authorization: Bearer " + token,
             "-H", "Accept: application/vnd.github+json",
             "https://api.github.com/repos/ooep/JR-nearby/contents/index.json?ref=main"],
            capture_output=True, text=True, timeout=45)
        d = json.loads(p.stdout)
        remote = json.loads(_b64.b64decode("".join(d["content"].split())))
    except Exception as e:
        print("[merge] remote index unavailable, keeping local: %s" % str(e)[:90],
              flush=True)
        return
    local = json.load(open("repo/index.json", encoding="utf-8"))
    adopted = 0
    for k, v in remote.items():
        if k not in keep and k in local and local[k].get("sha256") != v.get("sha256"):
            local[k] = v
            adopted += 1
    with open("repo/index.json", "w", encoding="utf-8") as f:
        json.dump(local, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("[merge] adopted %d updated remote index entries before push" % adopted,
          flush=True)


def main():
    art = sys.argv[1]
    do_push = "--push" in sys.argv
    token = os.environ.get("GITHUB_TOKEN")
    meta = {s["slug"]: s for s in json.load(open("stations_all.json", encoding="utf-8"))["stations"]}

    rep = json.load(open(os.path.join(art, "_report.json"), encoding="utf-8"))
    print("[runner report] radius=%s started=%s" % (rep.get("radius"), rep.get("started_at")))
    for it in rep["items"]:
        print("  %-9s ok=%-5s cands=%-4s %ss %s" %
              (it["slug"], it["ok"], it["candidates"], it["secs"],
               (it.get("stderr") or "")[:90]))

    os.makedirs("t1_quarantine", exist_ok=True)
    accepted, failed = [], []
    for it in rep["items"]:
        slug = it["slug"]
        if not it.get("ok"):
            failed.append((slug, "runner-fetch-failed"))
            continue
        if os.path.exists(f"repo/data/{slug}.json"):
            failed.append((slug, "already-present"))
            continue
        cands = json.load(open(os.path.join(art, slug + ".json"), encoding="utf-8"))
        raw_n = len(cands)
        # Overpass `around:2000` matches ways by ANY of their member nodes, so
        # a way's centroid can legitimately sit beyond 2000 m. Such candidates
        # are outside our declared coverage radius and must not be published:
        # validate.py enforces pois[].distance_m <= 2000. Drop them explicitly
        # and log the count; never clamp or rewrite a distance to make it fit.
        cands = [c for c in cands
                 if isinstance(c.get("distance_m"), (int, float))
                 and 1 <= c["distance_m"] <= 2000]
        if len(cands) != raw_n:
            print("[filter] %-9s dropped %d/%d out-of-radius candidates (>2000m)"
                  % (slug, raw_n - len(cands), raw_n))
        if not cands:
            failed.append((slug, "empty-candidates-after-radius-filter"))
            continue
        make_cache(slug, cands, meta)
        doc, review = generate(slug)
        ok, out = validate_files([f"repo/data/{slug}.json"])
        if not ok:
            os.replace(f"repo/data/{slug}.json", f"t1_quarantine/{slug}.json")
            failed.append((slug, "validate: " + out.strip()[-180:]))
            print("[FAIL] %s validate\n%s" % (slug, out[-600:]))
            continue
        accepted.append(slug)
        print("[gen] %-9s %-10s pois=%-3d cands=%-4d review=%d pending=%d" %
              (slug, doc["station"]["name"]["ja"], len(doc["pois"]),
               len(cands), len(review),
               sum(1 for p in doc["pois"] if p.get("official_url_pending"))))

    if not accepted:
        print("[abort] no station accepted; nothing changed")
        return 1

    total = refresh_index_manifest(accepted)
    ok, out = validate_all()
    tail = "\n".join(out.strip().splitlines()[-3:])
    print("[validate-all] %s | %s" % ("PASS" if ok else "FAIL", tail))
    if not ok:
        print(out[-3000:])
        print("[abort] full validate failed; NOT pushing")
        return 1

    print("[ready] accepted=%s index=%d failed=%s" % (accepted, total, failed))
    if not do_push:
        print("[dry-run] pass --push to publish")
        return 0
    if not token:
        print("[abort] GITHUB_TOKEN missing")
        return 1

    merge_remote_index(token, accepted)
    head = remote_head(token)
    print("[pre-push head] %s" % head)
    args = [f"repo/data/{s}.json=data/{s}.json" for s in accepted]
    args += ["repo/index.json=index.json", "repo/manifest.json=manifest.json"]
    msg = ("data: runner batch %d stations (%s); index/manifest -> %d stations"
           % (len(accepted), ",".join(accepted), total))
    r = run_py(["gh_push.py", "--repo", "ooep/JR-nearby", "--branch", "main",
                "--message", msg, *args], token)
    print("[push] rc=%s %s" % (r.returncode, (r.stdout + r.stderr).strip()[-400:]))
    if r.returncode != 0:
        return 1
    print("[post-push head] %s" % remote_head(token))
    return 0


if __name__ == "__main__":
    sys.exit(main())
