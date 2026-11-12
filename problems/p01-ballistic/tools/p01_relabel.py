#!/usr/bin/env python3
"""Decisive test: is the seat bias GEOGRAPHIC or is it a LABEL effect?

WHY (2026-11-10): both compensation knobs measured ~nothing (docs/data/hp-scan.json HP +40 = 0.8pp,
docs/data/raise-scan.json ground raise N=1..6 = noise). So the bias may not be about the inner seats
at all -- it may be about the worm-id LABEL. RULES 6.4 settles falling worms in
ascending worm-id order, and winner tie-breaks read G.dealt / alive by seat index.

THE TEST
  Take one map. Relabel the spawn markers with a rotation:
      geom 0 -> seat 1, geom 1 -> seat 2, geom 2 -> seat 3, geom 3 -> seat 0
  The terrain is byte-identical; only the seat NUMBER attached to each platform
  changes. If the winner keeps following the geometry (same x position), the bias
  is geographic. If the winner follows the new seat number instead, the bias is
  the label -- and no terrain or HP change can ever fix it.

  Also runs the identity relabel as a control: it must reproduce the baseline.
"""
from __future__ import annotations

import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
GAME = REPO / "problems" / "p01-ballistic"
PUB = GAME / "public"
BUILD = GAME / "build"

import importlib.util


def load(p, n):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


def relabel(rows, shift):
    """shift: how many seat numbers to rotate each platform by."""
    out = []
    for r in rows:
        line = []
        for c in r:
            if c in "0123":
                line.append(str((int(c) + shift) % 4))
            else:
                line.append(c)
        out.append("".join(line))
    return out


def main() -> int:
    j = load(PUB / "judge" / "judge.py", "pj")
    j4 = load(PUB / "judge" / "judge4.py", "pj4")
    eng = j.build_engine(GAME)
    bot = PUB / "baselines" / "bin" / "tide_normal.exe"
    maps = sorted((PUB / "maps4").glob("*.txt"))[:6]
    shifts = [0, 1, 2, 3]
    seeds = 16
    jobs = 12
    wa = j4.WALL_PER_JOB_S * (jobs - 1)

    vdir = BUILD / "relabel"
    if vdir.is_dir():
        shutil.rmtree(str(vdir))
    vdir.mkdir(parents=True)

    variants = {}
    for s in shifts:
        d = vdir / ("shift%d" % s)
        d.mkdir()
        outs = []
        for mp in maps:
            out = d / mp.name
            out.write_text("\n".join(relabel(
                mp.read_text(encoding="utf-8").splitlines(), s)) + "\n",
                encoding="utf-8", newline="\n")
            outs.append(out)
        variants[s] = outs

    print("maps=%d seeds=%d  4 identical tide_normal bots" % (len(maps), seeds))
    print("")
    print("%-8s %8s %-32s %s" % ("shift", "games", "wins by SEAT (0,1,2,3)", "wins by X-position (left->right)"))
    allrows = []
    for s in shifts:
        rd = REPO / "build" / ("relabel_%d" % s)
        if rd.is_dir():
            shutil.rmtree(str(rd))
        rd.mkdir(parents=True)
        tasks = [(mi, mp, sd) for mi, mp in enumerate(variants[s]) for sd in range(seeds)]

        def run(t):
            mi, mp, sd = t
            rp = rd / ("%d-%d.jsonl" % (mi, sd))
            j4.play_one(j, eng, mp, 1000 + sd * 37 + mi, [bot] * 4, (0, 1, 2, 3),
                        (0, 1, 2, 3), 0.5, replay_path=rp, replay_grid=False,
                        wall_allow_s=wa, jobs=jobs)
            return (sd, rp)

        with ThreadPoolExecutor(max_workers=jobs) as ex:
            rps = list(ex.map(run, tasks))

        wins = [0, 0, 0, 0]
        games = 0
        for rp in [r for _sd, r in rps]:
            if not rp.is_file():
                continue
            with rp.open(encoding="utf-8") as fh:
                for line in fh:
                    rec = json.loads(line)
                    if rec.get("type") == "result":
                        w = rec.get("winner", -1)
                        if 0 <= w < 4:
                            wins[w] += 1
                            games += 1
        # 几何位置：原图 seat0 在最左(x≈16)，seat3 在最右(x≈79)
        # shift 把 seat 号旋转了，所以「几何位置」= (seat - shift) % 4
        geo = [0, 0, 0, 0]
        for seat in range(4):
            geo[(seat - s) % 4] += wins[seat]
        print("%-8d %8d %-32s %s" % (s, games, wins, geo))
        allrows.append({"shift": s, "games": games, "wins_by_seat": wins, "wins_by_geom": geo})
        shutil.rmtree(str(rd), ignore_errors=True)

    print("")
    print("判读：")
    print("  wins by SEAT  一变，而 wins by X-position 不变  => 偏袒跟着【标签】走，跟地形无关")
    print("  wins by SEAT  跟 wins by X-position 一起转     => 偏袒是【几何】的")
    path = REPO / "problems" / "p01-ballistic" / "docs" / "data" / "relabel.json"
    path.write_text(json.dumps(allrows, indent=2), encoding="utf-8")
    print("wrote " + str(path.relative_to(REPO)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
