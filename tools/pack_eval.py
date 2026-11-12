#!/usr/bin/env python3
"""评测包：只装封装好的 anchor 二进制 + 隐藏地图 + 元数据，不含任何源码。

    python tools/pack_eval.py --game p01-ballistic
    python tools/pack_eval.py --game all --out dist/eval
    python tools/pack_eval.py --game p01-ballistic --seats 4

和 `pack.py` 的区别：
- `pack.py` 出的是**选手包**，只含 `public/`，给参赛 AI；
- `pack_eval.py` 出的是**评测包**，只含 anchor 二进制和隐藏图，给评测服务端。
两边都不含 `private/anchors*/*.cpp`——anchor 源码只留在出题人仓库里。

默认（2 席）解压后：
    anchors/<name>.exe      评测基准（无源码）
    anchors/manifest.json   anchor 清单 + hash + 平台
    maps_hidden/*.txt       隐藏地图
    meta.json / CONDITIONS.md
    manifest.json           包级自校验清单

`--seats 4` 打 4 席包（默认输出 `dist/eval4/<game>-4p.zip`，anchor 默认封到
`dist/anchors4/<game>/`）：
    anchors4/<name>.exe      random4 / greedy4 / scripted4 / strong4
    anchors4/manifest.json
    maps4_hidden/*.txt
    meta.json / CONDITIONS.md
    manifest.json

4 席包在写完后立即自检：不含源码、必须有 maps4_hidden/ 与 anchors4 二进制、
anchor 清单与包清单的 hash/大小/文件集合全部自洽；失败则 exit 1。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
import harness  # noqa: E402
import seal_anchors  # noqa: E402

FIXED_TIME = (2026, 1, 1, 0, 0, 0)
SOURCE_SUFFIX = {".cpp", ".cc", ".cxx", ".c", ".hpp", ".h"}
# 评测包里的可执行条目是 anchors/ 与 anchors4/ 下的 anchor 二进制（Linux 上无
# .exe 后缀）。缺执行位时解包后无法直接执行，所以单独给 0o755。
ANCHOR_DIRS = {"anchors", "anchors4"}


def entry_mode(rel: str) -> int:
    """zip 条目的 Unix mode：anchor 二进制 0o755，清单/隐藏图/meta 0o644。"""
    parts = Path(rel).parts
    if len(parts) == 2 and parts[0] in ANCHOR_DIRS and parts[1] != "manifest.json":
        return 0o755
    return 0o644


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def display_path(p: Path) -> str:
    try:
        return p.resolve().relative_to(REPO).as_posix()
    except ValueError:
        return p.resolve().as_posix()


def write_zip(out: Path, items: list[tuple[Path, str]], manifest: dict) -> None:
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9) as zf:
        for p, rel in items:
            info = zipfile.ZipInfo(rel, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = entry_mode(rel) << 16
            zf.writestr(info, p.read_bytes())
        info = zipfile.ZipInfo("manifest.json", date_time=FIXED_TIME)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        zf.writestr(info, json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))


def collect(game: Path, anchors_dir: Path, anchor_manifest: dict,
            include_hidden: bool) -> list[tuple[Path, str]]:
    items: list[tuple[Path, str]] = []
    for a in anchor_manifest["anchors"]:
        p = anchors_dir / a["file"]
        items.append((p, f"anchors/{a['file']}"))
    items.append((anchors_dir / "manifest.json", "anchors/manifest.json"))

    if include_hidden:
        hidden = game / "private" / "maps_hidden"
        if hidden.is_dir():
            for p in sorted(hidden.glob("*.txt")):
                items.append((p, f"maps_hidden/{p.name}"))

    for name in ("meta.json", "CONDITIONS.md"):
        f = game / name
        if f.exists():
            items.append((f, name))

    items.sort(key=lambda x: x[1])
    return items


def collect4(game: Path, anchors_dir: Path, anchor_manifest: dict,
             include_hidden: bool) -> list[tuple[Path, str]]:
    """4 席包：anchors4/ + maps4_hidden/ + meta/CONDITIONS。隐藏图缺失即报错。"""
    items: list[tuple[Path, str]] = []
    for a in anchor_manifest["anchors"]:
        p = anchors_dir / a["file"]
        if not p.is_file():
            raise SystemExit(f"4 席 anchor 缺文件：{p}")
        items.append((p, f"anchors4/{a['file']}"))
    items.append((anchors_dir / "manifest.json", "anchors4/manifest.json"))

    if include_hidden:
        hidden = game / "private" / "maps4_hidden"
        maps = sorted(hidden.glob("*.txt")) if hidden.is_dir() else []
        if not maps:
            raise SystemExit(f"{game.name}: 没有 private/maps4_hidden/*.txt，拒绝打空隐藏图包")
        for p in maps:
            items.append((p, f"maps4_hidden/{p.name}"))

    for name in ("meta.json", "CONDITIONS.md"):
        f = game / name
        if f.exists():
            items.append((f, name))

    items.sort(key=lambda x: x[1])
    return items


def _allowed4(name: str) -> bool:
    """4 席评测包白名单：anchors4/、maps4_hidden/、meta/CONDITIONS/包清单。"""
    if name in {"meta.json", "CONDITIONS.md", "manifest.json"}:
        return True
    parts = Path(name).parts
    if len(parts) == 2 and parts[0] == "anchors4":
        return parts[1] == "manifest.json" or Path(name).suffix.lower() in {".exe", ""}
    if len(parts) == 2 and parts[0] == "maps4_hidden":
        return Path(name).suffix.lower() == ".txt"
    return False


def verify_eval4(zip_path: Path, package_manifest: dict,
                 require_hidden: bool = True) -> list[str]:
    """4 席评测包自检：无源码、隐藏图在、两层 manifest 与实际字节自洽。"""
    fails: list[str] = []
    with zipfile.ZipFile(zip_path) as zf:
        names = {n for n in zf.namelist() if not n.endswith("/")}
        files = {n: zf.read(n) for n in names}

    for n in sorted(names):
        if not _allowed4(n):
            fails.append(f"不允许的文件：{n}")
        if Path(n).suffix.lower() in SOURCE_SUFFIX:
            fails.append(f"包含源码：{n}")
        if n.startswith("private/") or "/private/" in n:
            fails.append(f"包含 private/ 路径：{n}")
    for n, data in files.items():
        if Path(n).suffix.lower() in {".json", ".md", ".txt"} and b"private/" in data:
            fails.append(f"内容包含 private/ 路径：{n}")

    am_raw = files.get("anchors4/manifest.json")
    anchor_files: set[str] = set()
    if am_raw is None:
        fails.append("缺 anchors4/manifest.json")
        am: dict = {}
    else:
        try:
            am = json.loads(am_raw.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            fails.append(f"anchors4 manifest 解析失败：{exc}")
            am = {}
        if am.get("seats") != 4:
            fails.append(f"anchors4 manifest 的 seats={am.get('seats')}，应为 4")
        if not am.get("anchors"):
            fails.append("anchors4 manifest 没有 anchor 条目")
        for a in am.get("anchors", []):
            rel = f"anchors4/{a['file']}"
            anchor_files.add(rel)
            if rel not in files:
                fails.append(f"anchor 清单引用的文件不在包里：{rel}")
                continue
            got = hashlib.sha256(files[rel]).hexdigest()
            if got != a.get("sha256"):
                fails.append(f"anchor hash 不一致：{rel}")
            if len(files[rel]) != a.get("size", len(files[rel])):
                fails.append(f"anchor 大小不一致：{rel}")

    outer = {f"anchors4/{a['file']}" for a in package_manifest.get("anchors", [])}
    if anchor_files != outer:
        fails.append("包清单与 anchors4 manifest 的 anchor 列表不一致")

    hidden = {n for n in names if n.startswith("maps4_hidden/")}
    if require_hidden and not hidden:
        fails.append("缺 maps4_hidden/ 隐藏图")
    for n in sorted(hidden):
        if Path(n).suffix.lower() != ".txt" or not files[n]:
            fails.append(f"隐藏图异常：{n}")

    listed = {e["path"]: e for e in package_manifest.get("files", [])}
    body = names - {"manifest.json"}
    for rel in sorted(set(listed) - body):
        fails.append(f"包清单列了但包里没有：{rel}")
    for rel in sorted(body - set(listed)):
        fails.append(f"包里有但清单没列：{rel}")
    for rel in sorted(set(listed) & body):
        e = listed[rel]
        if hashlib.sha256(files[rel]).hexdigest() != e.get("sha256"):
            fails.append(f"包清单 hash 不一致：{rel}")
        if len(files[rel]) != e.get("size", len(files[rel])):
            fails.append(f"包清单大小不一致：{rel}")
    return fails


def pack_game4(game: Path, meta: dict, out_dir: Path, anchors_dir: Path,
               include_hidden: bool, do_strip: bool) -> tuple[dict, list[str]]:
    """封 4 席 anchor + 打 4 席评测包 + 自检返回 (report, fails)。"""
    names = meta.get("anchors4", [])
    if not names:
        raise SystemExit(f"{game.name}: meta.json 没有 anchors4，无法打 4 席评测包")

    print(f"packing eval4 {game.name}")
    anchor_manifest = seal_anchors.seal_game4(game, names, anchors_dir, do_strip)
    items = collect4(game, anchors_dir, anchor_manifest, include_hidden)

    manifest = {
        "game": game.name,
        "kind": "eval4",
        "seats": 4,
        "version": meta.get("version"),
        "rules_hash": meta.get("rules_hash"),
        "engine_hash": meta.get("engine_hash"),
        "platform": f"{platform.system().lower()}-{platform.machine().lower()}",
        "anchors": [{"name": a["name"], "file": a["file"]}
                    for a in anchor_manifest["anchors"]],
        "files": [{"path": rel, "sha256": sha256(p), "size": p.stat().st_size}
                  for p, rel in items],
    }

    out = out_dir / f"{game.name}-4p.zip"
    write_zip(out, items, manifest)
    fails = verify_eval4(out, manifest, require_hidden=include_hidden)
    digest = sha256(out)
    hidden_n = sum(1 for _p, rel in items if rel.startswith("maps4_hidden/"))
    print(f"  {len(items)} files  {display_path(out)}  "
          f"({out.stat().st_size / 1024:.1f} KB)  sha256 {digest[:16]}")
    if fails:
        for f in fails:
            print(f"  SELF-CHECK FAIL  {f}")
    else:
        print(f"  SELF-CHECK PASS  anchors4={len(anchor_manifest['anchors'])} "
              f"maps4_hidden={hidden_n} 源码=0 manifest 自洽")
    report = {"game": game.name, "path": display_path(out), "seats": 4,
              "files": len(items), "sha256": digest,
              "self_check": "fail" if fails else "pass"}
    return report, fails


def main() -> int:
    ap = argparse.ArgumentParser(description="生成封闭评测包")
    ap.add_argument("--game", default="all", help="题号或目录名；all = 所有可运行题")
    ap.add_argument("--seats", choices=("2", "4"), default="2",
                    help="2 = 现有 2 席评测包（默认）；4 = 4 席评测包（anchors4/ + maps4_hidden/）")
    ap.add_argument("--anchors-out", default="",
                    help="anchor 二进制输出目录；默认 dist/anchors/<game> 或 dist/anchors4/<game>")
    ap.add_argument("--out", default="",
                    help="评测包输出目录；默认 dist/eval，--seats 4 时默认 dist/eval4")
    ap.add_argument("--no-hidden", action="store_true", help="不打包隐藏地图")
    ap.add_argument("--strip", action="store_true", help="封装 anchor 时去符号")
    ap.add_argument("--json")
    args = ap.parse_args()

    games = harness.all_games() if args.game.strip().lower() == "all" \
        else [harness.game_dir(g.strip()) for g in args.game.split(",") if g.strip()]
    out_dir = Path(args.out).resolve() if args.out \
        else REPO / "dist" / ("eval4" if args.seats == "4" else "eval")
    out_dir.mkdir(parents=True, exist_ok=True)

    reports = []
    self_check_bad: list[str] = []
    for game in games:
        meta = json.loads((game / "meta.json").read_text(encoding="utf-8"))
        if args.seats == "4":
            if not meta.get("anchors4"):
                print(f"skip {game.name}: meta.json 没有 anchors4")
                print()
                continue
            anchors_dir = Path(args.anchors_out).resolve() if args.anchors_out \
                else REPO / "dist" / "anchors4" / game.name
            report, fails = pack_game4(game, meta, out_dir, anchors_dir,
                                       not args.no_hidden, args.strip)
            reports.append(report)
            if fails:
                self_check_bad.append(game.name)
            print()
            continue

        names = meta.get("anchors", [])
        anchors_dir = Path(args.anchors_out).resolve() if args.anchors_out \
            else REPO / "dist" / "anchors" / game.name

        print(f"packing eval {game.name}")
        anchor_manifest = seal_anchors.seal_game(game, names, anchors_dir, args.strip)
        items = collect(game, anchors_dir, anchor_manifest, not args.no_hidden)

        manifest = {
            "game": game.name,
            "kind": "eval",
            "version": meta.get("version"),
            "rules_hash": meta.get("rules_hash"),
            "engine_hash": meta.get("engine_hash"),
            "platform": f"{platform.system().lower()}-{platform.machine().lower()}",
            "files": [{"path": rel, "sha256": sha256(p), "size": p.stat().st_size}
                      for p, rel in items],
        }

        out = out_dir / f"{game.name}.zip"
        write_zip(out, items, manifest)

        digest = sha256(out)
        print(f"  {len(items)} files  {out.relative_to(REPO).as_posix()}  "
              f"({out.stat().st_size / 1024:.1f} KB)  sha256 {digest[:16]}")
        reports.append({"game": game.name, "path": out.relative_to(REPO).as_posix(),
                        "files": len(items), "sha256": digest})
        print()

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"tool": "pack_eval", "games": reports},
                                ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"{len(reports)} 个评测包已生成")
    if self_check_bad:
        print(f"4 席包自检失败：{', '.join(self_check_bad)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
