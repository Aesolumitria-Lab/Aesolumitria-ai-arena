#!/usr/bin/env python3
"""P07 MAZE HUNT · 泄漏检查。

扫描选手包，找三类东西：
  1. 路径泄漏：private/、anchors/、maps_hidden/
  2. 内容泄漏：隐藏地图名、绝对路径、anchor 源码片段
  3. 构建残留：.exe / .o / __pycache__ / build/

退出码：0 = 无 FAIL（有 WARN 仍返回 0）；1 = 有 FAIL。
"""
from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "tools"))
import harness  # noqa: E402

FAIL_PATTERNS = [
    (re.compile(r"private/"), "引用了 private/ 路径"),
    (re.compile(r"private/anchors4"), "引用了私有 anchor 目录"),
    (re.compile(r"anchors4/\S*\.cpp"), "出现了 anchor 源码路径"),
    (re.compile(r"anchors?/"), "引用了 anchors/ 路径"),
    (re.compile(r"maps4_hidden"), "引用了 4 席隐藏地图目录"),
    (re.compile(r"maps_hidden"), "引用了隐藏地图目录"),
    (re.compile(r"hidden-\d\d"), "出现了隐藏地图名"),
    (re.compile(r"[A-Za-z]:\\\\?(?:Users|WBWS|P01|workspace)"), "出现了 Windows 绝对路径"),
    (re.compile(r"/(?:home|Users)/[A-Za-z0-9_.-]+/"), "出现了 POSIX 绝对路径"),
]

WARN_PATTERNS = [
    (re.compile(r"\bTODO\b"), "残留 TODO"),
]

EVAL_FAIL_PATTERNS = [
    (re.compile(r"[A-Za-z]:\\\\?(?:Users|WBWS|workspace)"), "出现了 Windows 绝对路径"),
    (re.compile(r"/(?:home|Users)/[A-Za-z0-9_.-]+/"), "出现了 POSIX 绝对路径"),
]

BANNED_SUFFIX = {".exe", ".o", ".obj", ".pyc"}
BANNED_DIRS = {"build", "__pycache__", ".git"}

# 文本扫描白名单：已知文本后缀 + 【无后缀文件】。无后缀这一条是审计 A1 的根因：
# 旧版按后缀白名单跳过，NOTICE 里的 private/ 与本机绝对路径完全没被扫到。
TEXT_SUFFIX = {".md", ".json", ".hpp", ".cpp", ".txt", ".py"}
MAX_TEXT_BYTES = 2 * 1024 * 1024   # 超过按二进制处理：不误报，也不拖慢扫描

# 例外：pack.py 有意只发二进制的基准/挑战 bot（`public/baselines/bin/`、
# `public/challenges/bin/`）。这两处的 .exe 是【设计的一部分】，不是构建残留。
# 其它任何位置的 .exe 仍然 FAIL。
ALLOWED_BIN_DIRS = ("public/baselines/bin/", "public/challenges/bin/")


def _is_allowed_binary(name: str) -> bool:
    return name.lower().endswith(".exe") and any(
        name.startswith(d) for d in ALLOWED_BIN_DIRS)


def _eval_allowed(name: str) -> bool:
    """评测包只允许：anchor 二进制/清单、隐藏图、meta/CONDITIONS、包清单。"""
    p = Path(name)
    if name in {"meta.json", "CONDITIONS.md", "manifest.json"}:
        return True
    parts = p.parts
    if len(parts) == 2 and parts[0] in {"anchors", "anchors4"}:
        return parts[1] == "manifest.json" or p.suffix.lower() in {".exe", ""}
    if len(parts) == 2 and parts[0] in {"maps_hidden", "maps4_hidden"}:
        return p.suffix.lower() == ".txt"
    return False


def _text_of(zf: zipfile.ZipFile, name: str, info: zipfile.ZipInfo) -> str | None:
    """读取待扫描文本；二进制/超大文件返回 None。

    已知文本后缀沿用宽松解码；无后缀文件按严格 UTF-8 解码，失败即视为二进制。
    这样 NOTICE / LICENSE 会被扫到，而 Linux 上的 ELF（bin/linux/*）不会误报。
    """
    if info.file_size > MAX_TEXT_BYTES:
        return None
    suffix = Path(name).suffix.lower()
    if suffix:
        if suffix not in TEXT_SUFFIX:
            return None
        return zf.read(name).decode("utf-8", errors="replace")
    try:
        return zf.read(name).decode("utf-8")
    except UnicodeDecodeError:
        return None


def scan_zip(path: Path, eval_mode: bool = False) -> tuple[list[str], list[str]]:
    fails: list[str] = []
    warns: list[str] = []

    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        if eval_mode:
            for name in names:
                if not _eval_allowed(name):
                    fails.append(f"{name}: 评测包不允许的文件（评测包只应有 anchor 二进制"
                                 "、隐藏图、meta/CONDITIONS）")
        else:
            for name in names:
                low = Path(name).suffix.lower()
                if low in BANNED_SUFFIX and not _is_allowed_binary(name):
                    fails.append(f"{name}: 包含构建产物 {low}")
                if any(part in BANNED_DIRS for part in Path(name).parts):
                    fails.append(f"{name}: 包含不应发布的目录")
                if name.startswith("private/"):
                    fails.append(f"{name}: private/ 被打进包里")

        for name in names:
            if name == "CONTESTANT_RULES.md":
                # 参赛须知本身要写“禁止读 private/”，不是泄漏
                continue
            info = zf.getinfo(name)
            text = _text_of(zf, name, info)
            if text is None:
                continue
            pats = EVAL_FAIL_PATTERNS if eval_mode else FAIL_PATTERNS
            for lineno, line in enumerate(text.splitlines(), 1):
                for pat, desc in pats:
                    if pat.search(line):
                        fails.append(f"{name}:{lineno}: {desc} -> {line.strip()[:90]}")
                for pat, desc in WARN_PATTERNS:
                    if pat.search(line):
                        warns.append(f"{name}:{lineno}: {desc}")

    return fails, warns


def main() -> int:
    ap = argparse.ArgumentParser(description="题库 leakcheck")
    ap.add_argument("--game", default=None, help="题号（可选，用于定位私有路径）")
    ap.add_argument("--game-dir", default=None)
    ap.add_argument("--path", required=True)
    ap.add_argument("--eval", action="store_true", help="按评测包白名单检查（允许 anchor 二进制）")
    args = ap.parse_args()

    target = Path(args.path).resolve()
    if not target.exists():
        print(f"找不到 {target}", file=sys.stderr)
        return 2

    fails, warns = scan_zip(target, args.eval)

    for w in warns:
        print(f"[WARN] {w}")
    for f in fails:
        print(f"[FAIL] {f}")

    if not fails and not warns:
        print("PASS  零告警")
    print(f"\n{len(fails)} fail, {len(warns)} warn")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
