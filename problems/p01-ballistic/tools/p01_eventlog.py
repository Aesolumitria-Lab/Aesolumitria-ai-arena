#!/usr/bin/env python3
"""Rating from the EVENT LOG instead of the scoreboard: reconstruct true elimination order.

WHY (2026-11-10, owner): "focus on the evaluation system. Event-log analysis -- maybe
that has a surprising payoff, e.g. rating computation, someone tried it before."

Today's 4-seat rating has a real weakness (docs/fairness-audit.md §3.10.1): ffa4.places() ranks seats by
a SNAPSHOT key (final hp, dealt, alive), not by what actually happened. In an
elimination game every non-winner ends at 0 HP, so the snapshot separates them by a
residual number, not by when they died. That is a proxy, and it is labelled as one.

The replay JSONL already stores, per turn, every worm's [x, y, hp, grenades, drills,
alive]. So the TRUE survival time of each seat is recoverable exactly:
  extinction_round(seat) = the round in which that seat's last living worm died
  (or ROUNDS+1 if it survived to the end).

This tool:
  1. rebuilds extinction order per seat from the event log
  2. compares it with ffa4.places() snapshot order (how often do they disagree?)
  3. fits Plackett-Luce on both orderings and reports whether the RANKING changes

★ BUG FIXED 2026-11-10 (docs/rating-design.md §1): header['bots'] is CANONICAL order and
  seat_perm[k] = seat of canonical bot k (judge4.py:473/532), so the bot AT
  seat s is bots[perm.index(s)]. The PL fit now attributes seats to bots via
  seat_perm when the field has distinct bots (the old seat-space fit was only
  meaningful for same-bot multiseat runs; on mixed-bot replays it ranked
  seats, not bots). The §3.1 agree-rate above is order-space and unaffected.

Reads replays only -- no engine change, no judge change.
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
GAME = REPO / "problems" / "p01-ballistic"
PUB = GAME / "public"
SEATS = 4
WORMS_PER_SEAT = 3
ROUNDS = 40


def load(p, n):
    s = importlib.util.spec_from_file_location(n, str(p))
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


def seat_bots(header):
    """Per-seat bot names, correctly attributed (see module docstring)."""
    bots = (header or {}).get("bots")
    perm = (header or {}).get("seat_perm")
    if not bots:
        return None
    if not perm or sorted(perm) != list(range(len(bots))):
        return list(bots)  # 老回放没有 seat_perm：保持旧行为（按座位）
    return [bots[perm.index(s)] for s in range(len(bots))]


def read_replay(path: Path):
    """Return (header, turns, result) from one replay JSONL."""
    header = None
    result = None
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


def extinction_round(turns, seat):
    """Round in which this seat's LAST worm died; ROUNDS+1 if it survived."""
    last_death = None
    prev_alive = None
    for rec in turns:
        worms = rec.get("worms") or []
        alive = [worms[seat * WORMS_PER_SEAT + i][5] for i in range(WORMS_PER_SEAT)]
        if prev_alive is not None:
            for i in range(WORMS_PER_SEAT):
                if prev_alive[i] and not alive[i]:
                    last_death = rec.get("t", 0)
        prev_alive = alive
    if last_death is None or sum(alive) > 0:
        return ROUNDS + 1
    return last_death


def snapshot_key(turns, seat):
    """ffa4.places() key: (hp, dealt, alive) from the final turn."""
    if not turns:
        return (0, 0, 0)
    rec = turns[-1]
    worms = rec.get("worms") or []
    hp = sum(worms[seat * WORMS_PER_SEAT + i][2]
             for i in range(WORMS_PER_SEAT)
             if worms[seat * WORMS_PER_SEAT + i][5])
    alive = sum(1 for i in range(WORMS_PER_SEAT)
                if worms[seat * WORMS_PER_SEAT + i][5])
    dealt = (rec.get("dealt") or [0, 0, 0, 0])[seat]
    return (hp, dealt, alive)


def rank_from_desc(values):
    """1 = best (largest value), competition ranking."""
    return {s: 1 + sum(1 for t in values if values[t] > values[s]) for s in values}


def plackett_luce(orders, n, iters=4000, lr=0.35):
    """Fit strengths by MM on Plackett-Luce over full orderings (rank 1 best).

    orders: list of list[int] -- each is a complete ordering of all n competitors,
    best first. Returns log-strength vector (normalised to mean 0).
    """
    g = [0.0] * n
    for _ in range(iters):
        grad = [0.0] * n
        for order in orders:
            for pos, w in enumerate(order):
                rest = order[pos:]
                s = sum(math.exp(g[x] - max(g)) for x in rest)
                grad[w] += 1.0 - (math.exp(g[w] - max(g)) / s if s else 0.0)
        step = lr / max(1, len(orders))
        for i in range(n):
            g[i] += step * grad[i]
        m = sum(g) / n
        for i in range(n):
            g[i] -= m
    if max(g) - min(g) > 15.0:
        print("  [warn] PL fit diverging (gamma spread %.1f): near-deterministic "
              "ordering, unbounded MLE; gamma magnitudes are not meaningful. "
              "See docs/rating-design.md §2.6." % (max(g) - min(g)))
    return g


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replays", default=str(GAME / "build" / "evruns"),
                    help="directory of *.jsonl replays")
    ap.add_argument("--bots", default="", help="comma list if header lacks seat_bots")
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    root = Path(args.replays)
    files = sorted(root.glob("*.jsonl"))
    if not files:
        print("no replays under " + str(root))
        return 1

    games = []
    names = None
    for p in files:
        header, turns, result = read_replay(p)
        if not turns or not result:
            continue
        sb = seat_bots(header)
        if not sb and result:
            sb = result.get("seat_bots")
        if not sb and args.bots:
            sb = args.bots.split(",")
        if not sb or len(sb) != SEATS:
            continue
        if names is None:
            # canonical 名单优先取 header['bots']（归属的源），没有才用按座位名单
            names = list((header or {}).get("bots") or sb)
        winner_seat = result.get("winner", -1)
        ext = {s: extinction_round(turns, s) for s in range(SEATS)}
        snap = {s: snapshot_key(turns, s) for s in range(SEATS)}
        games.append({"file": p.name, "winner": winner_seat, "ext": ext,
                      "snap": snap, "reason": result.get("reason"),
                      "seat_bots": sb})

    if not games:
        print("no usable replays (need header seat_bots or --bots)")
        return 1

    print("games: %d  bots: %s" % (len(games), ", ".join(names)))
    print("")

    # 1) 淘汰序 vs 快照序 的一致率
    agree = 0
    diff = []
    for g in games:
        # 淘汰序：winner 第一，其余按灭绝轮次降序（活得久的排前）
        key_ext = {s: (g["ext"][s],) for s in range(SEATS)}
        if g["winner"] >= 0:
            key_ext[g["winner"]] = (ROUNDS + 2,)
        r_ext = rank_from_desc({s: (key_ext[s][0],) for s in range(SEATS)})
        r_snap = rank_from_desc(g["snap"])
        if r_ext == r_snap:
            agree += 1
        else:
            diff.append((g["file"], r_ext, r_snap))

    print("=== elimination order (event log) vs snapshot order (ffa4.places) ===")
    print("  identical for %d / %d games (%.1f%%)"
          % (agree, len(games), 100.0 * agree / len(games)))
    if diff:
        print("  first %d disagreements:" % min(3, len(diff)))
        for f, a, b in diff[:3]:
            print("    %-28s event=%s snapshot=%s" % (f, a, b))

    # 2) 两种序做 Plackett-Luce，看排序是否变化
    #    座位经 seat_perm 归属到 bot（docs/rating-design.md §1）：有区分度的阵容按 bot 拟合；
    #    同 bot 自战（multiseat）保留座位空间 = 纯座位效应，与旧行为一致。
    distinct = len(set(names)) == len(names) and len(set(names)) > 1
    bi = {b: i for i, b in enumerate(names)} if distinct else None

    def build_orders(use_event):
        out = []
        for g in games:
            if use_event:
                vals = {s: g["ext"][s] for s in range(SEATS)}
                if g["winner"] >= 0:
                    vals[g["winner"]] = ROUNDS + 2
            else:
                vals = {s: g["snap"][s] for s in range(SEATS)}
            r = rank_from_desc(vals)
            seats = sorted(range(SEATS), key=lambda s: r[s])
            out.append([bi[g["seat_bots"][s]] for s in seats] if distinct else seats)
        return out

    n_fit = len(names) if distinct else SEATS
    g_ev = plackett_luce(build_orders(True), n_fit)
    g_sn = plackett_luce(build_orders(False), n_fit)
    order_ev = sorted(range(n_fit), key=lambda i: -g_ev[i])
    order_sn = sorted(range(n_fit), key=lambda i: -g_sn[i])
    print("")
    if distinct:
        print("=== Plackett-Luce on each ordering (by BOT, seats attributed via seat_perm) ===")
        print("%-14s %10s %10s" % ("bot", "from event", "from snapshot"))
        for i in range(n_fit):
            print("%-14s %10.4f %10.4f" % (names[i], g_ev[i], g_sn[i]))
    else:
        print("=== Plackett-Luce on each ordering (by SEAT: same bot everywhere => pure seat effect) ===")
        print("%-6s %10s %10s" % ("seat", "from event", "from snapshot"))
        for s in range(SEATS):
            print("%-6d %10.4f %10.4f" % (s, g_ev[s], g_sn[s]))
    print("")
    label = (lambda i: names[i]) if distinct else (lambda s: "%d" % s)
    print("  order by event-log : %s" % [label(i) for i in order_ev])
    print("  order by snapshot  : %s" % [label(i) for i in order_sn])
    print("  same ordering      : %s" % (order_ev == order_sn))

    if args.json:
        Path(args.json).write_text(json.dumps({
            "games": len(games), "names": names,
            "agree_pct": round(100.0 * agree / len(games), 2),
            "gamma_event": [round(x, 5) for x in g_ev],
            "gamma_snapshot": [round(x, 5) for x in g_sn],
            "order_event": order_ev, "order_snapshot": order_sn,
        }, indent=2), encoding="utf-8")
        print("wrote " + args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
