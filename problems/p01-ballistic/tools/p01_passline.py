#!/usr/bin/env python3
"""P01 及格线面板：3 × TIDE-NORMAL + 1 个被测 bot。

★ 为什么需要它（2026-11-10 评审）：meta.json 的 target 就是 tide_normal，
  但 p01-audit 里的四个面板是 mixed / hard3 / strong3 / weak3，**从来没有
  「3×TIDE-NORMAL + 候选」这一组**。拿 hard3 的读数去反推「能不能稳定击败
  NORMAL」，在逻辑上不成立 —— 那两个是不同的对手分布。

用法：
    python problems/p01-ballistic/tools/p01_passline.py --bots name1=<path>,name2=<path> --maps 4 --jobs 8

输出：每个候选的 4-way win%（= 该 bot 在 48×maps 局里拿下的比例），
带 Wilson 95% 区间；并给一句「是否达到及格线」的判定。

判定口径（写死，别随手改）：
    · 及格 = win% 的 Wilson 95% **下限 > 25%**（4 席均分是 25%）
    · 拉伸 = 上限 > 25% 且下限 ≤ 25%（方向对，样本还不够）
    · 未达 = 上限 ≤ 25%
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
GAME = REPO / "problems" / "p01-ballistic"


def wilson(w: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 0.0)
    p = w / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def verdict(lo: float, hi: float, seat_expect: float = 0.25) -> str:
    if lo > seat_expect:
        return "PASS  及格（下限 > 25%）"
    if hi > seat_expect:
        return "STRETCH  方向对但样本不足（上限 > 25% ≥ 下限）"
    return "FAIL  未达（上限 ≤ 25%）"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bots", required=True,
                    help="候选，形如 name=path,name=path")
    ap.add_argument("--baseline", default=str(GAME / "public" / "baselines" / "bin" / "tide_normal.exe"))
    ap.add_argument("--maps", type=int, default=4, help="取前 N 张公开图")
    ap.add_argument("--seeds-per-perm", type=int, default=2)
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    sys.path.insert(0, str(GAME / "public" / "judge"))
    import judge4 as j4  # noqa: E402

    maps = sorted((GAME / "public" / "maps4").glob("*.txt"))[:args.maps]
    if not maps:
        ap.error("没有公开图")
    cands = [x.split("=", 1) for x in args.bots.split(",") if x.strip()]
    report: dict = {"tool": "p01_passline", "baseline": args.baseline,
                    "maps": [m.stem for m in maps],
                    "seeds_per_perm": args.seeds_per_perm, "bots": {}}

    j = j4._judge()
    engine = j.build_engine(GAME)
    perms = j4.schedule((0, 1, 2, 3), 24, args.seeds_per_perm)
    for name, path in cands:
        n = games = 0
        for mp in maps:
            bots = [Path(args.baseline)] * 3 + [Path(path)]
            for perm, _si, seed_abs in perms:
                r = j4.play_one(j, engine, mp, 1 + seed_abs, bots, (0, 1, 2, 3), perm,
                                0.5, wall_allow_s=j4.WALL_PER_JOB_S * max(0, args.jobs - 1))
                games += 1
                if not r.get("ok"):
                    continue
                if r.get("winner_bot") == 3:
                    n += 1
        lo, hi = wilson(n, games)
        report["bots"][name] = {"wins": n, "games": games,
                                "win_pct": round(100.0 * n / games, 2) if games else 0.0,
                                "wilson95": [round(100 * lo, 2), round(100 * hi, 2)],
                                "verdict": verdict(lo, hi)}
        print(f"{name:12s} {n}/{games} = {100.0*n/games:.1f}% "
              f"Wilson95 [{100*lo:.1f}%, {100*hi:.1f}%]  {verdict(lo, hi)}")

    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                  encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
