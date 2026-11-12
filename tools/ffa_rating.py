#!/usr/bin/env python3
"""P01 FFA 三列评分：pairwise Elo + 4-way win% + avg place。

为什么要三列，而不是只算一个 Elo
--------------------------------
P01 是 4 人 FFA，但"Elo"是两人胜负的语言。把一局拆成 6 个两两比较，
拟合出来的分数校准的是**两人胜率**；4 人局的真实胜率是另一个量。
两者排序在 round3 上完全相同，但绝对分不能互相解释：

  · Elo（pairwise winner-only BT）：校准两人胜率，和老榜连续；
  · win%（4 人 softmax / Plackett-Luce 第一段）：校准四人局胜率；
  · avg place：按 ffa4.places 的代理名次（HP/伤害/存活），描述表现。

输入
----
`--run` 可以是：
  · 一个 p01-eval run 目录（读 results/combo-*.jsonl），或
  · 一个 JSONL 文件（每行一局，字段见下）。

每局至少需要：`bots`（4 个名字，或给 `--bots`）、`winner_bot`（-1 = 平局）、
`perm`（bot 下标 -> 座位）、`place`（按座位的名次）、`map`。
可选：`reason`（FORFEIT 用于 clean Elo）、`hp`、`dealt`、`faults_by_bot`。

输出
----
Markdown 表 + JSON。默认按 win% 排序；`--rank-by elo|place` 可换。
不做 BT 拆对的 rank 模型；avg place 已经承担"表现"这一列。
"""
from __future__ import annotations

import argparse
import json
import math
import random
from collections import defaultdict
from pathlib import Path

ELO_C = 400.0 / math.log(10.0)


def wilson(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 0.0
    p = wins / n
    den = 1 + z * z / n
    center = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, center - half), min(1.0, center + half)


def pct(x: float) -> float:
    return x * 100.0


def read_rows(path: Path, exclude: set[str], bots_arg: list[str]) -> tuple[list[dict], int]:
    if path.is_dir():
        files = sorted(path.glob("results/combo-*.jsonl"))
        if not files:
            files = sorted(path.glob("*.jsonl"))
    else:
        files = [path]
    rows: list[dict] = []
    skipped = 0
    for f in files:
        for line in f.open(encoding="utf-8"):
            if not line.strip():
                continue
            r = json.loads(line)
            if not r.get("ok"):
                skipped += 1
                continue
            bots = r.get("bots") or bots_arg
            if len(bots) != 4 or any(b in exclude for b in bots):
                continue
            if r.get("winner_bot") is None or "place" not in r:
                skipped += 1
                continue
            rows.append(r)
    return rows, skipped


def build_maps(rows: list[dict], bots: list[str]):
    idx = {b: i for i, b in enumerate(bots)}
    n = len(bots)
    per_map = defaultdict(lambda: {
        "pn": [[0.0] * n for _ in range(n)],
        "pw": [[0.0] * n for _ in range(n)],
        "lineups": [], "winners": [],
        "games": [0] * n, "wins": [0] * n,
        "place_sum": [0.0] * n, "place_cnt": [0] * n,
        "hp_sum": [0.0] * n, "dealt_sum": [0.0] * n,
    })
    draws = forfeits = 0
    for r in rows:
        names = r["bots"]
        members = [idx[b] for b in names]
        winner = int(r.get("winner_bot", -1))
        perm = r.get("perm") or [0, 1, 2, 3]
        place = r.get("place")
        d = per_map[r["map"]]
        if winner < 0:
            draws += 1
        if r.get("reason") == "FORFEIT" or int(r.get("forfeit", -1)) >= 0:
            forfeits += 1
        for a in range(4):
            for b in range(a + 1, 4):
                ia, ib = members[a], members[b]
                d["pn"][ia][ib] += 1
                d["pn"][ib][ia] += 1
                if winner == a:
                    d["pw"][ia][ib] += 1
                elif winner == b:
                    d["pw"][ib][ia] += 1
                else:
                    d["pw"][ia][ib] += 0.5
                    d["pw"][ib][ia] += 0.5
        if winner >= 0:
            d["lineups"].append(tuple(members))
            d["winners"].append(members[winner])
        for i, b in enumerate(members):
            d["games"][b] += 1
            if winner == i:
                d["wins"][b] += 1
            if place is not None and len(place) >= 4:
                p = place[perm[i]]
                if p is not None:
                    d["place_sum"][b] += p
                    d["place_cnt"][b] += 1
                    if r.get("hp") and len(r["hp"]) >= 4:
                        d["hp_sum"][b] += r["hp"][perm[i]]
                    if r.get("dealt") and len(r["dealt"]) >= 4:
                        d["dealt_sum"][b] += r["dealt"][perm[i]]
    return per_map, draws, forfeits


def fit_bt(pn, pw, anchor: int, iters: int = 5000) -> list[float]:
    n = len(pn)
    gamma = [1.0] * n
    for _ in range(iters):
        new = [0.0] * n
        for i in range(n):
            wi = sum(pw[i])
            den = 0.0
            gi = gamma[i]
            for j in range(n):
                nij = pn[i][j]
                if nij:
                    den += nij / (gi + gamma[j])
            new[i] = max(wi, 1e-12) / den if den > 0 else gamma[i]
        logs = [math.log(max(v, 1e-300)) for v in new]
        shift = math.exp(-sum(logs) / n)
        new = [v * shift for v in new]
        diff = max(abs(math.log(max(new[i], 1e-300) / max(gamma[i], 1e-300)))
                   for i in range(n))
        gamma = new
        if diff < 1e-11:
            break
    r = [ELO_C * math.log(max(v, 1e-300)) for v in gamma]
    off = r[anchor]
    return [v - off for v in r]


def fit_softmax(lineups, winners, n: int, gamma0=None,
                iters: int = 500):
    gamma = [1.0] * n if gamma0 is None else list(gamma0)
    wc = [0] * n
    for w in winners:
        wc[w] += 1
    for _ in range(iters):
        den = [0.0] * n
        for lu in lineups:
            a, b, c, d = lu
            s = gamma[a] + gamma[b] + gamma[c] + gamma[d]
            inv = 1.0 / s
            den[a] += inv
            den[b] += inv
            den[c] += inv
            den[d] += inv
        new = gamma[:]
        for i in range(n):
            if den[i] > 0:
                new[i] = max(wc[i], 1e-12) / den[i]
        logs = [math.log(max(v, 1e-300)) for v in new]
        shift = math.exp(-sum(logs) / n)
        new = [v * shift for v in new]
        diff = max(abs(math.log(max(new[i], 1e-300) / max(gamma[i], 1e-300)))
                   for i in range(n))
        gamma = new
        if diff < 1e-11:
            break
    r = [ELO_C * math.log(max(v, 1e-300)) for v in gamma]
    return r, gamma


def win_probs(lineups, gamma, n: int) -> list[float]:
    pred = [0.0] * n
    cnt = [0] * n
    for lu in lineups:
        a, b, c, d = lu
        s = gamma[a] + gamma[b] + gamma[c] + gamma[d]
        inv = 1.0 / s
        pred[a] += gamma[a] * inv
        pred[b] += gamma[b] * inv
        pred[c] += gamma[c] * inv
        pred[d] += gamma[d] * inv
        cnt[a] += 1
        cnt[b] += 1
        cnt[c] += 1
        cnt[d] += 1
    return [pred[i] / cnt[i] if cnt[i] else 0.0 for i in range(n)]


def percentile(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * (len(xs) - 1)))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="run 目录或 JSONL 文件")
    ap.add_argument("--bots", default="", help="JSONL 里没有 bots 字段时，逗号分隔的 bot 顺序")
    ap.add_argument("--exclude", default="", help="逗号分隔；含这些 bot 的局整局跳过")
    ap.add_argument("--anchor", default="strong4", help="Elo 的 0 分基准")
    ap.add_argument("--boot", type=int, default=200, help="按地图 bootstrap 次数；0 = 只出点估计")
    ap.add_argument("--seed", type=int, default=20261105)
    ap.add_argument("--rank-by", choices=["win", "elo", "place"], default="win")
    ap.add_argument("--legacy", action="store_true",
                    help="输出旧三列口径（Wilson/Elo/avg place）；默认输出 v2 口径（problems/p01-ballistic/docs/rating-design.md §3–4）")
    ap.add_argument("--json", default="")
    ap.add_argument("--md", default="")
    args = ap.parse_args()

    exclude = {x for x in args.exclude.split(",") if x.strip()}
    bots_arg = [x for x in args.bots.split(",") if x.strip()]
    rows, skipped = read_rows(Path(args.run), exclude, bots_arg)
    if not rows:
        raise SystemExit("没有可用对局")
    bots = sorted({b for r in rows for b in r["bots"]})
    if args.anchor not in bots:
        raise SystemExit(f"anchor {args.anchor!r} 不在名单里：{', '.join(bots)}")
    ai = bots.index(args.anchor)
    per_map, draws, forfeits = build_maps(rows, bots)
    maps = [per_map[m] for m in sorted(per_map)]

    # point estimates
    n = len(bots)
    pn = [[0.0] * n for _ in range(n)]
    pw = [[0.0] * n for _ in range(n)]
    lineups, winners = [], []
    games = [0] * n
    wins = [0] * n
    place_sum = [0.0] * n
    place_cnt = [0] * n
    hp_sum = [0.0] * n
    dealt_sum = [0.0] * n
    for d in maps:
        for i in range(n):
            for j in range(n):
                pn[i][j] += d["pn"][i][j]
                pw[i][j] += d["pw"][i][j]
            games[i] += d["games"][i]
            wins[i] += d["wins"][i]
            place_sum[i] += d["place_sum"][i]
            place_cnt[i] += d["place_cnt"][i]
            hp_sum[i] += d["hp_sum"][i]
            dealt_sum[i] += d["dealt_sum"][i]
        lineups.extend(d["lineups"])
        winners.extend(d["winners"])

    elo = fit_bt(pn, pw, ai)
    _, gamma = fit_softmax(lineups, winners, n)
    win = win_probs(lineups, gamma, n)
    place = [place_sum[i] / place_cnt[i] if place_cnt[i] else None for i in range(n)]
    raw = [wins[i] / games[i] if games[i] else 0.0 for i in range(n)]

    # clean Elo: drop FORFEIT games
    clean_elo = elo
    if forfeits:
        clean_rows = [r for r in rows
                      if not (r.get("reason") == "FORFEIT" or int(r.get("forfeit", -1)) >= 0)]
        if clean_rows:
            cm, _, _ = build_maps(clean_rows, bots)
            cpn = [[0.0] * n for _ in range(n)]
            cpw = [[0.0] * n for _ in range(n)]
            for d in cm.values():
                for i in range(n):
                    for j in range(n):
                        cpn[i][j] += d["pn"][i][j]
                        cpw[i][j] += d["pw"][i][j]
            clean_elo = fit_bt(cpn, cpw, ai)

    # bootstrap by map
    samples = {"elo": [[] for _ in range(n)], "win": [[] for _ in range(n)],
               "place": [[] for _ in range(n)]}
    if args.boot > 0 and len(maps) >= 2:
        rng = random.Random(args.seed)
        for _ in range(args.boot):
            pick = [maps[rng.randrange(len(maps))] for _ in range(len(maps))]
            bpn = [[0.0] * n for _ in range(n)]
            bpw = [[0.0] * n for _ in range(n)]
            blineups, bwinners = [], []
            bps = [0.0] * n
            bpc = [0] * n
            for d in pick:
                for i in range(n):
                    for j in range(n):
                        bpn[i][j] += d["pn"][i][j]
                        bpw[i][j] += d["pw"][i][j]
                    bps[i] += d["place_sum"][i]
                    bpc[i] += d["place_cnt"][i]
                blineups.extend(d["lineups"])
                bwinners.extend(d["winners"])
            be = fit_bt(bpn, bpw, ai)
            _, bg = fit_softmax(blineups, bwinners, n, gamma0=gamma)
            bw = win_probs(blineups, bg, n)
            for i in range(n):
                samples["elo"][i].append(be[i])
                samples["win"][i].append(bw[i])
                samples["place"][i].append(bps[i] / bpc[i] if bpc[i] else 0.0)

    ci = {}
    for k in samples:
        ci[k] = [[percentile(v, 0.025), percentile(v, 0.975)] if v else [None, None]
                 for v in samples[k]]

    # ordering
    if args.rank_by == "elo":
        order = sorted(range(n), key=lambda i: (-elo[i], -win[i]))
    elif args.rank_by == "place":
        order = sorted(range(n), key=lambda i: (place[i] if place[i] is not None else 99, -win[i]))
    else:
        order = sorted(range(n), key=lambda i: (-win[i], -elo[i]))

    # ---- v2 口径（problems/p01-ballistic/docs/rating-design.md §3–4）：主分 + 部分序 ----
    # 固定 panel 下 win 的 MLE 就是原始胜率（不拟合）；成对判决直接用既有
    # bootstrap 样本配对做差（同一 rep 内两 bot 的 win% 相减），不重新拟合。
    def v2_verdict(a, b):
        d = win[a] - win[b]
        if args.boot and samples["win"][a] and samples["win"][b]:
            dl = sorted(x - y for x, y in zip(samples["win"][a], samples["win"][b]))
            lo, hi = percentile(dl, 0.025), percentile(dl, 0.975)
            p_ab = sum(1 for x in dl if x > 0) / len(dl)
            return d, lo, hi, p_ab, (lo > 0 or hi < 0)
        return d, None, None, None, None

    win_order = sorted(range(n), key=lambda i: (-win[i], -elo[i]))
    seg_ids = {win_order[0]: 1}
    seg = 1
    for k in range(1, n):
        a, b = win_order[k - 1], win_order[k]
        if v2_verdict(a, b)[4] is not True:
            seg_ids[b] = seg      # False=未分辨 / None=无置信区间：都不分段
        else:
            seg += 1
            seg_ids[b] = seg

    def v2_needed_n(d, lo, hi, n_obs):
        if lo is None:
            return None
        delta = abs(d)
        se = max((hi - lo) / 3.92, 1e-9)
        if delta < 0.01:
            return "同档（Δ<1pp）"
        return "需≈%d局" % int(round(max(1, n_obs) * (2.0 / max(delta / se, 1e-9)) ** 2))

    all_pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            d, lo, hi, p_ab, resolved = v2_verdict(i, j)
            all_pairs.append({"a": bots[i], "b": bots[j],
                              "delta": round(d, 5),
                              "ci": ([round(lo, 5), round(hi, 5)] if lo is not None else None),
                              "p_ab": (round(p_ab, 4) if p_ab is not None else None),
                              "resolved": resolved})

    payload = {
        "tool": "ffa-rating",
        "run": str(Path(args.run)),
        "bots": bots, "anchor": args.anchor,
        "games": len(rows), "maps": len(maps), "skipped": skipped,
        "draws": draws, "forfeits": forfeits, "boot": args.boot,
        "rows": [{
            "bot": b,
            "win_model": round(win[i], 5),
            "win_ci": ([round(ci["win"][i][0], 5), round(ci["win"][i][1], 5)]
                       if ci["win"][i][0] is not None else None),
            "win_raw": round(raw[i], 5),
            "wilson95": [round(x, 5) for x in wilson(wins[i], games[i])],
            "elo": round(elo[i], 1),
            "elo_ci": ([round(ci["elo"][i][0], 1), round(ci["elo"][i][1], 1)]
                       if ci["elo"][i][0] is not None else None),
            "elo_clean": round(clean_elo[i], 1),
            "avg_place": round(place[i], 3) if place[i] is not None else None,
            "place_ci": ([round(ci["place"][i][0], 3), round(ci["place"][i][1], 3)]
                         if ci["place"][i][0] is not None else None),
            "wins": wins[i], "games": games[i],
            "mean_hp": round(hp_sum[i] / place_cnt[i], 2) if place_cnt[i] else None,
            "mean_dealt": round(dealt_sum[i] / place_cnt[i], 2) if place_cnt[i] else None,
        } for i, b in enumerate(bots)],
        "ranking_by_win": [bots[i] for i in sorted(range(n), key=lambda i: (-win[i], -elo[i]))],
        "ranking_by_elo": [bots[i] for i in sorted(range(n), key=lambda i: (-elo[i], -win[i]))],
        "rating_version": 2,
        "score": "win_rate (panel multinomial MLE; no fitting, no pairwise decomposition)",
        "segments": [{"segment": seg_ids[i], "bot": bots[i]} for i in win_order],
        "pairs": all_pairs,
        "note": "v2: win% is the rating; CI by map-cluster bootstrap; segments mark "
                "statistically unresolved adjacency. Legacy columns (Wilson/Elo/avg place) "
                "only with --legacy.",
    }

    # markdown
    title_rank = {"win": "win%", "elo": "Elo", "place": "avg place"}[args.rank_by]
    lines = [
        "# P01 FFA 三列评分",
        "",
        f"- run: `{args.run}`",
        f"- {len(bots)} bots / {len(maps)} maps / {len(rows)} games"
        + (f"（跳过 {skipped} 行）" if skipped else ""),
        f"- anchor: **{args.anchor} = 0**（只影响 Elo；排序与 CI 不受影响）",
        f"- CI: 按地图 cluster bootstrap {args.boot} 次"
        + ("" if args.boot else "（未跑）"),
        f"- draws: {draws}（softmax 排除；Elo 记半胜）; forfeits: {forfeits}",
        "",
        f"按 **{title_rank}** 排序：",
        "",
        "| # | bot | 4-way win% (95% CI) | pairwise Elo (95% CI) | avg place | W/G | HP/局 | 伤害/局 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for rank, i in enumerate(order, 1):
        b = bots[i]
        wlo, whi = ci["win"][i]
        elo_lo, elo_hi = ci["elo"][i]
        wci = (f"{pct(win[i]):.2f}% [{pct(wlo):.2f}, {pct(whi):.2f}]"
               if wlo is not None else f"{pct(win[i]):.2f}%")
        eci = f"{elo[i]:.1f} [{elo_lo:.1f}, {elo_hi:.1f}]" if elo_lo is not None else f"{elo[i]:.1f}"
        lines.append(
            f"| {rank} | {b} | {wci} | {eci} | "
            f"{place[i]:.2f} | {wins[i]}/{games[i]} | "
            f"{(hp_sum[i] / place_cnt[i] if place_cnt[i] else 0):.1f} | "
            f"{(dealt_sum[i] / place_cnt[i] if place_cnt[i] else 0):.1f} |")
    lines += [
        "",
        "> win% 是 4 人 softmax 在**本次 field** 上的平均预测胜率；换对手池会变，不能跨 panel 比。",
        "> Elo 是 pairwise winner-only BT，校准的是两人胜率，不是四人胜率；0 = anchor。",
        "> avg place 是 `ffa4.places` 的代理名次（HP/伤害/存活），不是官方名次。",
    ]
    if forfeits and clean_elo != elo:
        diffs = sorted(abs(clean_elo[i] - elo[i]) for i in range(n))
        lines.append(f"> clean Elo（排除 {forfeits} 局 FORFEIT）最大变化 {diffs[-1]:.1f} 分；详见 JSON。")

    legacy_md = "\n".join(lines) + "\n"

    # ---- v2 表（problems/p01-ballistic/docs/rating-design.md §3–4）----
    v2_lines = [
        "# P01 Rating v2（problems/p01-ballistic/docs/rating-design.md §3–4，run 口径）",
        "",
        f"- run: `{args.run}`",
        f"- {len(bots)} bots / {len(maps)} maps / {len(rows)} games"
        + (f"（跳过 {skipped} 行）" if skipped else ""),
        "- 主分 = panel 内胜率（固定阵容下即多项 MLE；不拟合、不拆对、不做 BT）",
        "- Wilson 已废除（假设独立局、低估不确定性）；CI = 按地图 cluster bootstrap",
        f"- draws: {draws}（不计入分母）; forfeits: {forfeits}",
        "",
        "| 段 | bot | W/G | win% (95% CI) | 相邻对判决 | dealt/局 | HP/局(参考) |",
        "|---:|---|---:|---:|---|---:|---:|",
    ]
    for rank, i in enumerate(win_order):
        b = bots[i]
        wlo, whi = ci["win"][i]
        wci = (f"{pct(win[i]):.2f}% [{pct(wlo):.2f}, {pct(whi):.2f}]"
               if wlo is not None else f"{pct(win[i]):.2f}%")
        verdict = ""
        if rank > 0:
            a = win_order[rank - 1]
            d, lo, hi, _p, resolved = v2_verdict(a, i)
            if resolved is None:
                verdict = "—"
            elif resolved:
                verdict = "与 %s 已分辨 (Δ=%.1fpp)" % (bots[a], pct(abs(d)))
            else:
                extra = v2_needed_n(d, lo, hi, games[a])
                verdict = "与 %s 未分辨 (Δ=%.1fpp%s)" % (
                    bots[a], pct(abs(d)), "，" + extra if extra else "")
        v2_lines.append(
            f"| {seg_ids[i]} | {b} | {wins[i]}/{games[i]} | {wci} | {verdict} | "
            f"{(dealt_sum[i] / place_cnt[i] if place_cnt[i] else 0):.1f} | "
            f"{(hp_sum[i] / place_cnt[i] if place_cnt[i] else 0):.1f} |")
    v2_lines += [
        "",
        "> 「段」相同 = 该相邻对胜率差在本样本下不可分辨（不是「相等」）；",
        "> 未分辨对标了 2σ 口径所需局数，同档（Δ<1pp）给多少局也分不开。",
        "> HP/局有机制性胜者偏置，仅参考；完整过程画像（命中率/资源效率/破坏/淘汰序）",
        "> 需要回放输入 —— 用 `problems/p01-ballistic/tools/p01_rating.py --replays <dir>`。",
        "> 旧三列口径（Wilson/Elo/avg place）加 `--legacy`。",
    ]
    v2_md = "\n".join(v2_lines) + "\n"
    md = legacy_md if args.legacy else v2_md
    if args.md:
        Path(args.md).write_text(md, encoding="utf-8")
    else:
        print(md)
    if args.json:
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
