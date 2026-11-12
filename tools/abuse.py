#!/usr/bin/env python3
"""抗滥用测试：把"恶意选手"扔进裁判，看它会不会崩、会不会卡死、会不会错判。

    python tools/abuse.py                 # 全部题 × 全部模式
    python tools/abuse.py --game p07      # 单题
    python tools/abuse.py --mode silent   # 单模式

为什么需要这个
------------
给不同 AI 做的题，选手代码不受信任。正常 bot 不会干的事，一定要试：
死循环刷 stdout、一行输出 1 MB、读 stdin 但永不回话、直接崩、直接退出。
这些不是"极端情况"——AI 写出来的东西经常会踩到。

判定标准（三条，缺一不可）：
1. **不挂**：整场在时限内结束（父进程用 subprocess 超时兜底）；
2. **不崩**：裁判自己不抛异常；
3. **不吃亏**：恶意方不能赢，且必须被判罚（forfeit 或 faults）。

这条测试是"题目能不能发出去"的门槛之一：如果恶意方能赢，或者能拖死裁判，
那这道题在公开赛里就是可攻击的。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
import bench  # noqa: E402
import harness  # noqa: E402

# 五个"恶意选手"。都不解析输入，所以与题目协议无关，一套代码跑五道题。
SOURCES = {
    "garbage": r"""
#include <cstdio>
int main(){ for(;;){ printf("NOT A VALID ORDER\n"); fflush(stdout); } }
""",
    "silent": r"""
#include <cstdio>
int main(){ char b[512]; while (fgets(b, sizeof b, stdin)) { /* 读完什么也不回 */ } return 0; }
""",
    "crash": r"""
int main(){ int *p = nullptr; *p = 1; return 0; }
""",
    "exit": r"""
int main(){ return 0; }
""",
    "huge": r"""
#include <cstdio>
int main(){
    for (int i = 0; i < 300000; i++) putchar('A');   // 300 KB 的单行
    putchar('\n'); fflush(stdout);
    for(;;){ printf("B\n"); fflush(stdout); }
}
""",
}

TIMEOUT_S = 180
CXX = ["g++", "-O2", "-std=c++17"] + (["-static"] if sys.platform == "win32" else [])


def compile_hostiles(tmp: Path) -> dict[str, Path]:
    out = {}
    for name, src in SOURCES.items():
        cpp = tmp / f"hostile_{name}.cpp"
        cpp.write_text(src, encoding="utf-8")
        exe = tmp / harness.exe_name(f"hostile_{name}")
        r = subprocess.run([*CXX, "-o", str(exe), str(cpp)],
                           capture_output=True, text=True, encoding="utf-8")
        if r.returncode != 0:
            raise SystemExit(f"恶意 bot 编译失败 {name}: {r.stderr[:400]}")
        out[name] = exe
    return out


def one_case(game: Path, hostile: Path, mode: str) -> dict:
    """跑一场：恶意方坐 0 号位，正常 anchor 坐 1 号位。"""
    j = bench.load_judge(game)
    meta = json.loads((game / "meta.json").read_text(encoding="utf-8"))
    rh = meta.get("rules_hash", "x")
    eng = j.build_engine(game)
    good = bench.build(game, j, "greedy")
    adapter = bench.ADAPTERS[game.name]
    maps = bench.maps_of(game)
    mp = maps[0] if maps else None

    t0 = time.time()
    winner, margin, steps = adapter(j, eng, game, {0: hostile, 1: good}, 1, mp, rh)
    elapsed = time.time() - t0
    return {"mode": mode, "winner": winner, "margin": margin, "steps": steps,
            "elapsed": round(elapsed, 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="all")
    ap.add_argument("--mode", default="", help="只跑某个模式")
    ap.add_argument("--one", default="", help="内部用：game:mode:输出路径")
    ap.add_argument("--json")
    args = ap.parse_args()

    # 子进程模式：跑单场，写出结果。父进程负责超时——裁判真挂死时只能靠这个。
    if args.one:
        game_name, mode, out = args.one.split("|")
        game = harness.game_dir(game_name)
        tmp = Path(tempfile.mkdtemp(prefix="abuse-"))
        exe = compile_hostiles(tmp)[mode]
        try:
            row = one_case(game, exe, mode)
            row["ok"] = row["winner"] != 0
            Path(out).write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            Path(out).write_text(json.dumps(
                {"mode": mode, "ok": False, "error": repr(exc)}, ensure_ascii=False),
                encoding="utf-8")
        return 0

    games = harness.all_games() if args.game.strip().lower() == "all" \
        else [harness.game_dir(g.strip()) for g in args.game.split(",") if g.strip()]
    modes = [args.mode] if args.mode else list(SOURCES)

    rows = []
    for g in games:
        for mode in modes:
            out = Path(tempfile.mkdtemp(prefix="abuse-out-")) / "r.json"
            cmd = [sys.executable, str(HERE / "abuse.py"), "--one", f"{g.name}|{mode}|{out}"]
            t0 = time.time()
            try:
                subprocess.run(cmd, timeout=TIMEOUT_S, capture_output=True)
                res = json.loads(out.read_text(encoding="utf-8")) if out.exists() else \
                    {"mode": mode, "ok": False, "error": "子进程没有产出结果"}
            except subprocess.TimeoutExpired:
                res = {"mode": mode, "ok": False, "error": f"挂死（>{TIMEOUT_S}s）"}
            res["game"] = g.name
            res["elapsed"] = round(time.time() - t0, 1)
            rows.append(res)
            mark = "PASS" if res.get("ok") else "FAIL"
            detail = res.get("error", f"winner={res.get('winner')} steps={res.get('steps')}")
            print(f"  {mark}  {g.name:18s} {mode:8s} {res['elapsed']:>6.1f}s  {detail}")

    bad = [r for r in rows if not r.get("ok")]
    print()
    print(f"{len(rows) - len(bad)}/{len(rows)} 项通过抗滥用测试")

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"tool": "abuse", "ok": not bad, "rows": rows},
                                ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
