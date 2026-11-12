#!/usr/bin/env python3
"""Does the square layout survive its own falling problem? Measure falls, not guesses.

WHY (2026-11-10, owner): in a square, two seats stand on a raised platform. If that
platform gets breached, that worm falls to the floor below. The owner asks whether
this is a real problem. It is measurable:
  * RULES 6.4 / engine.cpp:245-261 -- a worm keeps falling while air is below it,
    counts fall tiles, and eats a FLAT 10 HP if fall > FALL_SAFE (3). Not per tile.
  * two raised seats sit 'side' rows above the floor, so any breach means a fall of
    ~side rows, which is way past FALL_SAFE => a flat 10 HP every time.
  * the raised seats also end up ON THE FLOOR after falling, i.e. they lose the
    height advantage entirely and join the low seats' neighbourhood.

So this tool does three things per layout:
  1. how often does a raised-seat worm fall more than 3 (>10 HP)?
  2. how much total fall damage does each seat take per game?
  3. does the seat share stay balanced, or does the label bias reappear after falls?

Compares line / sq17 / sq13 / sq21 on identical seeds and identical bots.
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
FALL_SAFE = 3


def load(p, n):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


def blank():
    g = [["." for _ in range(W)] for _ in range(H)]
    for y in range(H):
        for x in range(W):
            if x < 2 or x >= W - 2 or y < 2 or y >= H - 2:
                g[y][x] = "X"
    for y in range(DF, H - 2):
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=96)
    ap.add_argument("--jobs", type=int, default=12)
    args = ap.parse_args()

    j = load(PUB / "judge" / "judge.py", "f1")
    j4 = load(PUB / "judge" / "judge4.py", "f2")
    eng = j.build_engine(GAME)
    bot = PUB / "baselines" / "bin" / "tide_normal.exe"
    jobs = args.jobs
    wa = j4.WALL_PER_JOB_S * (jobs - 1)

    shapes = {"line": make_line(), "sq17": make_square(17), "sq13": make_square(13)}

    print("%-6s %6s %9s %9s %9s %9s %9s %8s"
          % ("shape", "games", "seat0掉%", "seat1掉%", "seat2掉%", "seat3掉%",
             "总摔落HP", "spread"))
    out = []
    for name, rows_txt in shapes.items():
        d = BUILD / "fallp" / name
        if d.is_dir():
            shutil.rmtree(str(d))
        d.mkdir(parents=True)
        mp = d / (name + ".txt")
        mp.write_text("\n".join(rows_txt) + "\n", encoding="utf-8", newline="\n")
        p = subprocess.run([str(eng)], input="LOAD %s 1 15\nSTATE\n" % mp,
                           capture_output=True, text=True)
        if any(ln.startswith("ERR") for ln in (p.stdout or "").splitlines()):
            print("%-6s ERR %s" % (name, [l for l in p.stdout.splitlines() if l.startswith("ERR")][0]))
            continue

        tasks = list(range(args.seeds))

        def run(sd):
            rp = d / ("r%d.jsonl" % sd)
            j4.play_one(j, eng, mp, 1000 + sd * 37, [bot] * 4, (0, 1, 2, 3),
                        (0, 1, 2, 3), 0.5, replay_path=rp, replay_grid=False,
                        wall_allow_s=wa, jobs=jobs)
            return rp

        with ThreadPoolExecutor(max_workers=jobs) as ex:
            rps = list(ex.map(run, tasks))

        wins = collections.Counter()
        fallbig = collections.Counter()      # 摔 > 3 格 = 吃 10 HP 的次数
        falltot = collections.Counter()      # 摔落格数合计
        wormturns = collections.Counter()    # 每条虫的存活回合数
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
            # 摔落检测：虫 y 增加且跨越了多行；用重力「悬空后落到底」的特征
            prev = None
            for rec in turns:
                worms = rec.get("worms") or []
                if prev is not None:
                    for wid in range(min(len(worms), len(prev))):
                        if not (prev[wid][5] and worms[wid][5]):
                            continue
                        dy = worms[wid][1] - prev[wid][1]
                        if dy > 0:
                            seat = wid // 3
                            falltot[seat] += dy
                            if dy > FALL_SAFE:
                                fallbig[seat] += 1
                for wid in range(len(worms)):
                    if worms[wid][5]:
                        wormturns[wid // 3] += 1
                prev = worms

        pct = [100.0 * wins[i] / max(1, games) for i in range(4)]
        spread = max(pct) - min(pct)
        fbp = [100.0 * fallbig[s] / max(1, wormturns[s]) for s in range(4)]
        total_hp = sum(fallbig.values()) * 10
        print("%-6s %6d %8.1f%% %8.1f%% %8.1f%% %8.1f%% %9d %8.1f"
              % (name, games, fbp[0], fbp[1], fbp[2], fbp[3], total_hp, spread))
        out.append({"shape": name, "games": games,
                    "fall_over3_pct": [round(x, 2) for x in fbp],
                    "fall_tiles_total": {str(k): falltot[k] for k in range(4)},
                    "fall_hp_total": total_hp, "spread": round(spread, 1),
                    "seat_pct": [round(x, 1) for x in pct]})
        shutil.rmtree(str(d), ignore_errors=True)

    p = REPO / "problems" / "p01-ballistic" / "docs" / "data" / "fall.json"
    p.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("")
    print("wrote " + str(p.relative_to(REPO)))
    print("")
    print("读法：")
    print("  seatN掉% = 该席虫「单次下落 > 3 格」的回合占比（每次固定吃 10 HP）")
    print("  RULES 6.4：fall > 3 扣 10 HP，平地 10 点，不按格数叠加")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
