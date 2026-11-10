#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 module3/ 上传到 GitHub 仓库，走 Git Data API，整个目录只产生一个提交。

用法：
    export GITHUB_TOKEN=ghp_xxx            # 不要把 token 写进代码或提交上去
    python module3/upload_to_github.py --owner <你> --repo <仓库名>

    # 仓库还不存在时，让它顺手建一个私有仓库
    python module3/upload_to_github.py --owner <你> --repo FTEC5510-Module3 --create

    # 只看会传哪些文件，不联网
    python module3/upload_to_github.py --owner <你> --repo xxx --dry-run

为什么用 Git Data API 而不是 Contents API：
    Contents API 一次只能写一个文件，10 个文件就是 10 个 commit，
    而且并发推送容易相互覆盖。Git Data API 先建 blob、再建一棵完整的 tree、
    最后一次性建 commit 并移动分支指针，要么全部成功要么全部不动。
"""

from __future__ import annotations

import argparse
import base64
import fnmatch
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"
DEFAULT_EXCLUDES = ("__pycache__", "*.pyc", ".DS_Store", "module3_outputs", ".pytest_cache")


def api(method: str, path: str, token: str, payload=None):
    """一次 REST 调用。path 以 / 开头，例如 /repos/o/r/git/ref/heads/main。"""
    url = API + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ftec5510-module3-uploader",
    }
    if data:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        hint = ""
        if exc.code == 401:
            hint = "\n排查：token 无效或已过期。"
        elif exc.code == 403:
            hint = "\n排查：token 缺少 Contents: Read and write 权限，或对该仓库没有写权限。"
        elif exc.code == 404:
            hint = "\n排查：仓库名/分支名写错，或 token 没有访问该仓库的权限（私有仓库常见）。"
        elif exc.code == 422:
            hint = "\n排查：分支已有更新、或 ref 已存在（加 --force）。"
        raise SystemExit(f"GitHub API {exc.code} {method} {url}\n{detail}{hint}")


def collect_files(root: Path, excludes: tuple[str, ...]) -> list[Path]:
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        parts = relative.parts
        if any(part in excludes for part in parts):
            continue
        if any(fnmatch.fnmatch(part, pattern) for part in parts for pattern in excludes):
            continue
        files.append(path)
    return files


def ensure_repo(owner: str, repo: str, token: str, private: bool, create: bool) -> None:
    try:
        api("GET", f"/repos/{owner}/{repo}", token)
        print(f"仓库已存在：{owner}/{repo}")
        return
    except SystemExit:
        if not create:
            raise
    whoami = api("GET", "/user", token)
    if whoami.get("login", "").lower() != owner.lower():
        raise SystemExit(
            f"--create 只能在你自己名下建仓库，当前 token 属于 {whoami.get('login')}。"
            f"请先手动在 GitHub 上创建 {owner}/{repo}，或把 --owner 改成 {whoami.get('login')}。")
    api("POST", "/user/repos", token, {
        "name": repo, "private": private, "auto_init": True,
        "description": "FTEC5510 Group 12 - Module 3 simulation engine (S0/S1)",
    })
    print(f"已创建仓库：{owner}/{repo}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--owner", required=True, help="GitHub 用户名或组织名")
    parser.add_argument("--repo", required=True, help="仓库名")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--source", default="module3", help="本地要上传的目录，默认 module3")
    parser.add_argument("--dest", default="module3", help="在仓库里的目标目录，默认 module3")
    parser.add_argument("--message", default="Add Module 3 simulation engine (S0/S1)")
    parser.add_argument("--create", action="store_true", help="仓库不存在时自动创建")
    parser.add_argument("--public", action="store_true", help="--create 时建公开仓库")
    parser.add_argument("--force", action="store_true", help="覆盖远端分支（慎用）")
    parser.add_argument("--dry-run", action="store_true", help="只列文件，不联网")
    parser.add_argument("--token", default=None,
                        help="不推荐：直接用环境变量 GITHUB_TOKEN")
    args = parser.parse_args()

    here = Path(__file__).resolve().parent
    source = Path(args.source)
    if not source.is_absolute():
        candidate = (Path.cwd() / source)
        source = candidate if candidate.is_dir() else (here.parent / source)
    source = source.resolve()
    if not source.is_dir():
        raise SystemExit(f"目录不存在：{source}")

    excludes = DEFAULT_EXCLUDES
    files = collect_files(source, excludes)
    if not files:
        raise SystemExit(f"{source} 下没有可上传的文件")

    total = sum(f.stat().st_size for f in files)
    print(f"待上传 {len(files)} 个文件，共 {total/1024:.1f} KB")
    for path in files:
        print(f"  {path.relative_to(source)}  ({path.stat().st_size} B)")

    if args.dry_run:
        print("\n--dry-run：未联网，未做任何改动。")
        return 0

    token = args.token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise SystemExit("缺少 token。请先 export GITHUB_TOKEN=...（不要写进代码）。")

    ensure_repo(args.owner, args.repo, token, private=not args.public, create=args.create)
    repo_path = f"/repos/{args.owner}/{args.repo}"

    # 1) 取分支当前指向的 commit 与其 tree
    parent_sha = None
    base_tree = None
    try:
        ref = api("GET", f"{repo_path}/git/ref/heads/{args.branch}", token)
        parent_sha = ref["object"]["sha"]
        commit = api("GET", f"{repo_path}/git/commits/{parent_sha}", token)
        base_tree = commit["tree"]["sha"]
        print(f"基于现有提交 {parent_sha[:7]} 追加")
    except SystemExit:
        print("分支为空，将创建首个提交")

    # 2) 每个文件建一个 blob
    tree_items = []
    for path in files:
        relative = path.relative_to(source)
        target = f"{args.dest.strip('/')}/{relative.as_posix()}"
        blob = api("POST", f"{repo_path}/git/blobs", token, {
            "content": base64.b64encode(path.read_bytes()).decode("ascii"),
            "encoding": "base64",
        })
        tree_items.append({"path": target, "mode": "100644", "type": "blob",
                           "sha": blob["sha"]})
        print(f"  blob {target}")

    # 3) 一次性建 tree（带上 base_tree，才是在原仓库上追加而不是清空）
    tree_payload = {"tree": tree_items}
    if base_tree:
        tree_payload["base_tree"] = base_tree
    tree = api("POST", f"{repo_path}/git/trees", token, tree_payload)

    # 4) 建 commit
    commit_payload = {"message": args.message, "tree": tree["sha"],
                      "parents": [parent_sha] if parent_sha else []}
    new_commit = api("POST", f"{repo_path}/git/commits", token, commit_payload)

    # 5) 移动分支指针
    if parent_sha:
        api("PATCH", f"{repo_path}/git/refs/heads/{args.branch}", token, {
            "sha": new_commit["sha"], "force": bool(args.force)})
    else:
        api("POST", f"{repo_path}/git/refs", token, {
            "ref": f"refs/heads/{args.branch}", "sha": new_commit["sha"]})

    print()
    print(f"完成：{len(files)} 个文件 -> 提交 {new_commit['sha'][:7]}")
    print(f"https://github.com/{args.owner}/{args.repo}/tree/{args.branch}/{args.dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
