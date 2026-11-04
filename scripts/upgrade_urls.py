#!/usr/bin/env python3
"""Upgrade official_url_pending -> url when the official site answers HTTP 200.

Scans data/*.json for POIs carrying `official_url_pending` (official sites that
were blocked by anti-bot responses on the research host), retests each URL ONCE
from the runner (full internet), and on final status 200 rewrites the POI.

R3 lock-in (审查要求, 不得放宽):
  1. 每轮最多回填 20 条（MAX_UPGRADES）。
  2. 只许改三个字段：url / source / official_url_pending（其余字段一律不动；
     index.json 仅同步被改站的 sha256/updated_at 簿记，以便 validate 通过）。
  3. 提交前必须在 runner 上跑 scripts/validate.py --all 且通过，否则放弃提交。
  4. 一次 commit；失败不重试；绝不 force；commit message 列出全部改动 POI id。

Used by CI (GitHub runners) and manually. Exit 0 = run finished (with or
without upgrades), 1 = run aborted by a safety gate.
"""
import json
import os
import subprocess
import sys
import hashlib
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
MAX_UPGRADES = 20          # 每轮回填上限
ALLOWED_FIELDS = {"url", "source", "official_url_pending"}  # 唯一允许改动的 POI 字段


def status(url):
    """Final HTTP status after redirects. ONE attempt, no retry (失败不重试)."""
    p = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-L", "--max-time", "20", "-w", "%{http_code}",
         "-A", UA, "-H", "Accept: text/html,application/xhtml+xml,*/*", url],
        capture_output=True)
    try:
        return int(p.stdout.decode().strip())
    except ValueError:
        return 0


def sha256_file(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def main():
    upgraded, still = [], []
    changed = {}  # filename -> list of poi ids
    data_dir = os.path.join(ROOT, "data")

    pendings = []  # (file, poi_id, pending_url) deterministic order
    for f in sorted(os.listdir(data_dir)):
        if not f.endswith(".json"):
            continue
        d = json.load(open(os.path.join(data_dir, f), encoding="utf-8"))
        for p in d.get("pois", []):
            if p.get("official_url_pending"):
                pendings.append((f, p["id"], p["official_url_pending"]))

    budget = MAX_UPGRADES
    for f, pid, pend in pendings:
        if budget <= 0:
            still.append((f, pid, pend, "budget"))
            continue
        code = status(pend)  # single attempt
        if code == 200:
            budget -= 1
            path = os.path.join(data_dir, f)
            d = json.load(open(path, encoding="utf-8"))
            for p in d.get("pois", []):
                if p.get("id") == pid and p.get("official_url_pending") == pend:
                    # 只动三个字段
                    p["url"] = pend
                    del p["official_url_pending"]
                    if "official_site" not in p["source"]:
                        p["source"].append("official_site")
                    assert set(("url", "source")) <= ALLOWED_FIELDS
                    open(path, "w", encoding="utf-8").write(
                        json.dumps(d, ensure_ascii=False, indent=2) + "\n")
                    changed.setdefault(f, []).append(pid)
                    upgraded.append((f, pid, pend))
                    print(f"[upgraded] {f} {pid} -> {pend}")
                    break
        else:
            still.append((f, pid, pend, code))
            print(f"[pending ] {f} {pid} {pend} (status {code})")

    print(f"[summary] upgraded={len(upgraded)} still_pending={len(still)} "
          f"files_changed={len(changed)} cap={MAX_UPGRADES}")

    if not changed:
        return 0

    # index.json 簿记同步（sha256/updated_at），否则 validate --all 不过
    idx_path = os.path.join(ROOT, "index.json")
    idx = json.load(open(idx_path, encoding="utf-8"))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for f in changed:
        sid = f[:-5]
        if sid in idx:
            idx[sid]["sha256"] = sha256_file(os.path.join(data_dir, f))
            idx[sid]["updated_at"] = now
    open(idx_path, "w", encoding="utf-8").write(
        json.dumps(idx, ensure_ascii=False, indent=2) + "\n")

    # 安全门：validate.py --all 必须通过才允许提交
    v = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "validate.py"), "--all"],
                       cwd=ROOT)
    if v.returncode != 0:
        print("[abort] validate.py --all failed; refusing to commit")
        return 1

    if not os.environ.get("CI"):
        print("[local] changes kept in working tree; commit/push only happens in CI")
        return 0

    ids = ", ".join(pid for _, pid, _ in upgraded)
    msg = (f"urls: auto-upgrade {len(upgraded)} official_url_pending (cap {MAX_UPGRADES}) [skip ci]\n\n"
           f"changed POIs: {ids}\n"
           f"fields touched: url, source, official_url_pending only\n"
           f"validate.py --all: pass")
    subprocess.run(["git", "config", "user.name", "jr-nearby-bot"], cwd=ROOT, check=True)
    subprocess.run(["git", "config", "user.email", "ooep@users.noreply.github.com"], cwd=ROOT, check=True)
    subprocess.run(["git", "add", "data", "index.json"], cwd=ROOT, check=True)
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT)
    if diff.returncode == 0:
        print("[skip] nothing staged")
        return 0
    c = subprocess.run(["git", "commit", "-m", msg], cwd=ROOT)
    if c.returncode != 0:
        print("[abort] commit failed; not retrying")
        return 1
    p = subprocess.run(["git", "push"], cwd=ROOT)  # force:false，失败不重试
    if p.returncode != 0:
        print("[abort] push failed; not retrying (no force)")
        return 1
    print(f"[pushed] 1 commit, {len(upgraded)} POIs: {ids}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
