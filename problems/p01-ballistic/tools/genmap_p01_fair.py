#!/usr/bin/env python3
"""P01 v2.2 4 席 【公平地图生成器】—— 替换 pair2x2。

为什么换掉 pair2x2（2026-11-07 实测）：
  · 108 张公开图里 0 张垂直镜像、0 张 rot180；内席头顶永压顶棚
    （顶棚率 19%/25%，外席 2%/4%），内席开火仰角只剩 2–5 格，外席 21 格。
  · 4 个【完全相同】的 bot 跑 24 全排列：106/108 张图只有单一座次能赢，
    总胜率 seat0 36.8% / seat3 34.0% / seat1 14.2% / seat2 15.1%。
    —— 这些数字与 bot 强弱无关，是地图发的。

新方案：**四席镜像构造（mirror4）**
  1. 只生成左半边 x ∈ [2, 47]，右半边由 x -> 95-x 镜像得到；出生点标记
     0<->3、1<->2 成对镜像。⇒ 图【在任何镜像对称意义上都严格对称】。
  2. 四个基地等高（同一 BASE_Y）、等距（同一 BASE_GAP）、边距相等。
     ⇒ 座次在几何上没有任何差异可发。
  3. 布局族只改变【中央】与【天空】(土丘/浮岛/边缘井)，全部成对镜像出现，
     因此不会偏袒任何一席。
  4. 求出图后仍送正式引擎 strict LOAD；被拒就算失败，不自动修。

★ 仍然存在的、地图治不了的部分：引擎结算按虫 id 升序（settle）、
  爆炸按 id、涨水按 id。这是"标签顺序"，不是"地形"。实测把它做成
  完全对称的图之后，4 个相同 bot 仍然由低 id（=seat0/3 一侧）赢。
  那一条只能在【赛制】上消：见 RULES 第 0 节"一场 = 24 排列 × 2 seed"，
  以及 docs/fairness-audit.md。

用法：
    python problems/p01-ballistic/tools/genmap_p01_mp.py --out build/p01-v220/public4 --seed 2000 \
        --count 108 --prefix public4 --layout mirror4 --engine build/engine.exe
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import sys
from pathlib import Path

W, H = 96, 54
BORDER = 2
WATER_TOP = 50                 # y >= 50 是水
BASE_Y = 30                    # 基地台面 y（出生点在 BASE_Y - 1）
SPAWN_Y = BASE_Y - 1
BASE_X = (16, 37, 58, 79)      # 等高等距；95-16=79, 95-37=58 → 0<->3, 1<->2
BASE_HALF = 5                   # 台面半宽（含）
SPAWN_OFF = (-4, 0, 4)
MIRROR = {"0": "3", "3": "0", "1": "2", "2": "1"}
REPO = Path(__file__).resolve().parents[3]
DEFAULT_ENGINE = REPO / "problems" / "p01-ballistic" / "build" / "engine.exe"


# ------------------------------------------------------------------ 基本块

def blank() -> list[list[str]]:
    g = [["." for _ in range(W)] for _ in range(H)]
    for y in range(H):
        for x in range(W):
            if x < BORDER or x >= W - BORDER or y < BORDER or y >= H - BORDER:
                g[y][x] = "X"
    return g


def air_cells(g: list[list[str]]) -> int:
    return sum(1 for row in g for c in row if c == "." or "0" <= c <= "3")


def carve_sym(g, x0: int, x1: int, y0: int, y1: int, ch: str) -> None:
    """在 [x0,x1] × [y0,y1] 画 ch，并镜像到右半边。"""
    for y in range(max(BORDER, y0), min(H - BORDER, y1 + 1)):
        for x in range(max(BORDER, x0), min(W - BORDER, x1 + 1)):
            g[y][x] = ch
            g[y][W - 1 - x] = ch


def ground(g, rng: random.Random, heights: list[int]) -> None:
    """heights[x] = 地表 y（该行起往下是泥土）。已保证 heights 左右镜像。"""
    for x in range(BORDER, W - BORDER):
        for y in range(heights[x], WATER_TOP):
            g[y][x] = "X" if y >= H - BORDER else "#"
    for y in range(WATER_TOP, H - BORDER):
        for x in range(BORDER, W - BORDER):
            g[y][x] = "~"


def flat_heights() -> list[int]:
    return [BASE_Y] * W


def ramp_heights(left_heights: list[int]) -> list[int]:
    """把左半边的高度表镜像到右半边（索引 2..47 拷到 48..93）。"""
    hs = list(left_heights)
    for x in range(BORDER, W // 2):
        hs[W - 1 - x] = hs[x]
    return hs


# ------------------------------------------------------------------ 布局族（全部镜像）

def central_ridge(rng: random.Random, g, heights) -> dict:
    """中央可破坏山脊：抬到 BASE_Y-3..-5，宽度随机；镜像自动对称。"""
    top = BASE_Y - rng.randint(3, 5)
    half = rng.randint(4, 7)
    mid = W // 2                    # 47.5 -> 用 48 作中心，左右同步
    carve_sym(g, mid - half, mid + half - 1, top, BASE_Y - 1, ".")
    for x in range(mid - half, mid + half):
        heights[x] = top
        heights[W - 1 - x] = top
    return {"ridge_top": top, "ridge_half": half}


def sky_islands(rng: random.Random, g, heights) -> list[list[int]]:
    """中央空域浮岛：偶数对出现（左一块、右一块镜像）。"""
    rects = []
    for _ in range(rng.randint(1, 2)):
        w = rng.randint(5, 9)
        x0 = rng.randint(20, W // 2 - 8)
        y0 = rng.randint(8, 20)
        carve_sym(g, x0, x0 + w - 1, y0, y0, "#")
        rects.append([x0, y0, x0 + w - 1, y0])
    return rects


def side_pockets(rng: random.Random, g) -> list[list[int]]:
    """基地与中央之间的低洼口袋（成对镜像），给中盘提供掩体。"""
    rects = []
    for _ in range(rng.randint(1, 2)):
        w = rng.randint(3, 6)
        x0 = rng.randint(26, 40)
        depth = rng.randint(2, 4)
        carve_sym(g, x0, x0 + w - 1, BASE_Y, BASE_Y + depth - 1, ".")
        rects.append([x0, BASE_Y, x0 + w - 1, BASE_Y + depth - 1])
    return rects


def edge_wells(rng: random.Random, g, heights) -> list[list[int]]:
    """两侧边缘水井：挖到水面即为落水死亡沟；左右必成对。"""
    width = rng.choice((1, 2))
    top = BASE_Y - rng.randint(0, 2)
    carve_sym(g, BORDER, BORDER + width - 1, top, WATER_TOP - 1, ".")
    for x in range(BORDER, BORDER + width):
        heights[x] = top
        heights[W - 1 - x] = top
    return [[BORDER, top, BORDER + width - 1, WATER_TOP - 1]]


def canopy_pairs(rng: random.Random, g) -> list[list[int]]:
    """四席头顶各留一道【同宽同高】的短檐 —— 要么四席都有，要么都没有。"""
    if rng.random() >= 0.35:
        return []
    w = rng.randint(7, 9)
    h = rng.choice((1, 2))
    y0 = BASE_Y - rng.randint(6, 8)
    rects = []
    for cx in BASE_X:
        carve_sym(g, cx - w // 2, cx + w // 2, y0, y0 + h - 1, "#")
        rects.append([cx - w // 2, y0, cx + w // 2, y0 + h - 1])
    return rects


def compose_mirror4(seed: int) -> tuple[list[list[str]] | None, dict | None, str]:
    rng = random.Random(seed)
    g = blank()
    heights = ramp_heights([BASE_Y] * W)
    features: dict = {}

    features["wells"] = edge_wells(rng, g, heights)
    features["pockets"] = side_pockets(rng, g)
    features["ridge"] = central_ridge(rng, g, heights)
    features["islands"] = sky_islands(rng, g, heights)
    features["canopy"] = canopy_pairs(rng, g)
    ground(g, rng, heights)

    # 台面：四席同一 y、同一半宽；出生点同偏移 -> 几何完全等价。
    # ★ 地表本身就在 BASE_Y（上面是空气），所以这里【不能】再挖 BASE_Y 那一行，
    #   否则把脚下的地挖掉，引擎 strict LOAD 会报 "spawn has no ground"。
    for cx in BASE_X:
        carve_sym(g, cx - BASE_HALF, cx + BASE_HALF, SPAWN_Y, SPAWN_Y, ".")
        carve_sym(g, cx - BASE_HALF, cx + BASE_HALF, SPAWN_Y - 1, SPAWN_Y - 1, ".")
    for seat, cx in enumerate(BASE_X):
        for off in SPAWN_OFF:
            g[SPAWN_Y][cx + off] = str(seat)

    air = air_cells(g)
    if air * 100 < W * H * 25 or air * 100 > W * H * 60:
        return None, None, "air ratio"

    meta = {
        "air_cells": air,
        "air_percent": round(air * 100.0 / (W * H), 2),
        "base_y": BASE_Y,
        "base_x": list(BASE_X),
        "base_half": BASE_HALF,
        "spawn_offsets": list(SPAWN_OFF),
        "spawn_y": SPAWN_Y,
        "mirror_pairs": [[0, 3], [1, 2]],
        "spawns": [{"seat": seat, "x": cx + off, "y": SPAWN_Y}
                   for seat, cx in enumerate(BASE_X) for off in SPAWN_OFF],
        "features": features,
    }
    return g, meta, ""


LAYOUTS = {"mirror4": compose_mirror4}


# ------------------------------------------------------------------ 校验

def mirror_ok(rows: list[str]) -> bool:
    for y in range(H):
        for x in range(W):
            a, b = rows[y][x], rows[y][W - 1 - x]
            if MIRROR.get(a, a) != b:
                return False
    return True


def self_check(g: list[list[str]]) -> str:
    if len(g) != H or any(len(row) != W for row in g):
        return "size"
    for s in "0123":
        if sum(row.count(s) for row in g) != 3:
            return f"seat {s} spawn count"
    for y in range(H):
        for x in range(W):
            if x < BORDER or x >= W - BORDER or y < BORDER or y >= H - BORDER:
                if g[y][x] != "X":
                    return "border"
    rows = ["".join(r) for r in g]
    if not mirror_ok(rows):
        return "map is not mirror symmetric under 0<->3 / 1<->2"
    n = air_cells(g) * 100
    if n < W * H * 25 or n > W * H * 60:
        return "air ratio"
    return ""


def render(g: list[list[str]]) -> str:
    return "\n".join("".join(row) for row in g) + "\n"


def engine_check(engine: Path, path: Path) -> tuple[bool, str]:
    proc = subprocess.run(
        [str(engine)],
        input=f"LOAD {path.as_posix()} 1 15\nQUIT\n",
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    lines = [line for line in proc.stdout.splitlines() if line]
    return (lines[0].startswith("OK ") if lines else False), (lines[0] if lines else "")


def engine_label(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO).as_posix()
    except ValueError:
        return path.name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--prefix", default="map4")
    ap.add_argument("--layout", default="mirror4", choices=tuple(LAYOUTS))
    ap.add_argument("--engine", default=str(DEFAULT_ENGINE))
    args = ap.parse_args()

    if args.count < 1:
        ap.error("--count must be >= 1")
    engine = Path(args.engine)
    if not engine.is_file():
        ap.error(f"engine not found: {engine}")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    entries = []
    seed = args.seed
    attempts = failures = 0
    while len(entries) < args.count:
        if attempts >= args.count * 50:
            print(f"stopped after {attempts} attempts", file=sys.stderr)
            return 1
        attempts += 1
        used_seed = seed
        seed += 1
        g, meta, err = LAYOUTS[args.layout](used_seed)
        if g is None:
            failures += 1
            print(f"reject seed={used_seed}: {err}", file=sys.stderr)
            continue
        err = self_check(g)
        if err:
            failures += 1
            print(f"reject seed={used_seed}: {err}", file=sys.stderr)
            continue

        text = render(g)
        name = f"{args.prefix}-{len(entries) + 1:02d}.txt"
        tmp = out / f".{name}.tmp"
        tmp.write_text(text, encoding="utf-8", newline="\n")
        ok, line = engine_check(engine, tmp)
        if not ok:
            tmp.unlink()
            failures += 1
            print(f"reject seed={used_seed}: {line or 'no engine output'}", file=sys.stderr)
            continue
        tmp.replace(out / name)
        entries.append({
            "file": name,
            "seed": used_seed,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "mirror_symmetric": True,
            **meta,
        })
        print(f"{name} seed={used_seed} air={meta['air_percent']:.2f}% {line}")

    manifest = {
        "generator": "tools/genmap_p01_mp.py --layout mirror4",
        "layout": args.layout,
        "engine": engine_label(engine),
        "size": [W, H],
        "seats": 4,
        "worms_per_seat": 3,
        "load": "LOAD <file> 1 15",
        "prefix": args.prefix,
        "requested_seed": args.seed,
        "requested_count": args.count,
        "seed_first": entries[0]["seed"] if entries else None,
        "seed_last": entries[-1]["seed"] if entries else None,
        "attempts": attempts,
        "failures": failures,
        "fairness": "mirror_symmetric 0<->3 / 1<->2; equal base_y, equal base gap, equal edge margin",
        "maps": entries,
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")
    print(f"wrote {len(entries)} maps to {out} (attempts={attempts} failures={failures})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
