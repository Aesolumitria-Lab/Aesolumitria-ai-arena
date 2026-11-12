#!/usr/bin/env python3
"""把仓库外的私有题目内容挂到 problems/<game>/private。

隐藏图、anchor / 基准 / 挑战 bot 的源码不在本仓库里。出题人把它们放在另一个
目录（下称私有根目录），结构是：

    <私有根目录>/<game>/private/...

本脚本在 problems/<game>/private 处建一个目录联接（Windows）或符号链接（其他
系统）指过去；该路径已被 .gitignore 忽略。没有私有内容时不需要运行它：只依赖
public/ 的工具（规则测试的公开部分、选手包打包、选手自检）照常可用，需要隐藏图
或 anchor 源码的工具会报找不到文件。

    python tools/link_private.py --root /path/to/arena-private
    python tools/link_private.py            # 读环境变量 ARENA_PRIVATE
    python tools/link_private.py --unlink   # 只移除链接，不动私有内容
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROBLEMS = REPO / "problems"


def is_link(p: Path) -> bool:
    if p.is_symlink():
        return True
    is_junction = getattr(p, "is_junction", None)   # Python 3.12+
    if is_junction is not None:
        return is_junction()
    try:
        return bool(os.lstat(p).st_file_attributes & 0x400)  # REPARSE_POINT
    except (AttributeError, OSError):
        return False


def unlink(p: Path) -> None:
    if os.name == "nt":
        os.rmdir(p)          # 只删联接本身，不递归
    else:
        p.unlink()


def link(target: Path, at: Path) -> None:
    if os.name == "nt":
        res = subprocess.run(["cmd", "/c", "mklink", "/J", str(at), str(target)],
                             capture_output=True)
        if res.returncode != 0:
            msg = (res.stdout + res.stderr).decode(errors="replace")
            raise SystemExit(f"mklink failed: {msg}")
    else:
        at.symlink_to(target, target_is_directory=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="link private problem content into the repo")
    ap.add_argument("--root", default=os.environ.get("ARENA_PRIVATE", ""))
    ap.add_argument("--unlink", action="store_true")
    args = ap.parse_args()

    games = [d for d in sorted(PROBLEMS.iterdir()) if d.is_dir() and not d.name.startswith("_")]
    if args.unlink:
        for g in games:
            at = g / "private"
            if is_link(at):
                unlink(at)
                print(f"unlinked {at.relative_to(REPO)}")
        return 0

    if not args.root:
        print("no private root: pass --root or set ARENA_PRIVATE", file=sys.stderr)
        return 2
    root = Path(args.root).expanduser().resolve()
    rc = 0
    for g in games:
        target = root / g.name / "private"
        at = g / "private"
        if not target.is_dir():
            print(f"skip {g.name}: {target} not found")
            continue
        if is_link(at):
            unlink(at)
        elif at.exists():
            print(f"refuse {g.name}: {at} exists and is a real directory", file=sys.stderr)
            rc = 1
            continue
        link(target, at)
        print(f"linked {at.relative_to(REPO)} -> {target}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
