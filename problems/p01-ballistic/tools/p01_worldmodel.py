#!/usr/bin/env python3
"""Do the bots actually replicate the engine accurately? Numbers, not vibes.

WHY (2026-11-10, owner): "so the world model is broken -- all the bots are
worthless then". That is a testable claim and it deserves a real answer.

The bots replicate the engine physics in their own headers. Divergence only matters
if it changes what they SEE or what they DECIDE:
  1. constants -- a wrong W/H/GRAVITY/Q makes every prediction wrong
  2. in_map -- a narrower map means they never aim at reachable cells
  3. trig table -- a different table means every angle aims differently

This diffs the constants and the boundary predicate between engine.cpp and each
challenge bot header, and reports exactly what differs.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
GAME = REPO / "problems" / "p01-ballistic"
PUB = GAME / "public"
CH = GAME / "private" / "challenges4"

CONSTS = ("W", "H", "ROUNDS", "SEATS", "WORMS_PER_SEAT", "WORM_HP",
          "AP_MAX", "START_WATER", "Q", "GRAVITY", "WIND_UNIT",
          "FALL_SAFE", "FALL_DAMAGE")


def grab_int(text, name):
    m = re.search(r"\b" + name + r"\s*=\s*(-?\d+)", text)
    return int(m.group(1)) if m else None


def in_map_body(text):
    m = re.search(r"in_map\s*\([^)]*\)\s*(?:\{|const\s*\{)??\s*([^\n}]*)", text)
    return m.group(1).strip() if m else None


def main() -> int:
    eng = (PUB / "engine" / "engine.cpp").read_text(encoding="utf-8")
    engc = dict((k, grab_int(eng, k)) for k in CONSTS)
    print("=== engine.cpp constants ===")
    for k in CONSTS:
        print("  %-14s = %s" % (k, engc[k]))
    print("")
    print("  in_map:", in_map_body(eng))

    bots = []
    for d in sorted(CH.iterdir()):
        if not d.is_dir():
            continue
        hdr = d / "source" / "common" / "state.hpp"
        ph = d / "source" / "common" / "physics.hpp"
        if hdr.is_file():
            bots.append((d.name, hdr, ph))

    print("")
    print("=== per-bot constant diff (only differences shown) ===")
    for name, hdr, ph in bots:
        txt = hdr.read_text(encoding="utf-8")
        c = dict((k, grab_int(txt, k)) for k in CONSTS)
        diffs = [(k, engc[k], c[k]) for k in CONSTS if c[k] is not None and c[k] != engc[k]]
        missing = [k for k in CONSTS if c[k] is None]
        print("  %-10s  differing=%d  missing=%d" % (name, len(diffs), len(missing)))
        for k, a, b in diffs:
            print("      %-14s engine=%-6s bot=%-6s  DRIFT" % (k, a, b))
        if ph.is_file():
            body = in_map_body(ph.read_text(encoding="utf-8"))
            print("      in_map: %s" % body)
    print("")
    print("DRIFT on W/H/Q/GRAVITY = every predicted trajectory is wrong.")
    print("narrower in_map          = they never aim at cells they think are out of bounds.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
