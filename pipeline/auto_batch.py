#!/usr/bin/env python3
"""Self-sustaining Nearby batch runner. Executes ON the CI runner.

Every round:
  1. pull the freshest index.json / manifest.json from the remote (so the queue
     is always derived from what is actually published);
  2. build the queue = t2_order first, then every remaining slug in
     stations_all.json, minus already-published slugs;
  3. serially fetch Overpass candidates (low frequency, one retry, spacing),
     assemble station files with t1_gen, validate with the official validator,
     and publish one atomic commit via gh_push.py (force=false);
  4. stations that fail are excluded for the rest of THIS job (they are picked
     up again by the next scheduled job, because the queue is rebuilt from the
     remote). A failed station never blocks the rest of the queue.

Exits cleanly at the time budget; the scheduled next run continues from the
remote head. No state lives on the workstation.
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

API = "https://api.github.com/repos/ooep/JR-nearby"


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def api_get(path, token):
    cfg = ('header = "Authorization: Bearer %s"\n'
           'header = "Accept: application/vnd.github+json"\n' % token)
    r = subprocess.run(["curl", "-sL", "-m", "40", "--config", "-", API + path],
                       input=cfg.encode(), capture_output=True)
    return json.loads(r.stdout.decode("utf-8", "replace"))


def pull_index(work, token):
    """Always derive state from the remote, never from a stale local copy."""
    for name in ("index.json", "manifest.json"):
        d = api_get("/contents/%s?ref=main" % name, token)
        if "content" not in d:
            raise RuntimeError("cannot pull %s: %s" % (name, str(d)[:160]))
        raw = base64.b64decode(d["content"])
        with open(os.path.join(work, "repo", name), "wb") as f:
            f.write(raw)


def build_queue(work):
    idx = json.load(open(os.path.join(work, "repo", "index.json"), encoding="utf-8"))
    done = set(idx)
    t2 = json.load(open(os.path.join(work, "t2_order.json"), encoding="utf-8"))
    universe = {s["slug"]: s for s in
                json.load(open(os.path.join(work, "stations_all.json"),
                               encoding="utf-8"))["stations"]}
    seen, seq = set(), []
    for slug in t2:
        if slug in universe and slug not in seen:
            seen.add(slug)
            seq.append(slug)
    for slug in sorted(universe):
        if slug not in seen:
            seen.add(slug)
            seq.append(slug)
    return [s for s in seq if s not in done], universe


def fetch_one(work, slug, meta, radius, cdir):
    dest = os.path.join(cdir, slug + ".json")
    t0 = time.time()
    err = ""
    for attempt in range(1, 3):
        try:
            r = subprocess.run(
                [sys.executable, "repo/scripts/fetch_overpass.py",
                 "--lat", str(meta["lat"]), "--lon", str(meta["lon"]),
                 "--radius", str(radius), "--out", dest],
                cwd=work, capture_output=True, text=True, timeout=300)
            n = 0
            if r.returncode == 0 and os.path.exists(dest):
                try:
                    cands = json.load(open(dest, encoding="utf-8"))
                    n = len(cands)
                except Exception:
                    n = 0
            if n > 0:
                return {"slug": slug, "ok": True, "candidates": n,
                        "secs": round(time.time() - t0, 1),
                        "attempts": attempt, "stderr": ""}
            err = ((r.stderr or r.stdout or "")[-220:]).replace("\n", " ")
        except subprocess.TimeoutExpired:
            err = "timeout 300s"
        if os.path.exists(dest):
            os.remove(dest)
        if attempt == 1:
            time.sleep(12)
    return {"slug": slug, "ok": False, "candidates": 0,
            "secs": round(time.time() - t0, 1), "attempts": 2, "stderr": err}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--max-stations", type=int, default=200)
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--spacing", type=int, default=20)
    ap.add_argument("--deadline-minutes", type=int, default=310)
    ap.add_argument("--radius", type=int, default=2000)
    a = ap.parse_args()
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        sys.exit("[abort] GITHUB_TOKEN missing")

    work = a.work
    os.chdir(work)
    for d in ("t1cache", "t1_quarantine", "candidates"):
        os.makedirs(d, exist_ok=True)

    start = time.time()
    deadline = start + a.deadline_minutes * 60
    pushed, skipped, rounds = 0, set(), 0
    print("[auto] start=%s max=%d batch=%d spacing=%ds deadline=%dmin radius=%d"
          % (now(), a.max_stations, a.batch_size, a.spacing,
             a.deadline_minutes, a.radius), flush=True)

    while pushed < a.max_stations and time.time() < deadline:
        # time left for one more round? (worst case ~300s per station)
        if time.time() + a.batch_size * 330 > deadline:
            print("[auto] time budget reached, stopping cleanly", flush=True)
            break
        pull_index(work, token)
        queue, universe = build_queue(work)
        pick = [s for s in queue if s not in skipped][:a.batch_size]
        if not pick:
            print("[done] no fresh stations remain in this job", flush=True)
            break
        rounds += 1
        cdir = os.path.join(work, "candidates", "r%04d" % rounds)
        os.makedirs(cdir, exist_ok=True)
        items = []
        fails = 0
        for i, slug in enumerate(pick):
            it = fetch_one(work, slug, universe[slug], a.radius, cdir)
            items.append(it)
            print("  [fetch] %-9s ok=%-5s cands=%-4s %ss att=%s %s"
                  % (slug, it["ok"], it["candidates"], it["secs"],
                     it["attempts"], it["stderr"][:70]), flush=True)
            if it["ok"]:
                fails = 0
            else:
                fails += 1
                if fails >= 2:
                    print("[auto] %d consecutive fetch failures; upstream "
                          "likely throttled, cutting this round short" % fails,
                          flush=True)
                    break
            if i < len(pick) - 1:
                time.sleep(a.spacing)
        with open(os.path.join(cdir, "_report.json"), "w", encoding="utf-8") as f:
            json.dump({"radius": a.radius, "started_at": now(), "items": items},
                      f, ensure_ascii=False)

        r = subprocess.run([sys.executable, "runner_batch.py", cdir, "--push"],
                           cwd=work, env=dict(os.environ),
                           capture_output=True, text=True, timeout=900)
        out = (r.stdout or "") + (r.stderr or "")
        push_ok = "[push] rc=0" in out and "[post-push head]" in out
        if not push_ok:
            # Never swallow a crash: keyword-filtered logging once hid a
            # ModuleNotFoundError and made a green run look successful.
            print("[warn] assembler/publisher output (full tail):", flush=True)
            for line in out.strip().splitlines()[-60:]:
                print("    " + line, flush=True)
        else:
            for line in out.splitlines():
                if any(k in line for k in ("[filter]", "[gen]", "[result]",
                                           "[validate-all]", "[ready]", "[push]",
                                           "[pre-push head]", "[post-push head]")):
                    print("    " + line, flush=True)
        m = re.search(r"\[ready\] accepted=\[([^\]]*)\]", out)
        accepted = []
        if m:
            accepted = [x.strip().strip("'\"") for x in m.group(1).split(",")
                        if x.strip()]
        if not push_ok:
            # Roll back every file this round may have produced, so a later
            # round never sees "already-present" for an unpublished station.
            for slug in pick:
                for rel in ("repo/data/%s.json" % slug,
                            "t1cache/%s.json" % slug):
                    pth = os.path.join(work, rel)
                    if os.path.exists(pth):
                        os.remove(pth)
            pull_index(work, token)
            # Do not spin on the same queue head inside one job; the next
            # scheduled job rebuilds the queue from the remote and retries.
            for slug in pick:
                skipped.add(slug)
            print("[warn] publish did not land; rolled back and deferred "
                  "%d stations to a later job" % len(pick), flush=True)
            if items and all(not it["ok"] for it in items):
                print("[stop] every fetch failed; endpoint likely down, "
                      "not spinning further", flush=True)
                time.sleep(15)
                break
            time.sleep(15)
            continue

        for slug in pick:
            if slug not in accepted:
                skipped.add(slug)
        pushed += len(accepted)
        print("[round %d] accepted=%d total=%d skipped=%d elapsed=%.1fmin"
              % (rounds, len(accepted), pushed, len(skipped),
                 (time.time() - start) / 60), flush=True)
        if all(not it["ok"] for it in items):
            print("[stop] every fetch failed; endpoint likely down, "
                  "not spinning further", flush=True)
            break
        # keep disk bounded: candidates for this round are no longer needed
        for it in items:
            p = os.path.join(cdir, it["slug"] + ".json")
            if os.path.exists(p):
                os.remove(p)

    print("[final] pushed=%d rounds=%d skipped=%d elapsed=%.1fmin at %s"
          % (pushed, rounds, len(skipped), (time.time() - start) / 60, now()),
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
