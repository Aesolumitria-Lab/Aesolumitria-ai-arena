#!/usr/bin/env python3
"""Anchor 档位审计：逐题检查 random → template → greedy → scripted(→strong) 是否单调。

    python tools/anchor_audit.py                    # 全部题，每档 20 场
    python tools/anchor_audit.py --game p07 --matches 12
    python tools/anchor_audit.py --json build/anchor_audit.json

**只看胜负，不看 margin。**
`bench` 的 margin 是各题自己的分数差：P06 一场两局换座会把分差正负抵消成 0，
P07 的分差模型和胜负模型还不是一回事。
拿 margin 判档位会得出"档位相等"的错误结论。

判定：上档对下档的胜率 ≥ `--min-rate`（默认 0.6）且平局不超过 `--max-draw`（默认 0.25）。
档位为 0 胜说明倒挂，直接判 FAIL。重复的有效开局会跳过，不能靠重跑同一确定性对局扩充样本。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bench  # noqa: E402
import harness  # noqa: E402

# 每题的档位阶梯，从弱到强。template 是选手包里的 starter。
LADDERS = {
    "p01-ballistic": ["random", "template", "greedy", "scripted"],
    "p06-core-tactics": ["random", "template", "greedy", "scripted", "strong"],
    "p07-maze-hunt": ["random", "template", "greedy", "scripted"],
    "p08-tower-duel": ["random", "template", "greedy", "scripted"],
}


def effective_opening_key(game_name: str, mp, seed: int):
    """返回由引擎实际采用的开局状态组成的键，而不是仅比较 seed。"""
    if game_name == "p07-maze-hunt" and mp is not None:
        raw = [line.rstrip("\r\n") for line in mp.read_text(encoding="utf-8").splitlines() if line]
        spawn = []
        grid = []
        for y, row in enumerate(raw):
            chars = list(row)
            for x, ch in enumerate(chars):
                if ch == "P" or ch in "1234":
                    spawn.append((ch, x, y))
                    chars[x] = "_"
            grid.append(chars)
        pellets = sum(ch in ".o" for row in grid for ch in row)
        cut = ((seed % 21) + 21) % 21
        cut = min(cut, max(0, pellets - 150))
        cells = [(x, y) for y, row in enumerate(grid) for x, ch in enumerate(row) if ch == "."]
        state = seed & ((1 << 64) - 1)
        for _ in range(min(cut, len(cells))):
            state ^= (state << 13) & ((1 << 64) - 1)
            state ^= state >> 7
            state ^= (state << 17) & ((1 << 64) - 1)
            index = state % len(cells)
            x, y = cells[index]
            cells[index] = cells[-1]
            cells.pop()
            grid[y][x] = "_"
        return (tuple(spawn), tuple("".join(row) for row in grid))
    if game_name == "p06-core-tactics":
        return (mp.stem if mp else "-", seed % 2)
    if game_name == "p08-tower-duel":
        return (mp.stem if mp else "-", seed % 3)
    return (mp.stem if mp else "-", seed)


def opening_cases(game_name: str, maps: list, matches: int) -> list[tuple[int, object, int]]:
    """枚举请求范围内的唯一有效开局；确定性重复对局不增加证据。"""
    cases = []
    seen = set()
    for i in range(matches):
        seed = i + 1
        mp = maps[i % len(maps)] if maps else None
        variant = (seed % 2 if game_name == "p06-core-tactics"
                   else seed % 3 if game_name == "p08-tower-duel" else seed)
        key = effective_opening_key(game_name, mp, seed)
        if key in seen:
            continue
        seen.add(key)
        cases.append((seed, mp, variant))
    return cases


def run_pair(j, eng, game, lower, upper, matches, rh):
    bots = {0: bench.build(game, j, upper), 1: bench.build(game, j, lower)}
    maps = bench.maps_of(game)
    adapter = bench.ADAPTERS[game.name]
    cases = opening_cases(game.name, maps, matches)
    wins = draws = 0
    steps = []
    by_map = {}
    for seed, mp, _variant in cases:
        map_name = mp.stem if mp else "-"
        counts = by_map.setdefault(map_name, {"wins": 0, "losses": 0, "draws": 0})
        winner, _margin, n = adapter(j, eng, game, bots, seed, mp, rh)
        if winner < 0:
            draws += 1
        elif winner == 0:
            wins += 1
        counts["draws" if winner < 0 else "wins" if winner == 0 else "losses"] += 1
        steps.append(n)
    total = len(cases)
    return {
        "lower": lower, "upper": upper,
        "requested_matches": matches, "matches": total,
        "duplicate_openings_skipped": matches - total,
        "wins": wins, "losses": total - wins - draws, "draws": draws,
        "rate": wins / total,
        "steps_median": statistics.median(steps),
        "distinct_opening_parameters": total, "by_map": by_map,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="", help="题号或目录名；默认四题全跑")
    ap.add_argument("--matches", type=int, default=20, help="每档对局数")
    ap.add_argument("--min-rate", type=float, default=0.6)
    ap.add_argument("--max-draw", type=float, default=0.25)
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    if args.matches < 1:
        ap.error("--matches must be positive")

    games = [args.game] if args.game else list(LADDERS)
    rows = []
    failed = 0
    t0 = time.time()

    for gname in games:
        game = harness.game_dir(gname)
        if game.name not in LADDERS:
            print(f"{game.name}: 没有档位定义，跳过")
            continue
        j = bench.load_judge(game)
        rh = json.loads((game / "meta.json").read_text(encoding="utf-8")).get("rules_hash", "x")
        eng = j.build_engine(game)
        ladder = LADDERS[game.name]

        print(f"\n{game.name}  (请求 {args.matches} 场/档，重复开局跳过)")
        for lower, upper in zip(ladder, ladder[1:]):
            r = run_pair(j, eng, game, lower, upper, args.matches, rh)
            ok = r["rate"] >= args.min_rate and r["draws"] / r["matches"] <= args.max_draw
            tag = "PASS" if ok else "FAIL"
            if not ok:
                failed += 1
            print(f"  {upper:<9} vs {lower:<9} "
                  f"{r['wins']:>2}-{r['losses']:>2}-{r['draws']:>2}  "
                  f"胜率 {r['rate'] * 100:5.1f}%  p50 {r['steps_median']:.0f}  "
                  f"有效开局 {r['matches']}/请求{args.matches}  去重 {r['duplicate_openings_skipped']}  [{tag}]")
            rows.append({"game": game.name, "ok": ok, **r})

    elapsed = time.time() - t0
    print(f"\n{'通过' if not failed else f'{failed} 档未达阈值'}，"
          f"共 {len(rows)} 个相邻档位，{elapsed:.0f}s")

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "tool": "anchor_audit", "matches": args.matches,
            "min_rate": args.min_rate, "failed": failed, "rows": rows,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
