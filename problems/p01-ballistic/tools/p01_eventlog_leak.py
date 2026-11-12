#!/usr/bin/env python3
"""Is the event-log advantage real, or is it leaking the winner?

WHY: problems/p01-ballistic/tools/p01_eventlog_eval.py reported event logloss 1.3130 vs snapshot 1.3562.
That looks like a 3.2% gain. But the event ordering was built with

    vals[winner_seat] = ROUNDS + 2      # force the winner to rank 1

which is the answer being fed into the feature. snapshot (ffa4.places) never does
that. So the comparison is not apples to apples and the gain may be pure leakage.

THIS RUNS THREE ORDERINGS, all without touching the winner:
  snap    -- final (hp, dealt, alive)                    [today's ffa4.places]
  ext     -- extinction round, NO winner injection       [pure event-log signal]
  ext_w   -- extinction round WITH winner injection      [the leaky version]
and does a PAIRED per-game test (same game, same train/test split) plus a block
bootstrap by map, so the number has an interval instead of a point.

★ BUG FIXED 2026-11-10 (docs/rating-design.md §1): header['bots'] is CANONICAL order and
  seat_perm[k] = seat of canonical bot k (judge4.py:473/532), so the bot AT
  seat s is bots[perm.index(s)]. Earlier versions used bots[s] directly, which
  relabeled bots with seats for 23/24 of games; the snap/ext/ext_w numbers
  produced that way measured SEAT bias, not bot ranking.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
GAME = REPO / "problems" / "p01-ballistic"
SEATS = 4
WPS = 3
ROUNDS = 40


def read_replay(path):
    header = result = None
    turns = []
    with path.open(encoding="utf-8") as fh:
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


def seat_bots(header):
    """Per-seat bot names, correctly attributed (see module docstring)."""
    bots = (header or {}).get("bots")
    perm = (header or {}).get("seat_perm")
    if not bots:
        return None
    if not perm or sorted(perm) != list(range(len(bots))):
        return list(bots)  # 老回放没有 seat_perm：保持旧行为
    return [bots[perm.index(s)] for s in range(len(bots))]


def extinction_round(turns, seat):
    last = None
    prev = None
    for rec in turns:
        w = rec.get("worms") or []
        alive = [w[seat * WPS + i][5] for i in range(WPS)]
        if prev is not None:
            for i in range(WPS):
                if prev[i] and not alive[i]:
                    last = rec.get("t", 0)
        prev = alive
    if last is None or sum(prev or [0]) > 0:
        return ROUNDS + 1
    return last


def snapshot_key(turns, seat):
    if not turns:
        return (0, 0, 0)
    rec = turns[-1]
    w = rec.get("worms") or []
    hp = sum(w[seat * WPS + i][2] for i in range(WPS) if w[seat * WPS + i][5])
    alive = sum(1 for i in range(WPS) if w[seat * WPS + i][5])
    return (hp, (rec.get("dealt") or [0] * 4)[seat], alive)


def rank_desc(vals):
    return {s: 1 + sum(1 for t in vals if vals[t] > vals[s]) for s in vals}


def plackett_luce(orders, n, iters=3000, lr=0.5):
    g = [0.0] * n
    for _ in range(iters):
        grad = [0.0] * n
        mx = max(g)
        cache = {}
        for order in orders:
            for pos, w in enumerate(order):
                rest = tuple(order[pos:])
                if rest not in cache:
                    cache[rest] = sum(math.exp(g[x] - mx) for x in rest)
                d = cache[rest]
                grad[w] += 1.0 - (math.exp(g[w] - mx) / d if d else 0.0)
        step = lr / max(1, len(orders))
        for i in range(n):
            g[i] += step * grad[i]
        m = sum(g) / n
        for i in range(n):
            g[i] -= m
    if max(g) - min(g) > 15.0:
        print("  [warn] PL fit diverging (gamma spread %.1f): near-deterministic "
              "ordering, unbounded MLE; held-out log-loss is NOT an information "
              "verdict. See docs/rating-design.md §2.6." % (max(g) - min(g)))
    return g


def softmax_probs(gamma, lineup):
    mx = max(gamma[x] for x in lineup)
    d = sum(math.exp(gamma[x] - mx) for x in lineup)
    return {x: math.exp(gamma[x] - mx) / d for x in lineup}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replays", default=str(GAME / "build" / "evruns"))
    ap.add_argument("--boot", type=int, default=2000)
    args = ap.parse_args()

    files = sorted(Path(args.replays).glob("*.jsonl"))
    games, names = [], None
    for p in files:
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
                      "ext": {s: extinction_round(turns, s) for s in range(SEATS)},
                      "snap": {s: snapshot_key(turns, s) for s in range(SEATS)}})
    if not games:
        print("no usable replays")
        return 1
    map_ids = sorted(set(g["map"] for g in games))
    bot_idx = {n: i for i, n in enumerate(names)}

    def order_of(g, mode):
        if mode == "snap":
            vals = dict(g["snap"])
        elif mode == "ext":
            vals = dict(g["ext"])          # 不注入胜者
        else:                              # ext_w
            vals = dict(g["ext"])
            if g["winner_seat"] >= 0:
                vals[g["winner_seat"]] = ROUNDS + 2
        return sorted(range(SEATS), key=lambda s: rank_desc(vals)[s])

    def build(sub, mode):
        return [[bot_idx[b] for b in g["seat_bots"]][s] for s in order_of(g, mode)]
        # 注意：上一行写法有歧义，改成显式

    def build2(sub, mode):
        out = []
        for g in sub:
            stb = [bot_idx[b] for b in g["seat_bots"]]
            out.append([stb[s] for s in order_of(g, mode)])
        return out

    def per_game_ll(mode, held):
        train = [g for g in games if g["map"] != held]
        test = [g for g in games if g["map"] == held]
        gamma = plackett_luce(build2(train, mode), SEATS)
        out = {}
        for g in test:
            if g["winner_seat"] < 0:
                continue
            stb = [bot_idx[b] for b in g["seat_bots"]]
            probs = softmax_probs(gamma, stb)
            w = stb[g["winner_seat"]]
            out[g["file"]] = -math.log(max(1e-9, probs[w]))
        return out

    modes = ("snap", "ext", "ext_w")
    lls = {}
    for mode in modes:
        agg = {}
        for held in map_ids:
            agg.update(per_game_ll(mode, held))
        lls[mode] = agg

    print("games=%d maps=%d bots=%s" % (len(games), len(map_ids), ", ".join(names)))
    print("")
    print("%-8s %10s %10s %10s" % ("mode", "logloss", "vs snap", "top1_wrap"))
    base = sum(lls["snap"].values()) / len(lls["snap"])
    for mode in modes:
        v = list(lls[mode].values())
        key = sorted(v)
        # top1 用 logloss 的倒数关系近似不了，只报 logloss；top1 在下面单独算
        print("%-8s %10.4f %10.4f" % (mode, sum(v) / len(v),
                                      (sum(v) / len(v)) - base))
    print("")

    # 配对差 + 按地图分块的 bootstrap
    def diff(mode):
        return [lls[mode][k] - lls["snap"][k] for k in lls["snap"]]

    print("=== paired per-game delta vs snap (negative = better) ===")
    for mode in ("ext", "ext_w"):
        d = diff(mode)
        print("  %-6s mean=%+.4f  median=%+.4f  wins=%d/%d"
              % (mode, sum(d) / len(d), sorted(d)[len(d) // 2],
                 sum(1 for x in d if x < 0), len(d)))

    print("")
    print("=== block bootstrap by map (resample maps, %d rounds) ===" % args.boot)
    by_map = collections.defaultdict(list)
    for g in games:
        if g["winner_seat"] < 0:
            continue
        by_map[g["map"]].append(g["file"])
    rng = random.Random(20261110)
    for mode in ("ext", "ext_w"):
        stats = []
        for _ in range(args.boot):
            pick = [rng.choice(map_ids) for _ in map_ids]
            tot = n = 0
            for m in pick:
                for f in by_map[m]:
                    if f in lls[mode] and f in lls["snap"]:
                        tot += lls[mode][f] - lls["snap"][f]
                        n += 1
            if n:
                stats.append(tot / n)
        stats.sort()
        lo = stats[int(0.025 * len(stats))]
        hi = stats[int(0.975 * len(stats))]
        print("  %-6s mean delta 95%% CI = [%+.4f, %+.4f]  %s"
              % (mode, lo, hi, "SIGNIFICANT" if (lo < 0 and hi < 0) or (lo > 0 and hi > 0) else "not significant"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
