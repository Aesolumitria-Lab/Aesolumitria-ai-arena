#!/usr/bin/env python3
"""P01 v2 · 4-seat match driver with seat rotation.

--replay-dir 时每个 (map, perm) 写一份 4 席 JSONL（tools/replay4.py 消费）；
默认不写文件。
"""
from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
import os
import queue
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
GAME_DIR = HERE.parents[1]
SEATS = 4
GRID_H = 54
GRID_W = 96
MAX_FAULTS = 3


def _judge():
    """Load the 2-seat judge module next to this file.

    包里它叫 judge2.py（明确是 2 席遗留，避免选手照它写 bot）；仓库里叫 judge.py。
    先找 judge2.py，再退回 judge.py —— 两种排布都能跑。
    """
    for name in ("judge2.py", "judge.py"):
        cand = HERE / name
        if cand.is_file():
            spec = importlib.util.spec_from_file_location("p01_judge", cand)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m
    raise FileNotFoundError(f"judge2.py / judge.py not found next to {HERE}")


def _replay_meta(game_dir: Path = GAME_DIR) -> dict:
    """回放 header 用：rules_hash / engine_hash 以当前 meta 为准。"""
    try:
        return json.loads((game_dir / "meta.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


MAX_LINE_BYTES = 65536
QUEUE_MAX = 256

# ---- 单步计时：CPU 预算是判罚依据，wall 只做兜底 -------------------------
#
# 2026-11-07 实测（32 核机，heavy bots）：
#   48 并发时出现 3–6 次 "no-answer"，而同一批 bot 的 CPU 只用了 15 ms
#   （预算 50 ms）。原因是【裁判自己的 250 ms wall 上限】在 CPU 饥饿时先把
#   bot 掐了 —— 这是裁判在多开时误杀，不是 bot 超时。
# 处置：
#   ① 判超时只看 CPU（wall 上限随 jobs 放大，只用于防止进程真卡死）；
#   ② 一次超时不 kill 进程、不设 failed，让下一个回合继续（迟到的答案是
#      下一回合的错位风险，所以仍然丢弃积压行，但不再整局判死）。
TURN_CPU_MS = 50.0
# ★ 宿主标定系数：预算按「参考机」定义，实测时用 s 换算到本机。
#   s = 本机标尺 bot 的单回合中位成本 / 参考机同一标尺的成本
#   实际判罚阈值 = TURN_CPU_MS × s
#   1.0 = 不标定（等于"本机就是参考机"）。怎么量 s 见 tools/p01_calibrate.py。
#   环境变量 P01_TURN_SCALE 让 selfcheck4 / rating4 / ffa4 不用改代码就继承同一口径。
def _env_scale() -> float:
    raw = os.environ.get("P01_TURN_SCALE", "").strip()
    if not raw:
        return 1.0
    try:
        v = float(raw)
    except ValueError:
        return 1.0
    return v if v > 0 else 1.0


TURN_SCALE = _env_scale()
WALL_BASE_S = 0.25
WALL_FIRST_S = 2.0
# 每个并发工作进程放宽多少 wall 余量：等 CPU 而不是等钟。
WALL_PER_JOB_S = 0.25


# ---- 并发因子（v2.4.3）：判罚阈值要随 --jobs 放大 -------------------------
#
# ★ 为什么需要它（2026-11-10 评审确认的残差）：
#   s（turn scale）是在【单进程】下标的（tools/p01_calibrate.py 只起一个 bot），
#   但排名是 --jobs 20 跑的。并发本身就会把同一个 bot 的每步 CPU 抬起来 ——
#   实测（p01-audit/concurrency_sweep2.log）：
#     TIDE-HARD  jobs=1 5.03 ms → jobs=20 7.41 ms (1.47x) → jobs=24 8.27 ms (1.64x)
#     TYPHON     jobs=1 19.21ms → jobs=20 27.53ms(1.43x) → jobs=24 31.46ms(1.64x)
#   ⇒ 不补偿的话，jobs=20 上的实际判罚线等效于单进程口径的 ~70%，
#     等于「开得多就更容易被判超时」，而这是裁判的账、不是 bot 的账。
#
# 口径（写死，别随手改）：
#   turn_budget_ms(jobs) = 50 × s × c(jobs)
#   c(jobs) = 1 + CONCURRENCY_PER_JOB × (jobs - 1)，并夹在 [1, CONCURRENCY_MAX]
#   常数取得保守：实测 24 并发是 1.64x，这里按 1.75x 封顶，宁松勿紧。
CONCURRENCY_PER_JOB = 0.03     # 每多 1 个并发 +3%（jobs=20 → 1.57x）
CONCURRENCY_MAX = 1.75         # 封顶，超过就是采样噪声了


def concurrency_factor(jobs: int) -> float:
    """并发补偿系数 c(jobs)。jobs<=1 时恒为 1（单进程不加任何余量）。"""
    if jobs is None or jobs <= 1:
        return 1.0
    return min(CONCURRENCY_MAX, 1.0 + CONCURRENCY_PER_JOB * (jobs - 1))


def turn_budget_ms(jobs: int | None = None) -> float:
    """本机实际的单回合 CPU 预算（毫秒）= 50 × s × c(jobs)。

    jobs 省略时按单进程（c=1），保持与旧行为一致。
    """
    return TURN_CPU_MS * TURN_SCALE * concurrency_factor(jobs)


class Bot:
    """Bounded reader queue: same abuse defence as the 2-seat judge."""

    def __init__(self, exe: Path, cpu_meter=None, wall_allow_s: float = 0.0,
                 jobs: int = 1):
        # jobs：本局所在的并发度，只用来算 CPU 预算的并发补偿 c(jobs)（见 turn_budget_ms）。
        # 它不改变 wall 兜底 —— wall 走的是 wall_allow_s，另一条账。
        self.jobs = max(1, int(jobs))
        self.proc = subprocess.Popen([str(exe)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, bufsize=1, encoding="utf-8")
        if cpu_meter is None:
            cpu_meter = _judge().ProcessCPU
        self.cpu = cpu_meter(self.proc.pid)
        self.turns = 0
        self.failed = False
        # dead = 进程已经退出（不是迟到/饥饿）。RULES 15「崩溃 / 非零退出 → 直接判负」，
        # play_one 靠它把"崩了"与"这一步没答上来"分开：
        #   · dead ⇒ 该席立刻判负（走 play_one 里的 _lost_now / _forfeit）；
        #   · ask 返回 None 但进程还活着 ⇒ 仍然只是 fault +1（迟到/饥饿不判死）。
        self.dead = False
        self.exit_code: int | None = None   # 崩溃时记下 returncode（诊断用）
        self.wall_allow_s = wall_allow_s
        self.q: queue.Queue[str | None] = queue.Queue(maxsize=QUEUE_MAX)
        self.stale = 0
        self.oversize = 0
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        assert self.proc.stdout
        try:
            while True:
                line = self.proc.stdout.readline(MAX_LINE_BYTES + 1)
                if not line:
                    break
                if len(line) > MAX_LINE_BYTES:
                    self.oversize += 1
                    while line and not line.endswith("\n"):
                        line = self.proc.stdout.readline(MAX_LINE_BYTES)
                    continue
                try:
                    self.q.put(line.rstrip("\r\n"), timeout=5.0)
                except queue.Full:
                    self.stale += 1
        except Exception:
            pass
        try:
            self.q.put(None, timeout=5.0)
        except queue.Full:
            pass

    def ask(self, text: str, timeout: float):
        """Read one answer. The CPU budget decides; wall is only a liveness guard.

        timeout（main --timeout / play_one 的同名参数）只抬高 wall 兜底的【下限】
        （max(timeout, WALL_BASE_S)），不参与 CPU 判罚：判罚依据始终是
        turn_budget_ms()。timeout <= WALL_BASE_S 时它不改变任何行为；
        让它是死参数才是原罪 —— 帮助文本写着能调，改它却没有任何效果。
        """
        if self.failed or self.proc.poll() is not None:
            self.failed = True
            self.dead = True      # 进程已经退出 ⇒ 调用方按 RULES 15 立即判负
            self.exit_code = self.proc.poll()
            return None
        first = self.turns == 0
        # timeout 只当 wall 的下限用；传 None/0 时退回 WALL_BASE_S，行为与旧版一致。
        wall_floor = max(WALL_BASE_S, timeout or 0.0)
        wall_s = (WALL_FIRST_S if first else wall_floor) + self.wall_allow_s
        try:
            assert self.proc.stdin
            self.proc.stdin.write(text)
            self.proc.stdin.flush()
        except Exception:
            self.turns += 1
            if self.proc.poll() is not None:
                self.dead = True   # 写不进去 + 进程已退出 = 崩溃，不是超时
                self.exit_code = self.proc.poll()
            return None

        base = self.cpu.sample()
        deadline = time.monotonic() + wall_s
        line = None
        while True:
            try:
                line = self.q.get_nowait()
                break
            except queue.Empty:
                pass
            now = self.cpu.sample()
            if (base is not None and now is not None
                    and (now - base) * 1000.0 >= turn_budget_ms(self.jobs) + self.cpu.granularity_ms):
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.001)
        self.turns += 1
        if line is None:
            # 迟到 / 饥饿：丢弃积压，但【不杀进程、不设 failed】——
            # 一次超时不该终结整局；判负仍由"累计 3 次 fault"承担。
            while True:
                try:
                    extra = self.q.get_nowait()
                except queue.Empty:
                    break
                if extra is not None:
                    self.stale += 1
            return None
        while True:
            try:
                extra = self.q.get_nowait()
            except queue.Empty:
                break
            if extra is not None:
                self.stale += 1
        return line

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.kill()
        except Exception:
            pass
        try:
            self.cpu.close()
        except Exception:
            pass

def _cmd(proc, line: str) -> str:
    assert proc.stdin and proc.stdout
    proc.stdin.write(line + "\n")
    proc.stdin.flush()
    out = proc.stdout.readline()
    if not out:
        raise RuntimeError("engine closed stdout")
    return out.rstrip("\r\n")


def _read_grid(engine) -> list[str]:
    """GRID 查询：返回 54 行 96 列地形（'#' 泥土 / 'X' 基岩 / '.' 空气）。

    地形每轮都可能被钻头/爆炸改变，所以不缓存整局，只缓存到下一次 STEP 之前。
    """
    assert engine.stdin and engine.stdout
    engine.stdin.write("GRID\n")
    engine.stdin.flush()
    rows: list[str] = []
    while True:
        line = engine.stdout.readline()
        if not line:
            raise RuntimeError("engine closed stdout")
        line = line.rstrip("\r\n")
        if line == "GEND":
            break
        if line.startswith("ERR"):
            raise RuntimeError(line)
        if line.startswith("G "):
            rows.append(line[2:])
    if len(rows) != GRID_H or any(len(r) != GRID_W for r in rows):
        raise RuntimeError(f"engine GRID 不是 {GRID_W}x{GRID_H}")
    return rows


def _grid_block(grid: list[str]) -> str:
    return "".join(f"GRID {row}\n" for row in grid) + "END\n"


def resolve_bot(name: str, game_dir: Path) -> Path:
    p = Path(name)
    if p.exists():
        return p.resolve()
    cand = game_dir / "private" / "anchors4" / f"{name}.cpp"
    if cand.exists():
        return cand
    # 包里没有私有目录：默认 bots 与选手显式名字必须能从公开目录解析出来。
    # 仓库自检仍先走上面的私有 anchors4 源码目录（tools/ffa4.py 按名字解析 anchor 依赖它）。
    # 包内同时分发 Windows .exe 与 Linux ELF；Linux 上必须优先 bin/linux/，
    # 否则会命中 PE，Popen 直接报 Exec format error。
    exe_rels = (f"public/baselines/bin/{name}.exe",
                f"public/challenges/bin/{name}.exe")
    linux_rels = (f"public/baselines/bin/linux/{name}",
                  f"public/challenges/bin/linux/{name}")
    if sys.platform == "win32":
        rels = (exe_rels[0], linux_rels[0], exe_rels[1], linux_rels[1])
    else:
        rels = (*linux_rels, *exe_rels)
    for rel in rels:
        cand = game_dir / rel
        if cand.exists():
            return cand.resolve()
    raise FileNotFoundError(name)


def resolve_map(name: str, game_dir: Path) -> Path:
    """Accept a path or a bare map id such as ``public4-01`` (backward compatible).

    找不到就抛 FileNotFoundError，把试过的候选路径都写进错误里 —— 返回一个
    不存在的 Path 只会把错误推迟到引擎的 ERR，那时候已经分不清是图名写错
    还是引擎拒收。
    """
    p = Path(name)
    if p.is_file():
        return p.resolve()
    tried = [game_dir / "public" / "maps4" / f"{name}.txt",
             game_dir / "public" / "maps" / f"{name}.txt"]
    for cand in tried:
        if cand.is_file():
            return cand
    raise FileNotFoundError(
        f"找不到地图 {name!r}；试过：" + ", ".join(str(c) for c in tried))


def _bot_registry():
    """Load tools/bot_registry.py without depending on cwd or sys.path order."""
    tools_dir = GAME_DIR.parents[1] / "tools"
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))
    import bot_registry  # noqa: PLC0415 - optional registry integration
    return bot_registry


def build_registry_bot(j, record: dict, game_dir: Path) -> Path:
    """Compile an entries/<id>/ entry to a per-id output (no bot.cpp collision).

    The public anchor helpers live outside the entry, so the entry directory is
    added as include path.  Output is keyed by id + code hash, making code
    updates recompile while keeping the existing anchors' build/ untouched.
    """
    src = Path(record["entry"])
    safe = record["id"].replace("@", "_at_").replace("#", "_run")
    out_dir = game_dir / "build" / "entries4" / safe
    suffix = ".exe" if os.name == "nt" else ""
    out = out_dir / f"{record['code_sha256'][:16]}-{src.stem}{suffix}"
    deps = [src, *sorted(p for p in src.parent.rglob("*")
                         if p.is_file() and p.suffix.lower() in
                         (".cpp", ".cc", ".cxx", ".hpp", ".h"))]
    if j._up_to_date(out, deps):
        return out
    return j.compile_cpp(src, out, include_dir=src.parent)


def _worm_alive(parts: list[str], wid: int) -> bool:
    o = 20 + wid * 6
    return len(parts) > o + 5 and parts[o + 5] == "1"


def _shots_from_answer(answer: str, parts: list[str], seat: int) -> list[dict]:
    """Visual shot metadata from a bot answer, using pre-step worm cells."""
    out: list[dict] = []
    cur_wid: int | None = None
    for part in answer.split(";"):
        toks = part.split()
        if not toks:
            continue
        if toks[0].isdigit() and len(toks) >= 2:
            cur_wid = int(toks[0])
            toks = toks[1:]
        if cur_wid is None or len(toks) < 4 or toks[0] != "F":
            continue
        try:
            angle, power = int(toks[2]), int(toks[3])
        except ValueError:
            continue
        wid = cur_wid
        o = 20 + wid * 6
        if wid < 0 or wid >= SEATS * 3 or len(parts) <= o + 5:
            continue
        if parts[o + 5] != "1":
            continue
        out.append({"seat": seat, "worm": wid, "kind": toks[1].upper(),
                    "angle": angle, "power": power,
                    "x": int(parts[o]), "y": int(parts[o + 1])})
    return out


def _has_live(parts: list[str], seat: int) -> bool:
    return any(_worm_alive(parts, i) for i in range(seat * 3, seat * 3 + 3))


def _first_live(parts: list[str], seat: int) -> int:
    for i in range(seat * 3, seat * 3 + 3):
        if _worm_alive(parts, i):
            return i
    return seat * 3


def _sanitize_answer(answer: str | None, seat: int) -> str | None:
    if answer is None:
        return None
    text = answer.strip()
    if not text or "|" in text:
        return None
    parts = text.split(maxsplit=1)
    try:
        act = int(parts[0])
    except ValueError:
        return None
    if act < seat * 3 or act >= seat * 3 + 3:
        return None
    body = parts[1].strip() if len(parts) > 1 else "WAIT"
    return f"{act} {body}"


def play_one(j, engine_exe: Path, map_path: Path, seed: int, bots: list[Path],
             active: tuple[int, ...], perm: tuple[int, ...], timeout: float,
             replay_path: Path | None = None, replay_grid: bool = True,
             map_id: str | None = None, labels: list[str] | None = None,
             meta: dict | None = None, bot_ids: list[str] | None = None,
             bot_labels: list[str] | None = None,
             bot_meta: list[dict] | None = None,
             wall_allow_s: float = 0.0, jobs: int = 1) -> dict:
    """跑一场 2/3/4 席对局。

    replay_path 非空时写 4 席 JSONL：LOAD 后写 header/map，每个 STEP 写一条
    turn（结算后的 hp/dealt/bad/worms/state_hash；replay_grid 为真时附 GRID），
    结束写 result。默认不写任何文件。

    wall_allow_s：并发时给每个 bot 的 wall 余量（秒）。判超时仍然只看 CPU；
    wall 只防止"进程真卡死"。单进程跑保持 0。

    timeout：wall 兜底的【下限】（见 Bot.ask），不参与 CPU 判罚 —— 预算永远是
    turn_budget_ms(jobs)。

    jobs：本局所在的并发度。它**只**参与 CPU 预算的并发补偿 c(jobs)
    （见模块顶部的 concurrency_factor）；不改变 wall 兜底，也不改变单进程口径。
    跑 --jobs N 时必须传 N，否则并发抬高 CPU 的成本会算到 bot 头上。
    """
    engine = subprocess.Popen([str(engine_exe)], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              text=True, bufsize=1, encoding="utf-8")
    bot_procs = [Bot(b, j.ProcessCPU, wall_allow_s=wall_allow_s, jobs=jobs) for b in bots]
    faults_by_seat = [0] * SEATS
    rounds = 0
    last_hash = ""
    replay_f = None
    map_id = map_id or map_path.stem

    def _lost_now(extra: list[int]) -> list[int]:
        """判负席位 = 今天已经崩的（extra）+ 累计 fault 已满 MAX_FAULTS 的。

        崩溃的那一席一定算输；其余席位有没有"已经满 3 次 fault"由同一口径算，
        所以崩溃与攒满 fault 走的是同一条裁决路径，不会出现两套账。
        """
        return [s for s in active if s in extra or faults_by_seat[s] >= MAX_FAULTS]

    def _forfeit(active_seats: tuple[int, ...], lost: list[int],
                 perm_: tuple[int, ...], n_rounds: int,
                 faults: list[int], state_hash: str) -> dict:
        """裁判判负的统一出口。凡 lost 里的席位一并判负，剩下的席位若是唯一一席就赢。

        ★ 4 席多人同时出局的裁决（RULES 15 只写了 2 席，这里是它的 4 席推广）：
          · 剩 1 席 ⇒ 该席赢（winner=该 seat，winner_bot=perm 里它的下标）；
          · 剩 0 席 ⇒ 全灭，winner=-1（DRAW）；
          · 剩多席（4 席里只崩了 1 个）⇒ 按现有 fault 累计裁决 —— 也就是原来
            520-530 那条路：lost 不空而 remaining 有多席时 win=-1，本局记
            FORFEIT + winner=-1（平局），不凭空造赢家；谁被判负写在
            forfeit / forfeits 里，记账方（rating4）按 ok 行统计。
        """
        remaining = [s for s in active_seats if s not in lost]
        win = remaining[0] if len(remaining) == 1 else -1
        return {"ok": True, "winner": win,
                "winner_bot": perm_.index(win) if win in perm_ else -1,
                "reason": "FORFEIT", "rounds": n_rounds,
                "faults": sum(faults), "faults_by_seat": faults,
                "forfeit": lost[0] if lost else -1,
                "forfeits": list(lost),
                "state_hash": state_hash}

    def finish(res: dict) -> dict:
        if replay_f is not None:
            rec = {"type": "result",
                   "winner": res.get("winner", -1),
                   "winner_bot": res.get("winner_bot", -1),
                   "winner_seat": res.get("winner", -1),
                   "reason": res.get("reason", "?"),
                   "rounds": res.get("rounds", rounds),
                   "faults": res.get("faults", 0),
                   "forfeit": res.get("forfeit", -1),
                   "state_hash": res.get("state_hash", last_hash)}
            if bot_ids is not None:
                rec["bot_ids"] = list(bot_ids)
                wb = res.get("winner_bot", -1)
                if isinstance(wb, int) and 0 <= wb < len(bot_ids):
                    rec["winner_bot_id"] = bot_ids[wb]
            if res.get("error"):
                rec["error"] = res["error"]
            replay_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            replay_f.flush()
        return res

    # 一次引擎抖动（_read_grid 抛 RuntimeError / LOAD 回来不是 OK / 回放开局帧解析
    # 失败）不该炸掉整批 —— ffa4.py:231-232 早就是这么包的，judge4 自己漏了。
    # 异常一律变成一行 ok:False，坏局被统计、其余局照跑。
    try:
        mask = sum(1 << s for s in active)
        ok = _cmd(engine, f"LOAD {map_path.as_posix()} {seed} {mask}")
        if not ok.startswith("OK"):
            return {"ok": False, "error": ok}
        tok = ok.split()
        if len(tok) > 1:
            last_hash = tok[1]
        state = _cmd(engine, "STATE")
        grid = _read_grid(engine)
        grid_text = _grid_block(grid)
        if replay_path is not None:
            md = _replay_meta() if meta is None else meta
            names = list(labels) if labels else [Path(b).stem for b in bots]
            # 引擎 GRID 已经把出生点 0..3 抹成 '.'，重算 LOAD 需要原始地图。
            map_grid = j.load_grid(map_path)
            replay_path.parent.mkdir(parents=True, exist_ok=True)
            replay_f = replay_path.open("w", encoding="utf-8", newline="\n")
            # version 以 meta.json 为准：judge.py 里的 GAME_VERSION 是 2 席判官的
            # 遗留常量（写死 2.0.0），照它写会让所有回放在争议复盘时自称 2.0.0。
            header = {"type": "header",
                      "game": getattr(j, "GAME_ID", "p01-ballistic"),
                      "version": md.get("version") or getattr(j, "GAME_VERSION", "2.0.0"),
                      "rules_hash": md.get("rules_hash", ""),
                      "engine_hash": md.get("engine_hash", ""),
                      "seed": seed, "map": map_id,
                      "active_mask": mask, "seats": SEATS,
                      "seat_perm": list(perm), "bots": names}
            if bot_ids is not None:
                header["bot_ids"] = list(bot_ids)
                header["bot_labels"] = list(bot_labels) if bot_labels else list(names)
                if bot_meta is not None:
                    header["bot_meta"] = [dict(m) for m in bot_meta]
            replay_f.write(json.dumps(header, ensure_ascii=False) + "\n")
            initial_parts = state.split()
            if len(initial_parts) < 20 + SEATS * 3 * 6 or initial_parts[0] != "STATE":
                raise RuntimeError("initial STATE 不是 4-seat layout")
            initial = {"initial": True, "t": 0, "round": int(initial_parts[1]),
                       "wind": int(initial_parts[2]) - 32,
                       "water": int(initial_parts[3]), "over": int(initial_parts[4]),
                       "winner": int(initial_parts[5]),
                       "hp": [int(x) for x in initial_parts[6:10]],
                       "dealt": [int(x) for x in initial_parts[10:14]],
                       "reason": initial_parts[14],
                       "bad": [int(x) for x in initial_parts[15:19]],
                       "state_hash": initial_parts[19],
                       "worms": [[int(x) for x in initial_parts[20 + i * 6:26 + i * 6]]
                                 for i in range(SEATS * 3)],
                       "grid": grid, "orders": {}, "shots": []}
            map_rec = {"type": "map", "id": map_id, "grid": map_grid,
                       "initial": initial}
            if bot_ids is not None:
                map_rec["bot_ids"] = list(bot_ids)
            replay_f.write(json.dumps(map_rec, ensure_ascii=False) + "\n")
            replay_f.flush()
        for _ in range(40):
            parts = state.split()
            if len(parts) < 20 or parts[0] != "STATE":
                return finish({"ok": False, "error": "bad state"})
            if parts[4] == "1":
                break
            answers = ["0 WAIT"] * SEATS
            invalid = [False] * SEATS
            for i, seat in enumerate(perm):
                if not _has_live(parts, seat):
                    answers[seat] = f"{seat * 3} WAIT"
                    invalid[seat] = True
                    continue
                # TURN 头里的第三个字段是地形行数（0 表示不下发），
                # 让 SDK 能确定性地判断 STATE 后面是否还有 GRID/END 块。
                block = ("TURN " + str(seat) + " " + str(len(grid)) + chr(10) + state + chr(10) + grid_text)
                raw = bot_procs[i].ask(block, timeout)
                # 进程已退出 ≠ 这一步迟到：RULES 15 写的是"崩溃 / 非零退出 → 直接判负"，
                # 不靠攒满 MAX_FAULTS，也不再往引擎送 STEP。崩溃本身不记 fault
                #（fault 是"超时/非法动作"的账，判负原因写在 reason/forfeit 里）。
                if bot_procs[i].dead:
                    res = _forfeit(active, _lost_now([seat]), perm, rounds,
                                   faults_by_seat, last_hash)
                    res["exit_code"] = bot_procs[i].exit_code
                    return finish(res)
                answer = _sanitize_answer(raw, seat)
                if raw is None or answer is None:
                    faults_by_seat[seat] += 1
                    invalid[seat] = True
                    answers[seat] = f"{_first_live(parts, seat)} WAIT"
                else:
                    answers[seat] = answer
            wind = int(parts[2]) - 32
            shots: list[dict] = []
            for seat in active:
                shots.extend(_shots_from_answer(answers[seat], parts, seat))
            state = _cmd(engine, "STEP " + " | ".join(answers))
            if state.startswith("ERR"):
                return finish({"ok": False, "error": state, "faults": sum(faults_by_seat),
                               "faults_by_seat": faults_by_seat, "rounds": rounds})
            sparts = state.split()
            if len(sparts) < 20 + SEATS * 3 * 6:
                return finish({"ok": False, "error": "state layout is not 4-seat",
                               "faults": sum(faults_by_seat),
                               "faults_by_seat": faults_by_seat, "rounds": rounds})
            # 钻头/爆炸可能改地形，刷新后再发给下一轮的 4 个 bot。
            # 回放要记录结算后的地形，所以 GRID 在写 turn 之前取。
            grid = _read_grid(engine)
            grid_text = _grid_block(grid)
            rounds += 1
            last_hash = sparts[19]
            if replay_f is not None:
                turn_rec = {"type": "turn", "t": rounds,
                            "orders": {str(s): answers[s] for s in active},
                            "active_mask": mask, "state_hash": last_hash,
                            "wind": wind, "shots": shots,
                            "hp": [int(x) for x in sparts[6:10]],
                            "dealt": [int(x) for x in sparts[10:14]],
                            "bad": [int(x) for x in sparts[15:19]],
                            "worms": [[int(x) for x in sparts[20 + i * 6:26 + i * 6]]
                                      for i in range(SEATS * 3)]}
                if replay_grid:
                    turn_rec["grid"] = grid
                replay_f.write(json.dumps(turn_rec, ensure_ascii=False) + "\n")
                replay_f.flush()
            for seat in active:
                if invalid[seat] or len(sparts) <= 15 + seat:
                    continue
                if sparts[15 + seat] == "1" and _worm_alive(sparts, _first_live(sparts, seat)):
                    faults_by_seat[seat] += 1
            lost = _lost_now([])
            if lost:
                return finish(_forfeit(active, lost, perm, rounds,
                                       faults_by_seat, last_hash))
        parts = state.split()
        winner = int(parts[5]) if len(parts) > 5 else -1
        return finish({"ok": True, "winner": winner,
                       "winner_bot": perm.index(winner) if winner in perm else -1,
                       "reason": parts[14] if len(parts) > 14 else "?",
                       "rounds": rounds, "faults": sum(faults_by_seat),
                       "faults_by_seat": faults_by_seat, "forfeit": -1,
                       "state_hash": last_hash})
    except Exception as exc:
        return finish({"ok": False, "error": repr(exc),
                       "faults": sum(faults_by_seat),
                       "faults_by_seat": faults_by_seat, "rounds": rounds,
                       "state_hash": last_hash})
    finally:
        if replay_f is not None:
            try:
                replay_f.close()
            except Exception:
                pass
        for b in bot_procs:
            b.close()
        try:
            engine.stdin.close()
        except Exception:
            pass
        try:
            engine.kill()
        except Exception:
            pass


def rotations(active: tuple[int, ...]) -> list[tuple[int, ...]]:
    """All seat permutations, not just cyclic rotations."""
    return list(itertools.permutations(active))


def circular_orders(active: tuple[int, ...]) -> list[tuple[int, ...]]:
    """6 个"环形座位序"：固定第一个席，其余 3 席全排列。

    为什么需要它：24 全排列里真正影响 4 席 FFA 的是【谁在你旁边】。
    循环轮换只平衡座位轴向，邻居永远是同样两个 bot。6 个环形序 × 4 旋转
    = 24 局，把邻居也平衡掉了（实测邻居计数 spread 0）。
    """
    first = active[0]
    return [(first,) + rest for rest in itertools.permutations(active[1:])]


def schedule(active: tuple[int, ...], perms_per_map: int, seeds_per_perm: int,
             balance_neighbours: bool = True) -> list[tuple[tuple[int, ...], int, int]]:
    """一场地图的赛程：[(perm, seed_index, seed_absolute), ...]

    seed_index ∈ {0..seeds_per_perm-1} 只是"第几批"；seed_absolute 是全局自增的
    0..len-1，两者【不能互相替代】：调用方取 seed 时一律用第三个字段
    （seed = seed_base + seed_absolute）。用 seed_index 会让一张图的 48 局退化成
    2 个 seed，等于把 RULES 11.2.2 换 seed 的那条规则原地撤销。

    ★ 2026-11-07 实测的核心结论：
      · 同一张图所有 perm 共用【一个 seed】= 一套风程序 ⇒ 24 个 perm 其实是
        同一局换了座位名字。4 个相同 bot 跑出来 106/108 张图只有单一座次能赢，
        seat0 36.8% / seat3 34.0% / seat1 14.2% / seat2 15.1%。
      · 让 perm 之间换 seed，座次垄断立刻散开（对称图上 24/24 同座 → 10/10/2/2）。
    ⇒ 默认赛程 = 邻接平衡的 perm 子集 × 若干 seed，且 seed 在 perm 之间变化。
    """
    if balance_neighbours and perms_per_map >= 12:
        pool = [tuple(c[(i + r) % len(active)] for i in range(len(active)))
                for c in circular_orders(active) for r in range(len(active))]
    else:
        pool = rotations(active)
    pool = list(dict.fromkeys(pool))
    k = min(perms_per_map, len(pool))
    picks = [pool[(i * len(pool)) // k] for i in range(k)]
    out = []
    idx = 0
    for si in range(seeds_per_perm):
        for p in picks:
            out.append((p, si, idx))
            idx += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-dir", default=str(GAME_DIR))
    ap.add_argument("--map", default="")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--bots", default=None,
                    help="legacy: anchor names or file paths；配合 --entries 时是 registry ID")
    ap.add_argument("--entries", default="",
                    help="registry 目录；提供后 --bots 接收 model@harness#run ID")
    ap.add_argument("--seats", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=0.25,
                    help="单步 wall 兜底的【下限】（秒），只在超过 WALL_BASE_S 0.25s 时生效；"
                         "判超时看的是 CPU 预算（50ms × s），不是它")
    ap.add_argument("--json", default="")
    ap.add_argument("--replay-dir", default="",
                    help="每个 (map, perm) 写一份 <map>-seed<seed>-perm<...>.jsonl")
    ap.add_argument("--no-replay-grid", action="store_true",
                    help="回放不逐回合记录 GRID（文件更小，但看不到爆炸后的地形）")
    ap.add_argument("--jobs", type=int, default=1,
                    help="并发对局数（默认 1）。>1 时每局多给 0.25s×(jobs-1) 的 wall 余量，"
                         "判超时仍然只看 CPU")
    ap.add_argument("--seeds-per-perm", type=int, default=2,
                    help="每个座位排列跑几个 seed（默认 2）。同一 seed 下 24 个排列 "
                         "等于同一局换座位名字，会放大座次偏差")
    ap.add_argument("--perms-per-map", type=int, default=0,
                    help="每张图用多少个座位排列（默认 0 = 全排列；12/24 走邻接平衡）")
    ap.add_argument("--turn-scale", type=float, default=None,
                    help="宿主标定系数 s：实际预算 = 50ms × s。默认读环境变量 "
                         "P01_TURN_SCALE，都没有则为 1.0（本机当参考机）。"
                         "用 tools/p01_calibrate.py 量出来")
    args = ap.parse_args()
    global TURN_SCALE
    if args.turn_scale is not None:
        if args.turn_scale <= 0:
            ap.error("--turn-scale 必须 > 0")
        TURN_SCALE = args.turn_scale
    if TURN_SCALE <= 0:
        ap.error("TURN_SCALE 必须 > 0")
    game = Path(args.game_dir).resolve()
    j = _judge()
    engine = j.build_engine(game)
    raw_bots = args.bots if args.bots is not None else (
        "" if args.entries else "tide_hard,tide_normal,tide_easy,random4")
    names = [x.strip() for x in raw_bots.split(",") if x.strip()]
    if not names:
        ap.error("--bots 不能为空（--entries 模式请给逗号分隔的 registry ID）")
    if args.seats < 2 or args.seats > SEATS or len(names) != args.seats:
        ap.error("--seats must be 2-4 and match --bots length")
    active = (0, 2) if args.seats == 2 else tuple(range(args.seats))
    perms_pool = rotations(active)
    if args.perms_per_map <= 0 or args.perms_per_map > len(perms_pool):
        perms_per_map = len(perms_pool)
    else:
        perms_per_map = args.perms_per_map
    plan = schedule(active, perms_per_map, max(1, args.seeds_per_perm))

    bot_ids: list[str] | None = None
    bot_labels: list[str] | None = None
    bot_meta: list[dict] | None = None
    if args.entries:
        registry = _bot_registry()
        entries_dir = Path(args.entries).resolve()
        try:
            records = [registry.resolve_entry(entries_dir, name) for name in names]
        except registry.BotRegistryError as exc:
            ap.error(str(exc))
        bot_ids = [r["id"] for r in records]
        bot_labels = [r["display_name"] for r in records]
        bot_meta = [{k: r[k] for k in ("id", "model_key", "harness", "code_sha256")}
                    for r in records]
        bot_paths = [Path(r["entry"]) for r in records]
        built = [build_registry_bot(j, r, game) for r in records]
    else:
        bot_paths = [resolve_bot(n, game) for n in names]
        built = [j.build_bot(p, game / "build", game / "public")
                 for p in bot_paths]
    labels = bot_labels if bot_labels is not None else [Path(n).stem for n in names]

    maps = ([resolve_map(args.map, game)] if args.map
            else sorted((game / "public" / "maps4").glob("*.txt")))
    replay_dir = Path(args.replay_dir) if args.replay_dir else None
    if replay_dir is not None:
        replay_dir.mkdir(parents=True, exist_ok=True)
    meta = _replay_meta(game)
    rows = []
    wall_allow = WALL_PER_JOB_S * max(0, args.jobs - 1)
    work = []
    for mp in maps:
        # ★ 第三个字段是【全局自增的绝对 seed 下标】，不是 0/1 的 seed 序号。
        # 拿 seed_idx 当 seed 用，一张图的 48 局会退化成 2 个 seed（每 12 个排列
        # 共用一个）—— 那正是 RULES 11.2.2 要消灭的"同一套风程序换座位名字"。
        for perm, _seed_idx, seed_abs in plan:
            seed = args.seed + seed_abs
            rp = None
            if replay_dir is not None:
                tag = "".join(str(s) for s in perm)
                rp = replay_dir / f"{mp.stem}-seed{seed}-perm{tag}.jsonl"
            work.append((mp, perm, seed, rp))

    def run_one(item):
        mp, perm, seed, rp = item
        r = play_one(j, engine, mp, seed, built, active, perm, args.timeout,
                     replay_path=rp, replay_grid=not args.no_replay_grid,
                     map_id=mp.stem, labels=labels, meta=meta,
                     bot_ids=bot_ids, bot_labels=bot_labels, bot_meta=bot_meta,
                     wall_allow_s=wall_allow, jobs=args.jobs)
        return mp.stem, perm, seed, r

    if args.jobs > 1:
        with ThreadPoolExecutor(max_workers=args.jobs) as ex:
            results = list(ex.map(run_one, work))
    else:
        results = [run_one(w) for w in work]

    per_map: dict[str, dict] = {}
    for stem, perm, seed, r in results:
        d = per_map.setdefault(stem, {"wins": [0] * len(built), "draws": 0, "faults": 0})
        d["faults"] += r.get("faults", 0)
        if not r.get("ok"):
            rows.append({"map": stem, "perm": perm, "seed": seed, **r})
            continue
        if r["winner_bot"] >= 0:
            d["wins"][r["winner_bot"]] += 1
        else:
            d["draws"] += 1
        rows.append({"map": stem, "perm": perm, "seed": seed, **r})
    for mp in maps:
        d = per_map.get(mp.stem, {"wins": [0] * len(built), "draws": 0, "faults": 0})
        print(f"{mp.stem}: wins={dict(zip(labels, d['wins']))} "
              f"draws={d['draws']} faults={d['faults']}")
    summary = {"tool": "judge4", "seats": args.seats, "bots": names,
               "seed": args.seed, "seeds_per_perm": max(1, args.seeds_per_perm),
               "perms_per_map": perms_per_map, "jobs": args.jobs,
               "replay_dir": str(replay_dir) if replay_dir is not None else "",
               "rows": rows}
    if bot_ids is not None:
        summary["bot_ids"] = bot_ids
        summary["bot_labels"] = bot_labels
        summary["bot_meta"] = bot_meta
    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if all(r.get("ok") for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())

