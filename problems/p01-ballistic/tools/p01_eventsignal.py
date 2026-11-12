#!/usr/bin/env python3
"""Does the event log carry signal that winner-only cannot see?

WHY: on 6 maps (no TYPHON) the event ordering beat winner-only by -0.0088 with a
significant CI; on 12 maps (TYPHON in the lineup) it was +0.0018, not significant.
Two lineups, two answers. That means the earlier "payoff" was not robust, and this
tool tries to find out WHY rather than picking the favourable number.

Three readings:
  1. per-seat event-log stats, and how they correlate with the actual finishing place
  2. the same correlation computed WITHIN each bot (removing bot identity), which is
     the part that winner-only structurally cannot see
  3. whether the two replay sets actually disagree, or whether it is just n
"""
from __future__ import annotations

import argparse
import collections
import json
import math
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
GAME = REPO / "problems" / "p01-ballistic"
SEATS, WPS, ROUNDS = 4, 3, 40


def seat_bots(header):
    """Per-seat bot names, correctly attributed.

    judge4.py:532 writes header['bots'] in CANONICAL order (the --bots order,
    constant across a run) and seat_perm[k] = seat of canonical bot k
    (judge4.py:473: winner_bot = perm.index(winner_seat)). So the bot AT
    seat s is bots[perm.index(s)]. Treating header['bots'] as a per-seat
    list silently relabels bots with seats for every non-identity perm
    (23/24 of a full rotation run) -- the pre-2026-11-10 version of this
    tool did exactly that (docs/rating-design.md §1).
    """
    bots = (header or {}).get("bots")
    perm = (header or {}).get("seat_perm")
    if not bots:
        return None
    if not perm or sorted(perm) != list(range(len(bots))):
        return list(bots)  # 老回放没有 seat_perm：保持旧行为
    return [bots[perm.index(s)] for s in range(len(bots))]


def read_replay(p):
    header = result = None
    turns = []
    with p.open(encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            t = rec.get("type")
            if t == "header":
                header = rec
            elif t == "result":
                result = rec
            elif t == "turn":
                turns.append(rec)
    return header, turns, result


def seat_stats(turns, seat, nseats):
    dealt = hits = 0
    first_blood = None
    last_death = None
    hp_end = alive_end = 0
    # 初值取引擎 LOAD 不变量（开局全活、dealt=0），第一条 turn 记录（第 1 步
    # 结算后）的差分照常处理。旧版 prev=None 守卫把首回合的命中/首血/承伤
    # 一起跳过——而强 bot 约 90% 的局第 1 回合就命中，hit_rounds/first_blood
    # 因此系统性偏低（2026-11-11 修，见 docs/swing-design.md §5 注记）。
    prev = [True] * WPS
    prev_dealt = 0
    for rec in turns:
        w = rec.get("worms") or []
        alive = [w[seat * WPS + i][5] for i in range(WPS)]
        d = (rec.get("dealt") or [0] * nseats)[seat]
        if d > prev_dealt:
            hits += 1
            if first_blood is None:
                first_blood = rec.get("t")
        for i in range(WPS):
            if prev[i] and not alive[i]:
                last_death = rec.get("t")
        prev = alive
        prev_dealt = d
        dealt = d
        hp_end = sum(w[seat * WPS + i][2] for i in range(WPS) if w[seat * WPS + i][5])
        alive_end = sum(alive)
    return {"dealt": dealt, "hit_rounds": hits,
            "first_blood": first_blood if first_blood is not None else ROUNDS + 1,
            "last_death": (ROUNDS + 1) if last_death is None else last_death,
            "hp_end": hp_end, "alive_end": alive_end}


def spearman(xs, ys):
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j + 2) / 2.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = ranks(xs), ranks(ys)
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    dx = math.sqrt(sum((rx[i] - mx) ** 2 for i in range(n)))
    dy = math.sqrt(sum((ry[i] - my) ** 2 for i in range(n)))
    return num / (dx * dy) if dx and dy else 0.0


def load(path: Path):
    games, names = [], None
    for p in sorted(path.glob("*.jsonl")):
        header, turns, result = read_replay(p)
        if not turns or not result:
            continue
        bots = seat_bots(header)
        if not bots or len(bots) != SEATS:
            continue
        if names is None:
            names = list(bots)
        games.append({"file": p.name, "map": header.get("map"),
                      "seat_bots": bots, "winner_seat": result.get("winner", -1),
                      "stats": {s: seat_stats(turns, s, SEATS) for s in range(SEATS)}})
    return games, names


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replays", default=str(GAME / "build" / "evruns"))
    args = ap.parse_args()

    games, names = load(Path(args.replays))
    if not games:
        print("no usable replays")
        return 1
    print("games=%d  bots=%s" % (len(games), ", ".join(names)))
    print("")

    # 名次：赢家 1 名，其余按灭绝轮降序
    rows = []
    for g in games:
        vals = {s: g["stats"][s]["last_death"] for s in range(SEATS)}
        if g["winner_seat"] >= 0:
            vals[g["winner_seat"]] = ROUNDS + 2
        r = {s: 1 + sum(1 for t in vals if vals[t] > vals[s]) for s in vals}
        for s in range(SEATS):
            rows.append({"bot": g["seat_bots"][s], "place": r[s],
                         "seat": s, **g["stats"][s]})
    print("=== 事件流指标与最终名次的相关（全体）===")
    print("%-14s %10s" % ("metric", "spearman"))
    for m in ("dealt", "hit_rounds", "first_blood", "last_death", "hp_end", "alive_end"):
        rho = spearman([r[m] for r in rows], [r["place"] for r in rows])
        print("%-14s %10.3f" % (m, rho))
    print("")
    print("  first_blood 越小 = 越早出手；对名次应是负相关（越早越好，名次数字越大=越差）")

    print("")
    print("=== 同一个 bot 内部的相关（去掉 bot 强度差异，这是 winner-only 看不到的部分）===")
    bybot = collections.defaultdict(list)
    for r in rows:
        bybot[r["bot"]].append(r)
    print("%-14s %-14s %6s %8s" % ("metric", "bot", "n", "within_rho"))
    for m in ("dealt", "last_death", "hp_end", "hit_rounds"):
        vals = []
        for b in sorted(bybot):
            sub = bybot[b]
            if len(sub) < 8:
                continue
            v = spearman([x[m] for x in sub], [x["place"] for x in sub])
            vals.append(v)
        if vals:
            print("%-14s %-14s %6s %8.3f" % (m, "(mean)", "", sum(vals) / len(vals)))
        for b in sorted(bybot):
            sub = bybot[b]
            if len(sub) < 8:
                continue
            v = spearman([x[m] for x in sub], [x["place"] for x in sub])
            print("%-14s %-14s %6d %8.3f" % (m, b, len(sub), v))

    print("")
    print("=== 各 bot 的事件流画像（每局平均）===")
    print("%-12s %8s %8s %10s %10s %8s" %
          ("bot", "dealt", "hit炉", "首血轮", "灭绝轮", "残血"))
    for b in sorted(bybot):
        sub = bybot[b]
        n = len(sub)
        print("%-12s %8.1f %8.2f %10.1f %10.1f %8.1f"
              % (b, sum(x["dealt"] for x in sub) / n,
                 sum(x["hit_rounds"] for x in sub) / n,
                 sum(x["first_blood"] for x in sub) / n,
                 sum(x["last_death"] for x in sub) / n,
                 sum(x["hp_end"] for x in sub) / n))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
