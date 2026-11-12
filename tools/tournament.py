#!/usr/bin/env python3
"""正式赛编排：赛程冻结、断点续跑、BT 排名、回放归档。

    python tools/tournament.py --game p01 --matches 2 --seeds 1-2
    python tools/tournament.py --game p06 --bots scripted,greedy,random,template --format round-robin
    python tools/tournament.py --game p07 --format swiss --rounds 3 --matches 2
    python tools/tournament.py --game p01 --anchors-bin dist/anchors/p01-ballistic --map-set hidden

和 `rating.py` 的分工
---------------------
`rating.py` 只回答"三档 anchor 谁更强、CI 多少"；本工具回答"一整届比赛怎么排"：
它多了赛程（round-robin / swiss）、冻结与续跑、逐局归档、地图/座位偏差、回放。

评分口径直接复用 `rating.fit`：胜负 + 每对一局虚拟平局，`400·log10` 刻度。
所以同一批对局，`tournament` 与 `rating` 给出的相对强度一致（差距只在赛程与样本组织）。

赛程冻结与断点续跑
------------------
`--out` 指向的 JSON 就是赛程档案，第一次运行即写入 `config` + `config_hash`。
配置同时冻结 bot/engine 二进制、地图内容、规则与裁判指纹；任一变化都拒绝续跑。
每跑完一局追加一条 `matches`；进程被杀掉后重跑，已完成的局会跳过，不会重打。
round-robin 的完整对阵在第一次运行时一次生成；swiss 的每一轮在开打前追加，
追加后同样不许改。

限制（写进 docs/known-issues.md 的 G09）
--------------------------
- swiss 的对手由上一轮结果决定，只能冻结"参数 + 已生成的轮次"，不能冻结整届对阵；
- 并行按线程实现（每一局自己起引擎进程），默认 `--workers 1`；
- 不连服务器、不发排行榜，只产出本地档案（同见 `docs/known-issues.md` 的 G09）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
import bench  # noqa: E402
import harness  # noqa: E402
import rating  # noqa: E402

SCHEMA = "bank-tournament/1"


# ---------------------------------------------------------------- 小工具

def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def bot_label(spec: str) -> str:
    p = Path(spec)
    return p.stem if p.exists() else spec


def resolve_maps(game: Path, map_set: str) -> list[Path]:
    """public / hidden / all。P08 没有地图，返回空表。"""
    pub = bench.maps_of(game)
    hidden = sorted((game / "private" / "maps_hidden").glob("*.txt"))
    if map_set == "public":
        return pub
    if map_set == "hidden":
        return hidden
    if map_set == "all":
        return pub + hidden
    raise SystemExit(f"未知 --map-set：{map_set}")


def parse_seeds(text: str, matches: int) -> list[int]:
    if not text:
        return list(range(1, max(1, matches) + 1))
    out: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.extend(range(int(lo), int(hi) + 1))
        elif part:
            out.append(int(part))
    return out


# ---------------------------------------------------------------- 赛程

def make_unit(uid: int, round_no: int | None, a: str, b: str,
              k: int, seeds: list[int], maps: list[Path], side_swap: bool) -> dict:
    sa, sb = (a, b) if (not side_swap or k % 2 == 0) else (b, a)
    return {
        "i": uid,
        "round": round_no,
        "a": sa,
        "b": sb,
        "seed": seeds[k % len(seeds)],
        # 地图按全局局号轮换：同一对局的 N 局会分散到不同图，瑞士各轮也不会挤在一张图上
        "map": maps[uid % len(maps)].stem if maps else "-",
    }


def round_robin_units(bots: list[str], matches: int, seeds: list[int],
                      maps: list[Path], side_swap: bool) -> list[dict]:
    units: list[dict] = []
    for i, a in enumerate(bots):
        for b in bots[i + 1:]:
            for k in range(matches):
                units.append(make_unit(len(units), None, a, b, k, seeds, maps, side_swap))
    return units


def swiss_round(bots: list[str], scores: dict[str, float], played: set[frozenset],
                round_no: int, uid0: int, matches: int, seeds: list[int],
                maps: list[Path], side_swap: bool) -> list[dict]:
    """一轮瑞士制：按积分排序，优先配没打过的对手。完全确定性，不用随机数。"""
    order = sorted(range(len(bots)), key=lambda i: (-scores[bots[i]], i))
    used: set[int] = set()
    pairs: list[tuple[str, str]] = []
    for i in order:
        if i in used:
            continue
        rest = [j for j in order if j not in used and j != i]
        fresh = [j for j in rest if frozenset((bots[i], bots[j])) not in played]
        j = (fresh or rest)[0]
        used.add(i)
        used.add(j)
        pairs.append((bots[i], bots[j]))
    units: list[dict] = []
    for k, (a, b) in enumerate(pairs):
        for m in range(matches):
            units.append(make_unit(uid0 + len(units), round_no, a, b, m, seeds, maps, side_swap))
    return units


def scores_of(bots: list[str], matches: list[dict]) -> dict[str, float]:
    s = dict.fromkeys(bots, 0.0)
    for m in matches:
        if m["winner"] == 0:
            s[m["a"]] += 1.0
        elif m["winner"] == 1:
            s[m["b"]] += 1.0
        else:
            s[m["a"]] += 0.5
            s[m["b"]] += 0.5
    return s


# ---------------------------------------------------------------- 执行

def run_unit(adapter, j, eng, game: Path, bots: dict[str, Path], rules_hash: str,
             unit: dict, map_paths: dict[str, Path], replay_dir: Path | None) -> dict:
    rp = None
    if replay_dir is not None:
        rp = replay_dir / (f"u{unit['i']:03d}-{unit['a']}-vs-{unit['b']}"
                           f"-{unit['map']}-s{unit['seed']}.jsonl")
    mp = map_paths.get(unit["map"])
    t0 = time.time()
    winner, margin, steps = adapter(
        j, eng, game,
        {0: bots[unit["a"]], 1: bots[unit["b"]]},
        unit["seed"], mp, rules_hash, replay_path=rp,
    )
    score = [1.0, 0.0] if winner == 0 else ([0.0, 1.0] if winner == 1 else [0.5, 0.5])
    return {
        "i": unit["i"], "round": unit["round"],
        "a": unit["a"], "b": unit["b"],
        "seed": unit["seed"], "map": unit["map"],
        "winner": winner, "score": score,
        "margin": margin, "steps": steps,
        "seconds": round(time.time() - t0, 2),
    }


# ---------------------------------------------------------------- 排名

def compute_ranking(state: dict, bootstrap: int, anchor: str = "") -> dict:
    bots: list[str] = state["config"]["bots"]
    samples: dict[tuple[str, str], list[float]] = {}
    for m in state["matches"]:
        key = tuple(sorted((m["a"], m["b"]), key=bots.index))
        first = key[0]
        if m["winner"] < 0:
            val = 0.5
        elif m["winner"] == 0:
            val = 1.0 if m["a"] == first else 0.0
        else:
            val = 1.0 if m["b"] == first else 0.0
        samples.setdefault(key, []).append(val)

    pairs = rating.to_pairs(samples, virtual_draw=True)
    strength = rating.fit(pairs, bots)

    rng = __import__("random").Random(20261102)
    boots: dict[str, list[float]] = {b: [] for b in bots}
    for _ in range(max(1, bootstrap)):
        resampled = {}
        for key, vals in samples.items():
            pick = [rng.choice(vals) for _ in vals]
            resampled[key] = (len(pick) + 1.0, sum(pick) + 0.5)
        s = rating.fit(resampled, bots, iters=800)
        for b in bots:
            boots[b].append(s[b])

    if anchor and anchor not in bots:
        raise SystemExit(f"--anchor {anchor} 不在本次 bot 列表里")
    anchor = anchor or min(bots, key=lambda b: strength[b])
    base = strength[anchor]

    def scaled(v: float) -> float:
        return 400.0 * math.log10(max(v, 1e-12) / base)

    def ci(b: str) -> list[float]:
        vals = sorted(scaled(v) for v in boots[b]) or [scaled(strength[b])]
        return [round(vals[int(0.025 * (len(vals) - 1))], 1),
                round(vals[int(0.975 * (len(vals) - 1))], 1)]

    wld = {b: [0, 0, 0] for b in bots}
    for m in state["matches"]:
        if m["winner"] == 0:
            wld[m["a"]][0] += 1
            wld[m["b"]][1] += 1
        elif m["winner"] == 1:
            wld[m["b"]][0] += 1
            wld[m["a"]][1] += 1
        else:
            wld[m["a"]][2] += 1
            wld[m["b"]][2] += 1

    matrix = {b: {o: None for o in bots} for b in bots}
    for (a, b), vals in samples.items():
        if not vals:
            continue
        matrix[a][b] = round(sum(vals) / len(vals), 3)
        matrix[b][a] = round(sum(1 - v for v in vals) / len(vals), 3)

    by_map: dict[str, list[int]] = {}
    by_side = [0, 0]
    for m in state["matches"]:
        if m["winner"] < 0:
            continue
        rec = by_map.setdefault(m["map"], [0, 0])
        rec[1] += 1
        by_side[1] += 1
        if m["winner"] == 0:
            rec[0] += 1
            by_side[0] += 1

    def rate(w: int, n: int) -> float:
        return round(w / n, 3) if n else 0.0

    map_rows = {k: {"seat0_wins": v[0], "decided": v[1], "seat0_rate": rate(*v)}
                for k, v in sorted(by_map.items())}
    bias = {
        "max_map": round(max((abs(v["seat0_rate"] - 0.5) * 100 for v in map_rows.values()),
                             default=0.0), 1),
        "side": round(abs(rate(*by_side) - 0.5) * 100, 1),
        "seat0_rate": rate(*by_side),
        "decided": by_side[1],
        "reliable": by_side[1] >= 10,
    }

    ranking = []
    for b in sorted(bots, key=lambda x: -strength[x]):
        ranking.append({
            "bot": b,
            "strength": round(scaled(strength[b]), 1),
            "ci95": ci(b),
            "w": wld[b][0], "l": wld[b][1], "d": wld[b][2],
        })
    for i, r in enumerate(ranking, 1):
        r["rank"] = i

    return {
        "bootstrap": bootstrap,
        "anchor": anchor,
        "smoothed": True,
        "ranking": ranking,
        "matrix": matrix,
        "by_map": map_rows,
        "bias": bias,
        "draws": sum(1 for m in state["matches"] if m["winner"] < 0),
    }


def print_table(rank: dict) -> None:
    bots = [r["bot"] for r in rank["ranking"]]
    head = f"{'rank':<5}{'bot':<14}{'strength':>10}{'ci95':>20}  {'W-L-D':<13}"
    head += "".join(f"{'vs ' + b:<14}" for b in bots)
    print(head)
    for r in rank["ranking"]:
        lo, hi = r["ci95"]
        record = f"{r['w']}-{r['l']}-{r['d']}"
        row = (f"{r['rank']:<5}{r['bot']:<14}{r['strength']:>10.1f}"
               f"{f'[{lo:.0f}, {hi:.0f}]':>20}  {record:<13}")
        for b in bots:
            v = rank["matrix"][r["bot"]][b]
            cell = "—" if v is None else f"{v * 100:.0f}%"
            row += f"{cell:<14}"
        print(row)

    print()
    if rank["by_map"]:
        cells = "  ".join(f"{k} {v['seat0_rate'] * 100:.0f}%" for k, v in rank["by_map"].items())
        print(f"map bias  {cells}  (max {rank['bias']['max_map']}%)")
    print(f"side bias seat0 {rank['bias']['seat0_rate'] * 100:.1f}%  "
          f"(dev {rank['bias']['side']}%)")
    print(f"draws     {rank['draws']}/{rank['bias']['decided']}")
    if not rank["bias"]["reliable"]:
        print(f"NOTE 已分胜负的局只有 {rank['bias']['decided']} 局，偏差/提示都只是占位，"
              "别据此调图。")
    elif rank["bias"]["side"] > 5.0:
        print("WARN 座位偏差 >5%：先查 map/座位统计，再决定是否加赛。")
    if rank["draws"]:
        print("WARN 有平局：本工具按 0.5 计入 BT，排名不做抓阄。")


def write_report(path: Path, state: dict, rank: dict) -> None:
    c = state["config"]
    lines = [
        f"# {state['game']} 正式赛评分",
        "",
        "## 条件",
        f"- 版本 / rules_hash / engine_hash：{state['version']} / "
        f"{state['rules_hash']} / {state['engine_hash']}",
        f"- 赛制：{c['format']}，每对 {c['matches_per_pair']} 局，seed {c['seeds']}",
        f"- 地图：{', '.join(c['maps']) or '（无地图）'}（{c['map_set']}）",
        f"- 换边：{'开' if c['side_swap'] else '关'}；bootstrap {rank['bootstrap']}",
        f"- 赛程 hash：{state['config_hash']}",
        "",
        "## 排名",
        "| 排名 | bot | 强度 | 95% CI | W-L-D |",
        "|---:|---|---:|---|---:|",
    ]
    for r in rank["ranking"]:
        lines.append(f"| {r['rank']} | {r['bot']} | {r['strength']:.1f} | "
                     f"[{r['ci95'][0]:.0f}, {r['ci95'][1]:.0f}] | {r['w']}-{r['l']}-{r['d']} |")
    lines += ["", "## 对位胜率", ""]
    bots = [r["bot"] for r in rank["ranking"]]
    lines.append("| | " + " | ".join(bots) + " |")
    lines.append("|---|" + "---:|" * len(bots))
    for a in bots:
        cells = []
        for b in bots:
            v = rank["matrix"][a][b]
            cells.append("—" if v is None else f"{v * 100:.0f}%")
        lines.append(f"| {a} | " + " | ".join(cells) + " |")
    lines += ["", "## 偏差", "",
              f"- 地图最大偏差：{rank['bias']['max_map']}%",
              f"- 座位偏差：seat0 {rank['bias']['seat0_rate'] * 100:.1f}%"
              f"（dev {rank['bias']['side']}%）",
              f"- 平局：{rank['draws']}", "", "## 结论", ""]
    lines.append("- 强度只能在本届对手池内比较，不能跨题、跨池比绝对值。")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_html(path: Path, state: dict, rank: dict) -> None:
    def esc(s: str) -> str:
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{:.1f}</td><td>[{:.0f}, {:.0f}]</td>"
        "<td>{}-{}-{}</td></tr>".format(r["rank"], esc(r["bot"]), r["strength"],
                                       r["ci95"][0], r["ci95"][1], r["w"], r["l"], r["d"])
        for r in rank["ranking"])
    bots = [r["bot"] for r in rank["ranking"]]
    head = "".join(f"<th>{esc(b)}</th>" for b in bots)
    body = "".join(
        "<tr><td>{}</td>{}</tr>".format(
            esc(a), "".join("<td>—</td>" if rank['matrix'][a][b] is None
                            else f"<td>{rank['matrix'][a][b] * 100:.0f}%</td>"
                            for b in bots))
        for a in bots)
    html = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>{esc(state['game'])} 正式赛</title>
<style>
body{{font:14px/1.6 system-ui,sans-serif;margin:32px;color:#222}}
h1{{font-size:22px}} table{{border-collapse:collapse;margin:12px 0}}
th,td{{border:1px solid #ddd;padding:4px 10px;text-align:right}}
th:first-child,td:first-child{{text-align:left}}
small{{color:#777}}
</style>
<h1>{esc(state['game'])} 正式赛</h1>
<small>{esc(state['version'])} · {esc(state['config_hash'])} ·
{esc(state['config']['format'])} · 每对 {state['config']['matches_per_pair']} 局</small>
<h2>排名</h2>
<table><tr><th>#</th><th>bot</th><th>强度</th><th>95% CI</th><th>W-L-D</th></tr>{rows}</table>
<h2>对位胜率</h2>
<table><tr><th></th>{head}</tr>{body}</table>
<small>强度只能在本届对手池内比较。</small></html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")


# ---------------------------------------------------------------- 主流程

def config_of(args, bots: list[str], maps: list[Path], seeds: list[int]) -> dict:
    return {
        "format": args.format,
        "bots": bots,
        "matches_per_pair": args.matches,
        "seeds": seeds,
        "side_swap": bool(args.side_swap),
        "map_set": args.map_set,
        "maps": [m.stem for m in maps],
        "rounds": args.rounds,
    }


def artifact_fingerprints(game: Path, meta: dict, engine: Path,
                          bots: dict[str, Path], maps: list[Path]) -> dict:
    """冻结实际参赛产物；路径不进指纹，档案可移动到另一目录续跑。"""
    return {
        "version": meta.get("version", "?"),
        "rules_hash": meta.get("rules_hash", "?"),
        "engine_hash": meta.get("engine_hash", "?"),
        "judge_sha256": sha256_file(game / "public" / "judge" / "judge.py"),
        "engine_binary_sha256": sha256_file(engine),
        "bot_binary_sha256": {name: sha256_file(path) for name, path in bots.items()},
        "map_sha256": {mp.stem: sha256_file(mp) for mp in maps},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="正式赛编排（赛程冻结 / 断点续跑 / BT 排名）")
    ap.add_argument("--game", default="p01-ballistic")
    ap.add_argument("--bots", default="", help="逗号分隔；默认读 meta.json 的 anchors")
    ap.add_argument("--anchors", action="store_true", help="强制只跑 meta.json 的 anchors")
    ap.add_argument("--format", choices=("round-robin", "swiss"), default="round-robin")
    ap.add_argument("--matches", type=int, default=10, help="每对局数")
    ap.add_argument("--seeds", default="")
    ap.add_argument("--side-swap", action="store_true", help="每对局交换先手")
    ap.add_argument("--map-set", choices=("public", "hidden", "all"), default="public")
    ap.add_argument("--rounds", type=int, default=0, help="swiss 轮数；默认 max(3, ceil(log2 n))")
    ap.add_argument("--bootstrap", type=int, default=200)
    ap.add_argument("--workers", type=int, default=1, help="并行线程数（每局自起引擎进程）")
    ap.add_argument("--anchor", default="", help="锚定为 0 的 bot；默认最弱")
    ap.add_argument("--anchors-bin", default="", help="封装好的 anchor 目录")
    ap.add_argument("--out", default="", help="赛程档案；默认 results/<game>/tournament.json")
    ap.add_argument("--replays", default="", help="每局回放目录（默认不写回放）")
    ap.add_argument("--report", default="", help="Markdown 报告路径")
    ap.add_argument("--json", default="", help="机器可读结果路径")
    ap.add_argument("--html", default="", help="单文件 HTML 报告路径")
    ap.add_argument("--reset", action="store_true", help="丢弃已有档案，重新生成赛程")
    args = ap.parse_args()

    game = harness.game_dir(args.game)
    if game.name not in bench.ADAPTERS:
        raise SystemExit(f"{game.name} 还没有 tournament 适配器")

    meta = json.loads((game / "meta.json").read_text(encoding="utf-8"))
    if args.anchors or not args.bots.strip():
        specs = list(meta.get("anchors", []))
    else:
        specs = [s.strip() for s in args.bots.split(",") if s.strip()]
    if len(specs) < 2:
        raise SystemExit("至少需要两个 bot")
    bots = [bot_label(s) for s in specs]
    if len(set(bots)) != len(bots):
        raise SystemExit(f"bot 名字重复：{bots}")
    if args.format == "swiss" and len(bots) % 2 != 0:
        raise SystemExit("swiss 需要偶数个 bot（本工具不做 bye）")

    maps = resolve_maps(game, args.map_set)
    seeds = parse_seeds(args.seeds, args.matches)

    # 先构建实际产物再冻结配置；同名换二进制、改图或改裁判都不能续用旧档案。
    j = bench.load_judge(game)
    eng = j.build_engine(game)
    rules_hash = meta.get("rules_hash", "x")
    built = {name: bench.build(game, j, spec, args.anchors_bin)
             for name, spec in zip(bots, specs)}
    map_paths = {m.stem: m for m in maps}

    config = config_of(args, bots, maps, seeds)
    config["artifacts"] = artifact_fingerprints(game, meta, eng, built, maps)
    config_hash = sha256_text(json.dumps(config, ensure_ascii=False, sort_keys=True))

    out = Path(args.out) if args.out else REPO / "results" / game.name / "tournament.json"
    if args.reset and out.exists():
        out.unlink()

    if out.exists():
        state = json.loads(out.read_text(encoding="utf-8"))
        if state.get("schema") != SCHEMA:
            raise SystemExit(f"{out} 不是本工具的档案（schema={state.get('schema')}）")
        if state.get("config_hash") != config_hash:
            raise SystemExit(
                "赛程与参赛产物已冻结，参数或文件指纹与档案不一致：\n"
                f"  档案 {state.get('config')}\n  本次 {config}\n"
                "请使用新的 --out；--reset 会显式丢弃已有对局。"
            )
    else:
        state = {
            "schema": SCHEMA,
            "game": game.name,
            "version": meta.get("version", "?"),
            "rules_hash": meta.get("rules_hash", "?"),
            "engine_hash": meta.get("engine_hash", "?"),
            "config": config,
            "config_hash": config_hash,
            "units": [],
            "matches": [],
            "ranking": {},
        }
        if args.format == "round-robin":
            state["units"] = round_robin_units(bots, args.matches, seeds, maps,
                                               bool(args.side_swap))
        atomic_write(out, state)
        print(f"新建赛程 {out}  hash {config_hash}  {len(state['units'])} 局")

    replay_dir = Path(args.replays) if args.replays else None
    rounds_max = args.rounds or max(3, math.ceil(math.log2(len(bots))))

    adapter = bench.ADAPTERS[game.name]
    print(f"{game.name} {state['version']}  {config['format']}  "
          f"{len(bots)} bots  {len(state['units']) or '?'} 局  seed {seeds}")
    print(f"bots {', '.join(bots)}   地图 {', '.join(config['maps']) or '无'}")
    t_start = time.time()

    def play(pending: list[dict]) -> None:
        done = {m["i"] for m in state["matches"]}
        todo = [u for u in pending if u["i"] not in done]
        if not todo:
            return
        print(f"  待跑 {len(todo)} 局（已完成 {len(done)}）", flush=True)
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
            futs = {ex.submit(run_unit, adapter, j, eng, game, built, rules_hash,
                              u, map_paths, replay_dir): u for u in todo}
            for n, fut in enumerate(as_completed(futs), 1):
                rec = fut.result()
                state["matches"].append(rec)
                state["matches"].sort(key=lambda m: m["i"])
                atomic_write(out, state)
                if n % 10 == 0 or n == len(todo):
                    print(f"  {n}/{len(todo)} 局", flush=True)

    if config["format"] == "round-robin":
        play(state["units"])
    else:
        played = {frozenset((m["a"], m["b"])) for m in state["matches"]}
        for r in range(rounds_max):
            if any(u["round"] == r for u in state["units"]):
                play([u for u in state["units"] if u["round"] == r])
                played |= {frozenset((m["a"], m["b"])) for m in state["matches"]}
                continue
            current = scores_of(bots, state["matches"])
            uid0 = len(state["units"])
            state["units"] += swiss_round(bots, current, played, r, uid0,
                                          args.matches, seeds, maps, bool(args.side_swap))
            atomic_write(out, state)
            print(f"瑞士第 {r + 1} 轮：{len(state['units']) - uid0} 局")
            play([u for u in state["units"] if u["round"] == r])
            played |= {frozenset((m["a"], m["b"])) for m in state["matches"]}

    rank = compute_ranking(state, args.bootstrap, args.anchor)
    state["ranking"] = rank
    atomic_write(out, state)
    elapsed = time.time() - t_start

    print()
    print_table(rank)
    print(f"\n档案 {out}   用时 {elapsed:.1f}s   "
          f"每局均 {statistics.mean([m['seconds'] for m in state['matches']]):.2f}s")

    if args.report:
        write_report(Path(args.report), state, rank)
        print(f"报告 {args.report}")
    if args.html:
        write_html(Path(args.html), state, rank)
        print(f"HTML {args.html}")
    if args.json:
        p = Path(args.json)
        atomic_write(p, {
            "tool": "tournament", "game": game.name, "version": state["version"],
            "format": config["format"], "matches_per_pair": config["matches_per_pair"],
            "bootstrap": rank["bootstrap"], "config_hash": config_hash,
            "anchor": rank["anchor"], "ranking": rank["ranking"],
            "matrix": rank["matrix"], "by_map": rank["by_map"],
            "bias": rank["bias"], "draws": rank["draws"],
        })
        print(f"JSON {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
