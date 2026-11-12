#!/usr/bin/env python3
"""Re-analyse the square layout with the RIGHT symmetry grouping + relabel control.

WHY: the first square run (docs/data/layout-sq.json) reported a "outer%" column, but that grouping is
meaningless for a square -- all four corners are equivalent by construction. The
corners split into TOP (seats 0,1) and BOTTOM (seats 2,3), so top-vs-bottom is the
real axis. This tool re-measures with:
  * top/bottom grouping instead of inner/outer
  * spread (max-min seat share) as the fairness metric
  * a relabel control: rotate the seat numbers on the same square. If the bias
    follows the numbers it will rotate too; if it follows the corners it will not.
  * drowning count, since the square puts two seats 21 rows higher than the other two
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
GAME = REPO / "problems" / "p01-ballistic"
PUB = GAME / "public"
BUILD = GAME / "build"
H, W = 54, 96
PX0, PX1 = 2, 93
DF = 31


def load(p, n):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


def water_at(t):
    w = 50
    for r in range(30, t + 1):
        if r % 2 == 0 and w > 44:
            w -= 1
    return w


def blank(dirt_from=DF):
    g = [["." for _ in range(W)] for _ in range(H)]
    for y in range(H):
        for x in range(W):
            if x < 2 or x >= W - 2 or y < 2 or y >= H - 2:
                g[y][x] = "X"
    for y in range(dirt_from, H - 2):
        for x in range(2, W - 2):
            g[y][x] = "#"
    return g


def perch(g, seat, cx, cy, half=5):
    for y in range(cy + 1, min(H - 2, cy + 4)):
        for x in range(max(PX0, cx - half), min(PX1 + 1, cx + half + 1)):
            if g[y][x] != "X":
                g[y][x] = "#"
    for x in range(max(PX0, cx - half), min(PX1 + 1, cx + half + 1)):
        g[cy][x] = "."
    for dx in (-4, 0, 4):
        if 2 <= cx + dx <= W - 3:
            g[cy][cx + dx] = str(seat)


def make_line():
    g = blank()
    for seat, cx in enumerate((16, 37, 58, 79)):
        perch(g, seat, cx, DF - 1)
    return ["".join(r) for r in g]


def make_square(side, xc=48):
    g = blank()
    yb = DF - 1
    yt = yb - side
    xl, xr = xc - side // 2, xc + side // 2
    for seat, (cx, cy) in enumerate(((xl, yt), (xr, yt), (xl, yb), (xr, yb))):
        perch(g, seat, cx, cy)
    return ["".join(r) for r in g]


def relabel(rows, shift):
    return ["".join(str((int(c) + shift) % 4) if c in "0123" else c for c in r)
            for r in rows]


def run_variant(j, j4, eng, bot, rows_txt, tag, seeds, jobs):
    d = BUILD / "sq2" / tag
    if d.is_dir():
        shutil.rmtree(str(d))
    d.mkdir(parents=True)
    mp = d / (tag + ".txt")
    mp.write_text("\n".join(rows_txt) + "\n", encoding="utf-8", newline="\n")
    p = subprocess.run([str(eng)], input="LOAD %s 1 15\nSTATE\n" % mp,
                       capture_output=True, text=True)
    if any(ln.startswith("ERR") for ln in (p.stdout or "").splitlines()):
        return None, [ln for ln in p.stdout.splitlines() if ln.startswith("ERR")][0]

    wa = j4.WALL_PER_JOB_S * (jobs - 1)
    tasks = list(range(seeds))

    def run(sd):
        rp = d / ("r%d.jsonl" % sd)
        j4.play_one(j, eng, mp, 1000 + sd * 37, [bot] * 4, (0, 1, 2, 3),
                    (0, 1, 2, 3), 0.5, replay_path=rp, replay_grid=False,
                    wall_allow_s=wa, jobs=jobs)
        return rp

    with ThreadPoolExecutor(max_workers=jobs) as ex:
        rps = list(ex.map(run, tasks))

    wins = collections.Counter()
    drown = collections.Counter()
    dead = collections.Counter()
    games = 0
    for rp in rps:
        if not rp.is_file():
            continue
        turns = []
        with rp.open(encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                if rec.get("type") == "turn":
                    turns.append(rec)
                elif rec.get("type") == "result":
                    wins[rec.get("winner", -1)] += 1
                    games += 1
        prev = None
        for rec in turns:
            worms = rec.get("worms") or []
            t = rec.get("t", 0)
            if prev is not None:
                for wid in range(min(len(worms), len(prev))):
                    if prev[wid][5] and not worms[wid][5]:
                        seat = wid // 3
                        dead[seat] += 1
                        if prev[wid][1] >= water_at(t - 1):
                            drown[seat] += 1
            prev = worms
    shutil.rmtree(str(d), ignore_errors=True)
    return {"wins": dict(wins), "games": games,
            "drown": dict(drown), "dead": dict(dead)}, ""


def summarize(tag, r, seamap=None):
    if r is None:
        return None
    g = r["games"]
    if not g:
        return None
    pct = [100.0 * r["wins"].get(i, 0) / g for i in range(4)]
    spread = max(pct) - min(pct)
    top = pct[0] + pct[1]
    bot = pct[2] + pct[3]
    return {"variant": tag, "games": g, "seat_pct": [round(x, 1) for x in pct],
            "spread": round(spread, 1), "top": round(top, 1), "bottom": round(bot, 1),
            "dead": r["dead"], "drown": r["drown"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=48)
    ap.add_argument("--jobs", type=int, default=12)
    args = ap.parse_args()

    j = load(PUB / "judge" / "judge.py", "q1")
    j4 = load(PUB / "judge" / "judge4.py", "q2")
    eng = j.build_engine(GAME)
    bot = PUB / "baselines" / "bin" / "tide_normal.exe"

    shapes = {"line": make_line(),
              "sq21": make_square(21),
              "sq17": make_square(17),
              "sq13": make_square(13)}

    print("%-7s %-7s %6s %8s %8s %8s %8s %8s %9s %8s"
          % ("variant", "shift", "games", "seat0%", "seat1%", "seat2%", "seat3%",
             "spread", "top%", "淹死"))
    out = []
    for name, rows_txt in shapes.items():
        for shift in (0, 1, 2, 3):
            r, err = run_variant(j, j4, eng, bot, relabel(rows_txt, shift),
                                 "%s-s%d" % (name, shift), args.seeds, args.jobs)
            if r is None:
                print("%-7s %-7d   ERROR %s" % (name, shift, err))
                continue
            s = summarize(name, r)
            s["shift"] = shift
            out.append(s)
            print("%-7s %-7d %6d %7.1f%% %7.1f%% %7.1f%% %7.1f%% %7.1f%% %8.1f%% %7d"
                  % (name, shift, s["games"], *s["seat_pct"], s["spread"], s["top"],
                     sum(s["drown"].values())))

    print("")
    print("=== 每个形状：4 个重贴号的平均 spread（越小越公平）===")
    by = collections.defaultdict(list)
    for s in out:
        by[s["variant"]].append(s)
    for name in shapes:
        v = by.get(name, [])
        if not v:
            continue
        print("  %-6s mean spread = %6.1f pp   （各行 %s）"
              % (name, sum(x["spread"] for x in v) / len(v),
                 ", ".join("%.1f" % x["spread"] for x in v)))

    p = REPO / "problems" / "p01-ballistic" / "docs" / "data" / "layout-sq.json"
    p.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("wrote " + str(p.relative_to(REPO)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
