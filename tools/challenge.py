#!/usr/bin/env python3
"""一键本地挑战：编译、对公开对手、给一个本地水平参考。

    python tools/challenge.py --game p01 --bot bots/mine.cpp --matches 20
    python tools/challenge.py --game p07 --bot bots/mine.cpp --anchors-bin dist/anchors/p07-maze-hunt

边界
----
- 只用公开地图与公开对手；**不读隐藏地图/隐藏 seed**，也不连服务器。
- `--anchors-bin` 只在选手拿得到封装二进制时才有意义（见 tools/README §14）。
- 输出的是本地练习参考，不是官方排名；不提供 percentile——题库没有选手分布可算。
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
import bench  # noqa: E402
import harness  # noqa: E402
import rating  # noqa: E402


def pct(values: list[int], p: float) -> int:
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * p))]


def main() -> int:
    ap = argparse.ArgumentParser(description="一键本地挑战（公开对手）")
    ap.add_argument("--game", default="p01-ballistic")
    ap.add_argument("--bot", required=True, help="待测 bot：.cpp 或已编译二进制")
    ap.add_argument("--vs", default="", help="逗号分隔的对手；默认 template")
    ap.add_argument("--matches", type=int, default=20, help="每个对手的局数（自动换边）")
    ap.add_argument("--seeds", default="")
    ap.add_argument("--anchors-bin", default="")
    ap.add_argument("--workers", type=int, default=1,
                    help="并行场数；默认 1。官方计时/验收请保持 1")
    ap.add_argument("--bootstrap", type=int, default=100)
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    game = harness.game_dir(args.game)
    if game.name not in bench.ADAPTERS:
        raise SystemExit(f"{game.name} 还没有 challenge 适配器")

    meta = json.loads((game / "meta.json").read_text(encoding="utf-8"))
    rules_hash = meta.get("rules_hash", "x")
    if args.vs.strip():
        opponents = [s.strip() for s in args.vs.split(",") if s.strip()]
    elif args.anchors_bin:
        opponents = list(meta.get("anchors", []))
    else:
        opponents = ["template"]

    j = bench.load_judge(game)
    t0 = time.time()
    eng = j.build_engine(game)
    me = bench.build(game, j, args.bot, args.anchors_bin)
    tries: dict[str, Path] = {}
    for name in opponents:
        try:
            tries[name] = bench.build(game, j, name, args.anchors_bin)
        except SystemExit:
            raise SystemExit(
                f"找不到对手 {name}。本地挑战默认只用 template；"
                "要用 anchor 二进制请加 --anchors-bin <dir>。"
            )
    compile_s = time.time() - t0

    seeds: list[int] = []
    for part in (args.seeds or f"1-{args.matches}").split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            seeds.extend(range(int(lo), int(hi) + 1))
        elif part:
            seeds.append(int(part))
    maps = bench.maps_of(game)
    adapter = bench.ADAPTERS[game.name]

    print(f"{game.name} {meta.get('version', '?')}  bot {args.bot}")
    print(f"编译/准备 {compile_s:.1f}s   对手 {', '.join(opponents)}   "
          f"每对手 {args.matches} 局   workers {args.workers}")

    def play_one(task: tuple[str, Path, int]) -> tuple[str, int, float, int, int]:
        name, opp, k = task
        seed = seeds[k % len(seeds)]
        mp = maps[k % len(maps)] if maps else None
        # 自动换边：单数局我坐 0，双数局对手坐 0
        if k % 2 == 0:
            winner, margin, n_steps = adapter(j, eng, game, {0: me, 1: opp},
                                              seed, mp, rules_hash)
            val = 0.5 if winner < 0 else (1.0 if winner == 0 else 0.0)
            signed = margin
        else:
            winner, margin, n_steps = adapter(j, eng, game, {0: opp, 1: me},
                                              seed, mp, rules_hash)
            val = 0.5 if winner < 0 else (0.0 if winner == 0 else 1.0)
            signed = -margin
        return name, k, val, signed, n_steps

    tasks = [(name, opp, k) for name, opp in tries.items() for k in range(args.matches)]
    if args.workers > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            results = list(pool.map(play_one, tasks))
    else:
        results = [play_one(t) for t in tasks]

    rows = []
    samples: dict[tuple[str, str], list[float]] = {}
    for name, opp in tries.items():
        picked = sorted((r for r in results if r[0] == name), key=lambda r: r[1])
        vals = [r[2] for r in picked]
        margins = [r[3] for r in picked]
        steps = [r[4] for r in picked]
        w = sum(1 for v in vals if v == 1.0)
        l = sum(1 for v in vals if v == 0.0)
        d = len(vals) - w - l
        samples[(args.bot, name)] = vals
        rows.append({"vs": name, "w": w, "l": l, "d": d,
                     "rate": round(sum(vals) / len(vals), 3),
                     "margin_mean": round(statistics.mean(margins), 1),
                     "steps_p50": pct(steps, 0.5), "steps_p95": pct(steps, 0.95)})
        print(f"vs {name:<12} {w}-{l}-{d}    {sum(vals) / len(vals) * 100:>5.1f}%   "
              f"margin {statistics.mean(margins):+.1f}   "
              f"steps p50 {pct(steps, 0.5)} p95 {pct(steps, 0.95)}")

    pairs = rating.to_pairs(samples, virtual_draw=True)
    names = [args.bot] + opponents
    strength = rating.fit(pairs, names)
    anchor = min(opponents, key=lambda n: strength[n])
    base = strength[anchor]

    def scaled(v: float) -> float:
        return 400.0 * math.log10(max(v, 1e-12) / base)

    print(f"\nBT 强度（{anchor}=0，虚拟平局）  {args.bot} {scaled(strength[args.bot]):.0f}")
    print("本地练习参考，不是官方排名；只用公开地图/seed。")

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "tool": "challenge", "game": game.name, "version": meta.get("version", "?"),
            "bot": args.bot, "matches": args.matches, "opponents": rows,
            "bt": {"anchor": anchor, "strength": round(scaled(strength[args.bot]), 1)},
            "local_only": True,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
