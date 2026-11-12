#!/usr/bin/env python3
"""Benchmark validity probe: does the seat bias corrupt the ranking?

WHY (2026-11-10, owner): "an unfair game without compensation may make the
benchmark bad." That is testable. The benchmark's job is to rank bots. If the
ranking is already absorbed by the seat-rotation schedule, the bias does not
corrupt the benchmark; if it is not, it does.

Reads measured here, all from ONE batch of games:

  1. seat_wins   -- raw per-seat win counts with 4 DIFFERENT bots. Shows how
                    much a seat is worth before the schedule cancels it.
  2. win% + Wilson95 per bot, pooled over 24 perms x 2 seeds.
  3. restricting to ONE seat gives the "if the schedule did not rotate" view;
     comparing it to the pooled view shows whether pooling actually changed
     the answer.

If pooled ordering and single-seat ordering disagree, bias leaks into the
benchmark. If they agree, the schedule already paid for it.
"""
from __future__ import annotations

import collections
import importlib.util
import json
import math
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
GAME = REPO / "problems" / "p01-ballistic"
PUB = GAME / "public"


def load(p, n):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


def wilson(w, n, z=1.96):
    if n <= 0:
        return (0.0, 1.0)
    p = w / float(n)
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def main() -> int:
    j2 = PUB / "judge" / "judge2.py"
    j = load(j2 if j2.is_file() else PUB / "judge" / "judge.py", "p01_j")
    j4 = load(PUB / "judge" / "judge4.py", "p01_j4")
    eng = j.build_engine(GAME)
    B = PUB / "baselines" / "bin"
    maps = sorted((PUB / "maps4").glob("*.txt"))[:6]
    names = ["tide_hard", "tide_normal", "tide_easy", "random4"]
    bots = [B / (n + ".exe") for n in names]
    perms = [p for p, _si, _sa in j4.schedule((0, 1, 2, 3), 24, 2)]
    jobs = 8
    wa = j4.WALL_PER_JOB_S * (jobs - 1)

    tasks = [(m, p, i) for m in maps for i, p in enumerate(perms)]

    def run(t):
        mp, perm, i = t
        r = j4.play_one(j, eng, mp, 1 + i, bots, (0, 1, 2, 3), perm, 0.5,
                        replay_grid=False, wall_allow_s=wa, jobs=jobs)
        return (mp.stem, tuple(perm), r.get("ok", False), r.get("winner_bot", -1))

    with ThreadPoolExecutor(max_workers=jobs) as ex:
        rows = list(ex.map(run, tasks))
    okn = sum(1 for r in rows if r[2])
    print("games=%d ok=%d maps=%d perms=%d" % (len(rows), okn, len(maps), len(perms)))

    # 座位本身的价码：4 个不同 bot，座位胜场直接可见
    byseat = collections.Counter()
    for stem, perm, ok, wb in rows:
        if ok and wb >= 0:
            # ⚠ 语义：judge4 的 winner_bot 已经是【bot 索引】，winner（seat）才是座位。
            # perm 的方向是 perm[bot] = seat（judge4.py:568 `for i, seat in enumerate(perm)`）。
            byseat[perm[wb]] += 1
    tot = sum(byseat.values())
    share = [round(100.0 * byseat.get(i, 0) / max(1, tot), 1) for i in range(4)]
    print("")
    print("=== raw seat wins (4 different bots, before rotation cancels it) ===")
    print("seat wins   : " + str([byseat.get(i, 0) for i in range(4)]) + "  total " + str(tot))
    print("seat share %: " + str(share))

    # 累计：全池 vs 只数某个座位的局
    def scores(keep):
        # w = wins, n = games PLAYED.
        # 分母必须是参赛局数，不是赢的局数 —— 上一版只在 bot 赢的那局才 n+=1，
        # 结果所有 bot 都显示 100%。这个坑写在这里，免得下一个人再踩。
        w = collections.Counter()
        n = collections.Counter()
        for stem, perm, ok, wb in rows:
            if not ok:
                continue
            # perm[bot] = seat（judge4.py `for i, seat in enumerate(perm)`）。
            # 所以「bot b 坐哪个座」= perm[b]，和它赢没赢是两件独立的事。
            for b in range(4):
                seat_b = perm[b]
                if not keep(seat_b):
                    continue
                n[b] += 1               # 该 bot 在该座上的参赛局数
            if wb >= 0 and keep(perm[wb]):
                w[wb] += 1              # winner_bot 已经是 bot 索引
        return w, n

    def report(label, w, n):
        out = {}
        items = sorted(range(4), key=lambda b: -(w[b] / float(n[b]) if n[b] else 0.0))
        print("")
        print("=== " + label + " ===")
        for b in items:
            lo, hi = wilson(w[b], n[b])
            print("%-14s %3d/%3d = %5.1f%%   Wilson95 [%5.1f, %5.1f]"
                  % (names[b], w[b], n[b], 100.0 * w[b] / max(1, n[b]), 100 * lo, 100 * hi))
            out[names[b]] = [w[b], n[b]]
        return [names[b] for b in items], out

    w_all, n_all = scores(lambda seat: True)
    ord_all, dat_all = report("pooled: 24 perms x 2 seed (the real schedule)", w_all, n_all)

    # 「如果不轮换」的诚实版本：每个 bot 固定坐某一个席，看它在这个席上的胜率。
    # 分母 = 该 bot 在该座的参赛局数（每人每座恰好 1/4 的局），不是「该座赢的局」。
    # 上一版拿「座位 0 赢的那些局」当分母 —— 那是选择偏差，会人为压低所有人。
    seat_reports = {}
    for s in range(4):
        w_s, n_s = scores(lambda seat, s=s: seat == s)
        _o, dat_s = report("fixed at seat %d (as if nobody rotated)" % s, w_s, n_s)
        seat_reports["seat%d" % s] = dat_s
    ord_s0 = [names[b] for b in sorted(range(4),
                                       key=lambda b: -(seat_reports["seat0"][names[b]][0] /
                                                       float(max(1, seat_reports["seat0"][names[b]][1]))))]
    dat_s0 = {k: v for k, v in seat_reports.items()}

    # 排序是否一致（拿 pooled 跟每一个「固定座」的排序比）
    print("")
    print("=== verdict: pooled ordering vs each fixed-seat ordering ===")
    print("pooled            : " + " > ".join(ord_all))
    same_any = True
    for s in range(4):
        dat = seat_reports["seat%d" % s]
        o = [names[b] for b in sorted(range(4),
                                     key=lambda b: -(dat[names[b]][0] / float(max(1, dat[names[b]][1]))))]
        print("fixed at seat %-2d  : %s   %s" % (s, " > ".join(o),
                                              "same" if o == ord_all else "DIFFERS"))
        if o != ord_all:
            same_any = False
    print("")
    print("all four fixed-seat orderings agree with pooled: " + str(same_any))
    print("""
⇒ 怎么读这张表：
  · 「raw seat wins」= 不同 bot 在不同座上各拿到多少胜 —— 它才是座位本身值多少。
  · 「pooled」= 正式赛制的算法（每人每座 6 次 × 2 seed），这是排名真正用的。
  · 「fixed at seat k」= 假想「不轮换、那个人一辈子坐这个座」时的胜率。
  三者排序一致 ⇒ 轮换已经把座位差吸掉了，benchmark 排序不受影响。
  若某座的分母很小，那个胜率的 Wilson 区间会很宽 —— 别把区间重叠读成「变强了」。
""")

    data = {
        "games": len(rows), "ok": okn, "maps": len(maps),
        "seat_wins": dict((str(k), v) for k, v in byseat.items()),
        "seat_share_pct": share,
        "pooled": dat_all, "fixed_seat": seat_reports,
        "pooled_order": ord_all, "order_agrees_across_seats": same_any,
    }
    path = REPO / "problems" / "p01-ballistic" / "docs" / "data" / "bench-validity.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print("wrote " + str(path.relative_to(REPO)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
