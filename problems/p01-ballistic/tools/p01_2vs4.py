#!/usr/bin/env python3
"""P01 2-seat vs 4-seat (2026-11-10 design question).

Two hard facts this script establishes, both verifiable from code:

  F1  2-seat mode (active_mask=0b0011) REQUIRES legacy mirror-symmetric maps:
      engine strict LOAD rejects any maps4 file under mask 3 with
      "map is not mirror symmetric" (maps4 mirrors 0<3 / 1<2, not 0<1).
      Only 3 legacy maps exist (public/maps/public-01..03).

  F2  judge4.play_one() is 4-seat only: it rejects a 2-seat STATE as
      "state layout is not 4-seat". 2-seat matches go through judge.play_match()
      (a separate 2-game match with seat swap), i.e. a DIFFERENT judging stack.

=> "make the official format 2 seats" would throw away all 216 maps4 maps,
   the whole 4-seat seat-rotation design, every 4-seat reading, and would also
   need a different judge. It is not a rules tweak.

This script still RUNS both so the numbers are real: 2-seat via judge.play_match
on the 3 legacy maps, 4-seat via judge4.play_one on maps4.
"""
from __future__ import annotations

import collections
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
GAME = REPO / "problems" / "p01-ballistic"
PUB = GAME / "public"


def load(p, n):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


def main() -> int:
    j2 = PUB / "judge" / "judge2.py"
    j = load(j2 if j2.is_file() else PUB / "judge" / "judge.py", "p01_j")
    j4 = load(PUB / "judge" / "judge4.py", "p01_j4")
    B = PUB / "baselines" / "bin"
    maps2 = sorted((PUB / "maps").glob("*.txt"))
    maps4 = sorted((PUB / "maps4").glob("*.txt"))[:12]
    out = {"legacy_maps": len(maps2), "maps4_maps_total": len(list((PUB / "maps4").glob("*.txt")))}

    # ---- 2 seat: judge.play_match, 3 legacy maps, 4 seeds, seat-swapped 2-game match
    eng2 = B / "tide_normal.exe"
    win2 = collections.Counter()
    n2 = 0
    for mp in maps2:
        for seed in (1, 7, 42, 99):
            r = j.play_match(j.build_engine(GAME), mp, mp.stem, "x",
                             {0: eng2, 1: eng2}, seed=seed)
            for g in r.get("games", []):
                w = g.get("winner", -1)
                if w >= 0:
                    win2[w] += 1; n2 += 1
    print("2 seat / legacy maps / 2 identical bots: games %d  seat %s"
          % (n2, [win2.get(i, 0) for i in range(2)]))
    out["two_seat"] = {"games": n2, "seat_counts": [win2.get(i, 0) for i in range(2)],
                       "note": "judge.play_match: each seed = a 2-game match with seat swap"}

    # ---- 4 seat: judge4.play_one, 12 maps4 maps
    eng = j.build_engine(GAME)
    win4 = collections.Counter()
    n4 = 0
    draws = 0
    per_map = collections.defaultdict(set)
    for mp in maps4:
        for perm, _si, sa in j4.schedule((0, 1, 2, 3), 24, 2):
            r = j4.play_one(j, eng, mp, 1 + sa, [B / "tide_normal.exe"] * 4,
                            (0, 1, 2, 3), perm, 0.5, replay_grid=False, jobs=1)
            if not r.get("ok"):
                continue
            w = r.get("winner", -1)
            if w < 0:
                draws += 1; continue
            win4[w] += 1; n4 += 1
            per_map[mp.stem].add(w)
    n = n4 or 1
    share4 = [round(100.0 * win4.get(i, 0) / n, 1) for i in range(4)]
    multi = sum(1 for s in per_map.values() if len(s) >= 2)
    print("4 seat / maps4 / 4 identical bots: games %d  seat %s  multi-seat maps %d/%d"
          % (n4, share4, multi, len(per_map)))
    out["four_seat"] = {"games": n4, "seat_share": share4, "draws": draws,
                        "multi_seat_maps": multi, "of_maps": len(per_map)}

    path = REPO / "problems" / "p01-ballistic" / "docs" / "data" / "2vs4.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote " + str(path.relative_to(REPO)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
