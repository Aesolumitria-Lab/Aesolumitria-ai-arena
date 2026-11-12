#!/usr/bin/env python3
"""P01 公平性审计：复现 docs/fairness-audit.md 里的每一个读数。

    python problems/p01-ballistic/tools/p01_fairness_audit.py            # 12 张公开图

三块读数：
  [A] 4 个相同 bot 的座位份额（新旧赛制对比）—— 看座次偏袒有没有被消掉
  [B] 不同强度 bot 的胜率分离 —— 看地图有没有把差异洗掉
  [C] 曾经 24/24 全 FORFEIT 的那几张图 —— 看争格判罚的修复
"""
from __future__ import annotations

import collections
import importlib.util
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
GAME = REPO / "problems" / "p01-ballistic"
PUB = GAME / "public"


def load(p: Path, n: str):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


def _load_judge2(pub: Path, name: str):
    """2 席判官：包里叫 judge2.py，仓库里叫 judge.py —— 两种排布都要能跑。

    硬写 judge.py 的话，在打包布局（包里只有 judge2.py）里这里直接找不到文件。
    """
    for cand in (pub / "judge" / "judge2.py", pub / "judge" / "judge.py"):
        if cand.is_file():
            return load(cand, name)
    raise SystemExit(f"judge2.py / judge.py 都不在 {pub / 'judge'}")


def main() -> int:
    j = _load_judge2(PUB, "p01_j")
    j4 = load(PUB / "judge" / "judge4.py", "p01_j4")
    eng = j.build_engine(GAME)
    B, C = PUB / "baselines" / "bin", PUB / "challenges" / "bin"
    maps = sorted((PUB / "maps4").glob("*.txt"))
    print(f"maps: {len(maps)}")

    def seat_share(bots, plan, label, n_maps=12, jobs=16):
        ms = maps[:n_maps]
        # ★ schedule() 的第三项是【全局自增的绝对 seed 下标】，第二项只是「第几批」。
        #   用第二项会让一张图的 48 局退化成 2 个 seed —— 那正是 RULES 11.2.2 要消灭的
        #   「同一套风程序换座位名字」。审计工具和 judge4 必须同一口径，否则读数不可比。
        tasks = [(m, p, 1 + seed_abs) for m in ms for (p, _si, seed_abs) in plan]
        wa = j4.WALL_PER_JOB_S * (jobs - 1)

        def run(t):
            return j4.play_one(j, eng, t[0], t[2], bots, (0, 1, 2, 3), t[1], 0.5,
                               replay_grid=False, wall_allow_s=wa)
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            rs = list(ex.map(run, tasks))
        w = collections.Counter(r.get("winner", -1) for r in rs)
        n = len(rs)
        share = [round(w.get(s, 0) / n * 100, 1) for s in range(4)]
        print(f"  {label:34s} games={n:5d} seat={share} draws={w.get(-1, 0)} "
              f"faults={sum(r.get('faults', 0) for r in rs)}")
        return rs

    print("\n[A] 12 张公开图，4 个完全相同的 tide_normal")
    seat_share([B / "tide_normal.exe"] * 4, j4.schedule((0, 1, 2, 3), 24, 4),
               "24 排列 x 4 seed（v2.2）")
    seat_share([B / "tide_normal.exe"] * 4,
               [(p, 0, 0) for p in j4.rotations((0, 1, 2, 3))],
               "24 排列 x 1 seed（旧格式）")

    print("\n[B] 不同强度 bot 的胜率分离")
    rs = seat_share([B / "tide_hard.exe", B / "tide_normal.exe", B / "tide_easy.exe",
                     B / "random4.exe"], j4.schedule((0, 1, 2, 3), 24, 4),
                    "24 排列 x 4 seed（v2.2）")
    wb = collections.Counter(r.get("winner_bot", -1) for r in rs)
    print("   winners by bot:",
          {["hard", "normal", "easy", "random"][k] if k >= 0 else "draw": v
           for k, v in sorted(wb.items())})

    print("\n[C] 以前 24/24 全 FORFEIT 的地图（争格判罚修复）")
    for mp in ("public4-12", "public4-24", "public4-32", "public4-35"):
        p = PUB / "maps4" / f"{mp}.txt"
        if not p.is_file():
            print(f"  {mp}: 不在当前图集（v2.2 已整批重生）")
            continue

        def run(perm, p=p):
            return j4.play_one(j, eng, p, 1, [B / "tide_normal.exe"] * 4, (0, 1, 2, 3),
                               perm, 0.5, replay_grid=False)
        with ThreadPoolExecutor(max_workers=12) as ex:
            rr = list(ex.map(run, j4.rotations((0, 1, 2, 3))))
        print(f"  {mp}: forfeits="
              f"{sum(1 for r in rr if r.get('reason') == 'FORFEIT')}/24 "
              f"faults={sum(r.get('faults', 0) for r in rr)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
