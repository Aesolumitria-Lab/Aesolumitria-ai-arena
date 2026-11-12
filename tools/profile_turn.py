#!/usr/bin/env python3
"""单局逐步耗时剖析：定位最慢的 turn。

    python tools/profile_turn.py --game p01 --bot scripted --vs greedy --seed 1 --top 20

裁判在每个 turn 记录 `timing`（每个 seat 的 cpu_ms / wall_ms），
`play_match` 把两局合并成 `result["timing"]`。这个工具只排序，不重跑规则。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
import bench  # noqa: E402
import harness  # noqa: E402


def run_game(j, eng, game, bots, seed, mp, rh):
    if game.name == "p07-maze-hunt":
        return j.play_match(engine_exe=eng, game_dir=game, map_path=mp,
                            map_id=mp.stem if mp else "-", rules_hash=rh,
                            bot_paths=bots, seed=seed)
    if game.name == "p08-tower-duel":
        return j.play_game(eng, rh, bots, seed=seed)
    return j.play_match(eng, mp, mp.stem if mp else "-", rh, bots, seed=seed)


def main() -> int:
    ap = argparse.ArgumentParser(description="单局逐步耗时剖析")
    ap.add_argument("--game", default="p01-ballistic", help="题号或目录名")
    ap.add_argument("--bot", required=True, help="座位 0 的 bot")
    ap.add_argument("--vs", required=True, help="座位 1 的 bot")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--map", default="")
    ap.add_argument("--anchors-bin", default="", help="封装好的 anchor 目录")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--json")
    args = ap.parse_args()

    game = harness.game_dir(args.game)
    j = bench.load_judge(game)
    eng = j.build_engine(game)
    rh = json.loads((game / "meta.json").read_text(encoding="utf-8")).get("rules_hash", "x")
    bots = {0: bench.build(game, j, args.bot, args.anchors_bin),
            1: bench.build(game, j, args.vs, args.anchors_bin)}

    maps = bench.maps_of(game)
    mp = Path(args.map).resolve() if args.map else (maps[0] if maps else None)
    r = run_game(j, eng, game, bots, args.seed, mp, rh)

    timing = r.get("timing") or []
    if not timing:
        raise SystemExit(f"{game.name} 还没有逐步 timing 记录（先给裁判加 timing 字段）")

    cpu = [x["cpu_ms"] for x in timing if x.get("cpu_ms") is not None]
    wall = [x["wall_ms"] for x in timing if x.get("wall_ms") is not None]

    def pct(vals: list[float], p: float) -> float:
        s = sorted(vals)
        return s[min(len(s) - 1, int(len(s) * p))]

    print(f"{game.name}  {args.bot} vs {args.vs}  seed {args.seed}  "
          f"{len(timing)} 个 (turn, seat) 样本")
    if cpu:
        print(f"  cpu   p50 {pct(cpu, 0.5):7.2f}  p95 {pct(cpu, 0.95):7.2f}  "
              f"max {max(cpu):7.2f} ms")
    if wall:
        print(f"  wall  p50 {pct(wall, 0.5):7.2f}  p95 {pct(wall, 0.95):7.2f}  "
              f"max {max(wall):7.2f} ms")
    print()
    print(f"{'game':>4}{'when':>10}{'seat':>6}{'cpu_ms':>10}{'wall_ms':>10}")

    rows = sorted(timing, key=lambda x: -(x.get("cpu_ms") or 0))[:args.top]
    for x in rows:
        c = "-" if x.get("cpu_ms") is None else f"{x['cpu_ms']:.2f}"
        w = "-" if x.get("wall_ms") is None else f"{x['wall_ms']:.2f}"
        when = str(x["t"]) if "t" in x else f"w{x.get('wave')}.{x.get('step')}"
        print(f"{str(x.get('game', '-')):>4}{when:>10}{x['seat']:>6}{c:>10}{w:>10}")

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "tool": "profile_turn", "game": game.name,
            "bot": args.bot, "vs": args.vs, "seed": args.seed,
            "samples": len(timing),
            "cpu_ms": {"p50": pct(cpu, 0.5), "p95": pct(cpu, 0.95),
                       "max": max(cpu)} if cpu else None,
            "wall_ms": {"p50": pct(wall, 0.5), "p95": pct(wall, 0.95),
                        "max": max(wall)} if wall else None,
            "top": rows,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
