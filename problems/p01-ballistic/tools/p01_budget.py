#!/usr/bin/env python3
"""量每个基准 bot 的「每回合 CPU 预算余量」。

这是题库能不能跨机器用的关键读数：RULES 给的预算是 50 ms/回合（CPU 时间），
但 bot 自己并不知道裁判跑在哪台机器上。同一份 TIDE 源码，在 32 核桌面上
每回合可能只用 20 ms，在 2 vCPU 云主机上就可能摸到 60 ms —— 那就不是
"选手太慢"，而是【基准自己越界】，排名会被宿主速度污染。

输出每档的 max / p95 / p99 / med 与"越界回合数"。

    python3 p01_budget.py --game <包根> --bots "TIDE-HARD=/path/hard,..." --map <图> --rounds 40
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path


def load(p: Path, n: str):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


def pct(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * q))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", required=True)
    ap.add_argument("--bots", required=True, help="label=path,...")
    ap.add_argument("--maps", required=True)
    ap.add_argument("--maps-count", type=int, default=2)
    ap.add_argument("--perms", type=int, default=4)
    ap.add_argument("--jobs", type=int, default=1)
    args = ap.parse_args()

    game = Path(args.game).resolve()
    j4 = load(game / "public" / "judge" / "judge4.py", "p01_j4")
    j = j4._judge()
    engine = j.build_engine(game)
    specs = [s.split("=", 1) for s in args.bots.split(",")]
    maps = sorted(Path(args.maps).glob("*.txt"))[:args.maps_count]

    for label, path in specs:
        bot = Path(path).resolve()
        samples: list[float] = []
        misses = 0
        # 4 个自己打自己：CPU 压力最真实（对手也是同档）
        orig_ask = j4.Bot.ask

        def ask(self, text, timeout, _s=samples):
            base = self.cpu.sample()
            out = orig_ask(self, text, timeout)
            now = self.cpu.sample()
            if base is not None and now is not None:
                _s.append((now - base) * 1000.0)
            return out

        j4.Bot.ask = ask
        try:
            for mp in maps[:1]:
                for perm in j4.rotations((0, 1, 2, 3))[:args.perms]:
                    r = j4.play_one(j, engine, mp, 1, [bot] * 4, (0, 1, 2, 3),
                                    perm, 0.5, replay_grid=False)
                    misses += r.get("faults", 0)
        finally:
            j4.Bot.ask = orig_ask

        over = sum(1 for x in samples if x >= j4.TURN_CPU_MS)
        print(f"{label:14s} turns={len(samples):5d} "
              f"max={max(samples) if samples else 0:6.1f} "
              f"p99={pct(samples, 0.99):6.1f} p95={pct(samples, 0.95):6.1f} "
              f"med={pct(samples, 0.5):6.1f}  "
              f"over50ms={over:3d} ({over / max(1, len(samples)) * 100:.1f}%)  "
              f"faults={misses}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
