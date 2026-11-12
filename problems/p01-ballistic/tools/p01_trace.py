#!/usr/bin/env python3
"""P01 跨平台 hash 轨迹对账（Windows / Linux 同一份脚本、同一批用例）。

不依赖 judge.py，不依赖仓库布局：只吃一个已编译的 engine 可执行文件 + 地图目录。

    python3 p01_trace.py run --engine ./engine --maps-dir ./maps --out linux.json
    python tools crosscheck.py --compare dist/_hash_win.json dist/_hash_linux.json

用例设计：3 张 2 席图 × 3 seed × 6 段动作 × 8 步重复推进。
每段动作喂 8 次，让轨迹长到能抓住"某一步开始漂移"这类问题，
而不是只比一个终局 hash。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HEX16 = re.compile(r"\b[0-9a-f]{16}\b")

SEQS = [
    "0 M R; F ROCKET 42 89 | 3 M L; F ROCKET 138 89",
    "0 M R; M R; M R | 3 M L; M L; M L",
    "0 J R; F GRENADE 60 70 | 3 J L; F GRENADE 120 70",
    "0 F DRILL 45 80 | 3 F DRILL 135 80",
    "0 WAIT | 3 WAIT",
    "0 M R; F ROCKET 90 100 | 3 M L; F ROCKET 90 100",
    "0 M R; M R; F GRENADE 30 100 | 3 M L; M L; F GRENADE 150 100",
    "0 J R; J R; F ROCKET 90 40 | 3 J L; J L; F ROCKET 90 40",
]
# 4 席（maps4）用的同一组动作，只是四段而不是两段
SEQS4 = [
    "0 M R; F ROCKET 42 89 | 3 M L; F ROCKET 138 89 | 6 M L; F ROCKET 160 70 | 9 M R; F ROCKET 20 70",
    "0 M R; M R; M R | 3 M L; M L; M L | 6 M R; M R; M R | 9 M L; M L; M L",
    "0 J R; F GRENADE 60 70 | 3 J L; F GRENADE 120 70 | 6 F GRENADE 30 90 | 9 F GRENADE 150 90",
    "0 F DRILL 45 80 | 3 F DRILL 135 80 | 6 F DRILL 80 90 | 9 F DRILL 100 90",
    "0 WAIT | 3 WAIT | 6 WAIT | 9 WAIT",
    "0 M R; F ROCKET 90 100 | 3 M L; F ROCKET 90 100 | 6 WAIT | 9 WAIT",
    "0 M R; M R; F GRENADE 30 100 | 3 M L; M L; F GRENADE 150 100 | 6 WAIT | 9 WAIT",
    "0 J R; J R; F ROCKET 90 40 | 3 J L; J L; F ROCKET 90 40 | 6 WAIT | 9 WAIT",
    "0 M R; M R; M R; F ROCKET 45 80 | 3 M L; M L; M L; F ROCKET 135 80 | 6 F ROCKET 90 60 | 9 F ROCKET 90 60",
]
SEEDS = (1, 7, 42)
REPEAT = 8

# 4 席地图必须显式给 active mask（第三个参数）；2 席地图不给（默认 0b0011）
FOUR_SEAT_PREFIXES = ("public4-", "hidden4-")


def _is_four_seat(name: str) -> bool:
    return name.startswith(FOUR_SEAT_PREFIXES)


def run_trace(engine: Path, map_name: str, lines: list[str], cwd: Path) -> list[str]:
    proc = subprocess.run([str(engine)], input="\n".join(lines) + "\n",
                          capture_output=True, text=True, timeout=600, cwd=str(cwd))
    if proc.returncode != 0:
        raise RuntimeError(f"engine failed ({proc.returncode}): {proc.stderr[:800]}")
    if not proc.stdout.startswith("OK "):
        raise RuntimeError(f"load failed: {proc.stdout[:300]}")
    hashes = HEX16.findall(proc.stdout)
    if not hashes:
        raise RuntimeError("no hashes in output")
    return hashes


def do_run(engine: Path, maps_dir: Path, out: str) -> int:
    maps = sorted(maps_dir.glob("*.txt"))
    if not maps:
        print(f"no maps in {maps_dir}", file=sys.stderr)
        return 2
    result: dict[str, list[str]] = {}
    for mp in maps:
        four = _is_four_seat(mp.name)
        seqs = SEQS4 if four else SEQS
        # 4 席要 active mask；2 席不给（引擎默认 0b0011）
        load_args = f"{maps_dir.name}/{mp.name} {1} 15" if four else f"{maps_dir.name}/{mp.name} {1}"
        for seed in SEEDS:
            for i, seq in enumerate(seqs):
                if four:
                    load = f"LOAD {maps_dir.name}/{mp.name} {seed} 15"
                else:
                    load = f"LOAD {maps_dir.name}/{mp.name} {seed}"
                lines = [load]
                for _ in range(REPEAT):
                    lines.append(f"STEP {seq}")
                lines.append("QUIT")
                tag = f"{mp.stem}/s{seed}/q{i}"
                result[tag] = run_trace(engine, mp.stem, lines, maps_dir.parent)
    total = sum(len(v) for v in result.values())
    print(f"{len(maps)} maps, {len(result)} cases, {total} hashes, "
          f"{len({h for v in result.values() for h in v})} distinct")
    if out:
        Path(out).write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"wrote {out}")
    return 0


def do_compare(a_path: str, b_path: str) -> int:
    a = json.loads(Path(a_path).read_text(encoding="utf-8"))
    b = json.loads(Path(b_path).read_text(encoding="utf-8"))
    if not a or not b:
        print("FAIL: empty")
        return 1
    missing = sorted(set(a) ^ set(b))
    if missing:
        print(f"case set mismatch: {len(missing)}")
        for t in missing[:10]:
            print("  " + t)
        return 1
    diffs = []
    for tag in a:
        ta, tb = a[tag], b[tag]
        if len(ta) != len(tb):
            diffs.append((tag, f"len {len(ta)} vs {len(tb)}", "", ""))
            continue
        for i, (x, y) in enumerate(zip(ta, tb)):
            if x != y:
                diffs.append((tag, f"hash #{i} of {len(ta)}", x, y))
                break
    total = sum(len(v) for v in a.values())
    if diffs:
        print(f"\nMISMATCH {len(diffs)}/{len(a)} cases (of {total} hashes)")
        for tag, where, x, y in diffs[:12]:
            print(f"  {tag}  {where}\n    A {x}\n    B {y}")
        return 1
    print(f"\nPASS  {len(a)}/{len(a)} cases, {total} hashes byte-identical")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--engine", required=True)
    r.add_argument("--maps-dir", required=True)
    r.add_argument("--out", default="")
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    args = ap.parse_args()
    if args.cmd == "run":
        return do_run(Path(args.engine).resolve(), Path(args.maps_dir).resolve(), args.out)
    return do_compare(args.a, args.b)


if __name__ == "__main__":
    sys.exit(main())
