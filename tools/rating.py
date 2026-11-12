#!/usr/bin/env python3
"""anchor 实力值：Bradley–Terry（胜负口径）+ 虚拟平局。

    python tools/rating.py --game p07 --matches 20
    python tools/rating.py --game p02 --anchors random,greedy,scripted --matches 12 --bootstrap 200

和 `tools/calibrate.py` 的分工
---------------------------
`calibrate.py` 用**分差**拟合（E[margin] = s_i − s_j），适合胜负饱和的题（P07 全是 100%）。
`rating.py` 用**胜负**拟合，和原项目 `meow-ai-arena/arena/bt.py` 同一口径：

- 每对交过手的 bot 额外加**一局虚拟平局**（`games += 1, wins += 0.5`）；
- 这个平滑是原项目解决"全胜导致估计发散"的办法，比改模型形式更轻；
- 刻度 `400 × log10(s / s_anchor)`：强 10 倍记 +400，旗鼓相当记 0。

虚拟平局的效果
------------
两个 bot 打 20 局全胜，原始 BT 的胜率是 1.0，logit 无穷大，拟合会发散。
加一局虚拟平局后变成 20.5/21 = 0.976，logit ≈ 3.7，强度有限、可比较。
代价是**绝对的强弱差距被压缩**——这是有意的：20 局全胜只能说明"更强"，
说明不了"强多少"。

口径限制
--------
实力值依赖**对手池**。换一批 anchor，同一份 bot 的分数会变。
所以这张表只能自己和自己比，不能跨题、跨池比绝对值。
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
import bench  # noqa: E402
import harness  # noqa: E402


def fit(pairs: dict[tuple[str, str], tuple[float, float]], names: list[str],
        iters: int = 5000) -> dict[str, float]:
    """MM 迭代求 BT 极大似然。pairs[(a,b)] = (局数, a 的胜场)。返回强度（几何均值 = 1）。"""
    wins = dict.fromkeys(names, 0.0)
    for (a, b), (g, w) in pairs.items():
        wins[a] += w
        wins[b] += g - w

    s = dict.fromkeys(names, 1.0)
    for _ in range(iters):
        denom = dict.fromkeys(names, 0.0)
        for (a, b), (g, _w) in pairs.items():
            v = g / (s[a] + s[b])
            denom[a] += v
            denom[b] += v
        new = {}
        for n in names:
            new[n] = wins[n] / denom[n] if denom[n] > 0 else 1e-9
        # 归一到几何均值 1，防止整体漂移
        logs = [math.log(max(v, 1e-300)) for v in new.values()]
        center = sum(logs) / len(logs)
        s = {n: math.exp(math.log(max(new[n], 1e-300)) - center) for n in names}
    return s


def collect(j, eng, game: Path, bots: dict[str, Path], names: list[str],
            matches: int, seeds: list[int], maps: list[Path], rules_hash: str):
    """跑 round-robin，返回 {(a,b): [每局 a 的得分贡献 0/0.5/1]}。"""
    adapter = bench.ADAPTERS[game.name]
    out: dict[tuple[str, str], list[float]] = {}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            out[(a, b)] = []
            for k in range(matches):
                mp = maps[k % len(maps)] if maps else None
                seed = seeds[k % len(seeds)]
                winner, _margin, _steps = adapter(j, eng, game,
                                                  {0: bots[a], 1: bots[b]}, seed, mp, rules_hash)
                out[(a, b)].append(0.5 if winner < 0 else (1.0 if winner == 0 else 0.0))
            w = sum(out[(a, b)])
            print(f"  {a:10s} vs {b:10s}  {w:4.1f}/{matches}", flush=True)
    return out


def to_pairs(samples: dict[tuple[str, str], list[float]], virtual_draw: bool) -> dict:
    pairs = {}
    for key, vals in samples.items():
        g = float(len(vals))
        w = float(sum(vals))
        if virtual_draw:
            g += 1.0
            w += 0.5
        pairs[key] = (g, w)
    return pairs


def main() -> int:
    ap = argparse.ArgumentParser(description="anchor 实力值（BT + 虚拟平局）")
    ap.add_argument("--game", default="p01-ballistic")
    ap.add_argument("--anchors", default="", help="逗号分隔；默认读 meta.json 的 anchors")
    ap.add_argument("--matches", type=int, default=20, help="每对局数")
    ap.add_argument("--seeds", default="")
    ap.add_argument("--bootstrap", type=int, default=200)
    ap.add_argument("--anchor", default="", help="锚定到 0 的 bot；默认取最弱")
    ap.add_argument("--anchors-bin", default="", help="封装好的 anchor 目录")
    ap.add_argument("--no-smoothing", action="store_true", help="关掉虚拟平局（用来对比发散）")
    ap.add_argument("--json")
    args = ap.parse_args()

    game = harness.game_dir(args.game)
    if game.name not in bench.ADAPTERS:
        raise SystemExit(f"{game.name} 还没有 rating 适配器")

    meta = json.loads((game / "meta.json").read_text(encoding="utf-8"))
    names = [s.strip() for s in args.anchors.split(",") if s.strip()] or meta.get("anchors", [])
    if len(names) < 2:
        raise SystemExit("至少需要两个 anchor")
    rules_hash = meta.get("rules_hash", "x")

    j = bench.load_judge(game)
    eng = j.build_engine(game)
    bots = {n: bench.build(game, j, n, args.anchors_bin) for n in names}

    seeds: list[int] = []
    for chunk in (args.seeds or f"1-{max(1, args.matches)}").split(","):
        if "-" in chunk:
            lo, hi = chunk.split("-", 1)
            seeds.extend(range(int(lo), int(hi) + 1))
        elif chunk.strip():
            seeds.append(int(chunk))
    maps = bench.maps_of(game)

    print(f"game {game.name}  每对 {args.matches} 场")
    samples = collect(j, eng, game, bots, names, args.matches, seeds, maps, rules_hash)

    smooth = not args.no_smoothing
    pairs = to_pairs(samples, smooth)
    strength = fit(pairs, names)

    # bootstrap：按对重采样每局结果，重估强度
    rng = random.Random(20261102)
    boots: dict[str, list[float]] = {n: [] for n in names}
    for _ in range(args.bootstrap):
        resampled = {}
        for key, vals in samples.items():
            pick = [rng.choice(vals) for _ in vals]
            g, w = float(len(pick)), float(sum(pick))
            if smooth:
                g += 1.0
                w += 0.5
            resampled[key] = (g, w)
        s = fit(resampled, names, iters=800)
        for n in names:
            boots[n].append(s[n])

    anchor = args.anchor or min(names, key=lambda n: strength[n])
    base = strength[anchor]

    def scaled(v: float) -> float:
        return 400.0 * math.log10(max(v, 1e-12) / base)

    def ci(n: str) -> tuple[float, float]:
        vals = sorted(scaled(v) for v in boots[n])
        return (vals[int(0.025 * (len(vals) - 1))], vals[int(0.975 * (len(vals) - 1))])

    order = sorted(names, key=lambda n: -strength[n])
    print()
    print(f"{'rank':<5}{'bot':<12}{'rating':>9}{'ci95':>20}{'W-L':>12}")
    for r, n in enumerate(order, 1):
        lo, hi = ci(n)
        w = sum(sum(samples[k]) for k in samples if k[0] == n)
        w += sum(len(v) - sum(v) for k, v in samples.items() if k[1] == n)
        tot = sum(len(v) for k, v in samples.items() if n in k)
        print(f"{r:<5}{n:<12}{scaled(strength[n]):>9.1f}{f'[{lo:.0f}, {hi:.0f}]':>20}"
              f"{f'{w:.0f}-{tot - w:.0f}':>12}")

    spread = scaled(max(strength.values())) - scaled(min(strength.values()))
    print()
    print(f"刻度 400·log10，{anchor} 锚定为 0；虚拟平局{'开' if smooth else '关'}")
    print(f"spread {spread:.0f}")
    if not smooth:
        print("  注意：关掉虚拟平局时，全胜对会让估计发散——这正是它存在的理由。")

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "tool": "rating", "game": game.name, "anchor": anchor,
            "smoothed": smooth, "matches_per_pair": args.matches,
            "ratings": {n: round(scaled(strength[n]), 1) for n in names},
            "ci95": {n: [round(x, 1) for x in ci(n)] for n in names},
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
