#!/usr/bin/env python3
"""P01 BALLISTIC 地图生成器。

96x54，水平镜像对称。只生成左半边（x=0..47），右半边由镜像产生——
这样"镜像对称"不是校验之后希望它成立，而是生成方式保证它必然成立。

约束（RULES.md 2.2）：
  - 恰好 3 个 '0' 和 3 个 '1'，且镜像位置对应；
  - 外圈 2 格必须是基岩；
  - 出生点不在水里，脚下有支撑，左右至少 1 格空气；
  - 空气比例 25%–60%。
"""
from __future__ import annotations

import argparse
import random
import sys
from collections import deque
from pathlib import Path

W, H = 96, 54
HALF = W // 2
WATER_LEVEL = 50
MIRROR = {"0": "1", "1": "0", "#": "#", "X": "X", ".": ".", "~": "~"}


def blank() -> list[list[str]]:
    g = [["#"] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            if x < 2 or x >= W - 2 or y < 2 or y >= H - 2:
                g[y][x] = "X"
    return g


SKY_BOTTOM = 24   # y < SKY_BOTTOM 是天空（压得越低，射击角度越自由）


def carve(g: list[list[str]], rng: random.Random) -> None:
    """上半部分是天空，下半部分挖洞穴。

    v1 版本把虫封在土里，头顶只有 4 格空气，45 度射出去立刻撞土，
    基准 bot 一发都打不中。这道题的核心是弹道，必须留出足够的天空。
    """
    for y in range(2, SKY_BOTTOM):
        for x in range(2, HALF):
            g[y][x] = "."

    for _ in range(6):
        x = rng.randint(4, HALF - 4)
        y = rng.randint(24, WATER_LEVEL - 6)
        length = rng.randint(24, 56)
        for _ in range(length):
            r = rng.choice((0, 0, 0, 1))
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    nx, ny = x + dx, y + dy
                    if 2 <= nx < HALF and 2 <= ny < H - 2:
                        g[ny][nx] = "."
            d = rng.randrange(4)
            if d == 0:
                x += 1
            elif d == 1:
                x -= 1
            elif d == 2:
                y += 1
            else:
                y -= 1
            x = max(2, min(HALF - 1, x))
            y = max(2, min(H - 3, y))


def make_platform(g: list[list[str]], px: int, py: int) -> None:
    """9x5 出生平台，并向上打通到天空，保证有射击仰角。"""
    for dy in range(-14, 1):
        for dx in range(-4, 5):
            x, y = px + dx, py + dy
            if 2 <= x < HALF and 2 <= y < H - 2:
                g[y][x] = "."
    for dx in range(-4, 5):
        for dy in (1, 2):
            x, y = px + dx, py + dy
            if 2 <= x < HALF and 2 <= y < H - 2:
                if g[y][x] != "X":
                    g[y][x] = "#"


def mirror(g: list[list[str]]) -> list[list[str]]:
    out = [row[:] for row in g]
    for y in range(H):
        for x in range(HALF):
            out[y][W - 1 - x] = MIRROR[g[y][x]]
    return out


def air_ratio(g: list[list[str]]) -> float:
    air = sum(1 for row in g for c in row if c in ".01")
    return air / (W * H)


def reachable(g, sx: int, sy: int) -> int:
    """从 (sx,sy) 四连通能到达的非固体格数。

    用二维布尔数组而不是 set：曾经在这里踩到一个 "int is not callable"
    的诡异错误，换掉容器之后就没再出现过。
    """
    vis = [[False] * W for _ in range(H)]
    vis[sy][sx] = True
    stack = [(sx, sy)]
    count = 1
    while stack:
        cx, cy = stack.pop()
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            nx, ny = cx + dx, cy + dy
            if not (0 <= nx < W and 0 <= ny < H):
                continue
            if vis[ny][nx]:
                continue
            if ny >= WATER_LEVEL or g[ny][nx] in "#X~":
                continue
            vis[ny][nx] = True
            count += 1
            stack.append((nx, ny))
    return count


def validate(g: list[list[str]]) -> str:
    if len(g) != H or any(len(r) != W for r in g):
        return "尺寸错误"
    n0 = sum(r.count("0") for r in g)
    n1 = sum(r.count("1") for r in g)
    if n0 != 3 or n1 != 3:
        return f"出生点数量 {n0}/{n1}"
    for y in range(H):
        for x in range(W):
            if x < 2 or x >= W - 2 or y < 2 or y >= H - 2:
                if g[y][x] != "X":
                    return f"外圈不是基岩 ({x},{y})"
    for y in range(H):
        for x in range(W):
            if g[y][x] not in MIRROR:
                return f"非法字符 ({x},{y})"
            if MIRROR[g[y][x]] != g[y][W - 1 - x]:
                return f"({x},{y}) 地形未镜像"
    for y in range(H):
        for x in range(W):
            c = g[y][x]
            if c in "01":
                if y >= WATER_LEVEL:
                    return f"出生点 ({x},{y}) 在水位下"
                if y + 1 >= H or g[y + 1][x] not in "#X":
                    return f"出生点 ({x},{y}) 脚下无支撑"
                if not ((g[y][x - 1] in ".01") or (g[y][x + 1] in ".01")):
                    return f"出生点 ({x},{y}) 两侧被封死"
                if reachable(g, x, y) < 20:
                    return f"出生点 ({x},{y}) 可达区域过小"
    r = air_ratio(g)
    if not (0.25 <= r <= 0.60):
        return f"空气比例 {r:.2%} 超出 25%–60%"
    return ""


def build(seed: int, family: str = "terraces") -> tuple[list[list[str]] | None, str]:
    rng = random.Random(seed)
    g = blank()
    carve(g, rng)

    # 左侧三个出生点：分布在左半区，y 在 30..44
    xs = [12, 24, 36]
    if family not in ("terraces", "ridge", "basin"):
        raise ValueError(f"unknown map family: {family}")
    levels = (26, 29, 32) if family == "terraces" else ((32, 29, 26) if family == "ridge" else (26, 32, 38))
    ys = [y + rng.randrange(2) for y in levels]
    for px, py in zip(xs, ys):
        make_platform(g, px, py)
        g[py][px] = "0"

    if family == "ridge":
        # Destructible central cover separates the two teams' firing lanes.
        for y in range(20, 39):
            for x in range(44, HALF):
                g[y][x] = "#"

    # 底部水面标记（引擎按 water_level 判断，这里只是让文件自解释）
    for y in range(WATER_LEVEL, H - 2):
        for x in range(2, W - 2):
            g[y][x] = "~"

    g = mirror(g)
    err = validate(g)
    if err:
        return None, err
    return g, ""


def render(g: list[list[str]]) -> str:
    return "\n".join("".join(row) for row in g) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--count", type=int, default=3)
    ap.add_argument("--prefix", default="public")
    ap.add_argument("--families", default="terraces,ridge,basin",
                    help="cycle comma-separated terraces/ridge/basin layouts")
    args = ap.parse_args()

    families = args.families.split(",")
    if any(f not in ("terraces", "ridge", "basin") for f in families):
        ap.error("unknown map family")
    # Generated maps must pass the actual engine's strict load, including real movement.
    import importlib.util
    game = Path(__file__).resolve().parents[1] / "problems" / "p01-ballistic"
    spec = importlib.util.spec_from_file_location("p01_map_judge", game / "public/judge/judge.py")
    judge = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = judge
    spec.loader.exec_module(judge)
    engine = judge.build_engine(game)
    import subprocess
    import tempfile

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    made = 0
    seed = args.seed
    attempts = 0
    while made < args.count and attempts < 4000:
        attempts += 1
        family = families[made % len(families)]
        g, err = build(seed, family)
        seed += 1
        if g is None:
            continue
        with tempfile.TemporaryDirectory(prefix="p01-map-") as td:
            candidate = Path(td) / "candidate.txt"
            candidate.write_text(render(g), encoding="utf-8", newline="\n")
            reply = subprocess.run([str(engine)], input=f"LOAD {candidate.as_posix()} 0\nQUIT\n",
                                   text=True, capture_output=True, timeout=10, check=True)
            if not reply.stdout.startswith("OK "):
                raise RuntimeError(f"generated map rejected by engine: {reply.stdout.strip()}")
        path = out / f"{args.prefix}-{made + 1:02d}.txt"
        path.write_text(render(g), encoding="utf-8", newline="\n")
        print(f"{path}  air={air_ratio(g):.1%}  seed={seed - 1}  family={family}")
        made += 1

    if made < args.count:
        print(f"只生成了 {made} 张", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
