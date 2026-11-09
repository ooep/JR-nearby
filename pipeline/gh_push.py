#!/usr/bin/env python3
"""Push files to GitHub via the Git Data API (blob-level commits, no clone).

Token: read from env GITHUB_TOKEN or GH_TOKEN. Never printed, never passed
via argv, never written to disk. Obtain it e.g. with:
  GITHUB_TOKEN="$(printf 'protocol=https\\nhost=github.com\\nusername=x-access-token\\n\\n' \
      | git credential fill | sed -n 's/^password=//p')"

Usage:
  python3 scripts/gh_push.py --repo ooep/JR-nearby --branch main \
      --message "..." local_path=remote_path [more ...]
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile

API = "https://api.github.com"


def req(method, path, token, body=None):
    """HTTP via curl --config - (token travels on stdin, never in argv).

    Large payloads (index.json grows with every station, and the station
    baseline is multi-MB) are fed on stdin with --data-binary @- instead of
    being passed in argv: Linux caps a single argv entry at 128 KiB. The curl
    config then moves to a 0600 temp file and is removed immediately.
    """
    url = API + path
    cfg = (f'header = "Authorization: Bearer {token}"\n'
           'header = "Accept: application/vnd.github+json"\n'
           'header = "User-Agent: jr-nearby-push"\n')
    payload = None
    if body is not None:
        cfg += 'header = "Content-Type: application/json"\n'
        payload = json.dumps(body)
    if payload is not None and len(payload) > 60000:
        fd, cfg_path = tempfile.mkstemp(prefix="ghpush-", suffix=".cfg")
        try:
            os.write(fd, cfg.encode())
            os.close(fd)
            os.chmod(cfg_path, 0o600)
            cmd = ["curl", "-s", "-X", method, "--config", cfg_path,
                   "--max-time", "180", "-w", "\n%{http_code}",
                   "--data-binary", "@-", url]
            p = subprocess.run(cmd, input=payload.encode(), capture_output=True)
        finally:
            os.unlink(cfg_path)
    else:
        cmd = ["curl", "-s", "-X", method, "--config", "-", "--max-time", "90",
               "-w", "\n%{http_code}", url]
        if payload is not None:
            cmd += ["-d", payload]
        p = subprocess.run(cmd, input=cfg.encode(), capture_output=True)
    out = p.stdout.decode(errors="replace")
    body_txt, _, code_txt = out.rpartition("\n")
    try:
        code = int(code_txt.strip())
    except ValueError:
        sys.exit(f"curl failed: {p.stderr.decode(errors='replace')[:300]}")
    try:
        return code, json.loads(body_txt or "{}")
    except Exception:
        return code, {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--branch", default="main")
    ap.add_argument("--message", required=True)
    ap.add_argument("--force", default=False, action="store_true",
                    help="refuse to run unless explicitly set; we keep this False")
    ap.add_argument("files", nargs="+", help="local=remote pairs")
    args = ap.parse_args()

    if args.force:
        sys.exit("refusing: force pushes are forbidden by project policy")
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        sys.exit("no token: set GITHUB_TOKEN env var (see module docstring)")

    # 1. current branch head (may not exist on an empty repo)
    code, ref = req("GET", f"/repos/{args.repo}/git/ref/heads/{args.branch}", token)
    base_commit = ref.get("object", {}).get("sha") if code == 200 else None

    base_tree = None
    if base_commit:
        code, c = req("GET", f"/repos/{args.repo}/git/commits/{base_commit}", token)
        if code != 200:
            sys.exit(f"cannot read base commit: {code}")
        base_tree = c["tree"]["sha"]

    # 2. blobs
    tree_items = []
    for pair in args.files:
        local, _, remote = pair.partition("=")
        remote = remote or os.path.basename(local)
        raw = open(local, "rb").read()
        code, b = req("POST", f"/repos/{args.repo}/git/blobs", token,
                      {"content": base64.b64encode(raw).decode(), "encoding": "base64"})
        if code not in (200, 201):
            sys.exit(f"blob create failed for {remote}: {code} {b.get('message')}")
        tree_items.append({"path": remote, "mode": "100644", "type": "blob", "sha": b["sha"]})

    # 3. tree, 4. commit, 5. ref update (force=false)
    body = {"tree": tree_items}
    if base_tree:
        body["base_tree"] = base_tree
    code, t = req("POST", f"/repos/{args.repo}/git/trees", token, body)
    if code not in (200, 201):
        sys.exit(f"tree create failed: {code} {t.get('message')}")

    cbody = {"message": args.message, "tree": t["sha"]}
    if base_commit:
        cbody["parents"] = [base_commit]
    code, c = req("POST", f"/repos/{args.repo}/git/commits", token, cbody)
    if code not in (200, 201):
        sys.exit(f"commit create failed: {code} {c.get('message')}")

    if base_commit:
        code, r = req("PATCH", f"/repos/{args.repo}/git/refs/heads/{args.branch}", token,
                      {"sha": c["sha"], "force": False})
    else:
        code, r = req("POST", f"/repos/{args.repo}/git/refs", token,
                      {"ref": f"refs/heads/{args.branch}", "sha": c["sha"]})
    if code not in (200, 201):
        sys.exit(f"ref update failed: {code} {r.get('message')}")
    print(f"[pushed] {args.repo}@{args.branch} {c['sha'][:10]} files={len(tree_items)}")


if __name__ == "__main__":
    main()
