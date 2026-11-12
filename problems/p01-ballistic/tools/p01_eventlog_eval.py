#!/usr/bin/env python3
"""Event-log vs scoreboard: does the richer signal help PREDICTION? (strict test)

WHY (2026-11-10, owner): "event-log analysis -- maybe a surprising payoff, e.g. rating."

The first comparison (problems/p01-ballistic/tools/p01_eventlog.py) already showed the two orderings disagree
in 57.6% of games and give different Plackett-Luce seat orderings. But "different" is
not "better". This runs the only honest test: predictive, on held-out maps.

Model A (snapshot): Plackett-Luce on the final (hp, dealt, alive) ordering -- what
                    ffa4.places() produces today.
Model B (event):    Plackett-Luce on the TRUE extinction ordering -- who died last.

Both are fitted on 5 maps and scored on the 6th, rotating. If B is genuinely richer it
must win on log-loss. If it only adds noise it will lose.

★ BUG FIXED THIS ROUND (1): the replay header stores the bot list under "bots", not
  "seat_bots" (judge4.py:538). The earlier version read "seat_bots" and silently
  discarded every replay.
★ BUG FIXED THIS ROUND (2, 2026-11-10, docs/rating-design.md §1): header['bots'] is CANONICAL
  order, not per-seat order; seat_perm[k] = seat of canonical bot k. The bot AT
  seat s is bots[perm.index(s)]. Earlier versions used bots[s] directly, which
  relabeled bots with seats for 23/24 of games -- the log-loss numbers produced
  that way measured SEAT bias, not bot ranking.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
GAME = REPO / "problems" / "p01-ballistic"
SEATS = 4
WPS = 3
ROUNDS = 40


def read_replay(path: Path):
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
    """Per-seat bot names, correctly attributed (see module docstring).

    judge4.py:473 writes winner_bot = perm.index(winner_seat), so seat s is
    occupied by canonical bot perm.index(s).
    """
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
    dealt = (rec.get("dealt") or [0, 0, 0, 0])[seat]
    return (hp, dealt, alive)


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
        print("  [warn] PL fit diverging (gamma spread %.1f): the ordering is "
              "near-deterministic (some bot always outranks another), the MLE is "
              "unbounded and held-out log-loss numbers are NOT an information "
              "verdict. See docs/rating-design.md §2.6." % (max(g) - min(g)))
    return g


def softmax_probs(gamma, lineup):
    mx = max(gamma[x] for x in lineup)
    d = sum(math.exp(gamma[x] - mx) for x in lineup)
    return {x: math.exp(gamma[x] - mx) / d for x in lineup}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replays", default=str(GAME / "build" / "evruns"))
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    files = sorted(Path(args.replays).glob("*.jsonl"))
    games = []
    names = None
    for p in files:
        header, turns, result = read_replay(p)
        if not turns or not result:
            continue
        bots = seat_bots(header)
        if not bots or len(bots) != SEATS:
            continue
        if names is None:
            names = list(bots)
        games.append({
            "file": p.name,
            "map": header.get("map") if header else p.name.split("-")[0],
            "seat_bots": bots,
            "winner_seat": result.get("winner", -1),
            "ext": {s: extinction_round(turns, s) for s in range(SEATS)},
            "snap": {s: snapshot_key(turns, s) for s in range(SEATS)},
        })
    if not games:
        print("no usable replays under " + args.replays)
        return 1

    map_ids = sorted(set(g["map"] for g in games))
    bot_idx = {n: i for i, n in enumerate(names)}
    print("games=%d  maps=%d  bots=%s" % (len(games), len(map_ids), ", ".join(names)))
    print("")

    def seat_order(g, mode):
        vals = (dict(g["ext"]) if mode == "event" else dict(g["snap"]))
        if mode == "event" and g["winner_seat"] >= 0:
            vals[g["winner_seat"]] = ROUNDS + 2
        r = rank_desc(vals)
        return sorted(range(SEATS), key=lambda s: r[s])

    def build(sub, mode):
        out = []
        for g in sub:
            stb = [bot_idx[b] for b in g["seat_bots"]]
            out.append([stb[s] for s in seat_order(g, mode)])
        return out

    print("=== leave-one-map-out: fit on 5 maps, predict the 6th ===")
    print("%-9s %-12s %6s %9s %7s" % ("mode", "heldout", "n", "logloss", "top1"))
    agg = collections.defaultdict(lambda: [0.0, 0, 0])
    for mode in ("snapshot", "event"):
        for held in map_ids:
            train = [g for g in games if g["map"] != held]
            test = [g for g in games if g["map"] == held]
            if not train or not test:
                continue
            gamma = plackett_luce(build(train, mode), SEATS)
            ll = 0.0
            top1 = 0
            n = 0
            for g in test:
                if g["winner_seat"] < 0:
                    continue
                stb = [bot_idx[b] for b in g["seat_bots"]]
                probs = softmax_probs(gamma, stb)
                w = stb[g["winner_seat"]]
                ll -= math.log(max(1e-9, probs[w]))
                best = max(stb, key=lambda b: probs[b])
                top1 += 1 if best == w else 0
                n += 1
            if n:
                print("%-9s %-12s %6d %9.4f %7.3f"
                      % (mode, held, n, ll / n, top1 / n))
                agg[mode][0] += ll
                agg[mode][1] += n
                agg[mode][2] += top1

    print("")
    print("=== summary (lower logloss / higher top1 = more signal) ===")
    for mode in ("snapshot", "event"):
        ll, n, t1 = agg[mode]
        if n:
            print("  %-9s logloss=%.4f  top1=%.3f  (n=%d)"
                  % (mode, ll / n, t1 / n, n))
    print("")
    print("snapshot = 现在的口径（ffa4.places 的 (hp, dealt, alive) 快照）")
    print("event    = 事件流重建的真实淘汰序（谁最后死）")
    if args.json:
        Path(args.json).write_text(json.dumps(
            {m: {"logloss": round(agg[m][0] / max(1, agg[m][1]), 5),
                 "top1": round(agg[m][2] / max(1, agg[m][1]), 4),
                 "n": agg[m][1]} for m in ("snapshot", "event")}, indent=2),
            encoding="utf-8")
        print("wrote " + args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
