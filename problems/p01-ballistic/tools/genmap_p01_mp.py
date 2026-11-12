#!/usr/bin/env python3
"""P01 v2 4 席不对称地图生成器。

96x54，四个基地不做镜像（4 席地图不要求镜像，公平性靠座位轮换）。
所有随机数都来自 random.Random(seed)，同 seed 同参数重复生成逐字节一致。
每张图都必须通过引擎 strict LOAD（mask=15 表示 4 席全开）：

    LOAD <path> <seed> 15

布局族（--layout）：
    line      四席各自随机起伏，默认，与历史输出逐字节一致
    pair2x2   两对相邻基地 + 宽中央间隔，对内高 0-1 行，中央土丘，内席屋顶

用法：
    python problems/p01-ballistic/tools/genmap_p01_mp.py --out build/p01-v2/candidates_public \
        --seed 1000 --count 24 --prefix pub4
    python problems/p01-ballistic/tools/genmap_p01_mp.py --out <dir> --seed 1000 --count 12 \
        --prefix public4 --layout pair2x2

输出：
    <out>/<prefix>-01.txt ... 地图文件
    <out>/manifest.json        layout / seed / 空气率 / 出生点 / params / features / sha256
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
WATER_ROWS = (50, 51)
BANDS = ((11, 16), (33, 38), (57, 62), (78, 83))  # 四个基地的中心 x 区间
MIN_GAP = 16                                      # 基地中心最小间距
REPO = Path(__file__).resolve().parents[3]
DEFAULT_ENGINE = REPO / "problems" / "p01-ballistic" / "build" / "engine.exe"


def blank() -> list[list[str]]:
    g = [["#"] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            if x < BORDER or x >= W - BORDER or y < BORDER or y >= H - BORDER:
                g[y][x] = "X"
    return g


def air_cells(g: list[list[str]]) -> int:
    # 与引擎一致：'.' 和出生点数字都算空气，'~' 不算
    return sum(1 for row in g for c in row if c == "." or "0" <= c <= "3")


def spawn_offsets(rng: random.Random) -> list[int]:
    """3 个出生标记相对基地中心的偏移，间隔 >= 3，避免互相堵住移动。"""
    while True:
        offs = sorted(rng.sample(range(-4, 5), 3))
        if offs[1] - offs[0] >= 3 and offs[2] - offs[1] >= 3:
            return offs


def make_bases(rng: random.Random) -> list[dict]:
    ys = [rng.randint(28, 33) for _ in range(4)]
    if len(set(ys)) == 1:  # 强制至少两个不同高度
        ys[-1] = 33 if ys[0] != 33 else 28
    bases = []
    for seat, (lo, hi) in enumerate(BANDS):
        bases.append({
            "seat": seat,
            "cx": rng.randint(lo, hi),
            "y": ys[seat],
            "half": rng.choice((5, 6)),
            "offsets": spawn_offsets(rng),
        })
    centers = [b["cx"] for b in bases]
    if any(b - a < MIN_GAP for a, b in zip(centers, centers[1:])):
        raise ValueError("base centers violate MIN_GAP")
    return bases


def surface_heights(rng: random.Random, bases: list[dict]) -> list[int]:
    hs = [0] * W
    cur = rng.randint(28, 32)
    for x in range(BORDER, W - BORDER):
        cur = max(27, min(33, cur + rng.choice((-1, 0, 0, 0, 1))))
        hs[x] = cur
    # 基地平台压平到各自的 y，左右高低差就是不对称的来源之一
    for b in bases:
        for x in range(b["cx"] - b["half"], b["cx"] + b["half"] + 1):
            hs[x] = b["y"]
    return hs


def paint(g: list[list[str]], hs: list[int]) -> None:
    for x in range(BORDER, W - BORDER):
        for y in range(BORDER, hs[x]):
            g[y][x] = "."
    for y in WATER_ROWS:
        for x in range(BORDER, W - BORDER):
            g[y][x] = "~"


def add_wells(g: list[list[str]], rng: random.Random, hs: list[int]) -> list[dict]:
    """两侧边缘水井：掉下去就是落水死亡，左右各 75% 概率出现。"""
    sides = [s for s in ("L", "R") if rng.random() < 0.75]
    if not sides:
        sides = [rng.choice(("L", "R"))]
    wells = []
    for side in sides:
        width = rng.choice((1, 2))
        x0 = BORDER if side == "L" else W - BORDER - width
        xs = list(range(x0, x0 + width))
        top = max(BORDER, min(hs[x] for x in xs) - rng.randint(0, 2))
        for x in xs:
            for y in range(top, WATER_ROWS[0]):
                g[y][x] = "."
        wells.append({"side": side, "x": [xs[0], xs[-1]], "top": top})
    return wells


def add_covers(g: list[list[str]], rng: random.Random) -> list[list[int]]:
    """随机浮空掩体 + 贴近地表的短檐，位置和形状都随机，不镜像。"""
    rects = []
    for _ in range(rng.randint(3, 6)):
        w, h = rng.randint(4, 10), rng.randint(1, 2)
        x0 = rng.randint(5, W - 6 - w)
        y0 = rng.randint(8, 22 - h + 1)
        for y in range(y0, y0 + h):
            for x in range(x0, x0 + w):
                g[y][x] = "#"
        rects.append([x0, y0, x0 + w - 1, y0 + h - 1])
    for _ in range(rng.randint(1, 3)):
        w = rng.randint(4, 8)
        x0 = rng.randint(6, W - 7 - w)
        y0 = rng.randint(20, 24)
        for x in range(x0, x0 + w):
            g[y0][x] = "#"
        rects.append([x0, y0, x0 + w - 1, y0])
    return rects


def fit_air(g: list[list[str]], rng: random.Random, bases: list[dict],
            hs: list[int]) -> bool:
    """空气率必须落在引擎的 25%-60%；偏高时在远离基地处堆土，偏低直接放弃。"""
    while air_cells(g) * 100 > W * H * 59:
        for _ in range(400):
            x0, w = rng.randint(5, 88), rng.randint(2, 4)
            xs = range(x0, x0 + w)
            if any(abs(x - b["cx"]) <= 8 for x in xs for b in bases):
                continue
            top = rng.randint(16, 24)
            for x in xs:
                for y in range(top, min(hs[x], WATER_ROWS[0])):
                    g[y][x] = "#"
            break
        else:
            return False
    return air_cells(g) * 100 >= W * H * 25


def self_check(g: list[list[str]]) -> str:
    if len(g) != H or any(len(row) != W for row in g):
        return "size"
    for s in "0123":
        if sum(row.count(s) for row in g) != 3:
            return f"seat {s} spawn count"
    for y in range(H):
        for x in range(W):
            if (x < BORDER or x >= W - BORDER or y < BORDER or y >= H - BORDER):
                if g[y][x] != "X":
                    return "border"
    n = air_cells(g) * 100
    if n < W * H * 25 or n > W * H * 60:
        return "air ratio"
    return ""


def compose_line(seed: int) -> tuple[list[list[str]] | None, dict | None, str]:
    rng = random.Random(seed)
    g = blank()
    bases = make_bases(rng)
    hs = surface_heights(rng, bases)
    paint(g, hs)
    wells = add_wells(g, rng, hs)
    rects = add_covers(g, rng)
    if not fit_air(g, rng, bases, hs):
        return None, None, "air ratio"
    for b in bases:
        for off in b["offsets"]:
            g[b["y"] - 1][b["cx"] + off] = str(b["seat"])
    err = self_check(g)
    if err:
        return None, None, err

    spawns = []
    base_meta = []
    for b in bases:
        x0, x1 = b["cx"] - b["half"], b["cx"] + b["half"]
        base_meta.append({
            "seat": b["seat"],
            "center": [b["cx"], b["y"]],
            "platform_x": [x0, x1],
            "spawn_y": b["y"] - 1,
        })
        for off in b["offsets"]:
            spawns.append({"seat": b["seat"], "x": b["cx"] + off, "y": b["y"] - 1})
    air = air_cells(g)
    meta = {
        "air_cells": air,
        "air_percent": round(air * 100.0 / (W * H), 2),
        "bases": base_meta,
        "spawns": spawns,
        "features": {
            "floating_cover_rects": len(rects),
            "floating_cover_cells": sum((r[2] - r[0] + 1) * (r[3] - r[1] + 1)
                                       for r in rects),
            "edge_wells": wells,
            "surface_y_min": min(hs[BORDER:W - BORDER]),
            "surface_y_max": max(hs[BORDER:W - BORDER]),
        },
    }
    return g, meta, ""


# ------------------------------------------------------------------ pair2x2 布局族

def _base(seat: int, cx: int, y: int, half: int, rng: random.Random) -> dict:
    return {"seat": seat, "cx": cx, "y": y, "half": half,
            "offsets": spawn_offsets(rng)}


def terrain(rng: random.Random, bases: list[dict], lo: int = 28, hi: int = 33,
            ramp: bool = True) -> list[int]:
    """低平原 + 基地台面（可选相邻基地间线性坡）；族特征（土丘）另外叠加。"""
    hs = [0] * W
    cur = rng.randint(30, 32)
    for x in range(BORDER, W - BORDER):
        cur = max(lo, min(hi, cur + rng.choice((-1, 0, 0, 0, 1))))
        hs[x] = cur
    # 顺序不能反：先削掉高过平台的土，再压平台，否则会把邻席台面压掉
    for s in bases:
        for x in range(s["cx"] - s["half"] - 5, s["cx"] + s["half"] + 6):
            if BORDER <= x < W - BORDER:
                hs[x] = max(hs[x], s["y"])
    for s in bases:
        for x in range(s["cx"] - s["half"], s["cx"] + s["half"] + 1):
            hs[x] = s["y"]
    if not ramp:
        return hs
    for a, c in zip(bases, bases[1:]):
        x0 = a["cx"] + a["half"]
        x1 = c["cx"] - c["half"]
        if x1 - x0 < 3:
            continue
        for x in range(x0 + 1, x1):
            t = (x - x0) / (x1 - x0)
            # 坡面只往低处抖：保证两个席之间至少留一条平射槽
            hs[x] = round(a["y"] + (c["y"] - a["y"]) * t) + rng.choice((0, 0, 1, 2))
    return hs


def shares_of(bases: list[dict]) -> list[float]:
    """每席按出生中心的 1/d^2 暴露份额。"""
    c = [(s["cx"] + sum(s["offsets"]) / 3.0, s["y"] - 1.0) for s in bases]
    e = [sum(1.0 / ((c[i][0] - c[j][0]) ** 2 + (c[i][1] - c[j][1]) ** 2)
             for j in range(4) if j != i) for i in range(4)]
    t = sum(e)
    return [v / t for v in e]


def balance_pairs(bases: list[dict], limit: int = 2) -> list[dict]:
    """后处理：左右微调外席 x，让四席的 1/d^2 暴露份额尽量接近 25%。"""
    best = None
    for d0 in range(-limit, limit + 1):
        for d3 in range(-limit, limit + 1):
            trial = [dict(s) for s in bases]
            trial[0]["cx"] += d0
            trial[3]["cx"] += d3
            xs = [s["cx"] for s in trial]
            if any(b - a < 12 for a, b in zip(xs, xs[1:])):
                continue
            if min(s["cx"] - s["half"] for s in trial) < 4:
                continue
            if max(s["cx"] + s["half"] for s in trial) > 90:
                continue
            sh = shares_of(trial)
            score = (round(max(sh) - min(sh), 4), abs(d0) + abs(d3))
            if best is None or score < best[0]:
                best = (score, trial)
    return best[1] if best else bases


def carve(g: list[list[str]], x0: int, x1: int, top: int) -> None:
    """把 x0..x1 从 top 一列挖到水面：挖空即落水死亡沟。"""
    for x in range(x0, x1 + 1):
        for y in range(top, WATER_ROWS[0]):
            g[y][x] = "."


def roof(g: list[list[str]], rng: random.Random, cx: int, y: int,
         cells: list[list[int]]) -> None:
    """基地正上方屋顶（可破坏泥土），宽度随机，1-2 层。"""
    w = rng.randint(9, 12)
    rows = rng.choice((1, 2))
    x0 = cx - w // 2
    for dy in range(rows):
        for x in range(x0, x0 + w):
            g[y + dy][x] = "#"
    cells.append([x0, y, x0 + w - 1, y + rows - 1])


def layout_pair2x2(rng: random.Random) -> tuple[list[dict], dict]:
    s0 = rng.randint(10, 13)
    g = rng.randint(14, 18)          # 左右两对用同一个对间距
    s1 = s0 + g
    s3 = rng.randint(81, 84)
    s2 = s3 - g
    if s2 - s1 < 32:
        raise ValueError("center gap too small")
    y0 = rng.randint(28, 30)
    y_in = y0 - rng.choice((0, 1))   # 最多错 1 行；再多就没有平射通道了
    bases = [_base(0, s0, y0, rng.choice((5, 6)), rng),
             _base(1, s1, y_in, rng.choice((5, 6)), rng),
             _base(2, s2, y_in, rng.choice((5, 6)), rng),
             _base(3, s3, y0, rng.choice((5, 6)), rng)]
    bases = balance_pairs(bases)
    hs = terrain(rng, bases)
    # 中央土丘：把两个内席之间抬到高于台面，压低 S1<->S2 的平射通道
    ridge_y = y0 - rng.randint(2, 4)
    half = rng.randint(4, 8)
    mid = (bases[1]["cx"] + bases[2]["cx"]) // 2
    for x in range(mid - half, mid + half + 1):
        hs[x] = min(hs[x], ridge_y)
    params = {"d01": bases[1]["cx"] - bases[0]["cx"],
              "d12": bases[2]["cx"] - bases[1]["cx"],
              "d23": bases[3]["cx"] - bases[2]["cx"],
              "y_in": y_in, "y_out": y0,
              "ridge": [mid, half, ridge_y],
              "share_spread_pp": round((max(shares_of(bases)) - min(shares_of(bases))) * 100, 2)}
    return bases, {"hs": hs, "params": params}


def decorate_pair2x2(g: list[list[str]], rng: random.Random,
                     bases: list[dict]) -> dict:
    roofs = []
    for s in (1, 2):
        roof(g, rng, bases[s]["cx"], bases[s]["y"] - rng.randint(6, 8), roofs)
    for _ in range(rng.randint(1, 2)):          # 中央空域浮岛
        w = rng.randint(5, 9)
        x0 = rng.randint(28, 70 - w)
        y0 = rng.randint(8, 18)
        for x in range(x0, x0 + w):
            g[y0][x] = "#"
        roofs.append([x0, y0, x0 + w - 1, y0])
    if rng.random() < 0.3:                      # 偶尔给边缘一小块
        s = rng.choice((0, 3))
        roof(g, rng, bases[s]["cx"], bases[s]["y"] - 7, roofs)
    carve(g, 2, 3, bases[0]["y"] - 2)
    carve(g, 92, 93, bases[3]["y"] - 2)
    return {"roofs": roofs}


def compose_pair2x2(seed: int) -> tuple[list[list[str]] | None, dict | None, str]:
    rng = random.Random(seed)
    try:
        bases, lay = layout_pair2x2(rng)
    except ValueError as exc:
        return None, None, str(exc)
    hs = lay["hs"]
    g = blank()
    paint(g, hs)
    feat = decorate_pair2x2(g, rng, bases)
    for s in bases:
        for off in s["offsets"]:
            g[s["y"] - 1][s["cx"] + off] = str(s["seat"])
    err = self_check(g)
    if err:
        return None, None, err

    air = air_cells(g)
    meta = {
        "air_cells": air,
        "air_percent": round(air * 100.0 / (W * H), 2),
        "params": lay["params"],
        "bases": [{"seat": s["seat"], "center": [s["cx"], s["y"]],
                   "platform_x": [s["cx"] - s["half"], s["cx"] + s["half"]],
                   "spawn_y": s["y"] - 1} for s in bases],
        "spawns": [{"seat": s["seat"], "x": s["cx"] + off, "y": s["y"] - 1}
                   for s in bases for off in s["offsets"]],
        "features": feat,
    }
    return g, meta, ""


LAYOUTS = {"line": compose_line, "pair2x2": compose_pair2x2}


def compose(seed: int, layout: str = "line") -> tuple[list[list[str]] | None, dict | None, str]:
    return LAYOUTS[layout](seed)


def render(g: list[list[str]]) -> str:
    return "\n".join("".join(row) for row in g) + "\n"


def engine_check(engine: Path, path: Path) -> tuple[bool, str]:
    proc = subprocess.run(
        [str(engine)],
        input=f"LOAD {path.as_posix()} 1 15\nQUIT\n",
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    lines = [line for line in proc.stdout.splitlines() if line]
    line = lines[0] if lines else ""
    return line.startswith("OK "), line


def engine_label(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO).as_posix()
    except ValueError:
        return path.name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--seed", type=int, default=1, help="first seed")
    ap.add_argument("--count", type=int, default=1, help="number of maps")
    ap.add_argument("--prefix", default="map4", help="file name prefix")
    ap.add_argument("--layout", default="line", choices=tuple(LAYOUTS),
                    help="layout family (default: line)")
    ap.add_argument("--engine", default=str(DEFAULT_ENGINE),
                    help="P01 engine used for strict LOAD")
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
        g, meta, err = compose(used_seed, args.layout)
        if g is None:
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
            print(f"reject seed={used_seed}: {line or 'no engine output'}",
                  file=sys.stderr)
            continue
        tmp.replace(out / name)
        entry = {
            "file": name,
            "seed": used_seed,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            **meta,
        }
        entry.setdefault("params", {})
        entries.append(entry)
        print(f"{name} seed={used_seed} air={meta['air_percent']:.2f}% {line}")

    manifest = {
        "generator": "tools/genmap_p01_mp.py",
        "layout": args.layout,
        "engine": engine_label(engine),
        "size": [W, H],
        "seats": 4,
        "worms_per_seat": 3,
        "load": "LOAD <file> 1 15",
        "prefix": args.prefix,
        "requested_seed": args.seed,
        "requested_count": args.count,
        "seed_first": entries[0]["seed"],
        "seed_last": entries[-1]["seed"],
        "attempts": attempts,
        "failures": failures,
        "maps": entries,
    }
    text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (out / "manifest.json").write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {len(entries)} maps to {out} "
          f"(attempts={attempts} failures={failures} seeds={entries[0]['seed']}"
          f"..{entries[-1]['seed']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
