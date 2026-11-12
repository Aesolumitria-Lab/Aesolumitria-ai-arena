#!/usr/bin/env python3
"""Scan a per-seat starting-HP bonus for the inner seats (1/2), and report where it lands.

WHY (2026-11-10, owner): inner seats die 1.42x more often, and the cause measured in
docs/fairness-audit.md 3.1b is DIRECT DAMAGE, not drowning. So the compensation target is
survivability against damage. This tool scans a range of starting-HP bonuses and
measures the resulting inner/outer death ratio and win share.

This is an EXPERIMENT, not a rule change:
  * it compiles a SCRATCH copy of engine.cpp (build/, gitignored) with a compile-time
    switch -DP01_INNER_HP=<delta>. The committed engine.cpp is NOT touched.
  * the numbers describe a HYPOTHETICAL rule. Nothing here is merged.
  * starting HP enters state_hash, so every replay/hash would change. That is a fact,
    not something this tool can avoid -- it is why this stays an experiment.

HP is also the FIRST tie-break key (RULES 11.2 / engine.cpp:1015), so a bonus hands
the inner seats free advantage in any game that reaches the round-40 tie-break. This
tool counts how often that happens so the pollution is visible, not hidden.

Damage steps that matter (rocket 25 / grenade 35 / drill 20):
    hp=100 -> 4 / 3 / 5 hits      hp=105 -> 5 / 3 / 6
    hp=110 -> 5 / 4 / 6           hp=120 -> 5 / 4 / 6      hp=125 -> 5 / 4 / 7
  so the meaningful deltas are 0, 5, 10, 20, 25 -- the rest are flat.
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

CONST_ANCHOR = "static const int FALL_DAMAGE = 10;"
CONST_ADD = """

#ifndef P01_INNER_HP
#define P01_INNER_HP 0
#endif
static const int INNER_HP_BONUS = P01_INNER_HP;"""

WORM_ANCHOR = "                G.worm[wid].x = x;\n                G.worm[wid].y = y;"
WORM_ADD = WORM_ANCHOR + """

                if (seat == 1 || seat == 2) G.worm[wid].hp += INNER_HP_BONUS;"""


def load(p, n):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    s.loader.exec_module(m)
    return m


def make_scratch_src() -> Path:
    src = (PUB / "engine" / "engine.cpp").read_text(encoding="utf-8")
    if CONST_ANCHOR not in src or WORM_ANCHOR not in src:
        raise SystemExit("engine.cpp anchors not found -- source changed?")
    src = src.replace(CONST_ANCHOR, CONST_ANCHOR + CONST_ADD, 1)
    src = src.replace(WORM_ANCHOR, WORM_ADD, 1)
    BUILD.mkdir(parents=True, exist_ok=True)
    out = BUILD / "engine_exp.cpp"
    out.write_text(src, encoding="utf-8", newline="\n")
    return out


def build_variant(src: Path, delta: int) -> Path:
    exe = BUILD / ("engine_exp_%d.exe" % delta)
    if exe.is_file():
        return exe
    # 必须给 -I 指向 public/engine：trig.hpp / arena4.hpp 都在那儿。
    # 不给会在 build/engine_exp.cpp:27 报 trig.hpp not found。
    cmd = ["g++", "-O2", "-std=c++17", "-static",
           "-I", str(PUB / "engine"),
           "-DP01_INNER_HP=%d" % delta, "-o", str(exe), str(src)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("build failed delta=%d\n%s" % (delta, r.stderr[-1200:]))
    return exe


def pick_maps(n: int):
    """Pick maps covering all three elevations and all three covers.

    ★ 地图文件名是 public4-01.txt 这种序号，不含类型；类型只在 manifest 的
    type_code 里（形如 "bare|mesa"）。按文件名切分会拿不到 elevation，
    上一版就是这么退化成「取排序最前 n 张」的。
    """
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
    ap.add_argument("--deltas", default="0,5,10,15,20,25,30")
    ap.add_argument("--maps", type=int, default=4)
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--bot", default="tide_normal")
    args = ap.parse_args()

    j2 = PUB / "judge" / "judge2.py"
    j = load(j2 if j2.is_file() else PUB / "judge" / "judge.py", "p01_j")
    j4 = load(PUB / "judge" / "judge4.py", "p01_j4")
    src = make_scratch_src()

    bp = Path(args.bot)
    cands = [bp] if bp.is_file() else []
    cands += [PUB / "baselines" / "bin" / (args.bot + ".exe"),
              PUB / "challenges" / "bin" / (args.bot + ".exe")]
    bot = next((c for c in cands if c.is_file()), None)
    if bot is None:
        ap.error("bot not found: " + args.bot)

    maps = pick_maps(args.maps)
    # ★★ 关键：4 个【完全相同】的 bot 时，perm 不改变任何东西 —— 已实测
    #    （排列验证脚本，未随仓库发布：同一张图同一 seed，24 个排列全部由 seat0 赢，
    #    只是 winner_bot 标签跟着换）。所以 24 排列 x 2 seed 在相同 bot 自战里
    #    实际只有 maps × seeds 个独立对局，其余是同一局换标签。
    #    量座位效应要变的是 SEED，不是 perm。固定用 1 个 perm。
    perms = [(0, 1, 2, 3)]
    jobs = args.jobs
    wa = j4.WALL_PER_JOB_S * (jobs - 1)

    print("bot=%s maps=%d perms=%d jobs=%d" % (bot.name, len(maps), len(perms), jobs))
    print("maps: " + ", ".join(m.stem for m in maps))
    print("")
    print("%-5s %6s %9s %9s %7s %9s %9s %8s"
          % ("dHP", "games", "inner死", "outer死", "ratio", "inner胜%", "判血%", "淹死"))

    rows = []
    for delta in [int(x) for x in args.deltas.split(",") if x.strip()]:
        exe = build_variant(src, delta)
        rd = REPO / "build" / ("hpscan_%d" % delta)
        if rd.is_dir():
            shutil.rmtree(str(rd))
        rd.mkdir(parents=True)

        tasks = [(mi, m, sd) for mi, m in enumerate(maps) for sd in range(args.seeds)]

        def run(t):
            mi, mp, sd = t
            rp = rd / ("%d-%d.jsonl" % (mi, sd))
            j4.play_one(j, exe, mp, 1000 + sd * 37 + mi, [bot] * 4, (0, 1, 2, 3),
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
        print("%-5d %6d %8.1f%% %8.1f%% %7.2f %8.1f%% %8.1f%% %7d"
              % (delta, games, di, do, ratio, iw, tb, sum(drown.values())), flush=True)
        rows.append({"delta": delta, "games": games,
                     "inner_death_pct": round(di, 2), "outer_death_pct": round(do, 2),
                     "ratio": None if ratio != ratio else round(ratio, 3),
                     "inner_win_pct": round(iw, 2),
                     "tiebreak_pct": round(tb, 2),
                     "drown_total": sum(drown.values()),
                     "dead_by_seat": {str(k): dead[k] for k in range(4)},
                     "wins_by_seat": {str(k): wins[k] for k in range(4)},
                     "reasons": dict(reasons)})
        shutil.rmtree(str(rd), ignore_errors=True)

    path = REPO / "problems" / "p01-ballistic" / "docs" / "data" / "hp-scan.json"
    path.write_text(json.dumps({"bot": bot.name,
                                "maps": [m.stem for m in maps],
                                "perms": len(perms), "rows": rows}, indent=2),
                    encoding="utf-8")
    print("")
    print("wrote " + str(path.relative_to(REPO)))
    print("")
    print("读法：")
    print("  inner死 / outer死 = 该席组虫阵亡率；ratio 1.00 就是补平，>1 内席仍吃亏")
    print("  inner胜% = 内席(1,2)赢的局占比；50.0 = 两席合计应得的一半（完全公平）")
    print("  判血%   = 走到第 40 轮总血量 tie-break 的局占比 —— 这个数越大，")
    print("            『内席白送血量优势』污染越重（血量是 tie-break 第一关键字）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
