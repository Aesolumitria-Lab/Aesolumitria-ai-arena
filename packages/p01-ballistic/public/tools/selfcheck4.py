#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""P01 public 4-seat self-check.

Runs the contestant against three copies of a public baseline on public 4-seat
maps. TIDE EASY/NORMAL/HARD and random4 are precompiled binaries (Windows:
bin/*.exe; Linux: bin/linux/*); template4 is the starter source. The public
target is to beat TIDE; the challenge bots (CHIMERA/PSEUDO/TYPHON) are available
as precompiled platform binaries via --baseline, are optional, and are not
required to beat. This is a sanity check, not an official rating: official
scoring uses 108 hidden maps; challenge bots are optional.

    python public/tools/selfcheck4.py --bot submission.cpp
    python public/tools/selfcheck4.py --bot submission.cpp --baseline tide-hard --maps 2

默认样本 = 4 张公开图 × 2 seed × 24 排列 = 192 局（jobs=8 下几十秒）。局数少到
几十局时，p≈0.25 基线下 Wilson 95% 区间有 ±17pp 那么宽 —— 两次 selfcheck 的
差异只要落在区间里就说明不了任何事，所以结果一律带 [lo, hi] 一起印。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True

PUBLIC = Path(__file__).resolve().parents[1]
ROOT = PUBLIC.parent
JUDGE4 = PUBLIC / "judge" / "judge4.py"
ENGINE_SRC = PUBLIC / "engine" / "engine.cpp"
TEMPLATE = PUBLIC / "template4.cpp"
IS_WIN = os.name == "nt"
BIN_SUFFIX = ".exe" if IS_WIN else ""
BIN_ROOT = PUBLIC / "baselines" / "bin"
CHALLENGE_ROOT = PUBLIC / "challenges" / "bin"
# Windows binaries live at bin/; Linux binaries at bin/linux/ (no extension).
TIDE_BIN = BIN_ROOT if IS_WIN else BIN_ROOT / "linux"
CHALLENGE_BIN = CHALLENGE_ROOT if IS_WIN else CHALLENGE_ROOT / "linux"
RANDOM_BIN = TIDE_BIN / f"random4{BIN_SUFFIX}"
CHALLENGE_BINS = {name: CHALLENGE_BIN / f"{name}{BIN_SUFFIX}"
                  for name in ("chimera", "pseudo", "typhon")}

# 小样本提醒阈值：低于这个局数，胜率的 95% 区间宽到会把噪声读成"我变强了"。
# 4 席 p≈0.25 时：24 局 ≈ ±17.3pp，48 局 ≈ ±12.3pp，192 局 ≈ ±6.1pp（Wilson）。
MIN_REPORTED_GAMES = 64


def wilson95(wins: int, games: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson 95% 区间（自包含，不引入新依赖）。

    比 p_hat ± z·sqrt(p_hat(1-p_hat)/n) 稳：后者在 n=24 / p=0.25 时会给出负下界，
    而 Wilson 的下界不会越界，小样本下也更接近真实覆盖率。
    """
    if games <= 0:
        return 0.0, 1.0
    p = wins / games
    denom = 1.0 + z * z / games
    center = (p + z * z / (2 * games)) / denom
    half = z * math.sqrt(p * (1 - p) / games + z * z / (4 * games * games)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def compile_cpp(src: Path, out: Path, includes: list[Path], extra_flags: list[str] | None = None) -> Path:
    cmd = ["g++", "-O2", "-std=c++17"]
    if os.name == "nt":
        cmd.append("-static")
    for inc in includes:
        cmd += ["-I", str(inc)]
    cmd += list(extra_flags or [])
    cmd += ["-o", str(out), str(src)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"compile failed: {src}\n{proc.stderr}")
    return out


def load_judge4():
    spec = importlib.util.spec_from_file_location("p01_judge4_selfcheck", JUDGE4)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {JUDGE4}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser(description="P01 public self-check (4 seats)")
    ap.add_argument("--bot", required=True, help="contestant .cpp or native binary")
    ap.add_argument("--baseline",
                    choices=["template", "random", "both", "all",
                             "tide-easy", "tide-normal", "tide-hard",
                             "chimera", "pseudo", "typhon"],
                    default="tide-normal",
                    help="public baseline; default is the TIDE NORMAL target")
    ap.add_argument("--maps", type=int, default=4,
                    help="前 N 张公开图（默认 4）。默认 4 张 × 2 seed × 24 排列 "
                         "= 192 局，jobs=8 下几十秒；旧的 1 张 1 seed 只有 24 局，"
                         "95%% 区间约 ±17pp，够看方向不够看进步")
    ap.add_argument("--seeds", type=int, default=2,
                    help="seed 1..N（默认 2）。每局只换排列不换 seed，会退化成"
                         "同一套风程序换座位名字")
    ap.add_argument("--timeout", type=float, default=0.5,
                    help="单步 wall 兜底的【下限】（秒）；判超时看的是 CPU 预算，不是它")
    ap.add_argument("--jobs", type=int, default=1,
                    help="并发对局数（默认 1；本机 8–32 一般合适）")
    ap.add_argument("--perms", type=int, default=0,
                    help="每张图用几个座位排列（默认 0 = 全 24 个；4/12 更快）")
    ap.add_argument("--include", action="append", default=[],
                    help="extra include dir for the contestant (repeatable)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.maps <= 0 or args.seeds <= 0:
        ap.error("--maps/--seeds must be > 0")

    bot_src = Path(args.bot)
    if not bot_src.is_file():
        ap.error(f"bot not found: {bot_src}")
    maps = sorted((PUBLIC / "maps4").glob("*.txt"))[:args.maps]
    if not maps:
        ap.error("no public 4-seat maps found")

    with tempfile.TemporaryDirectory(prefix="p01-selfcheck-", ignore_cleanup_errors=True) as td:
        tmp = Path(td)
        engine = compile_cpp(ENGINE_SRC, tmp / f"engine{BIN_SUFFIX}", [PUBLIC / "engine"])
        if bot_src.suffix.lower() in (".cpp", ".cc", ".cxx", ".c"):
            bot = compile_cpp(bot_src, tmp / f"contestant{BIN_SUFFIX}",
                              [PUBLIC, *[Path(p) for p in args.include]])
        else:
            bot = bot_src
        bases = {}
        if args.baseline in ("template", "both", "all"):
            bases["template"] = compile_cpp(TEMPLATE, tmp / f"template{BIN_SUFFIX}", [PUBLIC])
        if args.baseline in ("random", "both", "all"):
            if not RANDOM_BIN.is_file():
                raise SystemExit(f"missing baseline binary: {RANDOM_BIN}")
            bases["random"] = RANDOM_BIN
        tide_names = ([args.baseline] if args.baseline in
                      ("tide-easy", "tide-normal", "tide-hard")
                      else ["tide-easy", "tide-normal", "tide-hard"]
                      if args.baseline == "all" else [])
        for name in tide_names:
            binp = TIDE_BIN / f"{name.replace('-', '_')}{BIN_SUFFIX}"
            if not binp.is_file():
                raise SystemExit(f"missing baseline binary: {binp}")
            bases[name] = binp
        if args.baseline in CHALLENGE_BINS:
            binp = CHALLENGE_BINS[args.baseline]
            if not binp.is_file():
                raise SystemExit(f"missing challenge binary: {binp}")
            bases[args.baseline] = binp
        j4 = load_judge4()
        j = j4._judge()
        active = (0, 1, 2, 3)
        # 这里每种 baseline 只跑一遍 seed 1..N，所以只取排列；schedule 的第三个
        # 字段（绝对 seed 下标）不参与 —— 换 seed 的责任在上面 range(1, args.seeds+1)。
        if args.perms > 0:
            perms = [p for p, _, _ in j4.schedule(active, args.perms, 1)]
        else:
            perms = j4.rotations(active)
        # 与 judge4.py 同口径：单进程不加余量（jobs=1 ⇒ 0）。
        wall_allow = j4.WALL_PER_JOB_S * max(0, args.jobs - 1)
        report = []

        def run_one(item):
            name, base, mp, seed, perm = item
            bots = [bot, base, base, base]
            labels = ["contestant", name, name, name]
            r = j4.play_one(j, engine, mp, seed, bots, active, perm,
                            args.timeout, labels=labels, meta={},
                            wall_allow_s=wall_allow, jobs=args.jobs)
            return name, r

        tasks = [(name, base, mp, seed, perm) for name, base in bases.items()
                 for mp in maps for seed in range(1, args.seeds + 1)
                 for perm in perms]
        results = []
        if args.jobs > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=args.jobs) as ex:
                results = list(ex.map(run_one, tasks))
        else:
            results = [run_one(t) for t in tasks]

        for name, base in bases.items():
            wins = field = draws = faults = games = 0
            for got, r in results:
                if got != name:
                    continue
                games += 1
                faults += r.get("faults", 0)
                wb = r.get("winner_bot", -1)
                if wb == 0:
                    wins += 1
                elif wb < 0:
                    draws += 1
                else:
                    field += 1
            lo, hi = wilson95(wins, games)
            row = {
                "field": f"3x {name}", "games": games,
                "contestant_wins": wins,
                "contestant_win_rate": round(wins / games, 4) if games else 0.0,
                "wilson95": [round(lo, 4), round(hi, 4)],
                "field_wins": field, "draws": draws, "faults": faults,
            }
            report.append(row)
            # 区间就印在胜率旁边：小样本时它会宽得刺眼，这正是要让人看见的东西。
            print(f"{row['field']}: {wins}/{games} = "
                  f"{row['contestant_win_rate'] * 100:.1f}% "
                  f"[{lo * 100:.1f}%, {hi * 100:.1f}%] "
                  f"(field {field}, draw {draws}, faults {faults})")
        small = [r for r in report if r["games"] < MIN_REPORTED_GAMES]
        if small:
            detail = ", ".join(f"{r['field']} {r['games']} 局" for r in small)
            print(f"注意：{detail}，低于 {MIN_REPORTED_GAMES} 局 —— 95% 区间会很宽。"
                  "别把区间重叠读成没变化，也别把区间不重叠读成变强；"
                  "要更窄的误差棒就加 --maps / --seeds（官方评分用 108 张隐藏图）。")
        if args.json:
            print(json.dumps({
                "tool": "selfcheck4", "bot": str(bot_src),
                "maps": [m.stem for m in maps], "seeds": args.seeds,
                "jobs": args.jobs,
                "games_per_field": len(maps) * args.seeds * len(perms),
                "report": report,
                "wilson95_note": "每行 wilson95 = [lo, hi]；局数 <64 时区间很宽，别当结论",
                "note": "public self-check only; target TIDE; official uses 108 hidden maps; challenge bots (CHIMERA/PSEUDO/TYPHON) are optional and not required to beat",
            }, ensure_ascii=False, indent=2))
        else:
            print("note: the public target is TIDE (normal; hard is the stretch); "
                  "template/random are starter/floor. Challenge bots are optional "
                  "and are not required to beat. "
                  "Win% 后面的 [lo, hi] 是 Wilson 95% 区间，不是误差范围之外的真值。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
