#!/usr/bin/env python3
"""跨平台 hash 金标比对。

同一份 engine.cpp 在 Windows 和 Linux 各编译一次，喂同一批输入，逐个用例比对
`state_hash` 轨迹。轨迹比单个终局 hash 更严：中途任何一步漂移都会被抓到。

四题都支持，但协议不同，所以用例构造按题分开：

    p01  3 图 × 3 seed × 6 段动作（移动 / 跳跃 / 三种武器 / 越界角度）
    p06  3 图 × 2 开局 × 2 策略（120 步交替激活）
    p07  3 图 × 3 随机序列（120 步，双人同时下令）
    p08  3 seed × 2 构筑策略（25 波建造 + 战斗）

用法（Docker 镜像通道不可用，改用 WSL 原生 Ubuntu）：

    # Windows
    python tools/crosscheck.py --game all --json dist/_hash_win.json

    # Linux（WSL）
    wsl -d Ubuntu -- bash -lc "cd /mnt/<path-to>/Aesolumitria-ai-arena && \\
        python3 tools/crosscheck.py --game all --json dist/_hash_linux.json"

    # 任一侧比对
    python tools/crosscheck.py --compare dist/_hash_win.json dist/_hash_linux.json
"""

from __future__ import annotations

import argparse
import json
import importlib.util
import random
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PROBLEMS = REPO / "problems"

# 引擎输出的 hash 统一是 16 位小写十六进制（%016llx）
HEX16 = re.compile(r"\b[0-9a-f]{16}\b")

GAMES = ["p01-ballistic", "p06-core-tactics", "p07-maze-hunt", "p08-tower-duel"]


# ---------------------------------------------------------------- 用例构造

def maps_of(game: Path) -> list[Path]:
    return sorted((game / "public" / "maps").glob("*.txt"))


def p01_cases(game: Path) -> list[dict]:
    seqs = [
        "0 M R; F ROCKET 42 89 | 3 M L; F ROCKET 138 89",
        "0 M R; M R; M R | 3 M L; M L; M L",
        "0 J R; F GRENADE 60 70 | 3 J L; F GRENADE 120 70",
        "0 F DRILL 45 80 | 3 F DRILL 135 80",
        "0 WAIT | 3 WAIT",
        "0 M R; F ROCKET 90 100 | 3 M L; F ROCKET 90 100",
    ]
    out = []
    for m in maps_of(game):
        for seed in (1, 7, 42):
            for i, s in enumerate(seqs):
                out.append({
                    "tag": f"{m.stem}/s{seed}/q{i}",
                    "lines": [f"LOAD public/maps/{m.name} {seed}", f"STEP {s}", "QUIT"],
                })
    return out


def p06_cases(game: Path) -> list[dict]:
    """P06 一次 STEP = 一方激活一台机甲，双方交替。

    策略一 "idle"：每方按 0/1/2 轮转，动作全空（走 X、不做动作），
    120 步刚好覆盖两个 20 回合，主要验步号与 pass 连锁。
    策略二 "random"：确定性 RNG 生成非法/合法混杂的动作，引擎把非法激活标 bad
    但仍输出 STATE，所以不会中断轨迹。
    """
    out = []
    for m in maps_of(game):
        for first, seed in ((0, 1), (1, 7)):
            for policy in ("idle", "random"):
                lines = [f"LOAD public/maps/{m.name} {first} {seed}", "STATE"]
                if policy == "idle":
                    for i in range(120):
                        lines.append(f"STEP {(i // 2) % 3} X N X")
                else:
                    rng = random.Random(f"p06/{m.stem}/{first}/{seed}")
                    for _ in range(120):
                        lines.append("STEP %d %s %s %s" % (
                            rng.randrange(3),
                            rng.choice("NSEWX"),
                            rng.choice("FNLPRS"),
                            rng.choice("S012CX"),
                        ))
                lines.append("QUIT")
                out.append({"tag": f"{m.stem}/f{first}s{seed}/{policy}", "lines": lines})
    return out


def p07_cases(game: Path) -> list[dict]:
    """P07 一步 = 双人同时下令：STEP <r> <g0> <g1> <g2> <g3>。"""
    out = []
    for m in maps_of(game):
        for seed in (1, 7, 42):
            rng = random.Random(f"p07/{m.stem}/{seed}")
            lines = [f"LOAD public/maps/{m.name} {seed}", "SNAPSHOT"]
            for _ in range(120):
                lines.append("STEP %s %s" % (
                    rng.choice("NSEWX"),
                    " ".join(rng.choice("NSEWX") for _ in range(4)),
                ))
            lines.append("QUIT")
            out.append({"tag": f"{m.stem}/s{seed}", "lines": lines})
    return out


def _p08_build(wave: int, bs: int, seat: int, policy: str) -> str:
    if policy == "idle":
        return "W"
    if seat == 0:
        # 先铺三座塔，再升级；金币不够时 apply_action 会失败，但步与 hash 照常推进
        if bs <= 3:
            return f"B {bs - 1} {('arrow', 'cannon', 'frost')[bs - 1]}"
        return "U 0"
    if bs == 1:
        return "B 0 cannon"
    if bs == 2:
        return "D grunt 2"
    return "W"


def p08_cases(game: Path) -> list[dict]:
    """P08 一场 25 波：每波 5 个建造步 + 战斗，然后读一次 HASH。"""
    out = []
    for seed in (1, 7, 42):
        for policy in ("idle", "build"):
            lines = [f"LOAD {seed}", "INIT 0", "INIT 1"]
            for wave in range(1, 26):
                lines.append("TURN 0")
                lines.append("TURN 1")
                for bs in range(1, 6):
                    lines.append(
                        f"STEP {wave} {bs} {_p08_build(wave, bs, 0, policy)}"
                        f" | {wave} {bs} {_p08_build(wave, bs, 1, policy)}"
                    )
                lines.append("BATTLE")
                lines.append("HASH")
            lines.append("QUIT")
            out.append({"tag": f"s{seed}/{policy}", "lines": lines})
    return out


BUILDERS = {
    "p01-ballistic": p01_cases,
    "p06-core-tactics": p06_cases,
    "p07-maze-hunt": p07_cases,
    "p08-tower-duel": p08_cases,
}


# ---------------------------------------------------------------- 执行

def run_trace(engine: Path, game: Path, lines: list[str]) -> list[str]:
    """喂一段输入，按出现顺序收集所有 hash。"""
    proc = subprocess.run(
        [str(engine)], input="\n".join(lines) + "\n",
        capture_output=True, text=True, timeout=600, cwd=str(game),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"engine failed ({proc.returncode}): {proc.stderr[:1000]}")
    if not proc.stdout.startswith("OK "):
        raise RuntimeError(f"trace failed to load: {proc.stdout[:500]}")
    hashes = HEX16.findall(proc.stdout)
    if not hashes:
        raise RuntimeError("trace produced no hashes")
    return hashes


def resolve_games(spec: str) -> list[Path]:
    if spec.strip().lower() == "all":
        names = GAMES
    else:
        names = [s.strip() for s in spec.split(",") if s.strip()]
    out = []
    for n in names:
        hits = [d for d in PROBLEMS.iterdir() if d.is_dir() and d.name.startswith(n)]
        if len(hits) != 1:
            raise SystemExit(f"题目解析失败：{n} -> {[d.name for d in hits]}")
        out.append(hits[0].resolve())
    return out


def do_run(games: list[Path], json_out: str | None) -> int:
    result: dict[str, list[str]] = {}
    for game in games:
        module_name = "_crosscheck_" + game.name.replace("-", "_")
        spec = importlib.util.spec_from_file_location(module_name, game / "public/judge/judge.py")
        judge = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = judge
        spec.loader.exec_module(judge)
        engine = judge.build_engine(game)
        cases = BUILDERS[game.name](game)
        print(f"\n=== {game.name}  {len(cases)} cases ===")
        for c in cases:
            result[f"{game.name}/{c['tag']}"] = run_trace(engine, game, c["lines"])
        game_hashes = [h for c in cases for h in result[f"{game.name}/{c['tag']}"]]
        print(f"  {len(cases)} cases, {len(game_hashes)} hashes, "
              f"{len(set(game_hashes))} distinct")

    print(f"\n合计 {len(result)} 个用例 / {sum(len(v) for v in result.values())} 个 hash")
    if json_out:
        p = Path(json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"写入 {p}")
    return 0


def do_compare(a_path: str, b_path: str) -> int:
    a = json.loads(Path(a_path).read_text(encoding="utf-8"))
    b = json.loads(Path(b_path).read_text(encoding="utf-8"))

    if not a or not b or any(not trace for trace in list(a.values()) + list(b.values())):
        print("FAIL: empty cases or hash traces")
        return 1
    missing = sorted(set(a) ^ set(b))
    if missing:
        print(f"用例集合不一致：{len(missing)} 个")
        for t in missing[:10]:
            print(f"  {t}")
        return 1

    diffs = []
    for tag in a:
        ta, tb = a[tag], b[tag]
        if len(ta) != len(tb):
            diffs.append((tag, f"长度 {len(ta)} vs {len(tb)}", "", ""))
            continue
        for i, (x, y) in enumerate(zip(ta, tb)):
            if x != y:
                diffs.append((tag, f"第 {i} 个 hash", x, y))
                break

    total = sum(len(v) for v in a.values())
    if diffs:
        print(f"\nMISMATCH {len(diffs)}/{len(a)} 个用例（共 {total} 个 hash）")
        for tag, where, x, y in diffs[:10]:
            print(f"  {tag}  {where}\n    A {x}\n    B {y}")
        return 1

    same = sum(1 for t in a if a[t] == b[t])
    print(f"\nPASS  {len(a)}/{len(a)} 个用例、{total} 个 hash 完全一致（逐状态比对）")
    return 0 if same == len(a) else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="all", help="题号或目录名，逗号分隔；all = 四题")
    ap.add_argument("--json", help="把 hash 轨迹写成 JSON（跨平台比对用）")
    ap.add_argument("--compare", nargs=2, metavar=("A.json", "B.json"),
                    help="比对两份 --json 输出，不重新编译引擎")
    args = ap.parse_args()

    if args.compare:
        return do_compare(*args.compare)

    return do_run(resolve_games(args.game), args.json)


if __name__ == "__main__":
    sys.exit(main())
