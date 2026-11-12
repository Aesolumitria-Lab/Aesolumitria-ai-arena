#!/usr/bin/env python3
"""题库统一 bench：任意两道 bot 在任意一道题上对打。

    python tools/bench.py --game p07 --bot scripted --vs greedy --matches 8
    python tools/bench.py --game p07 --bot greedy --vs random --matches 20 --seeds 1-20

每题的对局函数签名不同（有的要地图、有的要 game_dir、有的是两局一场），
所以这里按题放一个薄适配器，只做一件事：把结果**归一化**成

    {"winner": 0|1|-1, "margin": <该题自己的分数差>, "steps": <该题自己的长度>}

`margin` 的单位**只在该题内部有意义**。P01 是剩余 HP 差、P07 是比赛分差、
P08 是核心血量差——不要跨题比较绝对值，只能各自做排序（见 calibrate.py）。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
import harness  # noqa: E402


def load_judge(game: Path):
    p = game / "public" / "judge" / "judge.py"
    name = "_judge_" + game.name.replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, p)
    if spec is None or spec.loader is None:
        raise SystemExit(f"无法加载裁判：{p}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def resolve_bot(spec: str, game: Path, anchors_bin: str | Path | None = None) -> Path:
    p = Path(spec)
    for cand in (p, REPO / spec, game / spec):
        if cand.exists() and cand.is_file():
            return cand.resolve()
    # 评测环境只挂载封装好的 anchor 二进制，源码不在那里。
    if anchors_bin:
        d = Path(anchors_bin)
        if not d.is_absolute():
            d = REPO / d
        for cand in (d / spec, d / f"{spec}.exe"):
            if cand.exists() and cand.is_file():
                return cand.resolve()
    for cand in (game / "private" / "anchors" / f"{spec}.cpp",
                 game / "public" / f"{spec}.cpp",
                 game / "private" / "anchors" / spec):
        if cand.exists():
            return cand.resolve()
    raise SystemExit(f"找不到 bot：{spec}")


def build(game: Path, j, name: str, anchors_bin: str | Path | None = None) -> Path:
    src = resolve_bot(name, game, anchors_bin)
    # 封装后的 anchor 是二进制（.exe 或无后缀 ELF），没有源码可编译。
    if src.suffix.lower() != ".cpp":
        return src
    build_dir = game / "build"
    build_dir.mkdir(exist_ok=True)
    try:
        return j.build_bot(src, build_dir, game / "public")
    except TypeError:
        return j.build_bot(src, build_dir)


# ------------------------------------------------------------------ 适配器
# 每个适配器返回 (winner, margin, steps)。margin 的语义见文件头。

def _p01(j, eng, game, bots, seed, mp, rh, replay_path=None):
    r = j.play_match(eng, mp, mp.stem, rh, bots, seed=seed, replay_path=replay_path)
    # 两局总剩余 HP 差，量程 −600..600（每局每方 300）
    steps = sum(g["round"] for g in r["games"])
    return r["winner"], r["total_hp"][0] - r["total_hp"][1], steps


def _p06(j, eng, game, bots, seed, mp, rh, replay_path=None):
    r = j.play_match(eng, mp, mp.stem, rh, bots, seed=seed, replay_path=replay_path)
    # 两局核心血量之和的差，量程约 −8..8
    steps = sum(g["steps"] for g in r["games"])
    return r["winner"], r["total_core"][0] - r["total_core"][1], steps


def _p07(j, eng, game, bots, seed, mp, rh, replay_path=None):
    r = j.play_match(eng, game, mp, mp.stem, rh, bots, seed=seed, replay_path=replay_path)
    steps = sum(h.get("steps", 0) for h in r.get("halves", []))
    return r["winner"], r["score"][0] - r["score"][1], steps


def _p08(j, eng, game, bots, seed, mp, rh, replay_path=None):
    r = j.play_game(eng, rh, bots, seed=seed, replay_path=replay_path)
    last = r["waves"][-1] if r.get("waves") else {"core": [0, 0]}
    return r["winner"], last["core"][0] - last["core"][1], r.get("wave", 0)


ADAPTERS = {
    "p01-ballistic": _p01,
    "p06-core-tactics": _p06,
    "p07-maze-hunt": _p07,
    "p08-tower-duel": _p08,
}


def maps_of(game: Path) -> list[Path]:
    d = game / "public" / "maps"
    return sorted(d.glob("*.txt")) if d.is_dir() else []


def parse_seeds(text: str) -> list[int]:
    out: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="p01-ballistic", help="题号或目录名")
    ap.add_argument("--bot", required=True, help="座位 0 的 bot：anchor 名或 .cpp 路径")
    ap.add_argument("--vs", required=True, help="座位 1 的 bot")
    ap.add_argument("--matches", type=int, default=20)
    ap.add_argument("--seeds", default="")
    ap.add_argument("--maps", default="", help="逗号分隔的地图路径；默认用该题全部公开图")
    ap.add_argument("--anchors-bin", default="",
                    help="封装好的 anchor 目录（优先于 private/anchors 源码）")
    ap.add_argument("--workers", type=int, default=1,
                    help="并行场数；默认 1。官方计时/验收请保持 1")
    ap.add_argument("--json")
    args = ap.parse_args()

    game = harness.game_dir(args.game)
    if game.name not in ADAPTERS:
        raise SystemExit(f"{game.name} 还没有 bench 适配器（见 tools/bench.py 的 ADAPTERS）")

    j = load_judge(game)
    rh = json.loads((game / "meta.json").read_text(encoding="utf-8")).get("rules_hash", "x")
    eng = j.build_engine(game)
    bots = {0: build(game, j, args.bot, args.anchors_bin),
            1: build(game, j, args.vs, args.anchors_bin)}

    maps = [Path(m).resolve() for m in args.maps.split(",") if m.strip()] or maps_of(game)
    seeds = parse_seeds(args.seeds) if args.seeds else list(range(1, args.matches + 1))

    def play_one(i: int) -> dict:
        seed = seeds[i % len(seeds)]
        mp = maps[i % len(maps)] if maps else None
        winner, margin, steps = ADAPTERS[game.name](j, eng, game, bots, seed, mp, rh)
        return {"seed": seed, "map": mp.stem if mp else "-",
                "winner": winner, "margin": margin, "steps": steps}

    t0 = time.time()
    if args.workers > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            rows = list(pool.map(play_one, range(args.matches)))
    else:
        rows = [play_one(i) for i in range(args.matches)]
    elapsed = time.time() - t0
    wins = [sum(1 for r in rows if r["winner"] == 0),
            sum(1 for r in rows if r["winner"] == 1)]
    draws = sum(1 for r in rows if r["winner"] < 0)
    margins = [r["margin"] for r in rows if r["winner"] >= 0]
    steps_all = [r["steps"] for r in rows]

    def pct(p: float) -> float:
        s = sorted(steps_all)
        return s[min(len(s) - 1, int(len(s) * p))]

    print(f"{game.name}: {args.bot} vs {args.vs}  "
          f"({args.matches} 场, {elapsed:.1f}s, workers {args.workers})")
    print(f"  W-L-D   {wins[0]}-{wins[1]}-{draws}")
    if margins:
        print(f"  margin  mean {statistics.mean(margins):+.1f}  "
              f"median {statistics.median(margins):+.1f}  "
              f"range [{min(margins)}, {max(margins)}]")
    print(f"  steps   p50 {pct(0.5):.0f}  p95 {pct(0.95):.0f}  max {max(steps_all)}")

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "tool": "bench", "game": game.name,
            "bot": args.bot, "vs": args.vs, "matches": args.matches,
            "wins": wins, "draws": draws,
            "margin_mean": statistics.mean(margins) if margins else 0.0,
            "steps_p50": pct(0.5), "steps_p95": pct(0.95),
            "elapsed_s": round(elapsed, 2), "workers": args.workers,
            "rows": rows,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return 0


if __name__ == "__main__":
    sys.exit(main())
