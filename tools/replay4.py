#!/usr/bin/env python3
"""P01 v2 · 4 席回放：逐回合重算校验 + 单文件 HTML 查看器。

    python tools/replay4.py verify --input build/replay4/
    python tools/replay4.py verify --input build/replay4/xxx.jsonl --json build/replay4/verify.json
    python tools/replay4.py html   --input build/replay4/xxx.jsonl --out build/replay4/xxx.html

只认 4 席 schema（judge4.py --replay-dir 产出）：

    {"type":"header","game":"p01-ballistic","version":"2.0.0",...,
     "seed":1,"map":"public4-01","active_mask":15,"seats":4,
     "seat_perm":[0,1,2,3],"bots":["random4","greedy4","scripted4","strong4"]}
    {"type":"map","id":"public4-01","grid":[54 行 96 字符],
     "initial":{"initial":true,"t":0,"worms":[12 条],"grid":[54 行]}}
    {"type":"turn","t":1,"orders":{"0":"...","1":"..."},"active_mask":15,
     "state_hash":"...","wind":-9,
     "shots":[{"seat":0,"worm":1,"kind":"ROCKET","angle":144,"power":49,"x":14,"y":28}],
     "hp":[4],"dealt":[4],"bad":[4],
     "worms":[[x,y,hp,grenades,drills,alive]×12],"grid":[54 行,可选]}
    {"type":"result","winner":seat,"winner_bot":i,"winner_seat":seat,
     "reason":"...","rounds":N,"faults":N,"forfeit":...,"state_hash":"..."}

legacy 2 席回放（tools/replay.py 的 v1.x schema）会被明确拒绝，原 tools/replay.py
保持不变。查看器只读 JSONL，不启动引擎；重算依赖 map + header(seed/mask) + 每回合 orders；map.initial 保存并校验开局帧，state_hash 不参与重建。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PROBLEMS = REPO / "problems"
BUILD = REPO / "build"

GAME_ID = "p01-ballistic"
SEATS = 4
WORMS = SEATS * 3
GRID_H = 54
GRID_W = 96
STATE_FIELDS = 20 + WORMS * 6


class ReplayError(RuntimeError):
    pass


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ReplayError(msg)


def _popcount(n: int) -> int:
    return bin(n).count("1")


def _as_int(v, default: int = -1) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------ 读取

def load_replay(path: Path) -> dict:
    """读一份 4 席 JSONL；schema 不对直接抛 ReplayError。"""
    _require(path.is_file(), f"回放文件不存在：{path}")
    rep = {"header": {}, "map": {}, "turns": [], "result": {}}
    unknown: list[tuple[int, object]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReplayError(f"第 {lineno} 行不是合法 JSON：{exc}") from exc
        _require(isinstance(rec, dict), f"第 {lineno} 行不是对象")
        t = rec.get("type")
        if t == "header":
            rep["header"] = rec
        elif t == "map":
            rep["map"] = rec
        elif t == "turn":
            rep["turns"].append(rec)
        elif t == "result":
            rep["result"] = rec
        else:
            unknown.append((lineno, t))
    # 先校验 header，legacy 2 席回放才能得到“不是 4 席 schema”的明确提示，
    # 而不是先撞上它的 game/turn 旧记录。
    _check_schema(rep, path)
    if unknown:
        lineno, t = unknown[0]
        raise ReplayError(
            f"第 {lineno} 行 type={t!r} 不是 4 席 replay 记录"
            "（legacy 2 席回放请用 tools/replay.py）")
    return rep


def _check_schema(rep: dict, path: Path) -> None:
    h = rep["header"]
    _require(bool(h), f"{path.name} 缺少 header 行")
    _require(h.get("type") == "header", "第一类记录不是 header")
    _require(h.get("game") == GAME_ID,
             f"game 不是 {GAME_ID}：{h.get('game')!r}")
    # 4 席 schema 的硬特征：header 明确写 seats/active_mask。legacy 2 席没有这两个字段。
    _require("seats" in h and "active_mask" in h,
             "不是 4 席 replay schema：header 缺少 seats/active_mask"
             "（legacy 2 席回放请用 tools/replay.py；replay4 只认 judge4 --replay-dir 的 4 席 JSONL）")
    _require(h.get("seats") == SEATS,
             f"不是 4 席 replay schema：header.seats={h.get('seats')!r}")
    mask = h.get("active_mask")
    _require(isinstance(mask, int) and 1 <= mask < (1 << SEATS),
             f"active_mask 非法：{mask!r}")
    _require(_popcount(mask) >= 2,
             f"active_mask={mask} 少于 2 席，引擎会退回 legacy 布局")
    _require(mask != 0b0011,
             "active_mask=3 会让引擎输出 legacy 2 席 STATE 布局，不是 4 席 schema")
    _require(isinstance(h.get("seed"), int), f"header.seed 不是整数：{h.get('seed')!r}")
    _require(isinstance(h.get("seat_perm"), list) and len(h["seat_perm"]) == _popcount(mask),
             f"header.seat_perm 与 active_mask 不匹配：{h.get('seat_perm')!r}")
    _require(isinstance(h.get("bots"), list),
             "header.bots 必须是数组（按 bot 序号）")

    grid = rep["map"].get("grid")
    _require(bool(grid), f"{path.name} 缺少 map 行")
    _require(len(grid) == GRID_H and all(isinstance(r, str) and len(r) == GRID_W for r in grid),
             f"map.grid 不是 {GRID_W}x{GRID_H}")
    initial = rep["map"].get("initial")
    if initial is not None:
        _require(isinstance(initial, dict) and initial.get("initial") is True
                 and initial.get("t") == 0,
                 f"{path.name} 的 map.initial 标记无效")
        for key in ("state_hash", "hp", "dealt", "bad", "worms", "grid"):
            _require(key in initial, f"{path.name} 的开局帧缺少 {key}")
        _require(isinstance(initial["worms"], list) and len(initial["worms"]) == WORMS
                 and all(isinstance(w, list) and len(w) == 6
                         and all(isinstance(x, int) for x in w) for w in initial["worms"]),
                 f"{path.name} 的开局帧虫数据不完整")
        for key in ("hp", "dealt", "bad"):
            _require(isinstance(initial.get(key), list) and len(initial[key]) == SEATS
                     and all(isinstance(x, int) for x in initial[key]),
                     f"{path.name} 的开局帧 {key} 数据不完整")
        _require(isinstance(initial["grid"], list) and len(initial["grid"]) == GRID_H
                 and all(isinstance(row, str) and len(row) == GRID_W for row in initial["grid"]),
                 f"{path.name} 的开局地形不是 {GRID_W}x{GRID_H}")

    _require(bool(rep["turns"]), f"{path.name} 没有任何 turn 行")
    for idx, turn in enumerate(rep["turns"], 1):
        _require(turn.get("t") == idx,
                 f"turn.t 必须从 1 连续递增（第 {idx} 条是 {turn.get('t')!r}）")
        _require(turn.get("active_mask", mask) == mask,
                 f"t{turn.get('t')} 的 active_mask 与 header 不一致")
        orders = turn.get("orders")
        _require(isinstance(orders, dict) and bool(orders), f"t{idx} 缺少 orders")
        for seat in range(SEATS):
            if (mask >> seat) & 1:
                _require(isinstance(orders.get(str(seat)), str),
                         f"t{idx} 缺少 active seat {seat} 的 orders")
        _require(isinstance(turn.get("state_hash"), str) and bool(turn["state_hash"]),
                 f"t{idx} 缺少 state_hash")
        for key in ("hp", "dealt", "bad"):
            _require(isinstance(turn.get(key), list) and len(turn[key]) == SEATS
                     and all(isinstance(x, int) for x in turn[key]),
                     f"t{idx} 的 {key} 不是 {SEATS} 个整数")
        worms = turn.get("worms")
        _require(isinstance(worms, list) and len(worms) == WORMS,
                 f"t{idx} 的 worms 不是 {WORMS} 条")
        _require(all(isinstance(w, list) and len(w) == 6
                     and all(isinstance(x, int) for x in w) for w in worms),
                 f"t{idx} 的 worms 每条必须是 6 个整数")


def judge_module(game_dir: Path):
    p = game_dir / "public" / "judge" / "judge.py"
    name = "_replay4_" + game_dir.name.replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, p)
    if spec is None or spec.loader is None:
        raise ReplayError(f"无法加载裁判：{p}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ 引擎

class Engine:
    """最小 4 席引擎客户端：STEP 用 ' | ' 串 4 段。"""

    def __init__(self, exe: Path):
        self.proc = subprocess.Popen(
            [str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1, encoding="utf-8",
        )

    def cmd(self, line: str) -> str:
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        out = self.proc.stdout.readline()
        if not out:
            raise ReplayError("engine 提前退出")
        return out.rstrip("\r\n")

    def grid(self) -> list[str]:
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.proc.stdin.write("GRID\n")
        self.proc.stdin.flush()
        rows: list[str] = []
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise ReplayError("engine 在读取初始 GRID 时提前退出")
            line = line.rstrip("\r\n")
            if line == "GEND":
                break
            if line.startswith("G "):
                rows.append(line[2:])
            elif line.startswith("ERR"):
                raise ReplayError(f"读取 GRID 失败：{line}")
        _require(len(rows) == GRID_H and all(len(row) == GRID_W for row in rows),
                 f"初始 GRID 不是 {GRID_W}x{GRID_H}")
        return rows

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.kill()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=2)
        except Exception:
            pass


def parse_state4(line: str) -> dict:
    """解析 4 席 STATE：round wind water over winner hp×4 dealt×4 reason bad×4 hash worm×12。"""
    p = line.split()
    if not p or p[0] != "STATE":
        raise ReplayError(f"不是 STATE 行：{line[:120]}")
    if len(p) < STATE_FIELDS:
        raise ReplayError(
            f"STATE 只有 {len(p)} 个字段（4 席布局需要 {STATE_FIELDS}）；"
            "如果 active_mask=3 会退回 legacy 2 席布局")
    return {
        "round": int(p[1]),
        "wind": int(p[2]) - 32,
        "water": int(p[3]),
        "over": p[4] == "1",
        "winner": int(p[5]),
        "hp": [int(p[6 + s]) for s in range(SEATS)],
        "dealt": [int(p[10 + s]) for s in range(SEATS)],
        "reason": p[14],
        "bad": [int(p[15 + s]) for s in range(SEATS)],
        "hash": p[19],
        "worms": [[int(x) for x in p[20 + i * 6:26 + i * 6]] for i in range(WORMS)],
    }


def _write_map(rep: dict, tmp: Path) -> Path:
    mid = str(rep["map"].get("id") or rep["header"].get("map") or "map")
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in mid)
    p = tmp / f"{safe}.txt"
    p.write_text("\n".join(rep["map"]["grid"]) + "\n", encoding="utf-8", newline="\n")
    return p


# ------------------------------------------------------------------ 重算

class Report:
    MAX_ENTRIES = 60

    def __init__(self, path: Path) -> None:
        self.out = {
            "tool": "replay4", "ok": True, "input": str(path), "file": path.name,
            "map": "", "seed": None, "active_mask": None,
            "turns": 0, "final": {},
            "hash_mismatch": [], "state_mismatch": [],
            "result_mismatch": [], "errors": [],
            "engine_hash": "", "current_engine_hash": "", "stale_engine": False,
        }
        self.dropped = 0

    def mismatch(self, bucket: str, entry: dict) -> None:
        self.out["ok"] = False
        arr = self.out[bucket]
        if len(arr) < self.MAX_ENTRIES:
            arr.append(entry)
        else:
            self.dropped += 1
            self.out["truncated"] = True

    def error(self, msg: str) -> None:
        self.out["ok"] = False
        if len(self.out["errors"]) < self.MAX_ENTRIES:
            self.out["errors"].append(msg)


def _check_turn(turn: dict, st: dict) -> list[dict]:
    bad = []
    t = turn["t"]
    if str(turn.get("state_hash")) != st["hash"]:
        bad.append({"bucket": "hash_mismatch", "t": t,
                    "what": "state_hash", "want": turn.get("state_hash"), "got": st["hash"]})
    for key in ("hp", "dealt", "bad"):
        want = [int(x) for x in turn[key]]
        if want != st[key]:
            bad.append({"bucket": "state_mismatch", "t": t,
                        "what": key, "want": want, "got": st[key]})
    want_worms = [[int(x) for x in w] for w in turn["worms"]]
    if want_worms != st["worms"]:
        for i, (a, b) in enumerate(zip(want_worms, st["worms"])):
            if a != b:
                bad.append({"bucket": "state_mismatch", "t": t,
                            "what": f"worm {i} [x,y,hp,g,d,alive]",
                            "want": a, "got": b})
    return bad


def verify_file(path: Path, game_dir: Path, exe: Path) -> dict:
    rep = load_replay(path)
    h = rep["header"]
    mask = int(h["active_mask"])
    seed = int(h["seed"])
    r = Report(path)
    r.out.update({"game": h.get("game"), "map": rep["map"].get("id") or h.get("map"),
                  "seed": seed, "active_mask": mask, "turns": len(rep["turns"])})
    meta_path = game_dir / "meta.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        meta = {}
    r.out["engine_hash"] = h.get("engine_hash", "")
    r.out["current_engine_hash"] = meta.get("engine_hash", "")
    r.out["rules_hash"] = h.get("rules_hash", "")
    r.out["current_rules_hash"] = meta.get("rules_hash", "")
    if r.out["engine_hash"] and r.out["current_engine_hash"] \
            and r.out["engine_hash"] != r.out["current_engine_hash"]:
        r.out["stale_engine"] = True

    BUILD.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="replay4-", dir=str(BUILD)) as td:
        mp = _write_map(rep, Path(td))
        e = Engine(exe)
        last = None
        try:
            load = e.cmd(f"LOAD {mp.as_posix()} {seed} {mask}")
            if not load.startswith("OK"):
                r.error(f"LOAD 失败：{load}")
            else:
                initial = rep["map"].get("initial")
                if initial is not None:
                    initial_state = parse_state4(e.cmd("STATE"))
                    for item in _check_turn(initial, initial_state):
                        bucket = item.pop("bucket")
                        item["what"] = "initial " + item.get("what", "state")
                        r.mismatch(bucket, item)
                    initial_grid = e.grid()
                    if initial.get("grid") != initial_grid:
                        r.mismatch("state_mismatch", {"what": "initial grid",
                                                       "want": "recorded terrain",
                                                       "got": "recomputed terrain"})
                    for key in ("round", "wind", "water", "over", "winner", "reason"):
                        if initial.get(key) != initial_state[key]:
                            r.mismatch("state_mismatch", {"what": "initial " + key,
                                                           "want": initial.get(key),
                                                           "got": initial_state[key]})
                for turn in rep["turns"]:
                    segs = []
                    for seat in range(SEATS):
                        if (mask >> seat) & 1:
                            segs.append(turn["orders"][str(seat)])
                        else:
                            segs.append("0 WAIT")   # 与 judge4 一致：inactive seat 的槽位不参与
                    line = e.cmd("STEP " + " | ".join(segs))
                    if line.startswith("ERR"):
                        r.error(f"t{turn['t']} 引擎报错：{line}")
                        break
                    st = parse_state4(line)
                    last = st
                    for item in _check_turn(turn, st):
                        bucket = item.pop("bucket")
                        r.mismatch(bucket, item)
        except ReplayError as exc:
            r.error(str(exc))
        finally:
            e.close()

    if last is None:
        r.error("没有重算出任何一帧")
        return r.out

    r.out["final"] = {k: last[k] for k in ("round", "winner", "reason", "hp", "dealt")}
    res = rep.get("result") or {}
    if not res:
        r.mismatch("result_mismatch", {"what": "result 行", "detail": "回放缺少 result"})
    else:
        n = len(rep["turns"])
        if _as_int(res.get("rounds")) != n:
            r.mismatch("result_mismatch",
                       {"what": "rounds", "want": n, "got": res.get("rounds")})
        if str(res.get("state_hash") or "") != last["hash"]:
            r.mismatch("result_mismatch",
                       {"what": "final state_hash",
                        "want": res.get("state_hash"), "got": last["hash"]})
        perm = h.get("seat_perm") or []
        winner = _as_int(res.get("winner"))
        if winner >= 0:
            if res.get("winner_seat") != winner:
                r.mismatch("result_mismatch",
                           {"what": "winner_seat", "want": winner,
                            "got": res.get("winner_seat")})
            wb = _as_int(res.get("winner_bot"))
            if wb >= 0 and (wb >= len(perm) or perm[wb] != winner):
                r.mismatch("result_mismatch",
                           {"what": "winner_bot", "want": f"seat {winner}",
                            "got": f"bot {wb} -> {perm[wb] if wb < len(perm) else '?'}"})
        if _as_int(res.get("forfeit")) < 0:
            # FORFEIT 的 winner/reason 是裁判判罚结果，不要求引擎状态一致；
            # 其余情况 result 直接取自最终 STATE，必须一致。
            if res.get("winner") != last["winner"]:
                r.mismatch("result_mismatch",
                           {"what": "winner", "want": last["winner"],
                            "got": res.get("winner")})
            if res.get("reason") != last["reason"]:
                r.mismatch("result_mismatch",
                           {"what": "reason", "want": last["reason"],
                            "got": res.get("reason")})
    return r.out


# ------------------------------------------------------------------ HTML

HTML = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{
  --bg0:#172033;--bg1:#223049;--panel:#1e2a3c;--panel2:#28374d;
  --line:#394d68;--line2:#4b6386;
  --fg:#eef4fb;--dim:#b7c7da;--dim2:#94a9c0;
  --s0:#34d7ec;--s1:#f9a35c;--s2:#b4e85f;--s3:#e78bf2;
  --warn:#fbbf24;--bad:#f87171;--accent:#34d7ec;--bw:1056px;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
}
*{box-sizing:border-box}
html,body{margin:0;min-height:100%}
body{
  background:
    radial-gradient(1100px 600px at 14% -12%,rgba(70,206,235,.20),transparent 62%),
    radial-gradient(980px 560px at 86% -10%,rgba(200,130,246,.16),transparent 64%),
    linear-gradient(158deg,var(--bg0) 0%,#1d2a3e 52%,var(--bg1) 100%);
  color:var(--fg);font:13px/1.5 var(--mono);-webkit-font-smoothing:antialiased;
}
header{display:flex;align-items:center;gap:14px;flex-wrap:wrap;padding:12px 20px 11px;
  border-bottom:1px solid var(--line2);
  background:linear-gradient(180deg,rgba(38,52,74,.96),rgba(30,42,60,.88))}
header h1{margin:0;font-size:15px;letter-spacing:.16em;font-weight:600;display:flex;align-items:center;gap:9px}
header h1 .brand{color:#04121a;background:linear-gradient(120deg,var(--s0),var(--s2));padding:2px 7px;
  border-radius:4px;font-size:12px;letter-spacing:.1em}
.meta{display:flex;gap:7px;flex-wrap:wrap}
.tag{font-size:11px;color:var(--dim);border:1px solid var(--line);border-radius:999px;
  padding:2px 9px;background:rgba(44,59,84,.75)}
.tag b{color:var(--fg);font-weight:500}
.clock{margin-left:auto;display:flex;align-items:center;gap:9px;font-size:12px;color:var(--dim)}
#turnPill{color:var(--fg);font-weight:600;letter-spacing:.06em}
#progWrap{display:block;width:132px;height:3px;background:#31425c;border-radius:99px;overflow:hidden;
  box-shadow:inset 0 0 0 1px rgba(150,190,225,.18)}
#progBar{display:block;height:100%;width:0;border-radius:99px;
  background:linear-gradient(90deg,var(--s0),var(--s3));transition:width .18s ease}
main{display:grid;grid-template-columns:minmax(0,1fr) 366px;align-items:start}
#stage{padding:16px 18px 20px;display:flex;flex-direction:column;align-items:center;gap:10px;min-width:0}
#frame{position:relative;width:100%;min-width:0;max-width:var(--bw);border-radius:10px;overflow:hidden;
  border:1px solid var(--line2);background:#1d2b3e;
  box-shadow:0 22px 58px -30px rgba(6,12,22,.9),0 0 0 1px rgba(110,205,235,.16) inset}
#board{display:block;width:100%;max-width:100%;min-width:0;height:auto;image-rendering:pixelated}
#vignette{position:absolute;inset:0;pointer-events:none;
  background:radial-gradient(120% 95% at 50% 38%,transparent 60%,rgba(12,21,35,.34) 100%);
  box-shadow:inset 0 0 64px 8px rgba(10,18,30,.20)}
#flash{position:absolute;inset:0;pointer-events:none;opacity:0;
  background:radial-gradient(70% 60% at 50% 50%,rgba(255,206,126,.34),transparent 70%)}
#flash.on{animation:flash .34s ease-out}
@keyframes flash{0%{opacity:.75}100%{opacity:0}}
#hud{position:absolute;inset:0;pointer-events:none;display:flex;justify-content:space-between;
  align-items:flex-start;padding:8px 10px;font-size:11px;letter-spacing:.08em}
#hud span{background:rgba(26,38,56,.82);border:1px solid rgba(96,128,166,.9);color:#e8f2fc;
  padding:2px 8px;border-radius:999px;backdrop-filter:blur(2px)}
#hud .accent{color:var(--s0)}
#banner{position:absolute;left:50%;top:46px;transform:translate(-50%,-6px);text-align:center;
  pointer-events:none;opacity:0;transition:opacity .3s ease,transform .3s ease;
  padding:10px 22px;border-radius:10px;background:linear-gradient(180deg,rgba(30,42,61,.94),rgba(23,32,49,.86));
  border:1px solid var(--bc,#34d7ec);box-shadow:0 0 36px -6px var(--bc,#34d7ec),0 0 0 1px rgba(255,255,255,.06) inset}
#banner.on{opacity:1;transform:translate(-50%,0);animation:bannerPulse 2.6s ease-in-out infinite}
@keyframes bannerPulse{
  0%,100%{box-shadow:0 0 30px -8px var(--bc,#34d7ec),0 0 0 1px rgba(255,255,255,.06) inset}
  50%{box-shadow:0 0 48px -4px var(--bc,#34d7ec),0 0 0 1px rgba(255,255,255,.12) inset}
}
#banner .bt{font-size:clamp(18px,2.6vw,30px);font-weight:700;letter-spacing:.14em;color:#fff;
  text-shadow:0 0 18px var(--bc,#34d7ec)}
#banner .bs{margin-top:2px;font-size:clamp(10px,1.1vw,12px);color:#c9d9ea;letter-spacing:.1em}
#banner .bs b{color:#fff;font-weight:500}
#legend{display:flex;gap:7px;flex-wrap:wrap;justify-content:center}
.lg{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);border-radius:999px;
  padding:3px 10px;background:rgba(38,52,74,.8);font-size:11px;color:var(--dim)}
.lg .dot{width:8px;height:8px;border-radius:50%;background:var(--c);box-shadow:0 0 8px var(--c);flex:0 0 auto}
.lg b{color:var(--fg);font-weight:600}
.lg .nm{color:var(--dim);max-width:132px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.lg .al{color:var(--fg);font-variant-numeric:tabular-nums}
.lg.inactive{opacity:.35}
.lg.dead{opacity:.55}
.lg.dead .al{color:var(--bad)}
.lg.win{border-color:var(--c);box-shadow:0 0 16px -4px var(--c)}
#under{color:var(--dim2);font-size:10.5px;letter-spacing:.06em;text-align:center}
#under b{color:var(--dim);font-weight:400}
aside{border-left:1px solid var(--line);padding:14px;display:flex;flex-direction:column;gap:10px;
  min-width:0;background:rgba(30,42,61,.55)}
.card{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);
  border-radius:7px;padding:10px 11px}
.k{color:var(--dim2);font-size:10px;letter-spacing:.16em;text-transform:uppercase;margin-bottom:7px}
#result{font-size:12px;line-height:1.7;color:var(--dim);white-space:pre-wrap;transition:opacity .25s}
#result.pre{opacity:.8}
#result .win{color:var(--fg);font-weight:600}
#result .wse{color:var(--wc,#eef4fb);text-shadow:0 0 10px var(--wc,#eef4fb)}
.seat{--c:#34d7ec;border:1px solid var(--line);border-left:3px solid var(--c);border-radius:6px;
  padding:7px 9px;margin-bottom:7px;background:rgba(38,52,74,.6);position:relative;overflow:hidden;
  transition:opacity .2s,box-shadow .25s}
.seat:last-child{margin-bottom:0}
.seat.inactive{opacity:.42}
.seat.dead{opacity:.72}
.seat.winner{box-shadow:0 0 0 1px var(--c) inset,0 0 26px -8px var(--c)}
.seat.hit{animation:seatHit .5s ease-out}
@keyframes seatHit{0%{background:rgba(255,255,255,.10)}100%{background:rgba(38,52,74,.6)}}
.seathead{display:flex;align-items:center;gap:7px;font-size:12px;min-width:0}
.seathead .dot{width:8px;height:8px;border-radius:50%;background:var(--c);
  box-shadow:0 0 8px var(--c);flex:0 0 auto}
.seathead .sn{color:var(--fg);font-weight:600}
.seathead .bot{color:var(--dim);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.seathead .hpnum{margin-left:auto;font-size:11px;color:var(--fg);font-variant-numeric:tabular-nums}
.seathead .hpnum small{color:var(--dim2);font-size:9.5px}
.hpbar{margin:6px 0 5px;height:4px;border-radius:99px;background:#1a2739;overflow:hidden;
  box-shadow:0 0 0 1px rgba(255,255,255,.07) inset}
.hpbar i{display:block;height:100%;width:0;border-radius:99px;
  background:linear-gradient(90deg,var(--c),#fff);transition:width .32s cubic-bezier(.3,.8,.3,1)}
.seat.dead .hpbar i{background:#5a6b82}
.chips{display:flex;gap:5px;flex-wrap:wrap}
.chip{border:1px solid var(--line);border-radius:3px;padding:1px 5px;font-size:10.5px;color:var(--fg);
  font-variant-numeric:tabular-nums;background:rgba(255,255,255,.03)}
.chip.out{color:var(--dim2);border-color:#44566f;text-decoration:line-through}
.chip.low{color:var(--warn);border-color:#7a5c22}
.seatfoot{display:flex;justify-content:space-between;margin-top:5px;font-size:10px;
  color:var(--dim2);letter-spacing:.06em}
.seatfoot .seatstate{color:var(--dim)}
.seat.winner .seatstate{color:var(--c)}
.orders{white-space:pre-wrap;font-size:11.5px;color:#c8ecff;word-break:break-word;line-height:1.6}
.hash{font-size:10.5px;color:var(--dim);word-break:break-all;line-height:1.6}
.dim{color:var(--dim2)}
footer{position:sticky;bottom:0;z-index:10;display:flex;gap:9px;align-items:center;flex-wrap:wrap;
  padding:10px 20px;border-top:1px solid var(--line2);
  background:linear-gradient(180deg,rgba(31,43,62,.88),rgba(25,35,51,.98));backdrop-filter:blur(8px)}
button{font:inherit;font-size:12px;color:var(--fg);background:linear-gradient(180deg,#33455f,#26344a);
  border:1px solid var(--line2);border-radius:5px;padding:6px 12px;cursor:pointer;
  transition:border-color .15s,color .15s,transform .08s}
button:hover{border-color:var(--accent);color:#fff;background:linear-gradient(180deg,#3b4f6d,#2a3a52)}
button:active{transform:translateY(1px)}
button.on{border-color:var(--accent);color:var(--accent);box-shadow:0 0 12px -4px var(--accent)}
#play{min-width:78px}
#sliderWrap{flex:1;min-width:180px;display:flex;align-items:center;gap:8px;padding:0 4px}
#sliderT{font-size:10.5px;color:var(--dim2);min-width:30px;text-align:right}
input[type=range]{flex:1;min-width:120px;height:4px;appearance:none;-webkit-appearance:none;outline:none;
  border-radius:99px;background:linear-gradient(90deg,var(--s0) 0%,var(--s3) var(--p,0%),#33445f var(--p,0%))}
input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:12px;height:12px;border-radius:50%;
  background:#f4faff;border:2px solid #26344a;box-shadow:0 0 10px rgba(52,215,236,.75);cursor:pointer}
.speeds{display:flex;gap:5px}
.speeds button{padding:5px 8px;font-size:11px;color:var(--dim)}
.speeds button.on{color:var(--accent)}
#hint{color:var(--dim2);font-size:10.5px;letter-spacing:.04em}
@media(max-width:1080px){
  main{grid-template-columns:1fr}
  aside{border-left:0;border-top:1px solid var(--line);display:grid;
    grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px}
  #stage{padding:12px 10px 16px}
}
@media(max-width:640px){
  header{padding:10px 12px;gap:9px}
  header h1{font-size:13px}
  .clock{margin-left:0;width:100%}
  #progWrap{flex:1;width:auto}
  #banner{top:36px;padding:8px 14px}
  footer{padding:9px 12px;gap:7px}
  #hint{display:none}
  #legend{gap:5px}
  .lg{padding:2px 7px}
  .lg .nm{max-width:88px}
}
@media(prefers-reduced-motion:reduce){
  *{animation-duration:.001ms !important;transition-duration:.001ms !important}
}
</style></head>
<body>
<header>
  <h1><span class="brand">P01</span> 4-SEAT ARENA</h1>
  <div class="meta">
    <span class="tag">map <b>__MAP__</b></span>
    <span class="tag">seed <b>__SEED__</b></span>
    <span class="tag">mask <b>__MASK__</b></span>
    <span class="tag">perm <b>__PERM__</b></span>
  </div>
  <div class="clock"><span id="turnPill">t1 / __TURNS__</span><span id="progWrap"><i id="progBar"></i></span></div>
</header>
<main>
  <section id="stage">
    <div id="frame">
      <canvas id="board" width="96" height="54" aria-label="4 seat battlefield"></canvas>
      <div id="vignette"></div>
      <div id="flash"></div>
      <div id="hud"><span id="hudTurn" class="accent">T 1</span><span id="hudAlive">alive 12/12</span></div>
      <div id="banner"><div class="bt" id="bannerTitle"></div><div class="bs" id="bannerSub"></div></div>
    </div>
    <div id="legend"></div>
    <div id="under">12 worms · 4 seats · <span id="aliveCount">alive 12/12</span> · <span id="gridSrc">terrain</span></div>
  </section>
  <aside>
    <div class="card"><div class="k">result</div><div id="result"></div></div>
    <div class="card"><div class="k">seats · hp · status</div><div id="seats"></div></div>
    <div class="card"><div class="k">orders · t<span id="ordersT">1</span></div><div class="orders" id="orders"></div></div>
    <div class="card"><div class="k">state hash</div><div class="hash" id="hash">-</div></div>
    <div class="card"><div class="k">rules / engine</div><div class="hash">__RULES__<br>__ENGINE__</div></div>
  </aside>
</main>
<footer>
  <button id="prev" title="上一轮 (←)">‹ prev</button>
  <button id="play" title="播放 / 暂停 (space)">play</button>
  <button id="next" title="下一轮 (→)">next ›</button>
  <div id="sliderWrap"><input id="slider" type="range" min="0" max="0" value="0" aria-label="回合"><span id="sliderT">t1</span></div>
  <div class="speeds">
    <button class="sp" data-ms="700">0.7s</button>
    <button class="sp on" data-ms="250">0.25s</button>
    <button class="sp" data-ms="80">0.08s</button>
  </div>
  <span id="hint">← → 切换 · space 播放</span>
</footer>
<script>
(function () {
"use strict";
const NL = String.fromCharCode(10);
const R = __PAYLOAD__;
const TURNS = Array.isArray(R.turns) ? R.turns : [];
const ROUND_COUNT = Math.max(0, TURNS.length - ((TURNS[0] && TURNS[0].initial) ? 1 : 0));
const BASE = (R.map && Array.isArray(R.map.grid)) ? R.map.grid : [];
const H = BASE.length;
const W = H ? String(BASE[0] || "").length : 0;
const RES = (R.result && typeof R.result === "object") ? R.result : {};
const HD = (R.header && typeof R.header === "object") ? R.header : {};
const MASK = (HD.active_mask === undefined || HD.active_mask === null) ? 15 : (HD.active_mask | 0);
const PERM = Array.isArray(HD.seat_perm) ? HD.seat_perm : [];
const BOTS = Array.isArray(HD.bots) ? HD.bots : [];
const SEATS = 4, WORMS = 12;
const LAST = Math.max(0, TURNS.length - 1);
const RM = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
const el = function (id) { return document.getElementById(id); };

/* ---------- 4 席配色：cyan / orange / lime / magenta，避免红绿混淆 ---------- */
const C = [
  { name: "cyan",    c: "#34d7ec", deep: "#0e7490" },
  { name: "orange",  c: "#f9a35c", deep: "#9a3412" },
  { name: "lime",    c: "#b4e85f", deep: "#4d7c0f" },
  { name: "magenta", c: "#e78bf2", deep: "#86198f" }
];
const RGB = C.map(function (o) { return hex2rgb(o.c); });
function hex2rgb(h) {
  const n = parseInt(h.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
function rgba(seat, a) { const p = RGB[seat]; return "rgba(" + p[0] + "," + p[1] + "," + p[2] + "," + a + ")"; }
function rgba3(p, a) { return "rgba(" + p[0] + "," + p[1] + "," + p[2] + "," + a + ")"; }

/* 地形调色板：# 泥土（暖色可破坏）/ X 基岩（冷色）/ ~ 水 */
const TER = {
  "#": { base: [100, 76, 54], top: [164, 124, 86], bot: [64, 45, 31], water: 0 },
  "X": { base: [84, 98, 120], top: [150, 168, 194], bot: [46, 56, 72], water: 0 },
  "~": { base: [44, 114, 156], top: [88, 174, 210], bot: [24, 72, 104], water: 1 }
};
function solidCh(ch) { return ch === "#" || ch === "X" || ch === "~"; }

/* ---------- header 兼容：bot ids / labels / meta 都尝试，缺失则 fallback ---------- */
function pickFrom(x, i) {
  if (x === undefined || x === null) return null;
  if (Array.isArray(x)) return (x[i] === undefined ? null : x[i]);
  if (typeof x === "object") {
    const keys = [BOTS[i], String(i), i];
    for (let k = 0; k < keys.length; k++) {
      const key = keys[k];
      if (key === undefined || key === null) continue;
      if (x[key] !== undefined) return x[key];
    }
  }
  return null;
}
function metaStr(m) {
  if (m === undefined || m === null) return null;
  if (typeof m === "string" || typeof m === "number") return String(m);
  if (typeof m === "object") {
    const keys = ["label", "name", "display_name", "displayName", "title"];
    for (let k = 0; k < keys.length; k++) {
      const v = m[keys[k]];
      if (typeof v === "string" && v) return v;
    }
  }
  return null;
}
function botLabel(i) {
  if (i < 0) return "";
  let v = metaStr(pickFrom(HD.bot_labels, i)) || metaStr(pickFrom(HD.bot_meta, i)) ||
          metaStr(pickFrom(HD.bot_ids, i)) || metaStr(BOTS[i]);
  if (!v) v = "bot" + i;
  return v;
}
function seatLabel(s) {
  const i = PERM.indexOf(s);
  return i >= 0 ? botLabel(i) : "";
}
function isActive(s) { return ((MASK >> s) & 1) === 1; }
function esc(v) {
  return String(v === undefined || v === null ? "" : v).replace(/[&<>"']/g, function (ch) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
  });
}

/* ---------- canvas ---------- */
const cv = el("board"), ctx = cv.getContext("2d");
const CELL = Math.max(8, Math.min(12, Math.floor(1120 / Math.max(1, W))));
cv.width = Math.max(1, W * CELL);
cv.height = Math.max(1, H * CELL);
document.documentElement.style.setProperty("--bw", cv.width + "px");
const TC = document.createElement("canvas");
TC.width = cv.width; TC.height = cv.height;
const tctx = TC.getContext("2d");
const flashEl = el("flash"), bannerEl = el("banner");

/* worm 光晕精灵：每个 seat 预渲染一次，绘制时直接贴图，避免每帧 shadowBlur */
function makeGlow(seat) {
  const d = Math.max(18, Math.round(CELL * 3));
  const cnv = document.createElement("canvas");
  cnv.width = d; cnv.height = d;
  const g = cnv.getContext("2d");
  const grd = g.createRadialGradient(d / 2, d / 2, 0, d / 2, d / 2, d / 2);
  grd.addColorStop(0, rgba(seat, 0.42));
  grd.addColorStop(0.4, rgba(seat, 0.15));
  grd.addColorStop(1, rgba(seat, 0));
  g.fillStyle = grd;
  g.fillRect(0, 0, d, d);
  return cnv;
}
const GLOW = C.map(function (_, seat) { return makeGlow(seat); });

let view = { frame: TURNS[0] || null, from: null, t0: 0, dur: 0 };
let cur = 0, timer = null, ms = 250;
let lastPainted = null;
let particles = [], rings = [], floats = [], deaths = [], projectiles = [];
const hits = {};
let shake = { t0: -1, dur: 0, mag: 0 };
let bannerShown = false;

function clamp(v, a, b) { return v < a ? a : (v > b ? b : v); }
function easeOut(t) { return 1 - Math.pow(1 - t, 3); }
function gridFor(f) {
  if (f && Array.isArray(f.grid) && f.grid.length === H) return f.grid;
  return BASE;
}
function rr(g, x, y, w, h, r) {
  r = Math.min(r, w / 2, h / 2);
  g.beginPath();
  g.moveTo(x + r, y);
  g.lineTo(x + w - r, y); g.quadraticCurveTo(x + w, y, x + w, y + r);
  g.lineTo(x + w, y + h - r); g.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  g.lineTo(x + r, y + h); g.quadraticCurveTo(x, y + h, x, y + h - r);
  g.lineTo(x, y + r); g.quadraticCurveTo(x, y, x + r, y);
  g.closePath();
}

/* ---------- 地形烘焙 ---------- */
function paintTerrain(grid) {
  const g = tctx;
  g.setTransform(1, 0, 0, 1, 0, 0);
  g.clearRect(0, 0, TC.width, TC.height);
  const bg = g.createLinearGradient(0, 0, 0, TC.height);
  bg.addColorStop(0, "#334a67");
  bg.addColorStop(0.42, "#2b3e58");
  bg.addColorStop(0.74, "#243349");
  bg.addColorStop(1, "#1e2b3e");
  g.fillStyle = bg;
  g.fillRect(0, 0, TC.width, TC.height);
  // 地平线暖光：让空域不那么“死黑”，也把视线推向地面
  const hz = g.createRadialGradient(TC.width * 0.5, TC.height * 0.54, 0,
    TC.width * 0.5, TC.height * 0.54, TC.width * 0.68);
  hz.addColorStop(0, "rgba(255,198,136,.14)");
  hz.addColorStop(0.55, "rgba(255,186,128,.05)");
  hz.addColorStop(1, "rgba(255,186,128,0)");
  g.fillStyle = hz;
  g.fillRect(0, 0, TC.width, TC.height);
  const skyRows = Math.max(1, Math.floor(H * 0.55));
  for (let k = 0; k < 70; k++) {
    const sx = ((k * 97 + 13) % Math.max(1, W)) * CELL + (k % 3) + 1;
    const sy = ((k * 53 + 7) % skyRows) * CELL + (k % 4);
    g.fillStyle = "rgba(226,242,255," + (0.08 + (k % 3) * 0.035).toFixed(3) + ")";
    g.fillRect(sx, sy, 1, 1);
  }
  for (let y = 0; y < H; y++) {
    const row = String(grid[y] || "");
    const up = y > 0 ? String(grid[y - 1] || "") : "";
    const dn = y < H - 1 ? String(grid[y + 1] || "") : "";
    for (let x = 0; x < W; x++) {
      const ch = row.charAt(x);
      const t = TER[ch];
      const px = x * CELL, py = y * CELL;
      if (t) {
        g.fillStyle = rgba3(t.base, 1);
        g.fillRect(px, py, CELL, CELL);
        if (!solidCh(up.charAt(x))) {
          g.fillStyle = rgba3(t.top, 1);
          g.fillRect(px, py, CELL, Math.max(2, Math.round(CELL * 0.24)));
        }
        if (!solidCh(dn.charAt(x))) {
          g.fillStyle = rgba3(t.bot, 1);
          const hh = Math.max(1, Math.round(CELL * 0.16));
          g.fillRect(px, py + CELL - hh, CELL, hh);
        }
        const n = ((x * 73856093) ^ (y * 19349663)) >>> 0;
        if (n % 9 === 0) {
          g.fillStyle = "rgba(255,255,255,.05)";
          g.fillRect(px, py, CELL, CELL);
        } else if (n % 7 === 0) {
          g.fillStyle = "rgba(8,14,24,.06)";
          g.fillRect(px, py, CELL, CELL);
        }
        if (t.water) {
          g.fillStyle = "rgba(200,246,255,.18)";
          g.fillRect(px, py + (CELL >> 1), CELL, 1);
        }
      } else if (ch >= "0" && ch <= "3") {
        const seat = ch.charCodeAt(0) - 48;
        g.fillStyle = rgba(seat, 0.14);
        g.fillRect(px, py, CELL, CELL);
        g.strokeStyle = rgba(seat, 0.58);
        g.lineWidth = 1;
        g.strokeRect(px + 1.5, py + 1.5, CELL - 3, CELL - 3);
        g.fillStyle = rgba(seat, 0.7);
        g.beginPath();
        g.arc(px + CELL / 2, py + CELL / 2, Math.max(1, CELL * 0.14), 0, Math.PI * 2);
        g.fill();
      }
    }
  }
  g.strokeStyle = "rgba(190,222,248,.10)";
  g.lineWidth = 1;
  g.beginPath();
  for (let x = 0; x <= W; x += 8) {
    const px = x * CELL + 0.5;
    g.moveTo(px, 0); g.lineTo(px, TC.height);
  }
  for (let y = 0; y <= H; y += 8) {
    const py = y * CELL + 0.5;
    g.moveTo(0, py); g.lineTo(TC.width, py);
  }
  g.stroke();
  g.strokeStyle = "rgba(110,220,255,.24)";
  g.strokeRect(0.5, 0.5, TC.width - 1, TC.height - 1);
}

/* ---------- 粒子 / 冲击环 / 飘字 ---------- */
function spawnParticle(x, y, vx, vy, life, size, col, now) {
  if (particles.length > 300) particles.shift();
  particles.push({ x: x, y: y, vx: vx, vy: vy, life: life, t0: now, size: size, col: col });
}
function stepParticles(now, dt) {
  for (let k = particles.length - 1; k >= 0; k--) {
    const p = particles[k];
    p.x += p.vx * dt;
    p.y += p.vy * dt;
    p.vy += 0.0011 * dt;
    p.vx *= 0.999;
    if (now - p.t0 > p.life) particles.splice(k, 1);
  }
  for (let k = rings.length - 1; k >= 0; k--) {
    if (now - rings[k].t0 > rings[k].life) rings.splice(k, 1);
  }
  for (let k = floats.length - 1; k >= 0; k--) {
    if (now - floats[k].t0 > floats[k].life) floats.splice(k, 1);
  }
  for (let k = deaths.length - 1; k >= 0; k--) {
    if (now - deaths[k].t0 > 950) deaths.splice(k, 1);
  }
}
function drawParticles(now) {
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  for (let k = 0; k < particles.length; k++) {
    const p = particles[k];
    const age = clamp((now - p.t0) / p.life, 0, 1);
    ctx.globalAlpha = (1 - age) * 0.95;
    ctx.fillStyle = p.col;
    const s = Math.max(1, p.size * (1 - age * 0.55));
    ctx.fillRect(p.x - s / 2, p.y - s / 2, s, s);
  }
  ctx.restore();
}
function drawRings(now) {
  for (let k = 0; k < rings.length; k++) {
    const r = rings[k];
    const t = clamp((now - r.t0) / r.life, 0, 1);
    ctx.globalAlpha = (1 - t) * 0.7;
    ctx.strokeStyle = r.col;
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    ctx.arc(r.x, r.y, r.r0 + t * r.r1, 0, Math.PI * 2);
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
}
function drawFloats(now) {
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  for (let k = 0; k < floats.length; k++) {
    const f = floats[k];
    const t = clamp((now - f.t0) / f.life, 0, 1);
    ctx.globalAlpha = 1 - t * t;
    ctx.font = "bold 11.5px ui-monospace,monospace";
    // 亮主题下用深色描边托底，保证 -HP 飘字落在土壤/天空上都清楚
    ctx.lineWidth = 3;
    ctx.strokeStyle = "rgba(12,20,34,.9)";
    ctx.strokeText(f.text, f.x, f.y - t * 20);
    ctx.fillStyle = f.col;
    ctx.fillText(f.text, f.x, f.y - t * 20);
  }
  ctx.globalAlpha = 1;
}

/* ---------- worms ---------- */
function drawWorm(x, y, frac, seat, id, now) {
  const col = C[seat].c;
  const cx = x * CELL + CELL / 2, cy = y * CELL + CELL / 2;
  const size = Math.max(6, CELL - 2), half = size / 2;
  const radius = Math.max(2, CELL * 0.22);
  const hitT = hits[id];
  if (hitT !== undefined && now - hitT < 520) {
    const k = 1 - (now - hitT) / 520;
    ctx.strokeStyle = "rgba(255,211,138," + (0.55 * k).toFixed(3) + ")";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.arc(cx, cy, half + 2 + (1 - k) * CELL * 0.9, 0, Math.PI * 2);
    ctx.stroke();
  }
  const gd = GLOW[seat].width;
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  ctx.globalAlpha = 0.62;
  ctx.drawImage(GLOW[seat], cx - gd / 2, cy - gd / 2);
  ctx.restore();
  ctx.save();
  ctx.shadowColor = col;
  ctx.shadowBlur = CELL >= 10 ? 7 : 3;
  ctx.fillStyle = col;
  rr(ctx, cx - half, cy - half, size, size, radius);
  ctx.fill();
  ctx.restore();
  ctx.strokeStyle = "rgba(15,25,40,.92)";
  ctx.lineWidth = 1.2;
  rr(ctx, cx - half - 0.5, cy - half - 0.5, size + 1, size + 1, radius + 0.5);
  ctx.stroke();
  ctx.fillStyle = "rgba(255,255,255,.42)";
  ctx.fillRect(cx - half + 1, cy - half + 1, size - 2, 1);
  ctx.fillStyle = "rgba(0,0,0,.30)";
  ctx.fillRect(cx - half + 1, cy + half - 2, size - 2, 1);
  if (CELL >= 10) {
    ctx.fillStyle = "rgba(12,22,36,.94)";
    ctx.font = "bold 7px ui-monospace,monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(String(id), cx, cy + 0.5);
  }
  const bw = Math.max(5, CELL - 2), bx = cx - bw / 2;
  const by = y * CELL + CELL - 3;
  ctx.fillStyle = "rgba(12,20,32,.72)";
  ctx.fillRect(bx, by, bw, 2);
  const fw = Math.max(0, Math.round(bw * clamp(frac, 0, 1)));
  if (fw > 0) {
    ctx.fillStyle = frac > 0.5 ? col : (frac > 0.25 ? "#fbbf24" : "#f87171");
    ctx.fillRect(bx, by, fw, 2);
  }
  if (frac > 0 && frac <= 0.25 && Math.sin(now / 160) > 0.6) {
    ctx.strokeStyle = "rgba(255,255,255,.75)";
    ctx.lineWidth = 1;
    rr(ctx, cx - half + 0.5, cy - half + 0.5, size - 1, size - 1, radius);
    ctx.stroke();
  }
}
function drawCollapse(x, y, seat, p) {
  const dp = clamp(p, 0, 1);
  const s = 1 - dp * 0.85;
  ctx.save();
  ctx.globalAlpha = 1 - dp;
  ctx.fillStyle = C[seat].c;
  const sz = Math.max(2, (CELL - 2) * s);
  ctx.fillRect(x - sz / 2, y - sz / 2, sz, sz);
  ctx.restore();
  ctx.strokeStyle = rgba(seat, 0.85 * (1 - dp));
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.arc(x, y, CELL * 0.45 + dp * CELL * 1.9, 0, Math.PI * 2);
  ctx.stroke();
  if (dp > 0.12) {
    ctx.globalAlpha = Math.min(1, (dp - 0.12) / 0.15) * (1 - Math.max(0, (dp - 0.85) / 0.15));
    ctx.fillStyle = "#e8f2fa";
    ctx.font = "bold 9px ui-monospace,monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.shadowColor = "rgba(0,0,0,.95)";
    ctx.shadowBlur = 3;
    ctx.fillText("OUT", x, y - CELL * 0.95);
    ctx.shadowBlur = 0;
    ctx.globalAlpha = 1;
  }
}
function drawTomb(w, seat) {
  const x = w[0] * CELL + CELL / 2, y = w[1] * CELL + CELL / 2;
  const r = Math.max(2, CELL * 0.18);
  ctx.globalAlpha = 0.52;
  ctx.strokeStyle = C[seat].c;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(x - r, y - r); ctx.lineTo(x + r, y + r);
  ctx.moveTo(x + r, y - r); ctx.lineTo(x - r, y + r);
  ctx.stroke();
  ctx.globalAlpha = 1;
}
function drawDeaths(now) {
  for (let k = 0; k < deaths.length; k++) {
    const d = deaths[k];
    drawCollapse(d.x, d.y, d.seat, (now - d.t0) / 900);
  }
}
function animP(now) {
  if (!view.from || !view.dur) return 1;
  return easeOut(clamp((now - view.t0) / view.dur, 0, 1));
}
function drawWorms(now) {
  const f = view.frame;
  if (!f || !Array.isArray(f.worms)) return;
  const p = animP(now);
  const prevW = (view.from && Array.isArray(view.from.worms)) ? view.from.worms : null;
  for (let id = 0; id < f.worms.length; id++) {
    const w = f.worms[id];
    const prev = prevW ? prevW[id] : null;
    const seat = Math.floor(id / 3);
    if (!w[5]) {
      drawTomb(w, seat);
      continue;
    }
    let x = w[0], y = w[1], hp = w[2];
    if (prev && prev[5]) {
      x = prev[0] + (w[0] - prev[0]) * p;
      y = prev[1] + (w[1] - prev[1]) * p;
      hp = prev[2] + (w[2] - prev[2]) * p;
    }
    drawWorm(x, y, hp / 100, seat, id, now);
  }
}

/* ---------- 弹道动画：按引擎同量纲的 Q16 常量近似 ---------- */
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
      if (gx < 0 || gx >= W || gy < 0 || gy >= H) { endX = cx; endY = cy; hit = true; break; }
      const ch = String(grid[gy] || "").charAt(gx);
      if (ch === "#" || ch === "X" || ch === "~") { endX = cx; endY = cy; hit = true; break; }
    }
    pts.push([endX, endY]);
    x = endX; y = endY;
    if (hit) break;
  }
  return pts;
}
function addShots(f, from, now) {
  if (!f || !Array.isArray(f.shots) || !f.shots.length) return;
  const grid = gridFor(from || f);
  const wind = Number(f.wind) || 0;
  for (let k = 0; k < f.shots.length; k++) {
    const shot = f.shots[k];
    const pts = buildShotPath(shot, grid, wind);
    if (pts.length < 2) continue;
    const cum = [0];
    for (let i = 1; i < pts.length; i++) {
      cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
    }
    projectiles.push({
      pts: pts, cum: cum, seat: clamp(Number(shot.seat) | 0, 0, 3),
      kind: String(shot.kind || "").toUpperCase(), t0: now,
      dur: Math.max(180, Math.min(620, ms * 1.5))
    });
  }
  if (projectiles.length > 32) projectiles.splice(0, projectiles.length - 32);
}
function drawProjectiles(now) {
  for (let k = projectiles.length - 1; k >= 0; k--) {
    const p = projectiles[k];
    const t = clamp((now - p.t0) / p.dur, 0, 1);
    if (t >= 1) {
      const last = p.pts[p.pts.length - 1];
      const px = last[0] * CELL, py = last[1] * CELL;
      rings.push({ x: px, y: py, r0: CELL * 0.25, r1: CELL * 2.0,
                   col: "rgba(255,214,150,.55)", t0: now, life: 420 });
      for (let j = 0; j < 12; j++) {
        const ang = Math.random() * Math.PI * 2, sp = 0.04 + Math.random() * 0.13;
        spawnParticle(px, py, Math.cos(ang) * sp, Math.sin(ang) * sp,
          320 + Math.random() * 380, 1 + Math.random() * 2.2,
          j % 3 === 0 ? rgba(p.seat, 0.9) : "rgba(255,208,142,.9)", now);
      }
      projectiles.splice(k, 1);
      continue;
    }
    const total = p.cum[p.cum.length - 1] || 0;
    const want = total * t;
    let acc = 0, hx = p.pts[0][0], hy = p.pts[0][1];
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    ctx.strokeStyle = rgba(p.seat, 0.52);
    ctx.lineWidth = Math.max(1.2, CELL * 0.16);
    ctx.beginPath();
    ctx.moveTo(p.pts[0][0] * CELL, p.pts[0][1] * CELL);
    for (let i = 1; i < p.pts.length; i++) {
      const seg = p.cum[i] - p.cum[i - 1];
      if (acc + seg > want) {
        const q = seg ? (want - acc) / seg : 0;
        hx = p.pts[i - 1][0] + (p.pts[i][0] - p.pts[i - 1][0]) * q;
        hy = p.pts[i - 1][1] + (p.pts[i][1] - p.pts[i - 1][1]) * q;
        ctx.lineTo(hx * CELL, hy * CELL);
        break;
      }
      acc += seg;
      ctx.lineTo(p.pts[i][0] * CELL, p.pts[i][1] * CELL);
      hx = p.pts[i][0]; hy = p.pts[i][1];
    }
    ctx.stroke();
    ctx.globalCompositeOperation = "source-over";
    const cx = hx * CELL, cy = hy * CELL;
    ctx.shadowColor = C[p.seat].c;
    ctx.shadowBlur = Math.max(5, CELL * 0.8);
    ctx.fillStyle = p.kind === "GRENADE" ? "#ffd28a" : "#fff4d8";
    ctx.beginPath();
    ctx.arc(cx, cy, Math.max(2.4, CELL * 0.34), 0, Math.PI * 2);
    ctx.fill();
    ctx.shadowBlur = 0;
    ctx.strokeStyle = C[p.seat].c;
    ctx.lineWidth = Math.max(1, CELL * 0.12);
    ctx.stroke();
    ctx.restore();
  }
}

/* ---------- 回合切换事件：伤害 / 死亡 / 地形爆炸 ---------- */
function onTransition(from, to, now) {
  addShots(to, from, now);
  const fw = Array.isArray(from.worms) ? from.worms : [];
  const tw = Array.isArray(to.worms) ? to.worms : [];
  let dmgTotal = 0;
  for (let id = 0; id < tw.length; id++) {
    const a = fw[id], b = tw[id];
    if (!a || !b) continue;
    const seat = Math.floor(id / 3);
    const cx = (b[0] + 0.5) * CELL, cy = (b[1] + 0.5) * CELL;
    if (a[5] && b[5] && b[2] < a[2]) {
      const d = a[2] - b[2];
      dmgTotal += d;
      hits[id] = now;
      floats.push({ x: cx, y: cy - CELL * 0.9, text: "-" + d, col: "#ffe2b4", t0: now, life: 1050 });
      for (let k = 0; k < 5; k++) {
        const ang = Math.random() * Math.PI * 2, sp = 0.05 + Math.random() * 0.12;
        spawnParticle(cx, cy, Math.cos(ang) * sp, Math.sin(ang) * sp - 0.05,
          420 + Math.random() * 280, 1.2 + Math.random() * 1.4, rgba(seat, 0.95), now);
      }
      rings.push({ x: cx, y: cy, r0: CELL * 0.3, r1: CELL * 1.6, col: rgba(seat, 0.75), t0: now, life: 420 });
    }
    if (a[5] && !b[5]) {
      deaths.push({ x: cx, y: cy, seat: seat, t0: now });
      rings.push({ x: cx, y: cy, r0: CELL * 0.4, r1: CELL * 2.4, col: rgba(seat, 0.8), t0: now, life: 620 });
    }
  }
  const ga = gridFor(from), gb = gridFor(to);
  const removed = [];
  if (ga !== gb) {
    for (let y = 0; y < H; y++) {
      if (ga[y] === gb[y]) continue;
      const ra = String(ga[y] || ""), rb = String(gb[y] || "");
      for (let x = 0; x < W; x++) {
        if (ra.charAt(x) === rb.charAt(x)) continue;
        if (solidCh(ra.charAt(x)) && !solidCh(rb.charAt(x))) removed.push({ x: x, y: y, ch: ra.charAt(x) });
      }
    }
  }
  if (removed.length) {
    const stride = removed.length > 110 ? 2 : 1;
    for (let k = 0; k < removed.length; k += stride) {
      const c = removed[k];
      // 亮主题下碎片用高饱和材质色，避免“土色碎屑”消失在土壤里
      const tip = c.ch === "~" ? [150, 226, 255]
        : (c.ch === "X" ? [204, 222, 244] : [255, 196, 138]);
      const cx = (c.x + 0.5) * CELL, cy = (c.y + 0.5) * CELL;
      for (let j = 0; j < 2; j++) {
        const ang = -Math.PI * 0.15 - Math.random() * Math.PI * 0.7;
        const sp = 0.05 + Math.random() * 0.16;
        spawnParticle(cx + (Math.random() - 0.5) * CELL, cy + (Math.random() - 0.5) * CELL,
          Math.cos(ang) * sp, Math.sin(ang) * sp - 0.03,
          380 + Math.random() * 520, 1 + Math.random() * 2, rgba3(tip, 0.95), now);
      }
    }
    let sx = 0, sy = 0;
    for (let k = 0; k < removed.length; k++) { sx += removed[k].x; sy += removed[k].y; }
    const mx = (sx / removed.length + 0.5) * CELL;
    const my = (sy / removed.length + 0.5) * CELL;
    rings.push({ x: mx, y: my, r0: CELL * 0.5, r1: CELL * 2.2, col: "rgba(255,208,140,.55)", t0: now, life: 520 });
    if (removed.length >= 4) {
      flashEl.style.background = "radial-gradient(60% 55% at " +
        ((mx / cv.width) * 100).toFixed(1) + "% " + ((my / cv.height) * 100).toFixed(1) +
        "%, rgba(255,196,120,.30), transparent 72%)";
      flashEl.classList.remove("on");
      void flashEl.offsetWidth;
      flashEl.classList.add("on");
      shake = { t0: now, dur: 380, mag: Math.min(7, 1.1 + removed.length * 0.045 + dmgTotal * 0.02) };
    }
  }
}

/* ---------- 主绘制 ---------- */
function drawScene(now) {
  const f = view.frame;
  if (!f || !W || !H) return;
  const grid = gridFor(f);
  if (grid !== lastPainted) { paintTerrain(grid); lastPainted = grid; }
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, cv.width, cv.height);
  let sx = 0, sy = 0;
  if (shake.mag > 0 && now - shake.t0 < shake.dur) {
    const k = 1 - (now - shake.t0) / shake.dur;
    sx = Math.sin(now * 0.09) * shake.mag * k;
    sy = Math.cos(now * 0.13) * shake.mag * k * 0.6;
  }
  ctx.setTransform(1, 0, 0, 1, Math.round(sx), Math.round(sy));
  ctx.drawImage(TC, 0, 0);
  drawWorms(now);
  drawProjectiles(now);
  drawDeaths(now);
  drawParticles(now);
  drawRings(now);
  drawFloats(now);
  ctx.setTransform(1, 0, 0, 1, 0, 0);
}
let lastT = 0;
function loop() {
  requestAnimationFrame(loop);
  const now = performance.now();
  const dt = lastT ? Math.min(40, now - lastT) : 16;
  lastT = now;
  stepParticles(now, dt);
  drawScene(now);
}

/* ---------- 侧栏 / 图例 ---------- */
const seatUI = [], legendUI = [];
function buildSeats() {
  const wrap = el("seats");
  let html = "";
  for (let s = 0; s < SEATS; s++) {
    const on = isActive(s);
    const label = on ? seatLabel(s) : "inactive";
    html += '<div class="seat' + (on ? "" : " inactive") + '" id="seat' + s +
      '" style="--c:' + C[s].c + '">' +
      '<div class="seathead"><span class="dot"></span><b class="sn">S' + s + '</b>' +
      '<span class="bot" title="seat ' + s + ' · ' + esc(label) + '">' + esc(label) + '</span>' +
      '<span class="hpnum">-</span></div>' +
      '<div class="hpbar"><i></i></div>' +
      '<div class="chips">' + [0, 1, 2].map(function (k) {
        return '<span class="chip" data-w="' + (s * 3 + k) + '">#' + (s * 3 + k) + '</span>';
      }).join("") + '</div>' +
      '<div class="seatfoot"><span class="alive">alive -/3</span><span class="seatstate"></span></div>' +
      '</div>';
  }
  wrap.innerHTML = html;
  for (let s = 0; s < SEATS; s++) {
    const root = el("seat" + s);
    seatUI[s] = {
      root: root,
      hpnum: root.querySelector(".hpnum"),
      bar: root.querySelector(".hpbar i"),
      chips: [].slice.call(root.querySelectorAll(".chip")),
      alive: root.querySelector(".alive"),
      state: root.querySelector(".seatstate")
    };
  }
}
function buildLegend() {
  const wrap = el("legend");
  let html = "";
  for (let s = 0; s < SEATS; s++) {
    const on = isActive(s);
    html += '<span class="lg' + (on ? "" : " inactive") + '" id="lg' + s + '" style="--c:' + C[s].c + '">' +
      '<i class="dot"></i><b>S' + s + '</b>' +
      '<span class="nm">' + esc(on ? seatLabel(s) : "inactive") + '</span>' +
      '<span class="al">-</span></span>';
  }
  wrap.innerHTML = html;
  for (let s = 0; s < SEATS; s++) {
    const root = el("lg" + s);
    legendUI[s] = { root: root, al: root.querySelector(".al") };
  }
}
function updateSeats(f, from, atEnd) {
  const hp = (f && Array.isArray(f.hp)) ? f.hp : [0, 0, 0, 0];
  for (let s = 0; s < SEATS; s++) {
    const ui = seatUI[s];
    if (!ui) continue;
    const on = isActive(s);
    const worms = (f && Array.isArray(f.worms)) ? f.worms.slice(s * 3, s * 3 + 3) : [];
    let alive = 0;
    for (let k = 0; k < ui.chips.length; k++) {
      const w = worms[k];
      const chip = ui.chips[k];
      if (!on || !w) {
        chip.textContent = "#" + (s * 3 + k);
        chip.className = "chip out";
        continue;
      }
      if (w[5]) {
        alive++;
        chip.textContent = "#" + (s * 3 + k) + " " + w[2];
        chip.className = "chip" + (w[2] <= 25 ? " low" : "");
      } else {
        chip.textContent = "#" + (s * 3 + k) + " OUT";
        chip.className = "chip out";
      }
    }
    const total = on ? hp[s] : 0;
    ui.hpnum.innerHTML = on ? (String(total) + '<small>/300</small>') : '<small>--</small>';
    ui.bar.style.width = (on ? clamp(total / 300, 0, 1) * 100 : 0) + "%";
    ui.alive.textContent = on ? ("存活 " + alive + "/3") : "";
    ui.root.classList.toggle("dead", on && alive === 0);
    ui.state.textContent = !on ? "not in game"
      : (alive === 0 ? "ELIMINATED" : (atEnd && RES.winner === s ? "WINNER" : ""));
    ui.root.classList.toggle("winner", !!(atEnd && RES.winner === s));
    if (on && from && Array.isArray(from.hp) && hp[s] < from.hp[s]) {
      ui.root.classList.remove("hit");
      void ui.root.offsetWidth;
      ui.root.classList.add("hit");
    }
  }
}
function updateLegend(f, atEnd) {
  const worms = (f && Array.isArray(f.worms)) ? f.worms : [];
  for (let s = 0; s < SEATS; s++) {
    const ui = legendUI[s];
    if (!ui) continue;
    const on = isActive(s);
    let alive = 0;
    for (let k = 0; k < 3; k++) { const w = worms[s * 3 + k]; if (w && w[5]) alive++; }
    ui.al.textContent = on ? (alive + "/3") : "-";
    ui.root.classList.toggle("dead", on && alive === 0);
    ui.root.classList.toggle("win", !!(atEnd && RES.winner === s));
  }
}

/* ---------- 文本块 ---------- */
function orderLines(f) {
  const lines = [];
  for (let s = 0; s < SEATS; s++) {
    if (!isActive(s)) continue;
    const order = (f && f.orders && f.orders[String(s)] !== undefined) ? f.orders[String(s)] : "(missing)";
    lines.push("S" + s + " (" + seatLabel(s) + "): " + order);
  }
  return lines.length ? lines.join(NL) : "(none)";
}
function resultHTML(atEnd) {
  if (!RES || RES.winner === undefined) return '<span class="dim">no result row</span>';
  const faults = (RES.faults === undefined || RES.faults === null) ? 0 : RES.faults;
  const reason = esc(RES.reason || "?");
  const rounds = (RES.rounds === undefined || RES.rounds === null) ? "?" : RES.rounds;
  if (RES.winner >= 0) {
    const si = RES.winner | 0;
    const label = seatLabel(si);
    let html = '<span class="win">winner</span> <span class="wse" style="--wc:' + C[si].c + '">S' +
      si + (label ? " · " + esc(label) : "") + '</span>' +
      '<br><span class="dim">reason</span> ' + reason +
      ' · <span class="dim">rounds</span> ' + rounds +
      ' · <span class="dim">faults</span> ' + faults;
    if (RES.forfeit !== undefined && RES.forfeit !== null && RES.forfeit >= 0) {
      html += '<br><span class="dim">forfeit</span> S' + RES.forfeit;
    }
    if (!atEnd) html += '<br><span class="dim">回放结束后揭晓</span>';
    return html;
  }
  return '<span class="dim">no winner</span><br><span class="dim">reason</span> ' + reason +
    ' · <span class="dim">rounds</span> ' + rounds + ' · <span class="dim">faults</span> ' + faults;
}

/* ---------- 胜者横幅 ---------- */
function showBanner(atEnd) {
  if (!atEnd) {
    if (bannerShown) { bannerEl.classList.remove("on"); bannerShown = false; }
    return;
  }
  if (bannerShown) return;
  let title, sub, col = "#34d7ec";
  if (RES.winner >= 0) {
    const s = RES.winner | 0;
    col = C[s].c;
    title = "S" + s + " WINS";
    sub = '<b>' + esc(seatLabel(s)) + '</b> · ' + esc(RES.reason || "") +
      ' · ' + ((RES.rounds === undefined || RES.rounds === null) ? "?" : RES.rounds) + ' rounds';
  } else {
    title = "NO WINNER";
    sub = esc(RES.reason || "replay end");
  }
  el("bannerTitle").textContent = title;
  el("bannerSub").innerHTML = sub;
  bannerEl.style.setProperty("--bc", col);
  bannerEl.classList.add("on");
  bannerShown = true;
  if (!RM) {
    for (let k = 0; k < 110; k++) {
      spawnParticle(Math.random() * cv.width, cv.height * (0.72 + Math.random() * 0.28),
        (Math.random() - 0.5) * 0.05, -0.09 - Math.random() * 0.14,
        1500 + Math.random() * 1100, 1.5 + Math.random() * 2.4, col, performance.now());
    }
  }
}

/* ---------- 帧切换 ---------- */
function setFrame(idx, mode) {
  idx = clamp(idx, 0, LAST);
  const target = TURNS[idx];
  if (!target) return;
  if (mode !== "step") {
    // 跳转/拖动时清掉上一段过场，避免残留坍缩与粒子
    particles.length = 0;
    rings.length = 0;
    floats.length = 0;
    deaths.length = 0;
    projectiles.length = 0;
    shake.mag = 0;
  }
  const from = (mode === "step" && idx !== cur) ? TURNS[cur] : null;
  const now = performance.now();
  const animate = !!from && !RM;
  const dur = animate ? Math.max(90, Math.min(360, ms * 0.9)) : 0;
  view = { frame: target, from: animate ? from : null, t0: now, dur: dur };
  if (animate) onTransition(from, target, now);
  cur = idx;
  updateUI(idx, from);
}
function updateUI(idx, from) {
  const f = TURNS[idx];
  const atEnd = idx === LAST;
  const alive = (f && Array.isArray(f.worms)) ? f.worms.filter(function (w) { return w[5]; }).length : 0;
  const frameLabel = f ? (f.initial ? "START" : "t" + f.t) : "-";
  el("turnPill").textContent = frameLabel + " / " + ROUND_COUNT;
  el("hudTurn").textContent = frameLabel + " / " + ROUND_COUNT;
  el("hudAlive").textContent = "alive " + alive + "/12";
  el("aliveCount").textContent = "alive " + alive + "/12";
  const pct = TURNS.length ? ((idx + 1) / TURNS.length * 100) : 0;
  el("progBar").style.width = pct.toFixed(2) + "%";
  el("slider").value = String(idx);
  el("slider").style.setProperty("--p", pct.toFixed(2) + "%");
  el("sliderT").textContent = frameLabel;
  el("ordersT").textContent = frameLabel;
  el("orders").textContent = orderLines(f);
  el("hash").textContent = (f && f.state_hash) ? f.state_hash : "(no hash)";
  const perTurn = !!(f && Array.isArray(f.grid) && f.grid.length === H);
  el("gridSrc").textContent = perTurn
    ? "每回合 GRID（含爆炸后地形）"
    : "基础地图（回放未记录每回合 GRID）";
  el("result").innerHTML = atEnd
    ? resultHTML(true)
    : '<span class="dim">回放进行中 · 结束帧揭晓胜者</span>';
  el("result").classList.toggle("pre", !atEnd);
  updateSeats(f, from, atEnd);
  updateLegend(f, atEnd);
  showBanner(atEnd);
}

/* ---------- 播放控制 ---------- */
function stop() {
  if (timer) { clearInterval(timer); timer = null; }
  el("play").textContent = "play";
  el("play").classList.remove("on");
}
function start() {
  stop();
  if (cur >= LAST) setFrame(0, "init");
  el("play").textContent = "pause";
  el("play").classList.add("on");
  timer = setInterval(function () {
    if (cur >= LAST) { stop(); return; }
    setFrame(cur + 1, "step");
  }, ms);
}
el("slider").max = String(LAST);
el("slider").oninput = function (e) { stop(); setFrame(+e.target.value, "jump"); };
el("prev").onclick = function () { stop(); setFrame(cur - 1, "step"); };
el("next").onclick = function () { stop(); setFrame(cur + 1, "step"); };
el("play").onclick = function () { if (timer) stop(); else start(); };
for (const b of document.querySelectorAll(".sp")) {
  b.onclick = function () {
    for (const x of document.querySelectorAll(".sp")) x.classList.remove("on");
    b.classList.add("on");
    ms = +b.dataset.ms;
    if (timer) start();
  };
}
document.addEventListener("keydown", function (e) {
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT")) return;
  if (e.key === "ArrowLeft" || e.key === "ArrowUp") { e.preventDefault(); stop(); setFrame(cur - 1, "step"); }
  else if (e.key === "ArrowRight" || e.key === "ArrowDown") { e.preventDefault(); stop(); setFrame(cur + 1, "step"); }
  else if (e.key === " " || e.key === "Spacebar") { e.preventDefault(); if (timer) stop(); else start(); }
  else if (e.key === "Home") { e.preventDefault(); stop(); setFrame(0, "jump"); }
  else if (e.key === "End") { e.preventDefault(); stop(); setFrame(LAST, "jump"); }
});

/* ---------- 深链接：#t14 或 ?t=14[&anim=1] ---------- */
function queryParam(k) {
  try { return new URLSearchParams(location.search).get(k); } catch (err) { return null; }
}
function deepFrame() {
  const digits = String(location.hash || "").replace(/[^0-9]/g, "");
  let t = digits ? parseInt(digits, 10) : NaN;
  if (!isFinite(t)) {
    const q = queryParam("t");
    t = q ? parseInt(String(q).replace(/[^0-9]/g, ""), 10) : NaN;
  }
  return isFinite(t) ? clamp(t - 1, 0, LAST) : 0;
}

buildSeats();
buildLegend();
const startIdx = deepFrame();
if (queryParam("anim") === "1" && startIdx > 0) {
  setFrame(startIdx - 1, "init");
  setTimeout(function () { setFrame(startIdx, "step"); }, 30);
} else {
  setFrame(startIdx, "init");
}
drawScene(performance.now());
requestAnimationFrame(loop);
})();
</script>
</body></html>
"""


def _placeholder_escape(value: object) -> str:
    """模板占位符只放标量：转义 HTML 元字符，避免 header 字段注入标记。"""
    return (str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;"))


def render_html(rep: dict, out: Path) -> None:
    h = rep["header"]
    map_id = str(rep["map"].get("id") or h.get("map") or "-")
    frames = rep["turns"]
    initial = rep["map"].get("initial")
    if initial is not None:
        frames = [initial, *frames]
    payload = {"header": h, "map": rep["map"], "turns": frames,
               "result": rep["result"]}
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    data = data.replace("</", "<\\/")
    perm = "-".join(str(s) for s in (h.get("seat_perm") or []))
    html = (HTML.replace("__TITLE__", _placeholder_escape(f"P01 4-seat replay · {map_id}"))
                .replace("__GAME__", _placeholder_escape(str(h.get("game") or GAME_ID).upper()))
                .replace("__MAP__", _placeholder_escape(map_id))
                .replace("__SEED__", _placeholder_escape(h.get("seed", "-")))
                .replace("__MASK__", _placeholder_escape(h.get("active_mask", "-")))
                .replace("__PERM__", _placeholder_escape(perm or "-"))
                .replace("__TURNS__", _placeholder_escape(len(rep["turns"])))
                .replace("__RULES__", _placeholder_escape(h.get("rules_hash") or "-"))
                .replace("__ENGINE__", _placeholder_escape(h.get("engine_hash") or "-"))
                .replace("__PAYLOAD__", data))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8", newline="\n")


# ------------------------------------------------------------------ CLI

def main() -> int:
    ap = argparse.ArgumentParser(description="P01 v2 · 4 席回放：重算校验 + HTML 查看器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("verify", help="用 4 席引擎逐回合重算并比对 state_hash/hp/worms/result")
    v.add_argument("--input", required=True, help="4 席 JSONL 文件或目录")
    v.add_argument("--json", default="", help="汇总报告路径")

    g = sub.add_parser("html", help="生成单文件 HTML 查看器（无外部依赖）")
    g.add_argument("--input", required=True, help="4 席 JSONL 文件")
    g.add_argument("--out", required=True, help="输出 .html")

    args = ap.parse_args()

    if args.cmd == "html":
        try:
            rep = load_replay(Path(args.input))
        except ReplayError as exc:
            print(f"[FAIL] {args.input}: {exc}", file=sys.stderr)
            return 1
        render_html(rep, Path(args.out))
        print(f"html {args.out}  ({len(rep['turns'])} turn · 12 worms · 4 seats)")
        return 0

    src = Path(args.input)
    files = sorted(src.rglob("*.jsonl")) if src.is_dir() else [src]
    if not files:
        print(f"没有找到回放：{src}", file=sys.stderr)
        return 1

    game_dir = PROBLEMS / GAME_ID
    if not game_dir.is_dir():
        print(f"找不到题目目录：{game_dir}", file=sys.stderr)
        return 1
    j = judge_module(game_dir)
    exe = j.build_engine(game_dir)

    reports: list[dict] = []
    failures: list[dict] = []
    all_ok = True
    for f in files:
        try:
            r = verify_file(f, game_dir, exe)
        except ReplayError as exc:
            all_ok = False
            failures.append({"input": str(f), "error": str(exc)})
            print(f"[FAIL] {f.name}: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001
            all_ok = False
            failures.append({"input": str(f), "error": repr(exc)})
            print(f"[FAIL] {f.name}: {exc!r}")
            continue
        reports.append(r)
        if not r["ok"]:
            all_ok = False
        mark = "PASS" if r["ok"] else "FAIL"
        stale = "  (旧引擎)" if r.get("stale_engine") else ""
        n_hash = len(r["hash_mismatch"])
        n_state = len(r["state_mismatch"])
        n_res = len(r["result_mismatch"]) + len(r["errors"])
        print(f"[{mark}] {f.name}: {r['map']} seed {r['seed']} mask {r['active_mask']}  "
              f"{r['turns']} 回合  hash {n_hash} / state {n_state} / result {n_res}{stale}")
        for bucket in ("hash_mismatch", "state_mismatch", "result_mismatch"):
            for x in r[bucket][:3]:
                print(f"        {x}")
        for x in r["errors"][:3]:
            print(f"        error: {x}")

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"tool": "replay4", "ok": all_ok,
                                 "reports": reports, "failures": failures},
                                ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
