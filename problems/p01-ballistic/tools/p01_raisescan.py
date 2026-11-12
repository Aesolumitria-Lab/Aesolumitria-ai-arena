#!/usr/bin/env python3
"""Scan raising the inner seats' ground (seats 1/2) and measure whether it fixes the seat bias.

WHY (2026-11-10, owner): HP compensation measured almost no effect on the inner-seat
death rate (+40 HP moved it 0.8pp, docs/data/hp-scan.json). The suspected root cause is
geometry: seats 1/2 sit between two opponents and get hit from both sides. This tool
tests the geometric knob instead of the HP knob.

WHAT IT DOES
  For each N in --raise, and for each seat in (1, 2):
    * move that seat's 3 spawn markers UP by N rows
    * fill the N rows beneath them with '#', so the ground surface under the seat
      rises by N and stays solid all the way down ("从底部顶上去")
    * everything else is untouched
  This is the owner's "补基岩" idea applied uniformly, with N swept to find the knee.

WHY IT IS SAFE TO TRY
  * seats 1 and 2 are mirror images of each other (95-41=54, 95-33=62), so raising
    both by the same N preserves the x -> 95-x mirror. validate4's mirror assertion
    would still hold.
  * maps are written to build/ (gitignored). The committed map set is NOT touched.
  * the engine is NOT touched and NOT recompiled: this is pure map data.
  * every variant is checked with strict LOAD before any game is played.

WHY THE RESULT IS STILL JUST AN EXPERIMENT
  Raising the ground also changes the firing geometry (height = range in a ballistic
  game), so this can overshoot and hand the inner seats an advantage. That is exactly
  what the sweep is looking for. Nothing here is merged.

MEASUREMENT
  4 identical TIDE-NORMAL bots => pure seat effect. Reads the per-turn replay record
  and counts deaths per seat, plus how many were drownings.
  ★ The sweep varies SEED, not permutation: with 4 identical bots a permutation only
    relabels who sits where, it does not change which seat wins (verified in
    a permutation check (not shipped with the repo): 24 perms, all won by seat 0).
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

BASE_X = (16, 37, 58, 79)
BASE_HALF = 5
W = 96
H = 54


def load(p, n):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


def raise_map(rows, n):
    """Raise seats 1/2 by n rows: spawns up, ground filled solid underneath."""
    if n == 0:
        return list(rows)
    out = [list(r) for r in rows]
    for seat in (1, 2):
        xs, sy = [], H
        for y in range(H):
            for x in range(W):
                if out[y][x] == str(seat):
                    xs.append(x)
                    sy = min(sy, y)
                    out[y][x] = "."
        if len(xs) != 3:
            raise SystemExit("seat %d has %d spawns, expected 3" % (seat, len(xs)))
        new_sy = sy - n
        if new_sy < 2:
            raise SystemExit("raise %d pushes seat %d off the map" % (n, seat))
        base = BASE_X[seat]
        # 把新地表以下、旧地表以上填成实心（从底部顶上去）
        for y in range(new_sy + 1, sy + 1):
            for x in range(max(2, base - BASE_HALF), min(W - 2, base + BASE_HALF + 1)):
                out[y][x] = "#"
        # 新地表那一行留空，再摆回三条虫（x 位置不变）
        for x in range(max(2, base - BASE_HALF), min(W - 2, base + BASE_HALF + 1)):
            out[new_sy][x] = "."
        for x in xs:
            out[new_sy][x] = str(seat)
    return ["".join(r) for r in out]


def write_variant(src_rows, n, out_dir: Path) -> Path:
    d = out_dir / ("raise%d" % n)
    d.mkdir(parents=True, exist_ok=True)
    return d


def strict_ok(exe: Path, mp: Path) -> str:
    inp = "LOAD %s 1 15\nSTATE\n" % mp
    p = subprocess.run([str(exe)], input=inp, capture_output=True, text=True)
    for line in (p.stdout or "").splitlines():
        if line.startswith("ERR"):
            return line
    return ""


def pick_maps(n: int):
    man = json.loads((PUB / "maps4" / "manifest.json").read_text(encoding="utf-8"))
    by_el = collections.OrderedDict()
    for rec in man["maps"]:
        tc = rec.get("type_code") or ""
        el = tc.split("|")[1] if "|" in tc else "?"
        p = PUB / "maps4" / rec["file"]
        if p.is_file():
            by_el.setdefault(el, []).append(p)
    out = []
    i = 0
    while len(out) < n and any(len(v) > i for v in by_el.values()):
        for v in by_el.values():
            if len(out) >= n:
                break
            if len(v) > i:
                out.append(v[i])
        i += 1
    return out


def water_at(t):
    w = 50
    for r in range(30, t + 1):
        if r % 2 == 0 and w > 44:
            w -= 1
    return w


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raise", default="0,1,2,3,4")
    ap.add_argument("--maps", type=int, default=12)
    ap.add_argument("--seeds", type=int, default=16)
    ap.add_argument("--jobs", type=int, default=12)
    ap.add_argument("--bot", default="tide_normal")
    args = ap.parse_args()

    j = load(PUB / "judge" / "judge.py", "p01_j")
    j4 = load(PUB / "judge" / "judge4.py", "p01_j4")
    eng = j.build_engine(GAME)

    bp = Path(args.bot)
    cands = [bp] if bp.is_file() else []
    cands += [PUB / "baselines" / "bin" / (args.bot + ".exe"),
              PUB / "challenges" / "bin" / (args.bot + ".exe")]
    bot = next((c for c in cands if c.is_file()), None)
    if bot is None:
        ap.error("bot not found: " + args.bot)

    maps = pick_maps(args.maps)
    jobs = args.jobs
    wa = j4.WALL_PER_JOB_S * (jobs - 1)
    print("bot=%s maps=%d seeds=%d jobs=%d" % (bot.name, len(maps), args.seeds, jobs))
    print("maps: " + ", ".join(m.stem for m in maps))
    print("")
    print("%-6s %6s %10s %10s %7s %9s %9s %8s %8s"
          % ("raise", "games", "inner死", "outer死", "ratio", "inner胜%", "判血%", "淹死", "堵图"))

    rows = []
    for n in [int(x) for x in getattr(args, "raise").split(",") if x.strip()]:
        vdir = BUILD / ("raise_%d" % n)
        if vdir.is_dir():
            shutil.rmtree(str(vdir))
        vdir.mkdir(parents=True)
        variant, bad = [], 0
        for mp in maps:
            out = vdir / (mp.stem + ".txt")
            out.write_text("\n".join(raise_map(
                mp.read_text(encoding="utf-8").splitlines(), n)) + "\n",
                encoding="utf-8", newline="\n")
            err = strict_ok(eng, out)
            if err:
                bad += 1
                if bad <= 2:
                    print("   strict LOAD rejected %s raise=%d: %s" % (out.name, n, err))
                continue
            variant.append(out)
        if not variant:
            print("%-6d  全部被 strict LOAD 拒绝，跳过" % n)
            continue

        rd = REPO / "build" / ("raisescan_%d" % n)
        if rd.is_dir():
            shutil.rmtree(str(rd))
        rd.mkdir(parents=True)
        tasks = [(mi, mp, sd) for mi, mp in enumerate(variant) for sd in range(args.seeds)]

        def run(t):
            mi, mp, sd = t
            rp = rd / ("%d-%d.jsonl" % (mi, sd))
            j4.play_one(j, eng, mp, 1000 + sd * 37 + mi, [bot] * 4, (0, 1, 2, 3),
                        (0, 1, 2, 3), 0.5, replay_path=rp, replay_grid=False,
                        wall_allow_s=wa, jobs=jobs)
            return rp

        with ThreadPoolExecutor(max_workers=jobs) as ex:
            rps = list(ex.map(run, tasks))

        dead = collections.Counter()
        drown = collections.Counter()
        wins = collections.Counter()
        reasons = collections.Counter()
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
                        reasons[rec.get("reason", "?")] += 1
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
                            if prev[wid][1] >= water_at(t):
                                drown[seat] += 1
                prev = worms

        di = 100.0 * (dead[1] + dead[2]) / max(1, 6 * games)
        do = 100.0 * (dead[0] + dead[3]) / max(1, 6 * games)
        ratio = di / do if do else float("nan")
        iw = 100.0 * (wins[1] + wins[2]) / max(1, games)
        tb = 100.0 * reasons.get("HP", 0) / max(1, games)
        print("%-6d %6d %9.1f%% %9.1f%% %7.2f %8.1f%% %8.1f%% %7d %8d"
              % (n, games, di, do, ratio, iw, tb,
                 sum(drown.values()), bad), flush=True)
        rows.append({"raise": n, "games": games, "maps_ok": len(variant),
                     "maps_rejected": bad,
                     "inner_death_pct": round(di, 2), "outer_death_pct": round(do, 2),
                     "ratio": None if ratio != ratio else round(ratio, 3),
                     "inner_win_pct": round(iw, 2),
                     "tiebreak_pct": round(tb, 2),
                     "drown_total": sum(drown.values()),
                     "dead_by_seat": {str(k): dead[k] for k in range(4)},
                     "wins_by_seat": {str(k): wins[k] for k in range(4)},
                     "reasons": dict(reasons)})
        shutil.rmtree(str(rd), ignore_errors=True)

    path = REPO / "problems" / "p01-ballistic" / "docs" / "data" / "raise-scan.json"
    path.write_text(json.dumps({"bot": bot.name, "maps": [m.stem for m in maps],
                                "seeds": args.seeds, "rows": rows}, indent=2),
                    encoding="utf-8")
    print("")
    print("wrote " + str(path.relative_to(REPO)))
    print("")
    print("读法：")
    print("  inner死 / outer死 = 该席组虫阵亡率；ratio 1.00 就是补平")
    print("  inner胜% = 内席(1,2)赢的局占比；50.0 = 完全公平（四席均分的一半）")
    print("  堵图     = strict LOAD 拒绝的变体数（不为 0 说明这个 N 改坏了地图）")
    print("  raise=0 必须与正式图集完全一致（no-op 校验）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
