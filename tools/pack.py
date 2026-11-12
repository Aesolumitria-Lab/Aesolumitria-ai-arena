#!/usr/bin/env python3
"""题库统一打包（选手包）。

规则只有一条：**只有 public/ 能进选手包**，且 `public/baselines/` 下的 bot 只允许
`public/baselines/bin/*.exe` 形式的二进制；`public/baselines/**/*.cpp` 等基准源码一律排除。
meta / README / RULES / prompt 白名单放行，再额外带一份 `CONTESTANT_RULES.md`。
其余一律拒绝。这样既不会漏 private/，也不会把基准 bot 源码发给选手。

同一输入两次打包，zip 内容的 sha256 必须一致：所以文件名排序、时间戳固定、压缩参数固定。

    python tools/pack.py --game p01-ballistic        # 单题 -> dist/p01-ballistic.zip
    python tools/pack.py --game all                  # 五题 -> dist/contestant/<game>.zip
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "tools"))
import harness  # noqa: E402

WHITELIST_ROOT = {"meta.json", "README.md", "RULES.md", "prompt.md"}
SHARED_FILES = {
    "CONTESTANT_RULES.md": REPO / "docs" / "contestant-rules.md",
    "LICENSE": REPO / "LICENSE",
    "NOTICE": REPO / "NOTICE",
}
EXCLUDE_SUFFIX = {".exe", ".o", ".obj", ".pyc", ".zip"}
EXCLUDE_NAMES = {".gitkeep"}
EXCLUDE_DIRS = {"build", "__pycache__", ".git"}
SOURCE_SUFFIX = {".cpp", ".cc", ".cxx", ".c", ".hpp", ".h"}
BASELINE_BIN_DIRS = ("public/baselines/bin/", "public/challenges/bin/")
# Linux 二进制必须带执行位：zip 的 external_attr 高 16 位是 Unix mode，缺 x 位时
# 解包后 Popen 直接 Permission denied（README 说 Linux 用 bin/linux/*）。
LINUX_BIN_DIRS = ("public/baselines/bin/linux/", "public/challenges/bin/linux/")
EXCLUDE_EXACT = {"public/template.cpp", "public/arena.hpp"}
# judge4 会 import 同目录的 judge.py（编译缓存 / ProcessCPU / Engine 封装），所以它必须在包里；
# 但 judge.py 里还留着 v1.x 的 2 席协议，直接叫 judge.py 会让选手照它写 bot。
# 处置：包里改名为 judge2.py（明确是 2 席遗留），并让 judge4 优先找这个名字。
RENAME_IN_Pack = {"public/judge/judge.py": "public/judge/judge2.py"}
FIXED_TIME = (2026, 1, 1, 0, 0, 0)


def collect(game: Path) -> list[tuple[Path, str]]:
    """返回 (绝对路径, 包内相对路径) 列表，按相对路径排序。"""
    items: list[tuple[Path, str]] = []

    public = game / "public"
    if not public.exists():
        raise SystemExit("缺少 public/")

    for p in sorted(public.rglob("*")):
        if p.is_dir():
            continue
        if any(part in EXCLUDE_DIRS for part in p.parts):
            continue
        rel = p.relative_to(game).as_posix()
        if rel in EXCLUDE_EXACT:
            continue
        # 4 席题不打包 2 席遗留地图：选手在包里看到两套图 + 两套协议会写错 bot。
        if rel.startswith("public/maps/") and (game / "public" / "maps4").is_dir():
            continue
        if p.suffix.lower() in EXCLUDE_SUFFIX:
            # Baseline bots are shipped as binaries only; no other .exe enters the pack.
            if not (any(rel.startswith(d) for d in BASELINE_BIN_DIRS)
                    and p.suffix.lower() == ".exe"):
                continue
        if rel.startswith("public/baselines/") and (
                p.suffix.lower() in SOURCE_SUFFIX or p.suffix.lower() == ".md"):
            continue
        if p.name in EXCLUDE_NAMES:
            continue
        items.append((p, RENAME_IN_Pack.get(rel, rel)))

    # bot 二进制是构建产物，不进版本库（problems/*/public/*/bin/ 被忽略）。干净克隆里
    # 没有它们时，沿用 packages/<game>/ 里已发布的那一份，否则打出来的包没有可对战的 bot。
    released = REPO / "packages" / game.name
    for d in BASELINE_BIN_DIRS:
        if (game / d).is_dir() or not (released / d).is_dir():
            continue
        print(f"note: {d} 不在题目目录里，沿用 packages/{game.name}/{d}")
        for p in sorted((released / d).rglob("*")):
            if p.is_file():
                items.append((p, p.relative_to(released).as_posix()))

    for name in WHITELIST_ROOT:
        f = game / name
        if f.exists():
            items.append((f, name))

    for pack_name, src in SHARED_FILES.items():
        if src.exists():
            items.append((src, pack_name))

    items.sort(key=lambda x: x[1])
    return items


def entry_mode(rel: str) -> int:
    """zip 条目的 Unix mode：Linux 二进制 0o755，其余 0o644。"""
    return 0o755 if any(rel.startswith(d) for d in LINUX_BIN_DIRS) else 0o644


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def pack_one(game: Path, out: Path) -> tuple[list[tuple[Path, str]], str]:
    items = collect(game)

    # manifest 的作用不只是"列文件"：它把**这份包绑到某个引擎版本**上。
    # 回放争议时，先看包的 engine_hash 对不对，再看回放——否则没法排除"用的是另一版引擎"。
    meta = json.loads((game / "meta.json").read_text(encoding="utf-8"))
    manifest = {
        "game": game.name,
        "kind": "contestant",
        "version": meta.get("version"),
        "license": "Apache-2.0",
        "copyright": "2026 Aesolumitria-Lab",
        "rules_hash": meta.get("rules_hash"),
        "engine_hash": meta.get("engine_hash"),
        "arena_hash": meta.get("arena_hash"),
        "generated_by": "tools/pack.py",
        "files": [{"path": rel, "sha256": sha256_file(p), "size": p.stat().st_size}
                  for p, rel in items],
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for p, rel in items:
            info = zipfile.ZipInfo(rel, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = entry_mode(rel) << 16
            zf.writestr(info, p.read_bytes())
        info = zipfile.ZipInfo("manifest.json", date_time=FIXED_TIME)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        zf.writestr(info, json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))

    digest = sha256_file(out)
    print(f"packing {game.name}  ({len(items)} files)")
    for p, rel in items:
        print(f"  {rel}")
    print(f"zip  {out}  ({out.stat().st_size / 1024:.1f} KB)  sha256 {digest[:16]}")
    return items, digest


def main() -> int:
    ap = argparse.ArgumentParser(description="题库 pack（选手包）")
    ap.add_argument("--game", default="p01-ballistic",
                    help="题号或目录名；all = 所有可运行题")
    ap.add_argument("--game-dir", default=None, help="兼容旧参数")
    ap.add_argument("--out", default="", help="输出 zip 或目录")
    args = ap.parse_args()

    if args.game.strip().lower() == "all":
        games = harness.all_games()
        out = Path(args.out).resolve() if args.out else REPO / "dist" / "contestant"
        if out.suffix.lower() == ".zip":
            raise SystemExit("--game all 时 --out 必须是目录，不能是单个 .zip")
        targets = [(g, out / f"{g.name}.zip") for g in games]
    else:
        game = Path(args.game_dir).resolve() if args.game_dir else harness.game_dir(args.game)
        if args.out and not args.out.lower().endswith(".zip"):
            out = Path(args.out).resolve() / f"{game.name}.zip"
        elif args.out:
            out = Path(args.out).resolve()
        else:
            out = REPO / "dist" / f"{game.name}.zip"
        targets = [(game, out)]

    for game, out in targets:
        pack_one(game, out)
        print()

    print(f"{len(targets)} 个选手包已生成")
    return 0


if __name__ == "__main__":
    sys.exit(main())

