#!/usr/bin/env python3
"""题库回放工具：统一 schema 的逐帧重算 + 单文件 HTML 查看器。

    python tools/replay.py verify --input problems/p01-ballistic/dist/replay.jsonl
    python tools/replay.py html   --input problems/p01-ballistic/dist/replay.jsonl \
                                 --out out/p01-replay.html
    python tools/replay.py verify --input replays/ --json out/verify.json

统一 schema（JSONL，每行一个记录）：

    {"type":"header","game":"p01-ballistic","version":"2.0.0",
     "rules_hash":"sha256:...","engine_hash":"sha256:...","seed":1,"map":"public-01"}
    {"type":"map","id":"public-01","grid":[...原始地图...]}
    {"type":"game","label":"A","first_seat":0,"t_seat":0,"runner_seat":0,"seat_bots":[0,1]}
    {"type":"turn","t":1,"seat":0,"orders":{"0":"...","1":"..."},"state_hash":"...",
     "state":{"hp":[300,300],"damage":[0,0],"survivors":[3,3],"faults":[0,0]}}
    {"type":"wave","wave":1,"acts0":[...],"acts1":[...],"leak":[0,0],"core":[40,40],
     "steps":10,"state_hash":"..."}
    {"type":"result",...}

`turn` 的 `seat` 只在“一步一方”的题（P06）出现；P08 用 `wave` 而不是 `turn`。
P01 的 `game.initial_state` 保存首个动作前的完整状态（`t=1`）；`turn.state` 保存每次
结算后的快照。`turn.state` 中 `hp/core/score` 用于数值面板，`grid/worms/mechs/runner/ghosts`
用于按帧绘制实时地形与单位，`wind/shots` 用于 P01 弹道动画。旧回放没有这些字段时
退回静态地图和 `result` 终局值。
重算只依赖 `map` + `game` 参数 + `turn/wave` 里的动作——`state_hash` 是用来校验的，
不是用来重建的。查看器只读这份 JSONL，不启动引擎。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import tempfile
from html import escape
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PROBLEMS = REPO / "problems"


# ------------------------------------------------------------------ 读取

def load_replay(path: Path) -> dict:
    rep = {"header": {}, "map": {}, "games": [], "turns": [], "waves": [], "result": {}}
    current: dict | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        t = rec.get("type")
        if t == "header":
            rep["header"] = rec
        elif t == "map":
            rep["map"] = rec
        elif t == "game":
            current = {"label": rec.get("label", ""),
                       "first_seat": rec.get("first_seat"),
                       "t_seat": rec.get("t_seat"),
                       "runner_seat": rec.get("runner_seat"),
                       "seat_bots": rec.get("seat_bots"),
                       "initial_state": rec.get("initial_state"),
                       "turns": []}
            rep["games"].append(current)
        elif t == "turn":
            if current is None:
                # 兼容旧 P07 回放：没有 game 分隔行，按 t 回绕切成两半
                current = {"label": "", "first_seat": None, "t_seat": None,
                           "runner_seat": None, "seat_bots": None, "turns": []}
                rep["games"].append(current)
            current["turns"].append(rec)
            rep["turns"].append(rec)
        elif t == "wave":
            rep["waves"].append(rec)
        elif t == "result":
            rep["result"] = rec
    if rep["games"] and not rep["games"][-1]["turns"]:
        rep["games"].pop()
    return rep


def judge_module(game: Path):
    p = game / "public" / "judge" / "judge.py"
    name = "_replay_" + game.name.replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, p)
    if spec is None or spec.loader is None:
        raise SystemExit(f"无法加载裁判：{p}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def write_map(rep: dict, tmp: Path, tag: str) -> Path:
    grid = rep["map"].get("grid")
    if not grid:
        raise SystemExit("回放缺少 map 行，无法重算")
    p = tmp / f"{rep['header'].get('game', 'game')}-{tag}.txt"
    p.write_text("\n".join(grid) + "\n", encoding="utf-8", newline="\n")
    return p


# ------------------------------------------------------------------ 重算

class Report:
    def __init__(self, game: str, path: Path) -> None:
        self.out = {
            "tool": "replay", "ok": True, "game": game, "input": str(path),
            "frames": 0, "games": [], "hash_mismatch": [], "result_mismatch": [],
            "engine_hash": "", "current_engine_hash": "",
        }

    def frame(self, label: str, t: int, want: str, got: str) -> None:
        self.out["frames"] += 1
        if want and want != got:
            self.out["hash_mismatch"].append({"game": label, "t": t,
                                              "want": want, "got": got})
            self.out["ok"] = False

    def mismatch(self, what: str, detail: str) -> None:
        self.out["result_mismatch"].append({"what": what, "detail": detail})
        self.out["ok"] = False


def _v_p01(j, exe: Path, rep: dict, tmp: Path, r: Report) -> None:
    seed = int(rep["header"].get("seed", 0))
    for i, g in enumerate(rep["games"]):
        mp = write_map(rep, tmp, g.get("label") or str(i))
        e = j.Engine(exe)
        last = None
        try:
            e.load(mp, seed)
            for turn in g["turns"]:
                last = e.step(turn["orders"]["0"], turn["orders"]["1"])
                r.frame(g.get("label") or str(i), turn["t"], turn.get("state_hash"),
                        last["hash"])
        finally:
            e.close()
        if last is not None:
            r.out["games"].append({"label": g.get("label") or str(i),
                                   "turns": len(g["turns"]),
                                   "final_hp": last["hp"], "final_damage": last["damage"]})
            want = (rep["result"].get("games") or [])[i] if rep["result"].get("games") else None
            if want and last["hp"] != want.get("hp"):
                r.mismatch(f"game {i} hp", f"replay {want.get('hp')} vs engine {last['hp']}")


def _v_p06(j, exe: Path, rep: dict, tmp: Path, r: Report) -> None:
    total_core = [0, 0]
    complete = True
    for i, g in enumerate(rep["games"]):
        mp = write_map(rep, tmp, g.get("label") or str(i))
        e = j.Engine(exe)
        last = None
        try:
            e.load(mp, strict=False, first_seat=g.get("first_seat") or 0,
                   seed=g.get("seed") or rep.get("header", {}).get("seed") or 0)
            for turn in g["turns"]:
                order = turn["orders"][str(turn["seat"])]
                last = e.step(int(order[0]), order[1], order[2], order[3])
                r.frame(g.get("label") or str(i), turn["t"], turn.get("state_hash"),
                        last["hash"])
        finally:
            e.close()
        if last is not None:
            r.out["games"].append({"label": g.get("label") or str(i),
                                   "turns": len(g["turns"]), "final_core": last["core"]})
            recorded = (rep["result"].get("games") or [])
            want = recorded[i] if i < len(recorded) else None
            if want and last["core"] != want.get("core"):
                r.mismatch(f"game {i} core", f"replay {want.get('core')} vs engine {last['core']}")
            mapping = g.get("seat_bots") or [0, 1]
            if sorted(mapping) != [0, 1]:
                raise ValueError("invalid seat_bots mapping")
            for seat in (0, 1):
                total_core[mapping[seat]] += last["core"][seat]
        else:
            complete = False
    want_total = rep["result"].get("total_core")
    if complete and want_total is not None and total_core != want_total:
        r.mismatch("match total_core", f"replay {want_total} vs engine {total_core}")


def _v_p07(j, exe: Path, rep: dict, tmp: Path, r: Report) -> None:
    for i, g in enumerate(rep["games"]):
        mp = write_map(rep, tmp, g.get("label") or str(i))
        e = j.Engine(exe)
        last = None
        try:
            e.load(mp, seed=g.get("seed") or rep.get("header", {}).get("seed") or 0)
            rs = g.get("runner_seat")
            if rs is None:
                rs = 0 if i == 0 else 1
            for turn in g["turns"]:
                rdir = turn["orders"][str(rs)]
                ghosts = list(turn["orders"][str(1 - rs)])
                last, _ = e.step(rdir, ghosts)
                r.frame(g.get("label") or str(i), turn["t"], turn.get("state_hash"),
                        last["hash"])
        finally:
            e.close()
        if last is not None:
            r.out["games"].append({"label": g.get("label") or str(i),
                                   "turns": len(g["turns"]), "final_score": last["score"],
                                   "final_hp": last["hp"]})


def _v_p08(j, exe: Path, rep: dict, tmp: Path, r: Report) -> None:
    e = j.Engine(exe)
    try:
        e.load(rep.get("header", {}).get("seed") or 0)
        for w in rep["waves"]:
            for a0, a1 in zip(w["acts0"], w["acts1"]):
                e.step(a0, a1)
            b = e.battle()
            if b["leak"] != w["leak"]:
                r.mismatch(f"wave {w['wave']} leak", f"replay {w['leak']} vs engine {b['leak']}")
            if b["core"] != w["core"]:
                r.mismatch(f"wave {w['wave']} core", f"replay {w['core']} vs engine {b['core']}")
            h = e.hash()
            r.frame("A", w["wave"], w.get("state_hash"), h)
    finally:
        e.close()
    r.out["games"].append({"label": "A", "waves": len(rep["waves"])})


ADAPTERS = {
    "p01-ballistic": _v_p01,
    "p06-core-tactics": _v_p06,
    "p07-maze-hunt": _v_p07,
    "p08-tower-duel": _v_p08,
}


def verify(path: Path) -> dict:
    rep = load_replay(path)
    game = rep["header"].get("game")
    if not game:
        raise SystemExit(f"{path} 缺少 header.game")
    game_dir = PROBLEMS / game
    if not game_dir.is_dir():
        raise SystemExit(f"找不到题目目录：{game}")
    meta = json.loads((game_dir / "meta.json").read_text(encoding="utf-8"))
    r = Report(game, path)
    r.out["engine_hash"] = rep["header"].get("engine_hash", "")
    r.out["current_engine_hash"] = meta.get("engine_hash", "")
    j = judge_module(game_dir)
    exe = j.build_engine(game_dir)
    with tempfile.TemporaryDirectory(prefix="replay-") as td:
        ADAPTERS[game](j, exe, rep, Path(td), r)
    if r.out["engine_hash"] and r.out["engine_hash"] != r.out["current_engine_hash"]:
        # 旧回放：不直接判失败，但必须显式说清楚
        r.out["stale_engine"] = True
    return r.out


# ------------------------------------------------------------------ HTML

HTML = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>REPLAY · __GAME__</title>
<style>
:root{
  --bg:#1a2837;--bg0:#131f2b;--panel:#223346;--panel2:#2a3e53;
  --line:#3b5268;--line2:#4d6880;
  --fg:#eaf3fb;--dim:#abc0d2;--dim2:#8299ad;
  --p0:#22d3ee;--p1:#fb923c;--gold:#fbbf24;--bad:#f87171;--ok:#4ade80;
  --accent:#22d3ee;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
}
*{box-sizing:border-box}
html,body{margin:0;min-height:100%}
body{background:
  radial-gradient(1000px 560px at 14% -10%,rgba(34,211,238,.10),transparent 60%),
  radial-gradient(900px 520px at 86% -12%,rgba(251,146,60,.09),transparent 62%),
  linear-gradient(180deg,#1c2b3b,#16222f 70%,#131e29);
  color:var(--fg);font:14px/1.5 var(--mono);-webkit-font-smoothing:antialiased}
header{display:flex;align-items:center;gap:14px;flex-wrap:wrap;padding:12px 22px;
  border-bottom:1px solid var(--line);
  background:linear-gradient(180deg,rgba(38,56,74,.92),rgba(26,40,55,.62))}
.brand{display:flex;align-items:baseline;gap:10px}
.logo{background:linear-gradient(120deg,var(--p0),var(--p1));color:#07131b;font-weight:700;
  font-size:11px;letter-spacing:.14em;padding:3px 8px;border-radius:4px}
h1{margin:0;font-size:17px;font-weight:600;letter-spacing:.08em}
.ver{font-size:11px;color:var(--dim)}
.meta{display:flex;gap:7px;flex-wrap:wrap;margin-left:8px}
.tag{font-size:11px;color:var(--dim);border:1px solid var(--line);border-radius:999px;
  padding:2px 9px;background:rgba(34,51,70,.65)}
.tag b{color:var(--fg);font-weight:500}
.clock{margin-left:auto;display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.phase{font-size:12px;font-weight:600;letter-spacing:.07em;color:#07131b;
  background:linear-gradient(120deg,var(--p0),#7dd3fc);padding:3px 10px;border-radius:999px}
.pill{display:inline-block;padding:1px 8px;border:1px solid var(--line);border-radius:999px;
  font-size:11px;color:var(--dim)}
.progress{height:4px;background:#0f1a24;border-bottom:1px solid var(--line)}
.progress i{display:block;height:100%;width:0;
  background:linear-gradient(90deg,var(--p0),#a5f3fc 55%,var(--p1));transition:width .16s ease}
main{display:grid;grid-template-columns:minmax(0,1fr) 372px;align-items:start;
  min-height:calc(100vh - 190px)}
#stage{padding:18px 20px 26px;display:flex;flex-direction:column;align-items:center;
  justify-content:center;gap:12px;min-width:0;min-height:calc(100vh - 230px)}
#boardWrap{position:relative;width:fit-content;max-width:100%;border:1px solid var(--line2);
  border-radius:10px;background:#0e1822;overflow:hidden;
  box-shadow:0 24px 60px -30px rgba(0,0,0,.85),inset 0 0 0 1px rgba(255,255,255,.03)}
canvas{display:block;width:auto;max-width:100%;height:auto;image-rendering:pixelated;
  margin:0 auto;background:#0d1721}
#fx{position:absolute;inset:0;width:100%;height:100%;background:transparent;
  pointer-events:none;image-rendering:pixelated;z-index:2}
#hud{position:absolute;inset:0;pointer-events:none;display:flex;justify-content:space-between;
  align-items:flex-start;padding:9px 11px;font-size:11px;letter-spacing:.07em}
#hud span{background:rgba(12,22,32,.82);border:1px solid rgba(77,104,128,.9);
  padding:3px 9px;border-radius:999px;color:#dbeaf7;white-space:nowrap;
  max-width:48%;overflow:hidden;text-overflow:ellipsis}
#hudLeft{color:#fff;font-weight:600}
#flash{position:absolute;inset:0;pointer-events:none;opacity:0}
#flash.on.leak{animation:flashBad .42s ease-out}
#flash.on.core{animation:flashCore .42s ease-out}
#flash.on.switch{animation:flashSwitch .38s ease-out}
@keyframes flashBad{0%{opacity:.9;background:radial-gradient(80% 70% at 50% 50%,rgba(248,113,113,.45),transparent 72%)}100%{opacity:0}}
@keyframes flashCore{0%{opacity:.85;background:radial-gradient(80% 70% at 50% 50%,rgba(251,191,36,.32),transparent 72%)}100%{opacity:0}}
@keyframes flashSwitch{0%{opacity:.7;background:radial-gradient(90% 80% at 50% 50%,rgba(34,211,238,.24),transparent 72%)}100%{opacity:0}}
#banner{position:absolute;left:50%;top:44px;transform:translate(-50%,-8px);opacity:0;
  pointer-events:none;transition:opacity .28s ease,transform .28s ease;text-align:center;
  padding:10px 24px;border-radius:10px;border:1px solid var(--bc,#fbbf24);
  background:linear-gradient(180deg,rgba(10,20,30,.92),rgba(10,20,30,.62));
  box-shadow:0 0 34px -8px var(--bc,#fbbf24),inset 0 0 0 1px rgba(255,255,255,.04)}
#banner.on{opacity:1;transform:translate(-50%,0)}
#banner .bt{font-size:clamp(20px,2.4vw,30px);font-weight:700;letter-spacing:.14em;color:#fff;
  text-shadow:0 0 18px var(--bc,#fbbf24)}
#banner .bs{margin-top:2px;font-size:11px;color:var(--dim);letter-spacing:.08em}
#legend{display:flex;gap:7px;flex-wrap:wrap;justify-content:center}
.lg{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);border-radius:999px;
  padding:3px 10px;background:rgba(22,37,52,.8);font-size:11px;color:var(--dim)}
.lg .dot{width:9px;height:9px;border-radius:3px;background:var(--c);box-shadow:0 0 8px var(--c)}
aside{border-left:1px solid var(--line);padding:16px 18px 24px;display:flex;flex-direction:column;
  gap:12px;background:rgba(20,32,44,.45);min-width:0}
.card{background:linear-gradient(180deg,var(--panel),#1e2f41);border:1px solid var(--line);
  border-radius:8px;padding:11px 12px;box-shadow:inset 0 1px 0 rgba(255,255,255,.03)}
.cardHead{display:flex;align-items:baseline;justify-content:space-between;gap:8px;margin-bottom:8px}
.k{color:var(--dim);font-size:11px;letter-spacing:.12em;text-transform:uppercase}
.sub{color:var(--dim2);font-size:11px}
#seats{display:flex;flex-direction:column;gap:8px}
.seat{--sc:var(--p0);position:relative;border:1px solid var(--line);border-left:3px solid var(--sc);
  border-radius:7px;padding:9px 10px 10px;
  background:linear-gradient(135deg,rgba(255,255,255,.035),rgba(255,255,255,0) 60%)}
.seat1{--sc:var(--p1)}
.seat.active{box-shadow:0 0 0 1px var(--sc),0 0 20px -10px var(--sc)}
.seat.win{background:linear-gradient(135deg,rgba(255,255,255,.07),transparent);
  box-shadow:0 0 0 1px var(--sc),0 0 22px -8px var(--sc)}
.seat.down{opacity:.72}
.seatTop{display:flex;align-items:center;gap:7px;flex-wrap:wrap}
.dot{width:9px;height:9px;border-radius:50%;background:var(--sc);box-shadow:0 0 9px var(--sc)}
.seatName{font-weight:700;letter-spacing:.08em;color:var(--sc)}
.tagMini{font-size:10px;color:var(--dim);border:1px solid var(--line);border-radius:999px;padding:0 6px}
.winTag{color:#07131b;background:var(--sc);border-color:var(--sc);font-weight:700}
.downTag{color:var(--bad);border-color:rgba(248,113,113,.5)}
.statRow{display:flex;align-items:baseline;gap:6px;margin-top:6px}
.statVal{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1}
.statUnit{font-size:11px;color:var(--dim)}
.bar{height:7px;border-radius:99px;background:#162330;overflow:hidden;margin-top:7px;
  border:1px solid rgba(255,255,255,.04)}
.bar i{display:block;height:100%;border-radius:99px;transition:width .25s ease;
  box-shadow:0 0 8px rgba(255,255,255,.12)}
.chips{display:flex;gap:5px;flex-wrap:wrap;margin-top:7px}
.chips span{font-size:10px;color:var(--dim);background:rgba(13,23,32,.55);
  border:1px solid var(--line);border-radius:999px;padding:1px 7px}
.orders{white-space:pre-wrap;font-size:12px;line-height:1.65;max-height:180px;overflow:auto}
.orderLine{display:block;color:var(--dim)}
.orderLine.s0{color:#8be9f7}
.orderLine.s1{color:#fdba74}
.hash{font-size:11px;color:var(--dim);word-break:break-all}
#result{font-size:12px}
.rtags{display:flex;gap:5px;flex-wrap:wrap}
.rtag{font-size:10px;color:var(--dim);border:1px solid var(--line);border-radius:999px;
  padding:1px 7px;background:rgba(13,23,32,.45)}
.rtag b{color:var(--fg);font-weight:500;margin-right:4px}
.gameRows{margin-top:8px;display:flex;flex-direction:column;gap:4px}
.gameRow{display:flex;gap:8px;font-size:11px;border-top:1px dashed var(--line);padding-top:4px}
.gl{color:var(--accent);min-width:58px;flex:0 0 auto}
.gv{color:var(--dim);word-break:break-word}
details{margin-top:8px;border-top:1px solid var(--line);padding-top:7px}
summary{cursor:pointer;color:var(--dim);font-size:11px;letter-spacing:.08em}
pre{margin:7px 0 0;max-height:220px;overflow:auto;font-size:10.5px;color:var(--dim);
  white-space:pre-wrap;word-break:break-all;background:#111c26;border:1px solid var(--line);
  border-radius:5px;padding:8px}
#waveTable{max-height:190px;overflow:auto}
table{border-collapse:collapse;width:100%;font-size:11px}
th,td{border-bottom:1px solid var(--line);padding:3px 6px;text-align:right}
th{color:var(--dim);font-weight:400;position:sticky;top:0;background:var(--panel)}
td:first-child,th:first-child{text-align:left}
tr.on td{background:rgba(34,211,238,.08);color:var(--fg)}
footer{position:sticky;bottom:0;z-index:5;background:rgba(21,33,45,.96);
  backdrop-filter:blur(6px);border-top:1px solid var(--line);padding:11px 20px;
  display:flex;gap:10px;align-items:center;flex-wrap:wrap}
button{background:var(--panel2);color:var(--fg);border:1px solid var(--line2);padding:6px 11px;
  cursor:pointer;border-radius:5px;font:inherit;font-size:12px}
button:hover{border-color:var(--accent);color:#fff;background:#31475d}
button.on{border-color:var(--accent);color:var(--accent)}
.timeline{flex:1;min-width:220px;display:flex;flex-direction:column;gap:3px}
#ticks{display:flex;gap:2px;height:5px;overflow:hidden}
.tick{flex:1;padding:0;border:0;border-radius:99px;background:#2c4055;cursor:pointer;
  min-width:1px;height:5px}
.tick.g1{background:#5a4430}
.tick:hover{background:#4b6a86}
.tick.on{background:linear-gradient(90deg,var(--p0),var(--p1));box-shadow:0 0 8px rgba(34,211,238,.6)}
input[type=range]{width:100%;accent-color:var(--accent);cursor:pointer}
.count{font-size:11px;color:var(--dim);min-width:74px;text-align:right}
#speedWrap{display:flex;gap:4px}
.sp{padding:4px 8px;font-size:11px}
@media(max-width:920px){main{grid-template-columns:1fr}aside{border-left:0;
  border-top:1px solid var(--line)}}
@media(max-width:640px){
  header{padding:10px 12px;gap:8px}
  .meta{margin-left:0;gap:5px}
  .clock{width:100%;margin-left:0;justify-content:flex-start}
  #stage{padding:10px 10px 18px;min-height:0;gap:9px}
  #boardWrap{width:100%;max-width:100%}
  canvas{width:100%!important;height:auto!important}
  #hud{padding:7px 8px;font-size:10px}
  #hud span{max-width:46%}
  #legend{gap:5px}
  aside{padding:12px 12px 20px}
  footer{padding:8px 10px;gap:7px}
  .timeline{min-width:140px}
  #speedWrap{gap:3px}
}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style></head>
<body>
<header>
  <div class="brand"><span class="logo">REPLAY</span><h1>__GAME__</h1><span class="ver">v__VERSION__</span></div>
  <div class="meta">
    <span class="tag">map <b>__MAP__</b></span>
    <span class="tag">seed <b>__SEED__</b></span>
    <span class="tag">rules <b>__RULES__</b></span>
    <span class="tag">engine <b>__ENGINE__</b></span>
  </div>
  <div class="clock">
    <span class="phase" id="phasePill">—</span>
    <span class="pill" id="frameLabel"></span>
  </div>
</header>
<div class="progress"><i id="progBar"></i></div>
<main>
  <section id="stage">
    <div id="boardWrap">
      <canvas id="board" width="960" height="540"></canvas>
      <canvas id="fx"></canvas>
      <div id="hud"><span id="hudLeft">—</span><span id="hudRight"></span></div>
      <div id="flash"></div>
      <div id="banner"><div class="bt"></div><div class="bs"></div></div>
    </div>
    <div id="legend"></div>
  </section>
  <aside>
    <section class="card">
      <div class="cardHead"><span class="k">seats</span><span class="sub" id="seatNote"></span></div>
      <div id="seats"></div>
    </section>
    <section class="card">
      <div class="cardHead"><span class="k">frame</span><span class="sub" id="frameNo"></span></div>
      <div class="orders" id="orders"></div>
    </section>
    <section class="card">
      <div class="cardHead"><span class="k">state hash</span></div>
      <div class="hash" id="hash"></div>
    </section>
    <section class="card" id="waveCard" style="display:none">
      <div class="cardHead"><span class="k">wave table</span></div>
      <div id="waveTable"></div>
    </section>
    <section class="card">
      <div class="cardHead"><span class="k">result</span></div>
      <div id="result"></div>
      <details><summary>raw json</summary><pre id="rawResult"></pre></details>
    </section>
  </aside>
</main>
<footer>
  <button id="prev" title="上一帧 (←)">‹</button>
  <button id="play" title="播放/暂停 (Space)">play</button>
  <button id="next" title="下一帧 (→)">›</button>
  <div class="timeline">
    <div id="ticks"></div>
    <input id="slider" type="range" min="0" max="0" value="0">
  </div>
  <span id="frameCount" class="count">0 / 0</span>
  <span id="speedWrap">
    <button class="sp" data-ms="600">0.6s</button>
    <button class="sp on" data-ms="250">0.25s</button>
    <button class="sp" data-ms="80">0.08s</button>
  </span>
</footer>
<script>
"use strict";
const R = __REPLAY__;
const HD = R.header || {};
const RES = (R.result && typeof R.result === "object") ? R.result : {};
const GAMES = Array.isArray(R.games) ? R.games : [];
const WAVES = Array.isArray(R.waves) ? R.waves : [];
const GAME = HD.game || "?";
const SEAT_COLORS = ["#22d3ee", "#fb923c"];
const MAX_HP = 300, MAX_CORE = 40;
const RM = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
const el = function (id) { return document.getElementById(id); };
function esc(v) {
  return String(v === undefined || v === null ? "" : v).replace(/[&<>"']/g, function (ch) {
    return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[ch];
  });
}
function num(v, d) {
  if (v === null || v === undefined || v === "") return (d || 0);
  const n = Number(v);
  return isFinite(n) ? n : (d || 0);
}
function faultOf(rg, seat) {
  if (!rg || rg.faults === undefined || rg.faults === null) return null;
  if (Array.isArray(rg.faults)) return num(rg.faults[seat]);
  if (typeof rg.faults === "object") return num(rg.faults[String(seat)]);
  return null;
}
function orderText(obj) {
  return Object.keys(obj || {}).map(function (k) { return k + ": " + obj[k]; }).join("\\n");
}
function waveOrderText(w) {
  const a = Array.isArray(w.acts0) ? w.acts0.join(" | ") : "";
  const b = Array.isArray(w.acts1) ? w.acts1.join(" | ") : "";
  return "P0 " + a + "\\nP1 " + b;
}

/* ---------- frames：turn schema 与 wave schema 共用同一条时间线 ---------- */
const FRAMES = [];
if (WAVES.length) {
  let cum = [0, 0];
  WAVES.forEach(function (w, wi) {
    cum = [cum[0] + num(w.leak && w.leak[0]), cum[1] + num(w.leak && w.leak[1])];
    FRAMES.push({
      kind: "wave", wi: wi, wave: num(w.wave, wi + 1), label: "WAVE " + num(w.wave, wi + 1),
      t: num(w.wave, wi + 1), orders: waveOrderText(w), hash: w.state_hash,
      core: Array.isArray(w.core) ? [num(w.core[0]), num(w.core[1])] : null,
      leak: Array.isArray(w.leak) ? [num(w.leak[0]), num(w.leak[1])] : null,
      cum: cum.slice(), steps: num(w.steps, 0)
    });
  });
} else {
  GAMES.forEach(function (g, gi) {
    const initial = g.initial_state;
    if (initial && initial.state && typeof initial.state === "object") {
      const t = num(initial.t, 1);
      FRAMES.push({
        kind: "turn", gi: gi, label: (g.label || "?") + " · START T" + t,
        t: t, seat: null, orders: "Initial state · before action",
        hash: initial.state_hash, game: g, state: initial.state, initial: true
      });
    }
    const turns = Array.isArray(g.turns) ? g.turns : [];
    turns.forEach(function (t) {
      FRAMES.push({
        kind: "turn", gi: gi, label: (g.label || "?") + " · T" + num(t.t),
        t: num(t.t), seat: (typeof t.seat === "number" ? t.seat : null),
        orders: orderText(t.orders), hash: t.state_hash, game: g,
        state: (t.state && typeof t.state === "object") ? t.state : null
      });
    });
  });
}
let cur = 0;
const qp = new URLSearchParams(window.location.search || "");
if (qp.has("frame")) {
  const qv = qp.get("frame");
  cur = qv === "last" ? Math.max(0, FRAMES.length - 1) : (parseInt(qv, 10) || 0);
}
cur = Math.max(0, Math.min(cur, Math.max(0, FRAMES.length - 1)));
let prevIndex = -1;
let timer = null, ms = 250;
let boardCache = {key: "", data: null};

function atEnd() { return FRAMES.length > 0 && cur >= FRAMES.length - 1; }
function resultGames() {
  if (Array.isArray(RES.games)) return RES.games;
  if (Array.isArray(RES.halves)) return RES.halves;
  return [];
}
function runnerSeatOf(g, gi) {
  if (g && g.runner_seat !== undefined && g.runner_seat !== null) return num(g.runner_seat);
  // 旧 P07 回放没有 game 分隔行：按半场顺序 fallback，与 verify 的 _v_p07 一致。
  return (gi === 1 ? 1 : 0);
}
function frameRunner(f) {
  const g = (f && f.game) ? f.game : (GAMES[0] || {});
  const gi = (f && typeof f.gi === "number") ? f.gi : 0;
  return runnerSeatOf(g, gi);
}

/* ---------- 2 席状态：P06 也只有 2 席，不假设 4 席 ---------- */
function seatBase() {
  const out = [];
  for (let s = 0; s < 2; s++) {
    out.push({seat: s, name: "P" + s, bot: null, role: "", value: null,
              max: null, unit: "", chips: [], win: false, down: false, active: false});
  }
  return out;
}
function applySeatBots(out, g, rg) {
  const mapping = (g && Array.isArray(g.seat_bots)) ? g.seat_bots
                : (rg && Array.isArray(rg.seat_bots) ? rg.seat_bots : null);
  if (!mapping) return;
  out.forEach(function (o) {
    if (typeof mapping[o.seat] === "number") o.bot = mapping[o.seat];
  });
}
function markWinner(out, w) {
  if (typeof w === "number" && w >= 0 && w < out.length) out[w].win = true;
}
function seatStats(f) {
  const out = seatBase();
  if (!f) return out;
  if (f.kind === "wave") {
    out.forEach(function (o) {
      o.unit = "CORE";
      o.max = MAX_CORE;
      o.value = f.core ? f.core[o.seat] : null;
      o.down = !!f.core && f.core[o.seat] <= 0;
      o.chips = ["leak + " + num(f.leak && f.leak[o.seat]),
                 "total " + num(f.cum && f.cum[o.seat]),
                 "steps " + num(f.steps)];
    });
    if (atEnd()) markWinner(out, RES.winner);
    return out;
  }
  const g = GAMES[f.gi] || {};
  const rg = resultGames()[f.gi] || null;
  const fs = (f.state && typeof f.state === "object") ? f.state : null;
  const final = atEnd();
  applySeatBots(out, g, rg);
  if (f.seat !== null) out[f.seat].active = true;
  else out.forEach(function (o) { o.active = true; });

  if (fs && Array.isArray(fs.hp)) {
    out.forEach(function (o) {
      const hp = num(fs.hp[o.seat]);
      o.value = hp; o.max = MAX_HP; o.unit = "HP"; o.down = hp <= 0;
      if (Array.isArray(fs.survivors)) o.chips.push("survivors " + num(fs.survivors[o.seat]));
      if (Array.isArray(fs.damage)) o.chips.push("damage " + num(fs.damage[o.seat]));
      const flt = faultOf(fs, o.seat);
      if (flt !== null) o.chips.push("faults " + flt);
    });
    if (final && rg) markWinner(out, rg.winner);
    return out;
  }
  if (fs && Array.isArray(fs.core)) {
    const maxc = Math.max(4, num(fs.core[0]), num(fs.core[1]));
    out.forEach(function (o) {
      const core = num(fs.core[o.seat]);
      o.value = core; o.max = maxc; o.unit = "CORE"; o.down = core <= 0;
      if (Array.isArray(fs.survivors)) o.chips.push("mechs " + num(fs.survivors[o.seat]));
      const flt = faultOf(fs, o.seat);
      if (flt !== null) o.chips.push("faults " + flt);
    });
    if (final && rg) markWinner(out, rg.winner);
    return out;
  }
  if (fs && rg && (rg.runner_seat !== undefined || rg.score !== undefined)) {
    const rs = (rg.runner_seat !== undefined) ? num(rg.runner_seat) : runnerSeatOf(g, f.gi);
    out.forEach(function (o) {
      o.role = (o.seat === rs) ? "runner" : "ghost";
      if (o.seat === rs) {
        o.active = true;
        o.value = num(fs.score);
        o.max = Math.max(10, num(fs.score));
        o.unit = "SCORE";
        o.down = num(fs.hp, 1) <= 0;
        o.chips.push("runner hp " + num(fs.hp));
        o.chips.push("eaten " + num(fs.eaten));
      } else {
        o.unit = "GHOST";
      }
      const flt = faultOf(fs, o.seat);
      if (flt !== null) o.chips.push("faults " + flt);
      if (final && rg.reason !== undefined) o.chips.push(String(rg.reason));
      if (final && Array.isArray(RES.score)) o.chips.push("match " + num(RES.score[o.seat]));
    });
    if (final) markWinner(out, typeof RES.winner === "number" ? RES.winner : rg.winner);
    return out;
  }
  if (!rg) return out;
  if (Array.isArray(rg.hp)) {
    out.forEach(function (o) {
      const hp = num(rg.hp[o.seat]);
      o.value = hp; o.max = MAX_HP; o.unit = "HP"; o.down = hp <= 0;
      if (Array.isArray(rg.survivors)) o.chips.push("survivors " + num(rg.survivors[o.seat]));
      if (Array.isArray(rg.damage)) o.chips.push("damage " + num(rg.damage[o.seat]));
      const flt = faultOf(rg, o.seat);
      if (flt !== null) o.chips.push("faults " + flt);
    });
    markWinner(out, rg.winner);
  } else if (Array.isArray(rg.core)) {
    const maxc = Math.max(4, num(rg.core[0]), num(rg.core[1]));
    out.forEach(function (o) {
      const core = num(rg.core[o.seat]);
      o.value = core; o.max = maxc; o.unit = "CORE"; o.down = core <= 0;
      if (Array.isArray(rg.survivors)) o.chips.push("mechs " + num(rg.survivors[o.seat]));
      const flt = faultOf(rg, o.seat);
      if (flt !== null) o.chips.push("faults " + flt);
    });
    markWinner(out, rg.winner);
  } else if (rg.runner_seat !== undefined || rg.score !== undefined) {
    const rs = (rg.runner_seat !== undefined) ? num(rg.runner_seat) : runnerSeatOf(g, f.gi);
    out.forEach(function (o) {
      o.role = (o.seat === rs) ? "runner" : "ghost";
      if (o.seat === rs) {
        o.active = true;
        o.value = num(rg.score);
        o.max = Math.max(10, num(rg.score));
        o.unit = "SCORE";
        o.down = num(rg.hp, 1) <= 0;
        o.chips.push("runner hp " + num(rg.hp));
      } else {
        o.unit = "GHOST";
      }
      if (Array.isArray(RES.score)) o.chips.push("match " + num(RES.score[o.seat]));
      const flt = faultOf(rg, o.seat);
      if (flt !== null) o.chips.push("faults " + flt);
      if (rg.reason !== undefined) o.chips.push(String(rg.reason));
    });
    markWinner(out, rg.winner);
  } else {
    if (Array.isArray(rg.score)) {
      const top = Math.max.apply(null, rg.score.concat([1]));
      out.forEach(function (o) {
        o.value = num(rg.score[o.seat]);
        o.max = top;
        o.unit = "SCORE";
      });
    }
    markWinner(out, rg.winner);
  }
  return out;
}

/* ---------- 侧栏 ---------- */
function renderSeats(stats, f) {
  let html = "";
  stats.forEach(function (s) {
    const pct = (s.value !== null && s.max) ? Math.max(0, Math.min(100, s.value / s.max * 100)) : 0;
    const cls = ["seat", "seat" + s.seat];
    if (s.active) cls.push("active");
    if (s.win) cls.push("win");
    if (s.down) cls.push("down");
    html += '<div class="' + cls.join(" ") + '">';
    html += '<div class="seatTop"><span class="dot"></span><span class="seatName">' + esc(s.name) + "</span>";
    if (s.bot !== null) html += '<span class="tagMini">bot ' + esc(s.bot) + "</span>";
    if (s.role) html += '<span class="tagMini">' + esc(s.role) + "</span>";
    if (s.win) html += '<span class="tagMini winTag">WIN</span>';
    else if (s.down) html += '<span class="tagMini downTag">DOWN</span>';
    html += "</div>";
    const unit = (s.value === null) ? esc(s.unit)
               : (s.max !== null ? "/ " + esc(s.max) + " " + esc(s.unit) : esc(s.unit));
    html += '<div class="statRow"><span class="statVal">' + esc(s.value === null ? "—" : s.value)
          + '</span><span class="statUnit">' + unit + "</span></div>";
    html += '<div class="bar"><i style="width:' + pct.toFixed(1) + "%;background:"
          + SEAT_COLORS[s.seat] + '"></i></div>';
    html += '<div class="chips">' + s.chips.map(function (c) {
      return "<span>" + esc(c) + "</span>";
    }).join("") + "</div>";
    html += "</div>";
  });
  el("seats").innerHTML = html;
  let note = "final from result (legacy replay)";
  if (f && f.kind === "wave") note = atEnd() ? "final core" : "core now";
  else if (f && f.state) note = atEnd() ? "final · state" : "state now";
  else if (f && f.seat !== null) note = "seat " + f.seat + " · final (legacy)";
  el("seatNote").textContent = note;
}
function renderOrders(text) {
  const lines = String(text || "").split("\\n");
  const html = lines.map(function (line) {
    let cls = "orderLine";
    if (/^(P0|0:)/.test(line)) cls += " s0";
    else if (/^(P1|1:)/.test(line)) cls += " s1";
    return '<span class="' + cls + '">' + esc(line) + "</span>";
  }).join("");
  el("orders").innerHTML = html || '<span class="orderLine">(no orders)</span>';
}
function renderWaveTable(f) {
  const card = el("waveCard");
  if (!WAVES.length) { card.style.display = "none"; return; }
  card.style.display = "block";
  let html = "<table><thead><tr><th>wave</th><th>core</th><th>leak</th><th>steps</th><th>hash</th></tr></thead><tbody>";
  WAVES.forEach(function (w, wi) {
    const on = (f && f.kind === "wave" && f.wi === wi) ? ' class="on"' : "";
    const hash = String(w.state_hash || "");
    html += "<tr" + on + "><td>" + esc(w.wave) + "</td><td>"
          + esc((w.core || []).join(" : ")) + "</td><td>"
          + esc((w.leak || []).join(" : ")) + "</td><td>"
          + esc(w.steps) + '</td><td title="' + esc(hash) + '">'
          + esc(hash.slice(0, 6)) + "</td></tr>";
  });
  el("waveTable").innerHTML = html + "</tbody></table>";
}
function winnerText() {
  const w = RES.winner;
  if (w === undefined || w === null) return "—";
  if (w === -1) return "draw";
  const botSide = GAME.indexOf("p01") === 0 || GAME.indexOf("p06") === 0;
  return (botSide ? "bot " : "seat ") + w;
}
function resultSummaryHTML() {
  const tags = [];
  function tag(k, v) {
    if (v === undefined || v === null || v === "") return;
    tags.push('<span class="rtag"><b>' + esc(k.replace(/_/g, " ")) + "</b>" + esc(v) + "</span>");
  }
  tag("winner", winnerText());
  tag("decided by", RES.decided_by);
  tag("points", Array.isArray(RES.points) ? RES.points.join(" : ") : RES.points);
  tag("total hp", Array.isArray(RES.total_hp) ? RES.total_hp.join(" : ") : RES.total_hp);
  tag("total core", Array.isArray(RES.total_core) ? RES.total_core.join(" : ") : RES.total_core);
  tag("total survivors", Array.isArray(RES.total_survivors) ? RES.total_survivors.join(" : ") : RES.total_survivors);
  tag("total damage", Array.isArray(RES.total_damage) ? RES.total_damage.join(" : ") : RES.total_damage);
  tag("score", Array.isArray(RES.score) ? RES.score.join(" : ") : RES.score);
  tag("match score", RES.match_score);
  tag("reason", RES.reason);
  tag("wave", RES.wave);
  tag("faults", Array.isArray(RES.faults) ? RES.faults.join(" : ") : RES.faults);
  let html = '<div class="rtags">' + tags.join("") + "</div>";
  const rows = resultGames();
  if (rows.length) {
    html += '<div class="gameRows">';
    rows.forEach(function (g, i) {
      const gl = (GAMES[i] && GAMES[i].label)
        ? "GAME " + GAMES[i].label
        : ((Array.isArray(RES.halves) ? "HALF " : "GAME ") + (i + 1));
      const bits = [];
      if (g.winner !== undefined) bits.push("winner " + (g.winner < 0 ? "draw" : "seat " + g.winner));
      if (g.reason !== undefined) bits.push(String(g.reason));
      if (Array.isArray(g.hp)) bits.push("hp " + g.hp.join(" : "));
      else if (typeof g.hp === "number") bits.push("hp " + g.hp);
      if (Array.isArray(g.core)) bits.push("core " + g.core.join(" : "));
      else if (typeof g.core === "number") bits.push("core " + g.core);
      if (g.score !== undefined && !Array.isArray(g.score)) bits.push("score " + g.score);
      if (Array.isArray(g.survivors)) bits.push("surv " + g.survivors.join(" : "));
      else if (typeof g.survivors === "number") bits.push("surv " + g.survivors);
      if (g.round !== undefined) bits.push("round " + g.round);
      if (g.steps !== undefined) bits.push("steps " + g.steps);
      if (g.runner_seat !== undefined) bits.push("runner P" + g.runner_seat);
      html += '<div class="gameRow"><span class="gl">' + esc(gl)
            + '</span><span class="gv">' + esc(bits.join(" · ")) + "</span></div>";
    });
    html += "</div>";
  }
  return html;
}

/* ---------- 地形烘焙：更亮的蓝灰底 + 鲜明的 2 席标记 ---------- */
function boardKey(f) {
  const mapId = (R.map && R.map.id) ? R.map.id : "";
  const live = !!(f && f.state && (Array.isArray(f.state.grid)
    || Array.isArray(f.state.worms) || Array.isArray(f.state.mechs)
    || (f.state.runner && typeof f.state.runner === "object")));
  return GAME + "|" + mapId + "|" + frameRunner(f) + "|"
       + (live ? (f.hash || f.label || "live") : "static");
}
function roundRect(g, x, y, w, h, r) {
  r = Math.min(r, w / 2, h / 2);
  g.beginPath();
  g.moveTo(x + r, y);
  g.arcTo(x + w, y, x + w, y + h, r);
  g.arcTo(x + w, y + h, x, y + h, r);
  g.arcTo(x, y + h, x, y, r);
  g.arcTo(x, y, x + w, y, r);
  g.closePath();
}
function paintMarker(g, ch, x, y, cell, p01, runner) {
  const cx = x * cell + cell / 2, cy = y * cell + cell / 2;
  let color = null, label = "", shape = "circle";
  if (p01 && (ch === "0" || ch === "1")) {
    color = SEAT_COLORS[+ch]; shape = "spawn";
  } else if (!p01 && ch >= "1" && ch <= "4") {
    color = SEAT_COLORS[1 - runner]; label = ch; shape = "ghost";
  } else if (ch === "A" || ch === "B" || ch === "C") {
    color = SEAT_COLORS[0]; label = ch === "A" ? "1" : (ch === "B" ? "2" : "3"); shape = "mech";
  } else if (ch === "a" || ch === "b" || ch === "c") {
    color = SEAT_COLORS[1]; label = ch === "a" ? "1" : (ch === "b" ? "2" : "3"); shape = "mech";
  } else if (ch === "D") {
    color = SEAT_COLORS[0]; label = "C"; shape = "core";
  } else if (ch === "d") {
    color = SEAT_COLORS[1]; label = "C"; shape = "core";
  } else if (ch === "P") {
    color = SEAT_COLORS[runner]; label = "R"; shape = "runner";
  }
  if (!color) return;
  const r = Math.max(2, cell * 0.30);
  g.save();
  g.shadowColor = color;
  g.shadowBlur = Math.max(4, cell * 0.55);
  if (shape === "spawn") {
    g.fillStyle = color;
    g.beginPath(); g.arc(cx, cy, r, 0, Math.PI * 2); g.fill();
    g.shadowBlur = 0;
    g.fillStyle = "#eafcff";
    g.beginPath(); g.arc(cx, cy, r * 0.34, 0, Math.PI * 2); g.fill();
  } else if (shape === "ghost" || shape === "runner") {
    const rr = r * (shape === "runner" ? 1.18 : 1);
    g.fillStyle = color;
    g.beginPath();
    g.moveTo(cx, cy - rr); g.lineTo(cx + rr, cy); g.lineTo(cx, cy + rr); g.lineTo(cx - rr, cy);
    g.closePath(); g.fill();
    g.shadowBlur = 0;
    g.strokeStyle = "rgba(255,255,255,.55)";
    g.lineWidth = Math.max(1, cell * 0.04);
    g.stroke();
  } else if (shape === "core") {
    const s = r * 1.28;
    g.fillStyle = "rgba(10,20,30,.92)";
    g.fillRect(cx - s, cy - s, s * 2, s * 2);
    g.shadowBlur = 0;
    g.strokeStyle = color;
    g.lineWidth = Math.max(2, cell * 0.08);
    g.strokeRect(cx - s, cy - s, s * 2, s * 2);
    g.beginPath(); g.arc(cx, cy, s * 0.46, 0, Math.PI * 2);
    g.stroke();
  } else {
    g.fillStyle = color;
    g.beginPath(); g.arc(cx, cy, r, 0, Math.PI * 2); g.fill();
    g.shadowBlur = 0;
    g.strokeStyle = "rgba(5,15,22,.75)";
    g.lineWidth = Math.max(1, cell * 0.06);
    g.stroke();
  }
  if (label && cell >= 18) {
    g.shadowBlur = 0;
    g.fillStyle = shape === "core" ? color : "#05121a";
    g.font = "700 " + Math.max(9, Math.floor(cell * 0.42)) + "px ui-monospace,monospace";
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillText(label, cx, cy + 0.5);
  }
  g.restore();
}
function paintLiveUnit(g, x, y, cell, color, label, shape, scale) {
  const cx = (x + 0.5) * cell, cy = (y + 0.5) * cell;
  const base = (typeof scale === "number") ? scale : (shape === "diamond" ? 0.38 : 0.30);
  const r = Math.max(2.5, cell * base);
  g.save();
  g.shadowColor = color;
  g.shadowBlur = Math.max(4, cell * 0.65);
  g.fillStyle = color;
  g.beginPath();
  if (shape === "diamond") {
    g.moveTo(cx, cy - r); g.lineTo(cx + r, cy);
    g.lineTo(cx, cy + r); g.lineTo(cx - r, cy); g.closePath();
  } else {
    g.arc(cx, cy, r, 0, Math.PI * 2);
  }
  g.fill();
  g.shadowBlur = 0;
  g.strokeStyle = "rgba(4,14,22,.85)";
  g.lineWidth = Math.max(1, cell * 0.05);
  g.stroke();
  if (label && cell >= 8) {
    g.fillStyle = "#04121b";
    g.font = "700 " + Math.max(7, Math.floor(cell * 0.46)) + "px ui-monospace,monospace";
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillText(label, cx, cy + 0.5);
  }
  g.restore();
}
function isStaticUnitChar(ch, p01, p06, p07) {
  if (p01) return ch === "0" || ch === "1";
  if (p06) return ch >= "A" && ch <= "C" || ch >= "a" && ch <= "c";
  if (p07) return ch === "P" || (ch >= "1" && ch <= "4");
  return false;
}
function buildBoard(f) {
  const st = (f && f.state) ? f.state : null;
  const baseGrid = (R.map && Array.isArray(R.map.grid))
    ? R.map.grid.filter(function (r) { return typeof r === "string"; }) : [];
  const stateGrid = (st && Array.isArray(st.grid))
    ? st.grid.filter(function (r) { return typeof r === "string"; }) : [];
  const grid = stateGrid.length ? stateGrid : baseGrid;
  if (!grid.length) return null;
  let W = 0;
  grid.forEach(function (r) { if (r.length > W) W = r.length; });
  const H = grid.length;
  const p01 = GAME.indexOf("p01") === 0;
  const p06 = GAME.indexOf("p06") === 0;
  const p07 = GAME.indexOf("p07") === 0;
  const liveUnits = !!(st && (Array.isArray(st.worms) || Array.isArray(st.mechs)
    || (st.runner && typeof st.runner === "object")));
  const cell = Math.max(4, Math.min(96, Math.floor(1200 / Math.max(1, W)),
                                  Math.floor(820 / Math.max(1, H))));
  const c = document.createElement("canvas");
  c.width = Math.max(1, W * cell);
  c.height = Math.max(1, H * cell);
  const g = c.getContext("2d");
  const bg = g.createLinearGradient(0, 0, 0, c.height);
  bg.addColorStop(0, p01 ? "#101c27" : "#14212e");
  bg.addColorStop(1, "#0b141d");
  g.fillStyle = bg;
  g.fillRect(0, 0, c.width, c.height);
  for (let y = 0; y < H; y++) {
    const row = grid[y] || "";
    const up = y > 0 ? (grid[y - 1] || "") : "";
    for (let x = 0; x < W; x++) {
      const ch = row.charAt(x) || ".";
      const px = x * cell, py = y * cell;
      let fill = null;
      if (ch === "#") fill = p01 ? "#6b5136" : "#3f5366";
      else if (ch === "X") fill = "#415466";
      else if (ch === "~") fill = "#1d5f8c";
      else if (ch === ".") fill = "#172532";
      else if (ch === "_") fill = "#203243";
      else if (ch === "o") fill = "#1c3427";
      else if (ch === " ") fill = "#172532";
      if (fill) {
        g.fillStyle = fill;
        g.fillRect(px, py, cell, cell);
      }
      if (ch === "#" || ch === "X" || ch === "~") {
        const above = up.charAt(x);
        if (above !== ch) {
          g.fillStyle = ch === "~" ? "rgba(140,220,255,.35)" : "rgba(255,255,255,.10)";
          g.fillRect(px, py, cell, Math.max(1, Math.floor(cell * 0.14)));
        }
        g.fillStyle = "rgba(0,0,0,.18)";
        g.fillRect(px, py + cell - Math.max(1, Math.floor(cell * 0.12)),
                   cell, Math.max(1, Math.floor(cell * 0.12)));
        if (cell >= 6) {
          g.strokeStyle = "rgba(10,20,28,.28)";
          g.strokeRect(px + 0.5, py + 0.5, cell - 1, cell - 1);
        }
      }
      if (ch === "." && p07 && cell >= 10) {
        g.fillStyle = "rgba(251,191,36,.55)";
        g.beginPath(); g.arc(px + cell / 2, py + cell / 2, Math.max(1, cell * 0.12), 0, Math.PI * 2); g.fill();
      } else if (ch === "o" && p07 && cell >= 10) {
        g.fillStyle = "#4ade80";
        g.beginPath(); g.arc(px + cell / 2, py + cell / 2, Math.max(2, cell * 0.20), 0, Math.PI * 2); g.fill();
      }
    }
  }
  const runner = frameRunner(f);
  for (let y = 0; y < H; y++) {
    const row = grid[y] || "";
    for (let x = 0; x < W; x++) {
      const ch = row.charAt(x) || ".";
      if (liveUnits && isStaticUnitChar(ch, p01, p06, p07)) continue;
      paintMarker(g, ch, x, y, cell, p01, runner);
    }
  }
  if (st && p01 && Array.isArray(st.worms)) {
    st.worms.forEach(function (w, i) {
      if (!w || !w.alive) return;
      const seat = i < 3 ? 0 : 1;
      paintLiveUnit(g, num(w.x), num(w.y), cell, SEAT_COLORS[seat],
                    String(i % 3 + 1), "circle", 0.52);
    });
  } else if (st && p06 && Array.isArray(st.mechs)) {
    st.mechs.forEach(function (m) {
      if (!m || num(m.hp) <= 0) return;
      paintLiveUnit(g, num(m.x), num(m.y), cell, SEAT_COLORS[num(m.seat)],
                    String(num(m.id) % 3 + 1), "circle");
    });
  } else if (st && p07 && st.runner) {
    paintLiveUnit(g, num(st.runner.x), num(st.runner.y), cell,
                  SEAT_COLORS[runner], "R", "diamond");
    (st.ghosts || []).forEach(function (gh) {
      if (!gh || !gh.alive) return;
      paintLiveUnit(g, num(gh.x), num(gh.y), cell,
                    SEAT_COLORS[1 - runner], "", "diamond");
    });
  }
  return {canvas: c, key: boardKey(f)};
}
function drawWaveBoard(f) {
  const W = 1000, H = 380;
  cv.width = W; cv.height = H;
  const g = ctx;
  const bg = g.createLinearGradient(0, 0, 0, H);
  bg.addColorStop(0, "#1a2b3c");
  bg.addColorStop(1, "#101a24");
  g.fillStyle = bg;
  g.fillRect(0, 0, W, H);
  g.strokeStyle = "rgba(120,160,190,.06)";
  g.lineWidth = 1;
  for (let x = 0; x < W; x += 40) { g.beginPath(); g.moveTo(x + .5, 0); g.lineTo(x + .5, H); g.stroke(); }
  for (let y = 0; y < H; y += 40) { g.beginPath(); g.moveTo(0, y + .5); g.lineTo(W, y + .5); g.stroke(); }
  const core = (f && f.core) ? f.core : [MAX_CORE, MAX_CORE];
  const leak = (f && f.leak) ? f.leak : [0, 0];
  const wi = (f && f.kind === "wave") ? f.wi : 0;
  const laneX = 70, laneW = 780, trackX = laneX + 18, trackW = laneW - 36;
  const coreX = laneX + laneW + 18, coreW = 130;
  const lanes = [{seat: 0, y: 92}, {seat: 1, y: 230}];
  lanes.forEach(function (l) {
    const col = SEAT_COLORS[l.seat];
    g.fillStyle = "rgba(33,51,68,.92)";
    roundRect(g, laneX, l.y - 38, laneW, 82, 10); g.fill();
    g.strokeStyle = "rgba(77,104,128,.9)"; g.lineWidth = 1; g.stroke();
    g.fillStyle = "rgba(16,27,38,.85)";
    roundRect(g, trackX, l.y - 15, trackW, 30, 8); g.fill();
    const step = trackW / 12;
    g.strokeStyle = "rgba(130,165,190,.25)";
    for (let i = 0; i < 12; i++) {
      const x = trackX + i * step;
      g.beginPath(); g.moveTo(x + .5, l.y - 15); g.lineTo(x + .5, l.y + 15); g.stroke();
    }
    [2, 4, 6, 8, 10, 11].forEach(function (p) {
      const x = trackX + (p + 0.5) * step;
      g.fillStyle = "rgba(34,211,238,.12)";
      g.strokeStyle = col;
      g.lineWidth = 1.5;
      g.beginPath(); g.arc(x, l.y - 25, 5, 0, Math.PI * 2); g.fill(); g.stroke();
    });
    g.fillStyle = "rgba(13,23,32,.95)";
    roundRect(g, coreX, l.y - 30, coreW, 60, 8); g.fill();
    g.strokeStyle = col; g.lineWidth = 2; g.stroke();
    const hp = num(core[l.seat]);
    const ratio = Math.max(0, Math.min(1, hp / MAX_CORE));
    g.fillStyle = "rgba(10,18,26,.9)";
    g.fillRect(coreX + 10, l.y + 8, coreW - 20, 8);
    g.fillStyle = col;
    g.fillRect(coreX + 10, l.y + 8, (coreW - 20) * ratio, 8);
    g.fillStyle = col;
    g.font = "700 18px ui-monospace,monospace";
    g.textAlign = "left"; g.textBaseline = "middle";
    g.fillText("P" + l.seat, coreX + 12, l.y - 12);
    g.fillStyle = "#eaf3fb";
    g.font = "700 20px ui-monospace,monospace";
    g.textAlign = "right";
    g.fillText(String(hp), coreX + coreW - 12, l.y - 11);
    g.textAlign = "left";
    g.fillStyle = "rgba(214,232,245,.85)";
    g.font = "600 11px ui-monospace,monospace";
    g.fillText("LANE " + l.seat, laneX + 8, l.y + 29);
    const lk = Math.min(6, num(leak[l.seat]));
    for (let k = 0; k < lk; k++) {
      g.fillStyle = "#f87171";
      g.beginPath(); g.arc(coreX - 14 - k * 11, l.y + 24, 4, 0, Math.PI * 2); g.fill();
    }
    if (num(leak[l.seat]) > 0) {
      g.fillStyle = "#fca5a5";
      g.font = "600 10px ui-monospace,monospace";
      g.textAlign = "right";
      g.fillText("+" + num(leak[l.seat]) + " leak", coreX - 8, l.y + 40);
    }
  });
  const total = Math.max(1, WAVES.length);
  const py = 330, pw = Math.min(34, laneW / total);
  g.textAlign = "left";
  g.fillStyle = "rgba(168,191,210,.9)";
  g.font = "600 11px ui-monospace,monospace";
  g.fillText("WAVES 1 → " + total, laneX, py - 12);
  for (let i = 0; i < total; i++) {
    const x = laneX + i * pw;
    const past = i < wi, current = i === wi;
    const wl = num(WAVES[i].leak && WAVES[i].leak[0]) + num(WAVES[i].leak && WAVES[i].leak[1]);
    let color = "#2c4055";
    if (past) color = wl > 0 ? "#7f1d1d" : "#14532d";
    if (current) color = "#22d3ee";
    g.fillStyle = color;
    roundRect(g, x, py, Math.max(2, pw - 3), 10, 4); g.fill();
    if (current) {
      g.strokeStyle = "#eaf3fb"; g.lineWidth = 1; g.stroke();
    }
    if (i === 0 || (i + 1) % 5 === 0 || i === total - 1) {
      g.fillStyle = "rgba(168,191,210,.7)";
      g.font = "10px ui-monospace,monospace";
      g.textAlign = "center";
      g.fillText(String(i + 1), x + (pw - 3) / 2, py + 22);
    }
  }
  g.textAlign = "left";
  g.fillStyle = "rgba(168,191,210,.75)";
  g.font = "11px ui-monospace,monospace";
  const st = (f && f.kind === "wave")
    ? "steps " + f.steps + " · cumulative leak " + f.cum[0] + " : " + f.cum[1]
    : "no wave selected";
  g.fillText(st, laneX, py + 38);
}
const cv = el("board");
const ctx = cv.getContext("2d");
const fx = el("fx");
const fctx = fx.getContext("2d");
const MAPGRID = (R.map && Array.isArray(R.map.grid)) ? R.map.grid : [];
let MAPW = 0, MAPH = MAPGRID.length;
MAPGRID.forEach(function (r) { if (r.length > MAPW) MAPW = r.length; });
let shotFx = {items: [], t0: 0, dur: 0, running: false};

function clearShotFx() {
  shotFx.items = [];
  shotFx.running = false;
  if (fx.width && fx.height) fctx.clearRect(0, 0, fx.width, fx.height);
}
function shotGrid(prev) {
  const st = (prev && prev.state) ? prev.state : null;
  return (st && Array.isArray(st.grid) && st.grid.length) ? st.grid : MAPGRID;
}
function buildShotPath(shot, grid, wind) {
  const kind = String(shot.kind || "ROCKET").toUpperCase();
  const drill = kind === "DRILL";
  const rad = (Number(shot.angle) || 0) * Math.PI / 180;
  const speed = drill ? 0.25 : (Number(shot.power) || 0) / 100;
  let x = (Number(shot.x) || 0) + 0.5;
  let y = (Number(shot.y) || 0) + 0.5;
  let vx = speed * Math.cos(rad), vy = -speed * Math.sin(rad);
  x += 0.5 * Math.cos(rad);
  y -= 0.5 * Math.sin(rad);
  const pts = [[x, y]];
  const maxTicks = drill ? 48 : (kind === "GRENADE" ? 180 : 300);
  for (let tick = 0; tick < maxTicks; tick++) {
    if (!drill) {
      vx += wind * 8 / 65536;
      vy += 655 / 65536;
    }
    const nx = x + vx, ny = y + vy;
    const sub = Math.max(1, Math.ceil(Math.max(Math.abs(nx - x), Math.abs(ny - y)) / 0.25));
    let endX = nx, endY = ny, hit = false;
    for (let s = 1; s <= sub; s++) {
      const cx = x + (nx - x) * s / sub;
      const cy = y + (ny - y) * s / sub;
      const gx = Math.floor(cx), gy = Math.floor(cy);
      if (gx < 0 || gx >= MAPW || gy < 0 || gy >= MAPH) { endX = cx; endY = cy; hit = true; break; }
      const ch = String(grid[gy] || "").charAt(gx);
      if (ch === "#" || ch === "X" || ch === "~") { endX = cx; endY = cy; hit = true; break; }
    }
    pts.push([endX, endY]);
    x = endX; y = endY;
    if (hit) break;
  }
  return pts;
}
function startShotFx(prev, f) {
  if (RM || GAME.indexOf("p01") !== 0 || !f || f.kind !== "turn"
      || !f.state || !Array.isArray(f.state.shots) || !f.state.shots.length) {
    clearShotFx();
    return;
  }
  const grid = shotGrid(prev);
  const wind = num(f.state.wind, 0);
  const items = [];
  f.state.shots.forEach(function (shot) {
    const pts = buildShotPath(shot, grid, wind);
    if (pts.length < 2) return;
    const cum = [0];
    for (let i = 1; i < pts.length; i++) {
      cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
    }
    items.push({pts: pts, cum: cum, seat: Math.max(0, Math.min(1, num(shot.seat, 0) | 0)),
                kind: String(shot.kind || "").toUpperCase()});
  });
  if (!items.length) return;
  if (fx.width !== cv.width || fx.height !== cv.height) {
    fx.width = cv.width; fx.height = cv.height;
  }
  shotFx.items = items;
  shotFx.t0 = performance.now();
  shotFx.dur = Math.max(240, Math.min(700, ms * 1.8));
  if (!shotFx.running) {
    shotFx.running = true;
    requestAnimationFrame(shotFxTick);
  }
}
function drawShotFx(now) {
  if (!shotFx.items.length) return;
  const cell = fx.width / Math.max(1, MAPW);
  const t = Math.max(0, Math.min(1, (now - shotFx.t0) / shotFx.dur));
  for (let k = 0; k < shotFx.items.length; k++) {
    const p = shotFx.items[k];
    const total = p.cum[p.cum.length - 1] || 0;
    const want = total * t;
    let acc = 0, hx = p.pts[0][0], hy = p.pts[0][1];
    fctx.save();
    fctx.globalCompositeOperation = "lighter";
    fctx.globalAlpha = 0.58;
    fctx.strokeStyle = SEAT_COLORS[p.seat];
    fctx.lineWidth = Math.max(1.2, cell * 0.18);
    fctx.beginPath();
    fctx.moveTo(p.pts[0][0] * cell, p.pts[0][1] * cell);
    for (let i = 1; i < p.pts.length; i++) {
      const seg = p.cum[i] - p.cum[i - 1];
      if (acc + seg > want) {
        const q = seg ? (want - acc) / seg : 0;
        hx = p.pts[i - 1][0] + (p.pts[i][0] - p.pts[i - 1][0]) * q;
        hy = p.pts[i - 1][1] + (p.pts[i][1] - p.pts[i - 1][1]) * q;
        fctx.lineTo(hx * cell, hy * cell);
        break;
      }
      acc += seg;
      fctx.lineTo(p.pts[i][0] * cell, p.pts[i][1] * cell);
      hx = p.pts[i][0]; hy = p.pts[i][1];
    }
    fctx.stroke();
    fctx.globalAlpha = 1;
    fctx.globalCompositeOperation = "source-over";
    const px = hx * cell, py = hy * cell;
    fctx.shadowColor = SEAT_COLORS[p.seat];
    fctx.shadowBlur = Math.max(5, cell * 0.8);
    fctx.fillStyle = p.kind === "GRENADE" ? "#ffd28a" : "#fff4d8";
    fctx.beginPath();
    fctx.arc(px, py, Math.max(2.2, cell * 0.34), 0, Math.PI * 2);
    fctx.fill();
    fctx.shadowBlur = 0;
    fctx.strokeStyle = SEAT_COLORS[p.seat];
    fctx.lineWidth = Math.max(1, cell * 0.12);
    fctx.stroke();
    fctx.restore();
  }
}
function shotFxTick(now) {
  if (!shotFx.running) return;
  fctx.clearRect(0, 0, fx.width, fx.height);
  drawShotFx(now);
  if (now - shotFx.t0 < shotFx.dur) {
    requestAnimationFrame(shotFxTick);
  } else {
    fctx.clearRect(0, 0, fx.width, fx.height);
    shotFx.running = false;
  }
}
function drawScene(f) {
  const hasGrid = !!(R.map && Array.isArray(R.map.grid) && R.map.grid.length);
  if (!hasGrid) { drawWaveBoard(f); return; }
  const key = boardKey(f);
  if (boardCache.key !== key) boardCache = {key: key, data: buildBoard(f)};
  if (!boardCache.data) return;
  const c = boardCache.data.canvas;
  if (cv.width !== c.width || cv.height !== c.height) {
    cv.width = c.width; cv.height = c.height;
  }
  ctx.clearRect(0, 0, cv.width, cv.height);
  ctx.drawImage(c, 0, 0);
}

/* ---------- 时间线 / 控制 ---------- */
function buildTicks() {
  const wrap = el("ticks");
  wrap.innerHTML = "";
  if (!FRAMES.length) return;
  FRAMES.forEach(function (f, idx) {
    const b = document.createElement("button");
    b.className = "tick" + (f.kind === "turn" && f.gi === 1 ? " g1" : "");
    b.dataset.idx = String(idx);
    b.title = f.label;
    b.setAttribute("aria-label", f.label);
    b.onclick = function () { stop(); cur = idx; show(); };
    wrap.appendChild(b);
  });
}
function markTicks() {
  const ticks = document.querySelectorAll("#ticks .tick");
  for (let k = 0; k < ticks.length; k++) {
    ticks[k].classList.toggle("on", ticks[k].dataset.idx === String(cur));
  }
}
function legendItems(f) {
  if (GAME.indexOf("p01") === 0) {
    return [["#6b5136", "dirt"], ["#415466", "bedrock"], ["#1d5f8c", "water"],
            ["#22d3ee", "P0 worm"], ["#fb923c", "P1 worm"]];
  }
  if (GAME.indexOf("p06") === 0) {
    return [["#3f5366", "wall"], ["#1d5f8c", "water"], ["#22d3ee", "P0 mech/core"],
            ["#fb923c", "P1 mech/core"]];
  }
  if (GAME.indexOf("p07") === 0) {
    const rs = frameRunner(f);
    return [["#3f5366", "wall"], ["#172532", "floor"], ["#fbbf24", "bean"],
            ["#4ade80", "power bean"], [SEAT_COLORS[rs], "runner P" + rs],
            [SEAT_COLORS[1 - rs], "ghost P" + (1 - rs)]];
  }
  if (GAME.indexOf("p08") === 0) {
    return [["#22d3ee", "P0 lane/core"], ["#fb923c", "P1 lane/core"],
            ["#f87171", "leak"], ["#4ade80", "clean wave"]];
  }
  return [["#172532", "floor"], ["#3f5366", "wall"], ["#22d3ee", "P0"], ["#fb923c", "P1"]];
}
function buildLegend(f) {
  el("legend").innerHTML = legendItems(f).map(function (it) {
    return '<span class="lg"><span class="dot" style="--c:' + esc(it[0]) + '"></span>' + esc(it[1]) + "</span>";
  }).join("");
}
function phaseInfo(f) {
  if (!f) return {main: "NO FRAMES", sub: "replay has no turn/wave", hud: "—"};
  if (f.kind === "wave") {
    return {
      main: "WAVE " + f.wave + " / " + Math.max(WAVES.length, f.wave),
      sub: "steps " + f.steps + " · leak " + f.leak[0] + " : " + f.leak[1],
      hud: "steps " + f.steps
    };
  }
  const g = f.game || {};
  let sub = f.initial ? "initial state · before action" : "simultaneous";
  if (!f.initial && f.seat !== null) {
    sub = "seat " + f.seat + " to move";
    if (g.first_seat !== undefined) sub = "seat " + f.seat + " · first " + g.first_seat;
  } else if (!f.initial && GAME.indexOf("p07") === 0) {
    const rs = runnerSeatOf(g, f.gi);
    sub = "runner P" + rs + " · ghosts P" + (1 - rs);
  }
  return {main: "GAME " + (g.label || "?") + " · TURN " + f.t, sub: sub, hud: "T" + f.t};
}
function flash(kind) {
  if (RM) return;
  const fl = el("flash");
  fl.className = "";
  void fl.offsetWidth;
  fl.className = "on " + kind;
  setTimeout(function () { if (fl.className === "on " + kind) fl.className = ""; }, 520);
}
function detectImpact(prev, f) {
  if (!prev || !f) return null;
  if (prev.kind === "wave" && f.kind === "wave") {
    if (num(f.leak[0]) + num(f.leak[1]) > 0) return "leak";
    if (prev.core && f.core && (f.core[0] < prev.core[0] || f.core[1] < prev.core[1])) return "core";
    return null;
  }
  if (prev.kind !== "turn" || f.kind !== "turn" || prev.gi !== f.gi) return null;
  const a = prev.state, b = f.state;
  if (!a || !b) return null;
  const nums = function (v) { return Array.isArray(v) ? v.map(num) : null; };
  const as = nums(a.survivors), bs = nums(b.survivors);
  if (as && bs && (bs[0] < as[0] || bs[1] < as[1])) return "leak";
  if (Array.isArray(a.mechs) && Array.isArray(b.mechs)) {
    const alive = function (ms) {
      return ms.filter(function (m) { return m && num(m.hp) > 0; }).length;
    };
    if (alive(b.mechs) < alive(a.mechs)) return "leak";
  }
  const ah = nums(a.hp), bh = nums(b.hp);
  if (ah && bh && (bh[0] < ah[0] || bh[1] < ah[1])) return "core";
  const ac = nums(a.core), bc = nums(b.core);
  if (ac && bc && (bc[0] < ac[0] || bc[1] < ac[1])) return "core";
  if (typeof a.hp === "number" && typeof b.hp === "number" && b.hp < a.hp) return "core";
  return null;
}
function updateBanner() {
  const b = el("banner");
  if (!atEnd() || !FRAMES.length) { b.className = ""; return; }
  const bt = b.querySelector(".bt"), bs = b.querySelector(".bs");
  const w = RES.winner;
  const botSide = GAME.indexOf("p01") === 0 || GAME.indexOf("p06") === 0;
  let title = "REPLAY END", sub = "";
  if (typeof w === "number") {
    if (w < 0) title = "DRAW";
    else title = (botSide ? "BOT " : "SEAT ") + w + " WINS";
    const parts = [];
    if (RES.decided_by) parts.push("by " + RES.decided_by);
    if (RES.reason !== undefined) parts.push("reason " + RES.reason);
    if (RES.wave !== undefined) parts.push("wave " + RES.wave);
    if (Array.isArray(RES.points)) parts.push("points " + RES.points.join(" : "));
    if (RES.match_score !== undefined) parts.push("match " + RES.match_score);
    sub = parts.join(" · ");
  }
  bt.textContent = title;
  bs.textContent = sub;
  let color = "#fbbf24";
  if (typeof w === "number" && w >= 0 && !botSide && w < SEAT_COLORS.length) color = SEAT_COLORS[w];
  b.style.setProperty("--bc", color);
  b.className = "on";
}
function show() {
  if (!FRAMES.length) {
    el("phasePill").textContent = "NO FRAMES";
    el("frameLabel").textContent = "—";
    el("hudLeft").textContent = "NO FRAMES";
    el("hudRight").textContent = "";
    el("frameNo").textContent = "";
    el("orders").innerHTML = '<span class="orderLine">(replay has no turn/wave)</span>';
    el("hash").textContent = "(no hash)";
    el("frameCount").textContent = "0 / 0";
    el("progBar").style.width = "0%";
    renderSeats(seatBase(), null);
    drawScene(null);
    renderWaveTable(null);
    buildLegend(null);
    updateBanner();
    return;
  }
  cur = Math.max(0, Math.min(cur, FRAMES.length - 1));
  const f = FRAMES[cur];
  const prev = (prevIndex >= 0 && prevIndex < FRAMES.length) ? FRAMES[prevIndex] : null;
  const info = phaseInfo(f);
  el("phasePill").textContent = info.main;
  el("frameLabel").textContent = f.label;
  el("frameNo").textContent = f.label;
  el("hudLeft").textContent = info.main;
  el("hudRight").textContent = info.sub;
  renderOrders(f.orders);
  el("hash").textContent = f.hash || "(no hash)";
  el("slider").value = cur;
  el("frameCount").textContent = (cur + 1) + " / " + FRAMES.length;
  el("progBar").style.width = (((cur + 1) / FRAMES.length) * 100).toFixed(2) + "%";
  drawScene(f);
  if (prev && cur > prevIndex) startShotFx(prev, f);
  else clearShotFx();
  renderSeats(seatStats(f), f);
  renderWaveTable(f);
  buildLegend(f);
  markTicks();
  updateBanner();
  if (cur > prevIndex) {
    const impact = detectImpact(prev, f);
    if (impact) flash(impact);
    else if (prev && f.kind === "turn" && prev.gi !== f.gi) flash("switch");
  }
  prevIndex = cur;
}
function stop() {
  if (timer) { clearInterval(timer); timer = null; }
  el("play").textContent = "play";
  el("play").classList.remove("on");
}
function startTimer() {
  timer = setInterval(function () {
    if (cur >= FRAMES.length - 1) { stop(); return; }
    cur++; show();
  }, ms);
}
function togglePlay() {
  if (timer) { stop(); return; }
  if (FRAMES.length < 2) return;
  if (cur >= FRAMES.length - 1) cur = 0;
  el("play").textContent = "pause";
  el("play").classList.add("on");
  startTimer();
}
el("slider").max = Math.max(0, FRAMES.length - 1);
el("slider").oninput = function (e) { cur = +e.target.value; show(); };
el("prev").onclick = function () { stop(); cur = Math.max(0, cur - 1); show(); };
el("next").onclick = function () { stop(); cur = Math.min(FRAMES.length - 1, cur + 1); show(); };
el("play").onclick = togglePlay;
const speedBtns = document.querySelectorAll(".sp");
for (let k = 0; k < speedBtns.length; k++) {
  speedBtns[k].onclick = function () {
    for (let j = 0; j < speedBtns.length; j++) speedBtns[j].classList.remove("on");
    speedBtns[k].classList.add("on");
    ms = +speedBtns[k].dataset.ms;
    if (timer) { clearInterval(timer); startTimer(); }
  };
}
document.addEventListener("keydown", function (e) {
  const tag = (e.target && e.target.tagName) || "";
  if (/INPUT|TEXTAREA|SELECT|BUTTON/.test(tag)) return;
  if (e.key === "ArrowLeft") { stop(); cur = Math.max(0, cur - 1); show(); e.preventDefault(); }
  else if (e.key === "ArrowRight") { stop(); cur = Math.min(FRAMES.length - 1, cur + 1); show(); e.preventDefault(); }
  else if (e.key === " ") { e.preventDefault(); togglePlay(); }
});
el("result").innerHTML = resultSummaryHTML();
el("rawResult").textContent = JSON.stringify(RES, null, 2);
buildTicks();
if (qp.get("anim") === "1" && cur > 0) {
  const target = cur;
  cur = target - 1;
  show();
  setTimeout(function () { cur = target; show(); }, 30);
} else {
  show();
}
</script>
</body></html>
"""


def render_html(rep: dict, out: Path) -> None:
    game = rep["header"].get("game", "?")
    # JSON 只进 <script>：把 < 统一写成 \u003c（同时挡住 </script> 与 <!--），
    # 并转义 U+2028/U+2029，避免提前闭合标签或 JS 换行。
    payload = (json.dumps(rep, ensure_ascii=False)
               .replace("<", "\\u003c")
               .replace("\u2028", "\\u2028")
               .replace("\u2029", "\\u2029"))

    def scalar(value: object) -> str:
        # 标题/元信息是 HTML 文本，必须按 HTML 规则转义。
        return escape(str(value), quote=True)

    values = {
        "GAME": scalar(game),
        "VERSION": scalar(rep["header"].get("version", "-")),
        "MAP": scalar(rep["header"].get("map", rep["map"].get("id", "-"))),
        "SEED": scalar(rep["header"].get("seed", "-")),
        "RULES": scalar(rep["header"].get("rules_hash", "-")),
        "ENGINE": scalar(rep["header"].get("engine_hash", "-")),
        "REPLAY": payload,
    }
    # 单次替换：即使某个字段值本身包含 __GAME__ 之类的字样，也不会被二次替换。
    html_text = re.sub(r"__(GAME|VERSION|MAP|SEED|RULES|ENGINE|REPLAY)__",
                       lambda m: values[m.group(1)], HTML)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_text, encoding="utf-8", newline="\n")


# ------------------------------------------------------------------ CLI

def main() -> int:
    ap = argparse.ArgumentParser(description="题库回放：重算 + HTML 查看器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("verify", help="用引擎逐帧重算并比对 state_hash")
    v.add_argument("--input", required=True, help="JSONL 文件或目录")
    v.add_argument("--json", help="汇总报告路径")

    h = sub.add_parser("html", help="生成单文件 HTML 查看器")
    h.add_argument("--input", required=True, help="JSONL 文件")
    h.add_argument("--out", required=True, help="输出 .html")

    args = ap.parse_args()

    if args.cmd == "html":
        rep = load_replay(Path(args.input))
        render_html(rep, Path(args.out))
        print(f"html {args.out}  ({len(rep['turns'])} turn / {len(rep['waves'])} wave)")
        return 0

    src = Path(args.input)
    files = sorted(src.rglob("*.jsonl")) if src.is_dir() else [src]
    if not files:
        raise SystemExit(f"没有找到回放：{src}")

    rows = []
    all_ok = True
    for f in files:
        try:
            r = verify(f)
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] {f}: {exc!r}")
            all_ok = False
            continue
        rows.append(r)
        if not r["ok"]:
            all_ok = False
        mark = "PASS" if r["ok"] else "FAIL"
        stale = "  (旧引擎)" if r.get("stale_engine") else ""
        print(f"[{mark}] {f.name}: {r['game']}  {r['frames']} 帧  "
              f"hash 不一致 {len(r['hash_mismatch'])}  "
              f"结果不一致 {len(r['result_mismatch'])}{stale}")
        for x in r["hash_mismatch"][:3]:
            print(f"        {x['game']} t{x['t']}: 回放 {x['want']} vs 引擎 {x['got']}")
        for x in r["result_mismatch"][:3]:
            print(f"        {x['what']}: {x['detail']}")

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"tool": "replay", "ok": all_ok, "reports": rows},
                                ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
