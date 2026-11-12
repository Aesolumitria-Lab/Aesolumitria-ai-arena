#!/usr/bin/env python3
"""P01 v2.4 【分类地图生成器】—— 在 mirror4 公平性之上加三个正交的类型轴。

背景（2026-11-09 实测，见 docs/map-types.md）：
  旧的 108 张 mirror4 图两两之间只差 **1.66%** 的格子，五个月集合在一起看：
  台面到水面恒为 21、上半屏空气 sd 只有 1.2%、几何轴与胜率的相关 |r| < 0.15。
  ⇒ 公平是公平，但**类型上几乎是同一张图**，隐藏图也只是"同分布的另一批 seed"，
    测不出泛化，也没法做「分类生存」的读数。

这一版加三个**互相正交、且都保持镜像对称**的类型轴：

  openness  中央障碍    open  = 无
                        mound = 可破坏土丘（#，能挖穿）
                        wall  = 永久基岩墙（X，只能高抛绕过）
  cover     掩体        bare  = 无
                        flank = 席间矮掩体（#）
                        flank+canopy = 矮掩体 + 四席同规短檐（降低头顶净空）
  elevation 座次高程    mesa  = 台面 y=26（高台，离水远）
                        level = 台面 y=30（平地）
                        basin = 台面 y=34（洼地，离水近）

  ⇒ 27 种组合，108 张公开图 = 每种 4 张（按 i % 27 交错分布，前 27 张覆盖全部类型）。
    隐藏图同类型、不同 seed。

公平性保持（与 mirror4 完全一致）：
  1. 只设计左半边，右半边 x -> 95-x 镜像；出生点 0<->3 / 1<->2 成对镜像；
  2. 四席等高等距、边距相等；三个轴都作用在【四席共有】的属性上，
     不会让某一席单独变高/变矮/多檐；
  3. 输出前必须由正式引擎 strict LOAD（出生点有支撑、有侧出口、20 格连通区、
     真实移动/跳跃可存活、空气 25%–60%）。

用法：
    python problems/p01-ballistic/tools/genmap_p01_types.py --out build/p01-v240/public4 --seed 3000 \
        --count 108 --prefix public4 --engine problems/p01-ballistic/build/engine.exe
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
WATER_TOP = 50
BASE_X = (16, 37, 58, 79)
BASE_HALF = 5
SPAWN_OFF = (-4, 0, 4)
MID = W // 2                       # 48；镜像中心在 47.5
MIRROR = {"0": "3", "3": "0", "1": "2", "2": "1"}
REPO = Path(__file__).resolve().parents[3]
DEFAULT_ENGINE = REPO / "problems" / "p01-ballistic" / "build" / "engine.exe"

# 两个轴（v2.4.3 起 openness 已移除） --------------------------------------
#
# ★ 为什么删掉 openness（2026-11-10 已确认）：
#   实测它对专精探针 bot 的胜率影响只有 1.4pp（cover 27.0 / elevation 25.3），
#   原因很具体：中央土丘/基岩柱挡的是【地面行进】，而主流打法走高抛，
#   能挡高抛的只有头顶的檐（那是 cover 轴的活）。
#   而它占着三分之一的类型组合（27 → 9），等于用 2/3 的图集换来一个测不出效应的轴。
#   ⇒ 删掉它，27 种组合降到 9 种；公开/隐藏仍各 108 张，改成每种 12 张。
#   生成器里 compose/axis_openness 仍然保留（历史图集要能复现），只是不再参与 combos()。
AXES = {
    "cover": ("bare", "flank", "flank+canopy"),
    "elevation": ("mesa", "level", "basin"),
}
# 每一档的类型数 = 3 x 3 = 9；108 张 = 每种 12 张。
COMBOS_PER_TYPE = 12
ELEV_Y = {"mesa": 26, "level": 30, "basin": 34}


def combos() -> list[tuple[str, str, str]]:
    """(openness, cover, elevation)，但 openness 固定为 open（v2.4.3 起不再分档）。

    保留三元组形状是为了不打断 compose() 的签名与历史图集的可复现性 ——
    compose(seed, "open", cover, elevation) 与 v2.4.2 的 open|cover|elevation 完全同义。
    """
    return [("open", c, e) for c in AXES["cover"] for e in AXES["elevation"]]


# 基本块 ------------------------------------------------------------------

def blank() -> list[list[str]]:
    g = [["." for _ in range(W)] for _ in range(H)]
    for y in range(H):
        for x in range(W):
            if x < BORDER or x >= W - BORDER or y < BORDER or y >= H - BORDER:
                g[y][x] = "X"
    return g


def air_cells(g) -> int:
    return sum(1 for row in g for c in row if c == "." or "0" <= c <= "3")


def carve_sym(g, x0: int, x1: int, y0: int, y1: int, ch: str) -> list[list[int]]:
    rects = []
    for y in range(max(BORDER, y0), min(H - BORDER, y1 + 1)):
        for x in range(max(BORDER, x0), min(W - BORDER, x1 + 1)):
            g[y][x] = ch
            g[y][W - 1 - x] = ch
    rects.append([x0, y0, x1, y1])
    return rects


def ramp_heights(values: list[int]) -> list[int]:
    hs = list(values)
    for x in range(BORDER, W // 2):
        hs[W - 1 - x] = hs[x]
    return hs


def ground(g, heights: list[int]) -> None:
    for x in range(BORDER, W - BORDER):
        for y in range(heights[x], WATER_TOP):
            g[y][x] = "X" if y >= H - BORDER else "#"
    for y in range(WATER_TOP, H - BORDER):
        for x in range(BORDER, W - BORDER):
            g[y][x] = "~"


# 三个轴的具体实现 --------------------------------------------------------

def axis_openness(rng, g, heights, level: str, base_y: int) -> dict:
    if level == "open":
        return {"kind": "open"}
    if level == "mound":
        top = base_y - rng.randint(3, 6)
        half = rng.randint(4, 7)
        carve_sym(g, MID - half, MID + half - 1, BORDER, base_y - 1, ".")
        for x in range(MID - half, MID + half):
            heights[x] = top
            heights[W - 1 - x] = top
        return {"kind": "mound", "top": top, "half": half, "material": "#"}
    # wall：基岩柱，永久，只能高抛绕
    top = base_y - rng.randint(8, 13)
    half = rng.randint(2, 3)
    return {"kind": "wall", "top": top, "half": half, "material": "X"}


def axis_cover(rng, g, base_y: int, spawn_y: int, level: str) -> dict:
    info: dict = {"kind": level}
    if level in ("flank", "flank+canopy"):
        blocks = []
        for a, b in zip(BASE_X, BASE_X[1:]):
            mid = (a + b) // 2
            w = rng.randint(3, 6)
            h = rng.randint(2, 5)
            top = base_y - h
            carve_sym(g, mid - w, mid + w, top, base_y - 1, "#")
            blocks.append({"x": mid, "top": top, "w": w})
        info["flank"] = blocks
    if level == "flank+canopy":
        w = rng.randint(7, 9)
        h = rng.choice((1, 2))
        y0 = spawn_y - rng.randint(6, 8)
        for cx in BASE_X:
            carve_sym(g, cx - w // 2, cx + w // 2, y0, y0 + h - 1, "#")
        info["canopy"] = {"y0": y0, "h": h, "w": w}
    return info


def surface_texture(rng, g, heights: list[int], base_y: int) -> dict:
    """类型保持的起伏扰动（v2.4.2 新增）。

    ★ 为什么需要它（2026-11-10 评审发现）：open|bare 这两档一个随机数都不消耗，
      于是 compose(seed) 变成 seed 无关的纯函数 —— 实测 open|bare|mesa 的前 4 个 seed
      输出 sha256 完全一样，108 张公开图只有 97 个不同内容，而且 hidden4-01 与
      public4-01 逐字节相同（隐藏图泄漏 25/108）。「每种 4 张」在那几类上是同一张抄 4 份。

    这一层只改形、不改类：
      1. 只动地表高度的 ±1~2 格，且在 BASE_X ± BASE_HALF 的台面范围内锁死（出生点不动）；
      2. 只动地表以下 1~4 行的泥土边界（挖出空腔，改变爆点落土与摔落距离）；
      3. 全部按 x -> 95-x 成对写入，镜像对称不变；
      4. 不碰中央土丘/基岩墙/掩体/檐（那是类型轴的事），不碰外圈基岩与水位线。
    """
    info: dict = {}
    for x in range(BORDER, MID):
        xr = W - 1 - x
        if any(abs(x - cx) <= BASE_HALF for cx in BASE_X):
            continue
        if heights[x] != base_y:
            continue
        dy = rng.choice((-2, -1, 0, 0, 1, 2))
        h = base_y + dy
        if h < BORDER + 2 or h > WATER_TOP - 2:
            continue
        heights[x] = h
        heights[xr] = h
        if dy < 0:
            for y in range(h, base_y):
                g[y][x] = g[y][xr] = '#'
        elif dy > 0:
            for y in range(base_y, h):
                g[y][x] = g[y][xr] = '.'
        info['x%d' % x] = dy
    for _ in range(rng.randint(2, 5)):
        x = rng.randint(BORDER + 3, MID - 6)
        xr = W - 1 - x
        if any(abs(x - cx) <= BASE_HALF + 1 for cx in BASE_X):
            continue
        y0 = heights[x] + rng.randint(2, 4)
        for k in range(rng.randint(2, 4)):
            y = y0 + k
            if y >= WATER_TOP - 1:
                break
            if g[y][x] == '#':
                g[y][x] = g[y][xr] = '.'
    return {'dy': info}


def compose(seed: int, openness: str, cover: str, elevation: str):
    rng = random.Random(seed)
    base_y = ELEV_Y[elevation]
    spawn_y = base_y - 1
    g = blank()
    heights = ramp_heights([base_y] * W)

    if openness == "mound":
        # 先抬地表，再铺地，这样土丘是地表的形状
        top = base_y - rng.randint(3, 6)
        half = rng.randint(4, 7)
        for x in range(MID - half, MID + half):
            heights[x] = top
            heights[W - 1 - x] = top
        oi = {"kind": "mound", "top": top, "half": half, "material": "#"}
    else:
        oi = {"kind": openness}
        if openness == "wall":
            top = base_y - rng.randint(8, 13)
            half = rng.randint(2, 3)
            oi.update({"top": top, "half": half, "material": "X"})

    ground(g, heights)

    # 基岩墙在地表之上另立（ground 之后画，免得被 # 覆盖）
    if openness == "wall":
        carve_sym(g, MID - oi["half"], MID + oi["half"] - 1, oi["top"], base_y - 1, "X")

    ci = axis_cover(rng, g, base_y, spawn_y, cover)

    # 类型保持的起伏扰动：让 open|bare 这类「不吃 seed」的组合也真的随 seed 变化。
    # 必须在 axis_cover 之后、台面刻画之前 —— 台面是出生点，不允许被动。
    tx = surface_texture(rng, g, heights, base_y)

    # 台面：四席同一 y、同一半宽、同一出生偏移
    for cx in BASE_X:
        carve_sym(g, cx - BASE_HALF, cx + BASE_HALF, spawn_y, spawn_y, ".")
        carve_sym(g, cx - BASE_HALF, cx + BASE_HALF, spawn_y - 1, spawn_y - 1, ".")
    for seat, cx in enumerate(BASE_X):
        for off in SPAWN_OFF:
            g[spawn_y][cx + off] = str(seat)

    air = air_cells(g)
    if air * 100 < W * H * 25 or air * 100 > W * H * 60:
        return None, None, "air ratio"

    meta = {
        "type": {"openness": openness, "cover": cover, "elevation": elevation},
        # v2.4.3：type_code 只保留真正在分的两轴（cover|elevation），openness 已移除。
        "type_code": f"{cover}|{elevation}",
        # 兼容字段：老脚本按三轴解析时还能拿到 openness，值恒为 open。
        "type_code_full": f"{openness}|{cover}|{elevation}",
        "air_cells": air,
        "air_percent": round(air * 100.0 / (W * H), 2),
        "base_y": base_y,
        "base_x": list(BASE_X),
        "base_half": BASE_HALF,
        "spawn_offsets": list(SPAWN_OFF),
        "spawn_y": spawn_y,
        "mirror_pairs": [[0, 3], [1, 2]],
        "spawns": [{"seat": seat, "x": cx + off, "y": spawn_y}
                   for seat, cx in enumerate(BASE_X) for off in SPAWN_OFF],
        "features": {"openness": oi, "cover": ci},
        "texture": {"kind": "surface_texture", "columns": len(tx["dy"])},
    }
    return g, meta, ""


# 校验 --------------------------------------------------------------------

def mirror_ok(rows: list[str]) -> bool:
    for y in range(H):
        for x in range(W):
            a, b = rows[y][x], rows[y][W - 1 - x]
            if MIRROR.get(a, a) != b:
                return False
    return True


def self_check(g) -> str:
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
    if not mirror_ok(["".join(r) for r in g]):
        return "map is not mirror symmetric under 0<->3 / 1<->2"
    n = air_cells(g) * 100
    if n < W * H * 25 or n > W * H * 60:
        return "air ratio"
    return ""


def render(g) -> str:
    return "\n".join("".join(row) for row in g) + "\n"


def engine_check(engine: Path, path: Path):
    proc = subprocess.run([str(engine)],
                          input=f"LOAD {path.as_posix()} 1 15\nQUIT\n",
                          capture_output=True, text=True, encoding="utf-8",
                          timeout=30)
    lines = [ln for ln in proc.stdout.splitlines() if ln]
    return (lines[0].startswith("OK ") if lines else False), (lines[0] if lines else "")


def engine_label(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO).as_posix()
    except ValueError:
        return path.name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=3000)
    ap.add_argument("--count", type=int, default=108)
    ap.add_argument("--prefix", default="public4")
    ap.add_argument("--engine", default=str(DEFAULT_ENGINE))
    ap.add_argument("--avoid", default="",
                    help="另一份 manifest.json 的路径；其里的 sha256 也被拒绝，"
                         "保证隐藏图不会和公开图逐字节相同（v2.4.2）")
    args = ap.parse_args()

    engine = Path(args.engine)
    if not engine.is_file():
        ap.error(f"engine not found: {engine}")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)


    all_combos = combos()
    entries = []
    seen_sha: set[str] = set()
    avoided: set[str] = set()
    if args.avoid:
        try:
            other = json.loads(Path(args.avoid).read_text(encoding="utf-8"))
            avoided = {m["sha256"] for m in other.get("maps", []) if m.get("sha256")}
            print(f"avoiding {len(avoided)} map hashes from {args.avoid}", file=sys.stderr)
        except Exception as exc:
            ap.error(f"cannot read --avoid manifest: {exc}")
    seed = args.seed
    attempts = failures = 0
    while len(entries) < args.count:
        if attempts >= args.count * 60:
            print(f"stopped after {attempts} attempts", file=sys.stderr)
            return 1
        use = all_combos[len(entries) % len(all_combos)]
        used_seed = seed
        seed += 1
        attempts += 1
        for _try in range(40):
            g, meta, err = compose(used_seed, *use)
            if g is not None and not err:
                err = self_check(g)
                if not err:
                    break
            used_seed = seed
            seed += 1
            attempts += 1
            g = None
            err = err or "retry"
        if g is None:
            failures += 1
            print(f"reject seed={used_seed}: {err}", file=sys.stderr)
            continue

        text = render(g)
        # ★ 内容去重（v2.4.2）：compose 里任何一条轴如果恰好不吃 seed，两个不同 seed 会
        #   产出逐字节相同的图。旧版 108 张公开图只有 97 个不同内容，隐藏图还和公开图撞了
        #   25 张 —— 「每种 4 张」在那些类上其实是同一张抄 4 份。这里直接按内容拒绝重复，
        #   重试下一个 seed。
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if sha in seen_sha or sha in avoided:
            failures += 1
            why = "duplicate content" if sha in seen_sha else "collides with --avoid split"
            print(f"reject seed={used_seed} {use}: {why}", file=sys.stderr)
            continue
        name = f"{args.prefix}-{len(entries) + 1:02d}.txt"
        tmp = out / f".{name}.tmp"
        tmp.write_text(text, encoding="utf-8", newline="\n")
        ok, line = engine_check(engine, tmp)
        if not ok:
            tmp.unlink()
            failures += 1
            print(f"reject seed={used_seed} {use}: {line or 'no engine output'}",
                  file=sys.stderr)
            continue
        tmp.replace(out / name)
        seen_sha.add(sha)
        entries.append({
            "file": name,
            "seed": used_seed,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "mirror_symmetric": True,
            **meta,
        })
        print(f"{name} seed={used_seed} {meta['type_code']:28s} "
              f"air={meta['air_percent']:.2f}% {line}")

    manifest = {
        "generator": "tools/genmap_p01_types.py",
        "layout": "mirror4+types",
        "engine": engine_label(engine),
        "size": [W, H],
        "seats": 4,
        "worms_per_seat": 3,
        "load": "LOAD <file> 1 15",
        "prefix": args.prefix,
        "requested_seed": args.seed,
        "requested_count": args.count,
        "attempts": attempts,
        "failures": failures,
        "fairness": "mirror_symmetric 0<->3 / 1<->2; equal base_y, base gap, edge margin",
        "type_axes": AXES,
        "type_combos": len(all_combos),
        "maps_per_type": len(entries) / len(all_combos) if entries else 0,
        "content_dedup": "sha256 unique within split (v2.4.2)",
        "texture": "surface_texture: type-preserving ±1..2 row relief + underground pockets",
        "maps": entries,
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")
    print(f"wrote {len(entries)} maps to {out} "
          f"(attempts={attempts} failures={failures})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
