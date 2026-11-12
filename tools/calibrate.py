#!/usr/bin/env python3
"""题库通用 anchor 校准（margin-based BT）。

用 `--game` 选题目；每题的 margin 单位不同（P01 是 HP 差、P06 是核心血量差……），
所以**强度只在同题内部可比**。跨题比较需要先做归一化，这一轮不做。


为什么不用普通 BT
----------------
普通 Bradley–Terry 的输入是胜负。P07 的比赛分是整数差，一颗豆就差 1 分，
强方的期望分差远大于 0 附近的波动，所以强弱一分明，弱方胜率就是 0%。
logistic 在这里会饱和，强度估计发散——实测三个 anchor 互打全是 100%。

这里的模型
----------
把"分差"当作观测量而不是把胜负当作观测量：

    E[score_i - score_j] = s_i - s_j

即强度 s 直接以"分"为单位，用加权最小二乘拟合：

    min  Σ_ij n_ij · (mean_margin_ij - (s_i - s_j))²

正规方程是一个图拉普拉斯系统 L·s = b：
    L_ii = Σ_j n_ij       L_ij = -n_ij
    b_i  = Σ_ij Σ_k margin_ijk

L 奇异（s 只定义到相差一个常数），把第一个 bot 锚定到 0 即可唯一解。

两个 bot 时这个解就是平均分差；多个 bot 时它是全局一致的最小二乘解。

等效胜率
--------
表格里的"vs X"用 p = sigmoid(margin / τ) 折算，τ 只影响显示，不影响强度。
P07 的实测胜率全是 100%，没法从数据里估 τ，所以给一个可调默认值。
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "tools"))
import bench  # noqa: E402
import harness  # noqa: E402

# margin 的单位。只用于显示，不影响拟合。
UNITS = {
    "p01-ballistic": "剩余 HP 差",
    "p06-core-tactics": "核心血量差",
    "p07-maze-hunt": "比赛分差",
    "p08-tower-duel": "核心血量差",
}


# ---------------------------------------------------------------- 线性代数

def solve_linear(A: list[list[float]], b: list[float]) -> list[float]:
    """带主元的高斯-约当消元。节点数只有个位数，不用 numpy。"""
    n = len(b)
    M = [list(A[i]) + [b[i]] for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            continue
        M[col], M[piv] = M[piv], M[col]
        pv = M[col][col]
        for c in range(col, n + 1):
            M[col][c] /= pv
        for r in range(n):
            if r == col:
                continue
            f = M[r][col]
            if f == 0.0:
                continue
            for c in range(col, n + 1):
                M[r][c] -= f * M[col][c]
    return [M[i][n] for i in range(n)]


def fit_strength(names: list[str], pairs: dict[tuple[str, str], list[int]],
                 anchor: str) -> dict[str, float]:
    """pairs[(i,j)] 是 bot_i 视角的全部分差（i 在前）。返回 {name: strength}。"""
    idx = {n: k for k, n in enumerate(names)}
    n = len(names)
    L = [[0.0] * n for _ in range(n)]
    b = [0.0] * n

    for (a, c), margins in pairs.items():
        if not margins:
            continue
        ia, ic = idx[a], idx[c]
        cnt = len(margins)
        L[ia][ia] += cnt
        L[ic][ic] += cnt
        L[ia][ic] -= cnt
        L[ic][ia] -= cnt
        total = float(sum(margins))
        b[ia] += total
        b[ic] -= total

    ai = idx[anchor]
    L[ai] = [1.0 if k == ai else 0.0 for k in range(n)]
    b[ai] = 0.0

    sol = solve_linear(L, b)
    return {name: sol[idx[name]] for name in names}


def sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


# ---------------------------------------------------------------- 对局采集

def collect(j, eng, game: Path, bots: dict[str, Path],
            pairs: list[tuple[str, str]], matches: int, seeds: list[int],
            maps: list[Path], rules_hash: str) -> tuple[dict, dict]:
    """跑 round-robin。对局由 bench.ADAPTERS[game] 执行，返回归一化的 margin。"""
    adapter = bench.ADAPTERS[game.name]
    margins: dict[tuple[str, str], list[int]] = {}
    wins: dict[tuple[str, str], int] = {}

    for a, c in pairs:
        key = (a, c)
        margins[key] = []
        wins[key] = 0
        for k in range(matches):
            mp = maps[k % len(maps)] if maps else None
            seed = seeds[k % len(seeds)]
            winner, margin, _ = adapter(j, eng, game, {0: bots[a], 1: bots[c]}, seed, mp,
                                        rules_hash)
            margins[key].append(margin)
            if winner == 0:
                wins[key] += 1
        print(f"  {a:10s} vs {c:10s}  {wins[key]:3d}/{matches}  "
              f"mean margin {statistics.mean(margins[key]):+7.1f}", flush=True)

    return margins, wins


def main() -> int:
    ap = argparse.ArgumentParser(description="题库 anchor 校准")
    ap.add_argument("--game", default="p01-ballistic", help="题号或目录名")
    ap.add_argument("--game-dir", default=None, help="兼容旧参数")
    ap.add_argument("--anchors", default="random,greedy,scripted")
    ap.add_argument("--matches", type=int, default=40)
    ap.add_argument("--seeds", default="1-40")
    ap.add_argument("--bootstrap", type=int, default=400)
    ap.add_argument("--tau", type=float, default=40.0)
    ap.add_argument("--anchor", default="random", help="锚定到 0 的 bot")
    ap.add_argument("--anchors-bin", default="", help="封装好的 anchor 目录")
    ap.add_argument("--json")
    args = ap.parse_args()

    game = Path(args.game_dir).resolve() if args.game_dir else harness.game_dir(args.game)
    if game.name not in bench.ADAPTERS:
        raise SystemExit(f"{game.name} 还没有校准适配器")
    j = bench.load_judge(game)
    eng = j.build_engine(game)
    rules_hash = json.loads((game / "meta.json").read_text(encoding="utf-8")).get("rules_hash", "x")

    names = [s.strip() for s in args.anchors.split(",") if s.strip()]
    bots: dict[str, Path] = {name: bench.build(game, j, name, args.anchors_bin)
                             for name in names}

    seeds: list[int] = []
    for chunk in args.seeds.split(","):
        if "-" in chunk:
            lo, hi = chunk.split("-", 1)
            seeds.extend(range(int(lo), int(hi) + 1))
        elif chunk.strip():
            seeds.append(int(chunk))

    maps = sorted((game / "public" / "maps").glob("*.txt"))
    pairs = [(a, c) for i, a in enumerate(names) for c in names[i + 1:]]

    print(f"game {game.name}  margin 单位 = {UNITS.get(game.name, '分差')}")
    print(f"anchors {names}")
    print(f"pairs {len(pairs)}  matches/pair {args.matches}")
    margins, wins = collect(j, eng, game, bots, pairs, args.matches, seeds, maps, rules_hash)

    strength = fit_strength(names, margins, args.anchor)

    # bootstrap：L 不变，只需重采样每对的分差
    rng = random.Random(20261101)
    boots: dict[str, list[float]] = {n: [] for n in names}
    for _ in range(args.bootstrap):
        sample: dict[tuple[str, str], list[int]] = {}
        for key, vals in margins.items():
            sample[key] = [rng.choice(vals) for _ in vals]
        s = fit_strength(names, sample, args.anchor)
        for n in names:
            boots[n].append(s[n])

    def ci(name: str) -> tuple[float, float]:
        vals = sorted(boots[name])
        lo = vals[int(0.025 * (len(vals) - 1))]
        hi = vals[int(0.975 * (len(vals) - 1))]
        return lo, hi

    # 等效胜率矩阵
    win_rate: dict[tuple[str, str], float] = {}
    for (a, c), vals in margins.items():
        win_rate[(a, c)] = wins[(a, c)] / max(1, len(vals))
        win_rate[(c, a)] = 1.0 - win_rate[(a, c)]

    eq_rate: dict[tuple[str, str], float] = {}
    for a in names:
        for c in names:
            if a == c:
                continue
            gap = strength[a] - strength[c]
            eq_rate[(a, c)] = sigmoid(gap / args.tau)

    order = sorted(names, key=lambda n: -strength[n])
    print()

    # 残差：观测平均分差 - 模型预测分差。残差大说明"分差不可传递"，
    # 最小二乘在折中，此时强度只能当序数看，不能当绝对刻度。
    resid: dict[tuple[str, str], float] = {}
    for (a, c), vals in margins.items():
        if not vals:
            continue
        obs = statistics.mean(vals)
        pred = strength[a] - strength[c]
        resid[(a, c)] = obs - pred

    header = f"{'rank':<5}{'bot':<12}{'strength':>9}{'ci95':>18}{'W-L':>10}"
    for c in order:
        header += f"{'vs ' + c:>13}"
    print(header)

    for r, name in enumerate(order, 1):
        lo, hi = ci(name)
        w = sum(wins.get((name, c), 0) + (len(margins.get((c, name), []))
                                          - wins.get((c, name), 0))
                for c in names if c != name)
        l = sum(len(margins.get((name, c), [])) + len(margins.get((c, name), []))
                for c in names if c != name) - w
        line = f"{r:<5}{name:<12}{strength[name]:>9.1f}{f'[{lo:.1f}, {hi:.1f}]':>18}{f'{w}-{l}':>10}"
        for c in order:
            line += f"{'—':>13}" if c == name else f"{win_rate.get((name, c), 0) * 100:>12.1f}%"
        print(line)

    print()
    print(f"{'pair':<26}{'observed':>10}{'fitted':>10}{'residual':>10}")
    max_abs = 0.0
    for (a, c), rv in sorted(resid.items(), key=lambda kv: -abs(kv[1])):
        obs = statistics.mean(margins[(a, c)])
        fit = strength[a] - strength[c]
        max_abs = max(max_abs, abs(rv))
        print(f"{a + ' vs ' + c:<26}{obs:>10.1f}{fit:>10.1f}{rv:>+10.1f}")

    print()
    print(f"strength 单位 = {UNITS.get(game.name, '分差')}，{args.anchor} 锚定为 0")
    print(f"equivalent win rate τ = {args.tau:.0f}（只影响显示，不影响强度）")
    spread = max(strength.values()) - min(strength.values())
    print(f"spread    {spread:.1f} 分")
    ratio = max_abs / max(1e-9, spread)
    if max_abs < 5:
        verdict = "线性拟合良好，强度可当绝对刻度"
    elif ratio < 0.05:
        verdict = "残差很小，可当近似绝对刻度"
    else:
        verdict = "存在系统偏差：分差有天花板效应，强度只能排序，不宜跨组比绝对值"
    print(f"max |residual| {max_abs:.1f} 分 ({ratio * 100:.1f}% of spread) — {verdict}")

    # 三角形完全图只有 1 个自由度：三个残差会被同一个约束绑住，
    # 所以"绝对值相等"才是异常信号，不是"同号"。
    if len(resid) == 3:
        vals = [abs(v) for v in resid.values()]
        if max(vals) - min(vals) < 1.0:
            print("              三条残差绝对值几乎相等 → 三角形只有 1 个自由度，")
            print("              这是模型形式失配（而非采样噪声）的特征。")

    if args.json:
        out = {
            "tool": "calibrate", "version": "1.0", "game": "p07-maze-hunt",
            "ok": True, "warnings": [], "errors": [],
            "data": {
                "model": "margin-based BT (least squares)",
                "anchor": args.anchor,
                "tau": args.tau,
                "matches_per_pair": args.matches,
                "strength": {n: round(v, 2) for n, v in strength.items()},
                "ci95": {n: [round(x, 2) for x in ci(n)] for n in names},
                "win_rate": {f"{a}|{c}": round(v, 4) for (a, c), v in win_rate.items()},
                "equiv_rate": {f"{a}|{c}": round(v, 4) for (a, c), v in eq_rate.items()},
                "spread": round(spread, 2),
            },
        }
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    return 0


if __name__ == "__main__":
    sys.exit(main())
