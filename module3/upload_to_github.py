#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Upload module3/ to a GitHub repository via the Git Data API, as a single commit.

Usage:
    export GITHUB_TOKEN=ghp_xxx            # never hardcode the token or commit it
    python module3/upload_to_github.py --owner <you> --repo <repo name>

    # create the private repo if it does not exist yet
    python module3/upload_to_github.py --owner <you> --repo FTEC5510-Module3 --create

    # list the files without touching the network
    python module3/upload_to_github.py --owner <you> --repo xxx --dry-run

Why the Git Data API instead of the Contents API:
    Contents API writes one file per call, so 10 files mean 10 commits,
    and concurrent pushes overwrite each other. Git Data API creates blobs, then a complete tree,
    then one commit and a single ref update: all-or-nothing.
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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

API = "https://api.github.com"
DEFAULT_EXCLUDES = ("__pycache__", "*.pyc", ".DS_Store", "module3_outputs", ".pytest_cache")


def api(method: str, path: str, token: str, payload=None):
    """One REST call. path starts with /, e.g. /repos/o/r/git/ref/heads/main."""
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
            hint = "\nCheck: the token is invalid or expired."
        elif exc.code == 403:
            hint = "\nCheck: the token lacks Contents: Read and write, or you have no write access to this repo."
        elif exc.code == 404:
            hint = "\nCheck: wrong owner/repo/branch, or the token cannot see this repo (common for private repos)."
        elif exc.code == 422:
            hint = "\nCheck: the branch moved, or the ref already exists (use --force)."
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
        print(f"Repository exists: {owner}/{repo}")
        return
    except SystemExit:
        if not create:
            raise
    whoami = api("GET", "/user", token)
    if whoami.get("login", "").lower() != owner.lower():
        raise SystemExit(
            f"--create only works under your own account; this token belongs to {whoami.get('login')}."
            f"Create {owner}/{repo} on GitHub first, or set --owner to {whoami.get('login')}.")
    api("POST", "/user/repos", token, {
        "name": repo, "private": private, "auto_init": True,
        "description": "FTEC5510 Group 12 - Module 3 simulation engine (S0/S1)",
    })
    print(f"Repository created: {owner}/{repo}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--owner", required=True, help="GitHub user or organisation")
    parser.add_argument("--repo", required=True, help="repository name")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--source", default="module3", help="local directory to upload (default module3)")
    parser.add_argument("--dest", default="module3", help="target directory inside the repo (default module3)")
    parser.add_argument("--message", default="Add Module 3 simulation engine (S0/S1)")
    parser.add_argument("--create", action="store_true", help="create the repository if it does not exist")
    parser.add_argument("--public", action="store_true", help="make the repo public when using --create")
    parser.add_argument("--force", action="store_true", help="overwrite the remote branch (use with care)")
    parser.add_argument("--dry-run", action="store_true", help="list files only, no network")
    parser.add_argument("--jobs", type=int, default=8,
                        help="parallel blob uploads (default 8)")
    parser.add_argument("--token", default=None,
                        help="not recommended: use the GITHUB_TOKEN env var")
    args = parser.parse_args()

    here = Path(__file__).resolve().parent
    source = Path(args.source)
    if not source.is_absolute():
        candidate = (Path.cwd() / source)
        source = candidate if candidate.is_dir() else (here.parent / source)
    source = source.resolve()
    if not source.is_dir():
        raise SystemExit(f"Directory not found: {source}")

    excludes = DEFAULT_EXCLUDES
    files = collect_files(source, excludes)
    if not files:
        raise SystemExit(f"{source}  contains no uploadable files")

    total = sum(f.stat().st_size for f in files)
    print(f"Uploading {len(files)} files, {total/1024:.1f} KB")
    for path in files:
        print(f"  {path.relative_to(source)}  ({path.stat().st_size} B)")

    if args.dry_run:
        print("\n--dry-run: no network calls, nothing changed.")
        return 0

    token = args.token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise SystemExit("Missing token. Run export GITHUB_TOKEN=... first (never hardcode it).")

    ensure_repo(args.owner, args.repo, token, private=not args.public, create=args.create)
    repo_path = f"/repos/{args.owner}/{args.repo}"

    # 1) current commit and tree of the branch
    parent_sha = None
    base_tree = None
    try:
        ref = api("GET", f"{repo_path}/git/ref/heads/{args.branch}", token)
        parent_sha = ref["object"]["sha"]
        commit = api("GET", f"{repo_path}/git/commits/{parent_sha}", token)
        base_tree = commit["tree"]["sha"]
        print(f"Appending on top of commit {parent_sha[:7]}")
    except SystemExit:
        print("Branch is empty; creating the first commit")

    # 2) one blob per file (concurrently: a fixture bundle is ~100 files)
    prefix = args.dest.strip("/")
    items = []
    for path in files:
        relative = path.relative_to(source)
        target = f"{prefix}/{relative.as_posix()}" if prefix else relative.as_posix()
        items.append((path, target))

    def make_blob(item):
        path, target = item
        blob = api("POST", f"{repo_path}/git/blobs", token, {
            "content": base64.b64encode(path.read_bytes()).decode("ascii"),
            "encoding": "base64",
        })
        return {"path": target, "mode": "100644", "type": "blob", "sha": blob["sha"]}

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        tree_items = list(pool.map(make_blob, items))
    for item in tree_items:
        print(f"  blob {item['path']}")

    # 3) one tree (base_tree keeps existing files instead of wiping them)
    tree_payload = {"tree": tree_items}
    if base_tree:
        tree_payload["base_tree"] = base_tree
    tree = api("POST", f"{repo_path}/git/trees", token, tree_payload)

    # 4) create the commit
    commit_payload = {"message": args.message, "tree": tree["sha"],
                      "parents": [parent_sha] if parent_sha else []}
    new_commit = api("POST", f"{repo_path}/git/commits", token, commit_payload)

    # 5) move the branch pointer
    if parent_sha:
        api("PATCH", f"{repo_path}/git/refs/heads/{args.branch}", token, {
            "sha": new_commit["sha"], "force": bool(args.force)})
    else:
        api("POST", f"{repo_path}/git/refs", token, {
            "ref": f"refs/heads/{args.branch}", "sha": new_commit["sha"]})

    print()
    print(f"Done: {len(files)} files -> commit {new_commit['sha'][:7]}")
    print(f"https://github.com/{args.owner}/{args.repo}/tree/{args.branch}/{args.dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
