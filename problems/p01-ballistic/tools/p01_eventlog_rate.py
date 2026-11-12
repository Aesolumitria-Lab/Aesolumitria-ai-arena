#!/usr/bin/env python3
"""The decisive comparison for RATING: winner-only vs place-order vs event-order.

WHY (2026-11-10, owner): "event-log analysis -- maybe a surprising payoff, e.g. rating."

problems/p01-ballistic/tools/p01_eventlog_leak.py already showed the event ordering beats the snapshot ordering
(log-loss -0.0405, 95% CI [-0.0715, -0.0183], and the gain survives removing winner
injection: "ext" without the winner still wins, so it is not leakage).

But the PRODUCTION rating does not use the snapshot ordering -- <repo>/docs/baseline-and-scoring.md and
tools/ffa_rating.py use three columns: pairwise BT, winner-only 4-way softmax, and
avg place. So the honest question is:

    does a full-order Plackett-Luce (event log) beat a winner-ONLY model?

This fits three models on the same games and scores them the same way:
  W   -- winner only            (the information ffa_rating's win% column uses)
  S   -- snapshot full order    (what ffa4.places gives)
  E   -- event-log full order   (who died last, no winner injection)
Metric: held-out map log-loss AND top-1 accuracy.

★ BUG FIXED 2026-11-10 (docs/rating-design.md §1): header['bots'] is CANONICAL order and
  seat_perm[k] = seat of canonical bot k (judge4.py:473/532), so the bot AT
  seat s is bots[perm.index(s)]. Earlier versions used bots[s] directly, which
  relabeled bots with seats for 23/24 of games; the W/S/E numbers produced
  that way measured SEAT bias, not bot ranking.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import random
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
GAME = REPO / "problems" / "p01-ballistic"
SEATS, WPS, ROUNDS = 4, 3, 40


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


def seat_bots(header):
    """Per-seat bot names, correctly attributed (see module docstring)."""
    bots = (header or {}).get("bots")
    perm = (header or {}).get("seat_perm")
    if not bots:
        return None
    if not perm or sorted(perm) != list(range(len(bots))):
        return list(bots)  # 老回放没有 seat_perm：保持旧行为
    return [bots[perm.index(s)] for s in range(len(bots))]


def extinction(turns, seat):
    last, prev = None, None
    for rec in turns:
        w = rec.get("worms") or []
        alive = [w[seat * WPS + i][5] for i in range(WPS)]
        if prev:
            for i in range(WPS):
                if prev[i] and not alive[i]:
                    last = rec.get("t", 0)
        prev = alive
    return (ROUNDS + 1) if (last is None or sum(prev or [0]) > 0) else last


def snap(turns, seat):
    rec = turns[-1]
    w = rec.get("worms") or []
    hp = sum(w[seat * WPS + i][2] for i in range(WPS) if w[seat * WPS + i][5])
    al = sum(1 for i in range(WPS) if w[seat * WPS + i][5])
    return (hp, (rec.get("dealt") or [0] * 4)[seat], al)


def rank_desc(v):
    return {s: 1 + sum(1 for t in v if v[t] > v[s]) for s in v}


def fit_pl(orders, n, iters=3000, lr=0.5):
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
        st = lr / max(1, len(orders))
        for i in range(n):
            g[i] += st * grad[i]
        m = sum(g) / n
        for i in range(n):
            g[i] -= m
    if max(g) - min(g) > 15.0:
        print("  [warn] PL fit diverging (gamma spread %.1f): near-deterministic "
              "ordering, unbounded MLE; held-out log-loss is NOT an information "
              "verdict. See docs/rating-design.md §2.6." % (max(g) - min(g)))
    return g


def fit_winner(rows, n, iters=3000, lr=0.5):
    """Winner-only softmax (Plackett-Luce truncated to the first segment)."""
    g = [0.0] * n
    for _ in range(iters):
        grad = [0.0] * n
        mx = max(g)
        for lineup, win in rows:
            d = sum(math.exp(g[x] - mx) for x in lineup)
            for x in lineup:
                grad[x] -= math.exp(g[x] - mx) / d if d else 0.0
            grad[win] += 1.0
        st = lr / max(1, len(rows))
        for i in range(n):
            g[i] += st * grad[i]
        m = sum(g) / n
        for i in range(n):
            g[i] -= m
    return g


def probs(g, lineup):
    mx = max(g[x] for x in lineup)
    d = sum(math.exp(g[x] - mx) for x in lineup)
    return {x: math.exp(g[x] - mx) / d for x in lineup}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replays", default=str(GAME / "build" / "evruns"))
    ap.add_argument("--boot", type=int, default=2000)
    args = ap.parse_args()

    games, names = [], None
    for p in sorted(Path(args.replays).glob("*.jsonl")):
        header, turns, result = read_replay(p)
        if not turns or not result:
            continue
        bots = seat_bots(header)
        if not bots or len(bots) != SEATS:
            continue
        if names is None:
            names = list(bots)
        games.append({"file": p.name, "map": header.get("map"), "seat_bots": bots,
                      "winner_seat": result.get("winner", -1),
                      "ext": {s: extinction(turns, s) for s in range(SEATS)},
                      "snap": {s: snap(turns, s) for s in range(SEATS)}})
    if not games:
        print("no usable replays")
        return 1
    maps = sorted(set(g["map"] for g in games))
    bi = {n: i for i, n in enumerate(names)}

    def order(g, mode):
        vals = g["snap"] if mode == "S" else g["ext"]
        r = rank_desc(vals)
        return sorted(range(SEATS), key=lambda s: r[s])

    print("games=%d maps=%d bots=%s" % (len(games), len(maps), ", ".join(names)))
    print("")
    print("%-4s %8s %9s %10s %7s" % ("mode", "n_test", "logloss", "vs winner", "top1"))
    res = {}
    for mode in ("W", "S", "E"):
        per = {}
        top1 = n = 0
        for held in maps:
            tr = [g for g in games if g["map"] != held]
            te = [g for g in games if g["map"] == held]
            if mode == "W":
                rows = []
                for g in tr:
                    if g["winner_seat"] < 0:
                        continue
                    stb = [bi[b] for b in g["seat_bots"]]
                    rows.append((stb, stb[g["winner_seat"]]))
                gam = fit_winner(rows, SEATS)
            else:
                gam = fit_pl([[bi[b] for b in g["seat_bots"]][s] if False else None
                              for s in []], SEATS) if False else \
                      fit_pl([[[bi[b] for b in g["seat_bots"]][s] for s in order(g, mode)]
                              for g in tr], SEATS)
            for g in te:
                if g["winner_seat"] < 0:
                    continue
                stb = [bi[b] for b in g["seat_bots"]]
                pr = probs(gam, stb)
                w = stb[g["winner_seat"]]
                per[g["file"]] = -math.log(max(1e-9, pr[w]))
                top1 += 1 if max(stb, key=lambda b: pr[b]) == w else 0
                n += 1
        res[mode] = per
        ll = sum(per.values()) / max(1, len(per))
        print("%-4s %8d %9.4f %10s %7.3f"
              % (mode, n, ll, "" if mode == "W" else "%+.4f" % (ll - sum(res["W"].values()) / max(1, len(res["W"]))), top1 / max(1, n)))
    print("")
    print("W = winner only（现在的 win% 口径）  S = 快照全序（ffa4.places）")
    print("E = 事件流全序（真实淘汰顺序，不注入胜者）")
    print("")

    # 配对 bootstrap
    rng = random.Random(20261110)
    bymap = collections.defaultdict(list)
    for g in games:
        if g["winner_seat"] >= 0:
            bymap[g["map"]].append(g["file"])
    print("=== paired delta vs winner-only, block bootstrap by map ===")
    for mode in ("S", "E"):
        stats = []
        for _ in range(args.boot):
            pick = [rng.choice(maps) for _ in maps]
            tot = k = 0
            for m in pick:
                for f in bymap[m]:
                    if f in res[mode] and f in res["W"]:
                        tot += res[mode][f] - res["W"][f]
                        k += 1
            if k:
                stats.append(tot / k)
        stats.sort()
        lo = stats[int(0.025 * len(stats))]
        hi = stats[int(0.975 * len(stats))]
        print("  %-4s mean=%+.4f  95%%CI=[%+.4f, %+.4f]  %s"
              % (mode, sum(stats) / len(stats), lo, hi,
                 "SIGNIFICANT" if (lo < 0 and hi < 0) or (lo > 0 and hi > 0) else "n.s."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
