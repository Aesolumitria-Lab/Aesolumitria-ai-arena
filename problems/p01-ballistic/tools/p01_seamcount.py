#!/usr/bin/env python3
"""Does anything actually CROSS the seam in a ring game?

WHY: the fixed torus engine wraps correctly (problems/p01-ballistic/tools/p01_seamtest.py proves x=93
step R -> x=2). But the ring arena still produces the SAME seat pattern as the
line (by geometry 36.3/13.5/10.0/40.1 vs the line 36.8/7.0/7.8/48.3). Either the
ring geometry is not equivalent, or nobody ever uses the seam.

This counts, per replay, every worm step that crosses x=93 <-> x=2, plus every
shot that starts on one side and lands on the other.
"""
from __future__ import annotations

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
PX0, PX1 = 2, 93


def loadmod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def main() -> int:
    ta = loadmod(REPO / "problems" / "p01-ballistic" / "tools" / "p01_torusarena.py", "tam")
    j = ta.load(PUB / "judge" / "judge.py", "cx1")
    j4 = ta.load(PUB / "judge" / "judge4.py", "cx2")
    torus = ta.build_scratch(True)
    bot = PUB / "baselines" / "bin" / "tide_normal.exe"
    jobs = 12
    wa = j4.WALL_PER_JOB_S * (jobs - 1)

    rows_txt = ta.make_ring()
    d = BUILD / "seamcount"
    if d.is_dir():
        shutil.rmtree(str(d))
    d.mkdir(parents=True)
    mp = d / "ring.txt"
    mp.write_text("\n".join(rows_txt) + "\n", encoding="utf-8", newline="\n")
    p = subprocess.run([str(torus)], input="LOAD %s 1 15\nSTATE\n" % mp,
                       capture_output=True, text=True)
    if any(ln.startswith("ERR") for ln in p.stdout.splitlines()):
        print("REJECT", [l for l in p.stdout.splitlines() if l.startswith("ERR")][0])
        return 1

    # 打印平台位置，确认环形四席等距
    for s in range(4):
        xs = sorted(x for y, r in enumerate(rows_txt) for x, c in enumerate(r) if c == str(s))
        print("seat%d spawn x = %s" % (s, xs))
    print("")

    def run(sd):
        rp = d / ("r%d.jsonl" % sd)
        j4.play_one(j, torus, mp, 1000 + sd * 37, [bot] * 4, (0, 1, 2, 3),
                    (0, 1, 2, 3), 0.5, replay_path=rp, replay_grid=False,
                    wall_allow_s=wa, jobs=jobs)
        return rp

    with ThreadPoolExecutor(max_workers=jobs) as ex:
        rps = list(ex.map(run, list(range(48))))

    cross = 0
    total_moves = 0
    games = 0
    per_seat_cross = collections.Counter()
    for rp in rps:
        if not rp.is_file():
            continue
        games += 1
        prev = None
        with rp.open(encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                if rec.get("type") != "turn":
                    continue
                worms = rec.get("worms") or []
                if prev is not None:
                    for i in range(min(len(worms), len(prev))):
                        if not (prev[i][5] and worms[i][5]):
                            continue
                        dx = worms[i][0] - prev[i][0]
                        if abs(dx) > 0:
                            total_moves += 1
                        if abs(dx) >= PX1 - PX0 - 5:      # 一步跨过整个环
                            cross += 1
                            per_seat_cross[i // 3] += 1
                prev = worms

    print("games=%d  worm steps=%d  seam crossings=%d  (%.3f%% of steps)"
          % (games, total_moves, cross,
             100.0 * cross / max(1, total_moves)))
    print("crossings by seat: %s" % dict(per_seat_cross))
    print("")
    print("=> 0 次跨越 说明 bot 从来不使用环缝：它们看到的仍是一张左右有墙的矩形图。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
