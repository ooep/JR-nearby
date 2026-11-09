#!/usr/bin/env python3
"""ooep/JR-nearby 远端辅助（令牌走 GITHUB_TOKEN 环境变量，运行时从钥匙串注入）。
  head  -> 打印远端 main head sha
  sync  -> 以远端树为准镜像本地 repo/（git blob sha 不同的文件下载覆盖），打印 head 与改动
"""
import base64, hashlib, json, os, subprocess, sys, time

REPO = "ooep/JR-nearby"
API = "https://api.github.com"
ROOT = "repo"


def req(method, path, body=None):
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        sys.exit("no token: set GITHUB_TOKEN")
    cfg = (f'header = "Authorization: Bearer {token}"\n'
           'header = "Accept: application/vnd.github+json"\n'
           'header = "User-Agent: jr-nearby-push"\n')
    cmd = ["curl", "-s", "-X", method, "--config", "-", "--max-time", "60", API + path]
    if body is not None:
        cfg += 'header = "Content-Type: application/json"\n'
        cmd += ["-d", json.dumps(body)]
    p = subprocess.run(cmd, input=cfg.encode(), capture_output=True)
    out = p.stdout.decode(errors="replace")
    try:
        return json.loads(out or "{}")
    except Exception:
        return {"_raw": out[:300]}


def git_blob_sha(content):
    return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()


def remote_tree():
    import time as _t
    ref = {}
    for _ in range(3):
        ref = req("GET", f"/repos/{REPO}/git/ref/heads/main")
        if ref.get("object", {}).get("sha"):
            break
        _t.sleep(10)
    head = ref.get("object", {}).get("sha")
    if not head:
        sys.exit(f"no head: {ref}")
    for _ in range(3):
        c = req("GET", f"/repos/{REPO}/git/commits/{head}")
        if "tree" in c:
            break
        _t.sleep(10)
    if "tree" not in c:
        sys.exit(f"cannot read commit tree (transient API error): {str(c)[:200]}")
    for _ in range(3):
        t = req("GET", f"/repos/{REPO}/git/trees/{c['tree']['sha']}?recursive=1")
        if "tree" in t:
            break
        _t.sleep(10)
    if "tree" not in t:
        sys.exit(f"cannot read tree (transient API error): {str(t)[:200]}")
    return head, {e["path"]: e["sha"] for e in t.get("tree", []) if e["type"] == "blob"}


def cmd_head():
    head, _ = remote_tree()
    print(head)


def cmd_sync():
    head, tree = remote_tree()
    changed = []
    for path, rsha in sorted(tree.items()):
        lp = os.path.join(ROOT, path)
        local = open(lp, "rb").read() if os.path.exists(lp) else None
        if local is not None and git_blob_sha(local) == rsha:
            continue
        for _ in range(4):
            b = req("GET", f"/repos/{REPO}/git/blobs/{rsha}")
            if "content" in b:
                break
            time.sleep(5)
        if "content" not in b:
            sys.exit(f"blob fetch failed {path}: {str(b)[:160]}")
        content = base64.b64decode(b["content"])
        if local is not None and git_blob_sha(content) != rsha:
            print(f"!! verify fail {path}", file=sys.stderr)
            continue
        d = os.path.dirname(lp)
        if d:
            os.makedirs(d, exist_ok=True)
        open(lp, "wb").write(content)
        changed.append(path)
    print(f"head={head}")
    print("synced:", ", ".join(changed) if changed else "none")


def cmd_tree():
    _, tree = remote_tree()
    for p in sorted(tree):
        print(p)


if __name__ == "__main__":
    {"head": cmd_head, "sync": cmd_sync, "tree": cmd_tree}[sys.argv[1]]()
