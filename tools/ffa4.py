#!/usr/bin/env python3
"""P01 v2 FFA（2/3/4 席）对局运行器：rating4 / anchor_audit4 共用。

复用 public/judge/judge4.py 的 play_one / rotations / resolve_bot，不复制引擎协议。
judge4 的 play_one 只导出 winner/reason/rounds/faults，这里用两个进程内包装补出：

- 包装 ``Bot.ask``：按 bot 记录超时/无应答次数（judge4 原来只汇总总 faults）；
- 包装 ``_cmd``：截获最后一条 STATE，读出每座最终 HP、伤害、存活数。

两个包装都只读观测，不改 judge4.py，也不改引擎协议；代价是依赖 judge4 的内部名
（``Bot`` / ``_cmd``），judge4 重构时需要同步这里。若拿不到 STATE，hp/名次记为
None，胜率仍可用。

编译产物沿用 judge4 的约定 problems/p01-ballistic/build；
不要与其他会重编同一 build 目录的任务并发跑。
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

import harness  # noqa: E402

GAME = "p01-ballistic"
DEFAULT_ANCHORS = ["random4", "greedy4", "scripted4", "strong4"]


def load_judge4(game: Path):
    path = game / "public" / "judge" / "judge4.py"
    if not path.is_file():
        raise SystemExit(f"找不到 judge4：{path}")
    spec = importlib.util.spec_from_file_location("p01_judge4_ffa", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"无法加载 judge4：{path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def parse_seeds(text: str) -> list[int]:
    out: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.extend(range(int(lo), int(hi) + 1))
        else:
            out.append(int(part))
    return out


def pick_maps(game: Path, explicit: list[str], maps_dir: str = "",
              hidden: bool = False) -> list[Path]:
    if explicit:
        found: list[Path] = []
        for raw in explicit:
            p = Path(raw)
            if p.is_absolute() and p.is_file():
                found.append(p.resolve())
                continue
            for base in (Path.cwd(), game, REPO,
                         game / "public" / "maps4", game / "private" / "maps4_hidden"):
                cand = base / p
                if cand.is_file():
                    found.append(cand.resolve())
                    break
            else:
                raise SystemExit(f"找不到地图：{raw}")
        return found
    d = Path(maps_dir) if maps_dir else (
        game / "private" / "maps4_hidden" if hidden else game / "public" / "maps4")
    if not d.is_absolute():
        d = REPO / d
    files = sorted(d.glob("*.txt"))
    if not files:
        raise SystemExit(f"地图目录为空：{d}")
    return files


def map_source(args) -> str:
    if args.maps_dir:
        return args.maps_dir
    if args.map:
        return "--map"
    return "private/maps4_hidden" if args.hidden else "public/maps4"


def resolve_anchors(game: Path, spec: str, seats: int) -> list[str]:
    names = [x.strip() for x in spec.split(",") if x.strip()]
    if not names:
        meta = json.loads((game / "meta.json").read_text(encoding="utf-8"))
        names = list(meta.get("anchors4") or DEFAULT_ANCHORS)[:seats]
    if len(names) != seats:
        raise SystemExit(f"--seats {seats} 需要 {seats} 个 anchor，收到 {len(names)} 个：{names}")
    return names


def wilson(wins: int, games: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson 95% 胜率区间；n=0 时返回 [0, 1]。"""
    if games <= 0:
        return 0.0, 1.0
    p = wins / games
    den = 1.0 + z * z / games
    center = (p + z * z / (2 * games)) / den
    half = z * math.sqrt(p * (1 - p) / games + z * z / (4 * games * games)) / den
    return max(0.0, center - half), min(1.0, center + half)


def parse_state(line: str) -> dict | None:
    """解析引擎 STATE 行；4 席布局 92 字段，旧 2 席布局 50 字段。"""
    p = line.split()
    if len(p) < 20 or p[0] != "STATE":
        return None
    if len(p) >= 92:
        hp = [int(x) for x in p[6:10]]
        dealt = [int(x) for x in p[10:14]]
        reason = p[14]
        base, nworm = 20, 12
    else:
        hp = [int(p[6]), int(p[7]), 0, 0]
        dealt = [int(p[8]), int(p[9]), 0, 0]
        reason = p[10]
        base, nworm = 14, 6
    alive = [0] * 4
    for i in range(nworm):
        o = base + i * 6
        if o + 5 >= len(p):
            break
        if int(p[o + 5]) == 1:
            alive[i // 3] += 1
    return {"round": int(p[1]), "winner": int(p[5]), "reason": reason,
            "hp": hp, "dealt": dealt, "alive": alive}


def places(hp: list[int], dealt: list[int], alive: list[int],
           active: tuple[int, ...]) -> dict[int, int]:
    """按引擎超时结算键 (HP, 伤害, 存活数) 排 1..n。

    只用于描述性名次：淘汰局里未胜者都是 0 HP，其先后由该键分开，
    不是官方名次，也不代表淘汰时间。并列共享靠前名次（competition ranking）。
    """
    key = {s: (hp[s], dealt[s], alive[s]) for s in active}
    return {s: 1 + sum(1 for t in active if key[t] > key[s]) for s in active}


class Runner:
    def __init__(self, game: Path, names: list[str], seats: int = 4,
                 timeout: float = 0.5, jobs: int = 1):
        # jobs：并发度。只影响 CPU 预算的并发补偿（judge4.concurrency_factor），
        # wall 余量另有 self.wall_allow_s 这条账。
        self.jobs = max(1, int(jobs))
        if seats < 2 or seats > 4:
            raise SystemExit("--seats 只支持 2/3/4")
        if len(names) != seats:
            raise SystemExit(f"--seats {seats} 需要 {seats} 个 anchor，收到 {len(names)} 个")
        self.game = game
        self.names = names
        self.seats = seats
        self.active = (0, 2) if seats == 2 else tuple(range(seats))
        self.timeout = timeout
        self.j4 = load_judge4(game)
        self.j = self.j4._judge()
        self.engine = self.j.build_engine(game)
        srcs = [self.j4.resolve_bot(n, game) for n in names]
        # anchors4 的公共头与源码同目录（引号 include 自动找到）；arena4.hpp 走 -I public
        self.built = [self.j.build_bot(p, game / "build", game / "public")
                      for p in srcs]
        self._instrument()

    def _instrument(self) -> None:
        j4 = self.j4
        if getattr(j4, "_ffa4_instrumented", False):
            raise RuntimeError("judge4 已包装过；同进程请复用同一个 Runner")
        j4._ffa4_instrumented = True
        created: list = []
        orig_ask = j4.Bot.ask

        class TrackedBot(j4.Bot):
            # v2.4.3：judge4.Bot 多了 jobs 参数（并发补偿 c(jobs) 用它算 CPU 预算），
            # 这里必须透传，否则 --jobs 下的预算会退回单进程口径。
            def __init__(self, exe, cpu_meter=None, wall_allow_s=0.0, jobs=1):
                super().__init__(exe, cpu_meter, wall_allow_s=wall_allow_s, jobs=jobs)
                self.fault_count = 0
                created.append(self)

            def ask(self, text, timeout):
                answer = orig_ask(self, text, timeout)
                if answer is None:
                    self.fault_count += 1
                return answer

        j4.Bot = TrackedBot
        box: dict[str, str | None] = {"state": None}
        orig_cmd = j4._cmd

        def cmd(proc, line):
            out = orig_cmd(proc, line)
            if out.startswith("STATE"):
                box["state"] = out
            return out

        j4._cmd = cmd
        self._created = created
        self._box = box

    @property
    def perms(self) -> list[tuple[int, ...]]:
        return self.j4.rotations(self.active)

    def plan(self, seeds_per_perm: int = 2, perms_per_map: int = 0) -> list:
        """judge4.schedule 的原样赛程：[(perm, seed_index, seed_absolute), ...]。

        ★ 2026-11-07 实测：同一张图所有 perm 共用一个 seed = 一套风程序，
          24 个"旋转"其实是同一局换座位名字；4 个相同 bot 会产生 106/108 张
          图只有单一座次能赢。所以默认让 perm 之间换 seed（2 个起步）。
        ★ 第二个字段是"第几批"（0/1），第三个才是全局自增的绝对 seed 下标；
          取 seed 要用第三个 —— 用第二个会让一张图的 48 局只剩 2 个 seed。
        """
        n = perms_per_map if perms_per_map > 0 else len(self.perms)
        return self.j4.schedule(self.active, n, max(1, seeds_per_perm))

    def play(self, mp: Path, seed: int, perm: tuple[int, ...]) -> dict:
        self._created.clear()
        self._box["state"] = None
        try:
            r = self.j4.play_one(self.j, self.engine, mp, seed, self.built,
                                 self.active, perm, self.timeout,
                                 wall_allow_s=getattr(self, "wall_allow_s", 0.0),
                                 jobs=getattr(self, "jobs", 1))
        except Exception as exc:  # 单局异常不打断整批
            r = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        row = {
            "map": mp.stem, "seed": seed, "perm": list(perm),
            "ok": bool(r.get("ok")), "error": r.get("error", ""),
            "winner": r.get("winner", -1), "winner_bot": r.get("winner_bot", -1),
            "reason": r.get("reason", "?"), "rounds": r.get("rounds", 0),
            "faults": r.get("faults", 0),
            "faults_by_bot": [b.fault_count for b in self._created],
        }
        st = parse_state(self._box["state"]) if self._box["state"] else None
        if st is None:
            return row
        row.update({"hp": st["hp"], "dealt": st["dealt"], "alive": st["alive"]})
        place = places(st["hp"], st["dealt"], st["alive"], self.active)
        row["place"] = [place.get(s) for s in range(4)]
        if row["winner"] >= 0 and place[row["winner"]] != 1:
            row["place_mismatch"] = True
        return row

    def iter_matches(self, maps: list[Path], seeds: list[int],
                     seeds_per_perm: int = 2, perms_per_map: int = 0):
        """默认：邻接平衡的排列 + perm 之间换 seed。"""
        plan = self.plan(seeds_per_perm, perms_per_map)
        for mp in maps:
            for seed in seeds:
                # 第三个字段是绝对 seed 下标（0..len(plan)-1），不是 0/1 的批次号。
                for perm, _seed_index, seed_abs in plan:
                    yield self.play(mp, seed + seed_abs, perm)


def bot_records(row: dict, count: int) -> list[dict]:
    """一局展开成每个 bot 一条记录（顺序与 names 一致）。"""
    out = []
    for i in range(count):
        seat = row["perm"][i]
        rec = {
            "bot_index": i, "seat": seat,
            "won": row.get("winner_bot", -1) == i,
            "draw": row.get("winner_bot", -1) < 0,
            "faults": row["faults_by_bot"][i] if i < len(row["faults_by_bot"]) else 0,
        }
        if "place" in row:
            rec["place"] = row["place"][seat]
            rec["hp"] = row["hp"][seat]
            rec["dealt"] = row["dealt"][seat]
        out.append(rec)
    return out


def aggregate(rows: list[dict], names: list[str]) -> list[dict]:
    """按 bot 汇总；未完成的局跳过。"""
    stats = [{
        "games": 0, "wins": 0, "draws": 0, "faults": 0,
        "place_sum": 0, "place_games": 0, "place_counts": [0] * len(names),
        "hp_sum": 0, "dealt_sum": 0,
        "seat_games": [0] * 4, "seat_wins": [0] * 4,
    } for _ in names]
    for row in rows:
        if not row.get("ok"):
            continue
        for rec in bot_records(row, len(names)):
            s = stats[rec["bot_index"]]
            s["games"] += 1
            if rec["won"]:
                s["wins"] += 1
            if rec["draw"]:
                s["draws"] += 1
            s["faults"] += rec["faults"]
            seat = rec["seat"]
            s["seat_games"][seat] += 1
            if rec["won"]:
                s["seat_wins"][seat] += 1
            if "place" in rec:
                s["place_games"] += 1
                s["place_sum"] += rec["place"]
                s["place_counts"][rec["place"] - 1] += 1
                s["hp_sum"] += rec["hp"]
                s["dealt_sum"] += rec["dealt"]
    return stats


def summary(name: str, s: dict) -> dict:
    games = s["games"]
    pg = s["place_games"]
    lo, hi = wilson(s["wins"], games)
    return {
        "anchor": name,
        "games": games, "wins": s["wins"], "draws": s["draws"],
        "win_rate": round(s["wins"] / games, 4) if games else 0.0,
        "wilson95": [round(lo, 4), round(hi, 4)],
        "avg_place": round(s["place_sum"] / pg, 2) if pg else None,
        "place_counts": list(s["place_counts"]),
        "mean_hp": round(s["hp_sum"] / pg, 1) if pg else None,
        "mean_dealt": round(s["dealt_sum"] / pg, 1) if pg else None,
        "faults": s["faults"],
        "seat_win_rate": [
            {"seat": i, "wins": s["seat_wins"][i], "games": s["seat_games"][i],
             "rate": round(s["seat_wins"][i] / s["seat_games"][i], 4)
             if s["seat_games"][i] else 0.0}
            for i in range(4)
        ],
    }