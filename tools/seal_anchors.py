#!/usr/bin/env python3
"""把 anchor 编译成封闭的二进制包：只出二进制 + manifest，不复制源码。

    python tools/seal_anchors.py --game p01-ballistic
    python tools/seal_anchors.py --game all --strip
    python tools/seal_anchors.py --game p01-ballistic --seats 4

默认（`--seats 2`）输出 `dist/anchors/<game>/`：

    manifest.json          anchor 清单、hash、编译器、平台
    random.exe / greedy.exe / scripted.exe / strong.exe

`--seats 4` 读 `meta.json` 的 `anchors4`，源码在 `private/anchors4/*.cpp`
（共用 `bot4_common.hpp`），输出默认 `dist/anchors4/<game>/`。4 席编译带
`-I public`、沿用裁判的 C++ 旗标（Windows 含 `-static`），并且
**只在系统临时目录编译**，不碰题目共享的 `build/`，可与其它构建并行。

评测环境只挂载这个目录，仓库里的 `private/anchors*/*.cpp` 不跟着走。
`bench.py --anchors-bin <dir>` 会优先用这里的二进制，而不是源码。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
import bench  # noqa: E402
import harness  # noqa: E402


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def display_path(p: Path) -> str:
    """优先显示仓库相对路径；`--out` 指到仓库外时退回绝对路径。"""
    try:
        return p.resolve().relative_to(REPO).as_posix()
    except ValueError:
        return p.resolve().as_posix()


def compiler_version() -> str:
    try:
        r = subprocess.run(["g++", "--version"], capture_output=True, text=True,
                           encoding="utf-8", timeout=10)
        return (r.stdout.splitlines()[0] if r.stdout else "g++ unknown").strip()
    except Exception:
        return "g++ unknown"


def strip_binary(p: Path) -> bool:
    strip = shutil.which("strip")
    if not strip:
        return False
    try:
        r = subprocess.run([strip, "-s", str(p)], capture_output=True, text=True,
                           timeout=30)
        return r.returncode == 0
    except Exception:
        return False


def seal_game(game: Path, names: list[str], out: Path, do_strip: bool) -> dict:
    j = bench.load_judge(game)
    meta = json.loads((game / "meta.json").read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)

    entries = []
    for name in names:
        src = bench.build(game, j, name)
        dst = out / (name + (".exe" if os.name == "nt" else ""))
        shutil.copy2(src, dst)
        stripped = strip_binary(dst) if do_strip else False
        entries.append({
            "name": name,
            "file": dst.name,
            "sha256": sha256(dst),
            "size": dst.stat().st_size,
            "stripped": stripped,
        })
        print(f"  {name:<10} -> {display_path(dst)}  "
              f"{dst.stat().st_size / 1024:.1f} KB" + ("  (stripped)" if stripped else ""))

    manifest = {
        "game": game.name,
        "version": meta.get("version"),
        "rules_hash": meta.get("rules_hash"),
        "engine_hash": meta.get("engine_hash"),
        "platform": f"{platform.system().lower()}-{platform.machine().lower()}",
        "compiler": compiler_version(),
        "anchors": entries,
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def seal_game4(game: Path, names: list[str], out: Path, do_strip: bool) -> dict:
    """4 席 anchor：编译到临时目录（不碰 game/build），只把二进制拷进 out。

    4 席源码共用一个头 `private/anchors4/bot4_common.hpp`（与源码同目录，引号
    include 自动找到）；它包含的 `arena4.hpp` 由 `-I public` 提供。
    """
    src_dir = game / "private" / "anchors4"
    if not src_dir.is_dir():
        raise SystemExit(f"{game.name} 没有 private/anchors4/，无法封装 4 席 anchor")
    meta = json.loads((game / "meta.json").read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    j = bench.load_judge(game)

    entries = []
    tmp = Path(tempfile.mkdtemp(prefix=f"seal4-{game.name}-"))
    try:
        for name in names:
            if Path(name).name != name or name in (".", ".."):
                raise SystemExit(f"非法 4 席 anchor 名：{name!r}")
            src = src_dir / f"{name}.cpp"
            if not src.is_file():
                raise SystemExit(f"找不到 4 席 anchor 源码：{src}")
            try:
                try:
                    built = j.build_bot(src, tmp, game / "public")
                except TypeError:  # 旧裁判的 build_bot 没有 include_dir 参数
                    built = j.build_bot(src, tmp)
            except Exception as exc:  # noqa: BLE001
                raise SystemExit(f"{name}: 4 席 anchor 编译失败\n{exc}") from exc
            dst = out / (name + (".exe" if os.name == "nt" else ""))
            shutil.copy2(built, dst)
            stripped = strip_binary(dst) if do_strip else False
            entries.append({
                "name": name,
                "file": dst.name,
                "sha256": sha256(dst),
                "size": dst.stat().st_size,
                "stripped": stripped,
            })
            print(f"  {name:<10} -> {display_path(dst)}  "
                  f"{dst.stat().st_size / 1024:.1f} KB" + ("  (stripped)" if stripped else ""))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    manifest = {
        "game": game.name,
        "kind": "anchors4",
        "seats": 4,
        "version": meta.get("version"),
        "rules_hash": meta.get("rules_hash"),
        "engine_hash": meta.get("engine_hash"),
        "platform": f"{platform.system().lower()}-{platform.machine().lower()}",
        "compiler": compiler_version(),
        "anchors": entries,
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description="把 anchor 封装成二进制基准")
    ap.add_argument("--game", default="all", help="题号或目录名；all = 所有可运行题")
    ap.add_argument("--seats", choices=("2", "4", "both"), default="2",
                    help="2 = 现有 2 席 anchors（默认）；4 = 4 席 anchors4；both = 两套都出")
    ap.add_argument("--anchors", default="", help="逗号分隔；默认读 meta.json 的 anchors")
    ap.add_argument("--anchors4", default="", help="逗号分隔；默认读 meta.json 的 anchors4")
    ap.add_argument("--out", default="", help="2 席输出目录；默认 dist/anchors/<game>")
    ap.add_argument("--out4", default="", help="4 席输出目录；默认 dist/anchors4/<game>")
    ap.add_argument("--strip", action="store_true", help="如果系统有 strip，就去符号")
    ap.add_argument("--json")
    args = ap.parse_args()

    games = harness.all_games() if args.game.strip().lower() == "all" \
        else [harness.game_dir(g.strip()) for g in args.game.split(",") if g.strip()]
    want2 = args.seats in ("2", "both")
    want4 = args.seats in ("4", "both")

    reports = []
    reports4 = []
    for game in games:
        meta = json.loads((game / "meta.json").read_text(encoding="utf-8"))
        if want2:
            names = [s.strip() for s in args.anchors.split(",") if s.strip()] \
                or meta.get("anchors", [])
            out = Path(args.out).resolve() if args.out else REPO / "dist" / "anchors" / game.name
            print(f"sealing {game.name}  ({len(names)} anchors)")
            reports.append(seal_game(game, names, out, args.strip))
            print()
        if want4:
            names4 = [s.strip() for s in args.anchors4.split(",") if s.strip()] \
                or meta.get("anchors4", [])
            if not names4:
                print(f"skip {game.name}: meta.json 没有 anchors4")
                continue
            out4 = Path(args.out4).resolve() if args.out4 \
                else REPO / "dist" / "anchors4" / game.name
            print(f"sealing {game.name} 4 seats  ({len(names4)} anchors)")
            reports4.append(seal_game4(game, names4, out4, args.strip))
            print()

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {"tool": "seal_anchors", "games": reports}
        if want4:
            payload["games4"] = reports4
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")

    if want2:
        total = sum(len(r["anchors"]) for r in reports)
        print(f"{len(reports)} 道题，{total} 个 anchor 已封装")
    if want4:
        total4 = sum(len(r["anchors"]) for r in reports4)
        print(f"{len(reports4)} 道题，{total4} 个 4 席 anchor 已封装")
    return 0


if __name__ == "__main__":
    sys.exit(main())
