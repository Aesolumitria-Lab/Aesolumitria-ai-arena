#!/usr/bin/env python3
"""Measure inner(1,2) vs outer(0,3) seat asymmetry: end-of-game ground height + how worms died.

WHY (2026-11-10): owner hypothesises inner seats get bombed downward more, sink
closer to the water line, drown more. Measure it instead of assuming it.

Water level after step t: starts 50, -1 at end of rounds 30,32,...,40, floor 44
(engine.cpp: round>=30 && round%2==0).
"""
from __future__ import annotations

import collections
import importlib.util
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
GAME = REPO / "problems" / "p01-ballistic"
PUB = GAME / "public"
BASE_X = (16, 37, 58, 79)
BASE_HALF = 5
W = 96


def load(p, n):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


BORDER = 2


def surface_y(grid, x0, x1):
    """Topmost solid tile below the border rows.

    ★ 必须从 BORDER 开始扫：第 0/1 行是外圈基岩（永远是 X），从 y=0 扫会全return 0，
      上一版就是这么把 end_surface 全算成 0.00 的。
    """
    tops = []
    for x in range(max(BORDER, x0), min(W - BORDER, x1 + 1)):
        for y in range(BORDER, len(grid) - BORDER):
            if grid[y][x] in "#X":
                tops.append(y)
                break
    return sum(tops) / float(len(tops)) if tops else None


def water_at(t):
    w = 50
    for r in range(30, t + 1):
        if r % 2 == 0 and w > 44:
            w -= 1
    return w


def main() -> int:
    j2 = PUB / "judge" / "judge2.py"
    j = load(j2 if j2.is_file() else PUB / "judge" / "judge.py", "p01_j")
    j4 = load(PUB / "judge" / "judge4.py", "p01_j4")
    eng = j.build_engine(GAME)
    B = PUB / "baselines" / "bin"
    maps = sorted((PUB / "maps4").glob("*.txt"))[:8]
    rd = REPO / "build" / "sinkprobe"
    if rd.is_dir():
        shutil.rmtree(str(rd))
    rd.mkdir(parents=True)
    perms = [p for p, _si, _sa in j4.schedule((0, 1, 2, 3), 8, 1)][:8]
    jobs = 8
    wa = j4.WALL_PER_JOB_S * (jobs - 1)
    tasks = [(m, p, i) for m in maps for i, p in enumerate(perms)]

    def run(t):
        mp, perm, i = t
        rp = rd / ("%s-p%d.jsonl" % (mp.stem, i))
        r = j4.play_one(j, eng, mp, 1 + i, [B / "tide_normal.exe"] * 4,
                        (0, 1, 2, 3), perm, 0.5, replay_path=rp,
                        replay_grid=True, map_id=mp.stem,
                        wall_allow_s=wa, jobs=jobs)
        return mp.stem, i, perm, r

    with ThreadPoolExecutor(max_workers=jobs) as ex:
        rows = list(ex.map(run, tasks))
    ok = sum(1 for r in rows if r[3].get("ok"))
    print("games=%d ok=%d" % (len(rows), ok))
    print("")

    surf = collections.defaultdict(list)
    drowned = collections.Counter()
    damaged = collections.Counter()
    wally = collections.defaultdict(list)
    used = 0
    for stem, i, perm, r in rows:
        if not r.get("ok"):
            continue
        p = rd / ("%s-p%d.jsonl" % (stem, i))
        if not p.is_file():
            continue
        turns = []
        for line in p.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec.get("type") == "turn":
                turns.append(rec)
        if not turns:
            continue
        used += 1
        g = turns[-1].get("grid")
        if g:
            for seat in range(4):
                y = surface_y(g, BASE_X[seat] - BASE_HALF, BASE_X[seat] + BASE_HALF)
                if y is not None:
                    surf[seat].append(y)
        prev = None
        for rec in turns:
            worms = rec.get("worms") or []
            t = rec.get("t", 0)
            if prev is not None:
                for wid in range(min(len(worms), len(prev))):
                    if prev[wid][5] and not worms[wid][5]:
                        seat = wid // 3
                        if prev[wid][1] >= water_at(t):
                            drowned[seat] += 1
                        else:
                            damaged[seat] += 1
            prev = worms
            for wid in range(len(worms)):
                if worms[wid][5]:
                    wally[wid // 3].append(worms[wid][1])

    print("replays used: %d" % used)
    print("")
    print("%-5s %-6s %12s %12s %8s %8s" % ("seat", "kind", "end_surface", "mean_worm_y", "drown", "damaged"))
    for seat in range(4):
        v = surf[seat]
        my = wally[seat]
        sm = sum(v) / float(len(v)) if v else float("nan")
        wm = sum(my) / float(len(my)) if my else float("nan")
        kind = "inner" if seat in (1, 2) else "outer"
        print("%-5d %-6s %12.2f %12.2f %8d %8d"
              % (seat, kind, sm, wm, drowned[seat], damaged[seat]))

    def mean(group, src):
        vals = [x for s in group for x in src[s]]
        return sum(vals) / float(len(vals)) if vals else float("nan")

    inner, outer = (1, 2), (0, 3)
    si, so = mean(inner, surf), mean(outer, surf)
    wi, wo = mean(inner, wally), mean(outer, wally)
    print("")
    print("=== inner(1,2) vs outer(0,3) ===")
    print("end surface y : inner %.2f  outer %.2f  diff %+.2f  (positive = inner ground lower)" % (si, so, si - so))
    print("mean worm y   : inner %.2f  outer %.2f  diff %+.2f" % (wi, wo, wi - wo))
    di, do = drowned[1] + drowned[2], drowned[0] + drowned[3]
    xi = di + damaged[1] + damaged[2]
    xo = do + damaged[0] + damaged[3]
    pi = 100.0 * di / xi if xi else 0.0
    po = 100.0 * do / xo if xo else 0.0
    print("deaths        : inner %d (drown %d, %.1f%%)   outer %d (drown %d, %.1f%%)"
          % (xi, di, pi, xo, do, po))

    out = {"replays": used,
           "end_surface_y": dict((str(k), surf[k]) for k in surf),
           "drowned": dict(drowned), "damaged": dict(damaged),
           "summary": {"inner_surface": si, "outer_surface": so, "diff": si - so,
                       "inner_worm_y": wi, "outer_worm_y": wo,
                       "inner_drown_pct": pi, "outer_drown_pct": po}}
    path = REPO / "problems" / "p01-ballistic" / "docs" / "data" / "sink-probe.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("wrote " + str(path.relative_to(REPO)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
