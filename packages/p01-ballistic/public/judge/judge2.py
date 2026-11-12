#!/usr/bin/env python3
"""P01 BALLISTIC · 本地裁判。

单局 40 轮，双方同时下令。裁判只做进程/计时/判罚/回放，不含任何物理或胜负规则。
地形每步从引擎随取随用（GRID 命令），不在裁判侧维护副本——副本一定会和引擎走偏。
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

GAME_ID = "p01-ballistic"
GAME_VERSION = "2.0.0"
ROUNDS = 40
MAX_FAULTS = 3
WALL_FACTOR = 4.0
WALL_FLOOR_MS = 250


def _replay_meta() -> dict:
    """回放 header 用：拿当前 meta 的两个 hash，判断回放是否还能重算。"""
    try:
        return json.loads((Path(__file__).resolve().parents[2] / "meta.json")
                          .read_text(encoding="utf-8"))
    except Exception:
        return {}


class CompileError(RuntimeError):
    pass


def _exe(name: str) -> str:
    return name + ".exe" if os.name == "nt" else name


CXX = ["g++", "-O2", "-std=c++17"] + (["-static"] if os.name == "nt" else [])


def _up_to_date(out: Path, inputs: list[Path]) -> bool:
    stamp = out.with_name(out.name + ".cxxflags")
    try:
        if stamp.read_text(encoding="utf-8").strip() != " ".join(CXX):
            return False
    except OSError:
        return False
    return out.exists() and out.stat().st_mtime >= max(p.stat().st_mtime for p in inputs)


def compile_cpp(src: Path, out: Path, include_dir: Path | None = None) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [*CXX, "-o", str(out), str(src)]
    if include_dir is not None:
        cmd += ["-I", str(include_dir)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise CompileError(f"编译失败 {src}\n{proc.stderr}")
    out.with_name(out.name + ".cxxflags").write_text(" ".join(CXX), encoding="utf-8")
    return out


def build_engine(game_dir: Path) -> Path:
    src = game_dir / "public" / "engine" / "engine.cpp"
    out = game_dir / "build" / _exe("engine")
    deps = [src, *(game_dir / "public" / "engine").glob("*.hpp")]
    if _up_to_date(out, deps):
        return out
    return compile_cpp(src, out, include_dir=game_dir / "public" / "engine")


_SOURCE_SUFFIX = {".cpp", ".cc", ".cxx", ".c"}


def build_bot(src: Path, out_dir: Path, include_dir: Path | None = None) -> Path:
    # 已是可执行文件就直接用：Windows .exe 与 Linux 的 bin/linux/*（无后缀 ELF）
    # 都属于这一类。旧逻辑只认 .exe，Linux 二进制会被当成源码去编译
    # （judge4 的 resolve_bot 在非 win32 上已优先返回 bin/linux/）。
    if src.suffix not in _SOURCE_SUFFIX:
        return src
    out = out_dir / _exe(src.stem)
    deps = [src, *(include_dir.rglob("*.hpp") if include_dir else [])]
    if _up_to_date(out, deps):
        return out
    return compile_cpp(src, out, include_dir=include_dir)


def resolve_spec(spec: str, game_dir: Path) -> Path:
    p = Path(spec)
    if p.exists():
        return p.resolve()
    for cand in (game_dir / "private" / "anchors" / f"{spec}.cpp",
                 game_dir / "public" / f"{spec}.cpp"):
        if cand.exists():
            return cand.resolve()
    raise FileNotFoundError(f"找不到 bot: {spec}")


# ---------------------------------------------------------------- 进程

def _shots_from_body(body: str, st: dict) -> list[dict]:
    """Extract visual shot metadata from a normalized order body.

    Origins are the pre-step worm cells.  The replay viewer uses them only for
    the projectile animation; state hashes still come from the engine.
    """
    out: list[dict] = []
    cur_wid: int | None = None
    for part in body.split(";"):
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
        if not 0 <= wid < len(st["worm"]):
            continue
        w = st["worm"][wid]
        if not w["alive"]:
            continue
        out.append({"seat": wid // 3, "worm": wid, "kind": toks[1].upper(),
                    "angle": angle, "power": power, "x": w["x"], "y": w["y"]})
    return out


def _replay_state(st: dict, grid: list[str], wind: int,
                  faults: list[int], shots: list[dict]) -> dict:
    """Capture the state payload shared by the initial and per-turn snapshots."""
    return {
        "hp": list(st["hp"]), "damage": list(st["damage"]),
        "survivors": [sum(1 for w in st["worm"]
                          if w["id"] // 3 == seat and w["alive"])
                      for seat in (0, 1)],
        "faults": list(faults), "grid": grid, "wind": wind, "shots": shots,
        "worms": [{"x": w["x"], "y": w["y"], "hp": w["hp"],
                   "alive": w["alive"]} for w in st["worm"]],
    }


class Engine:
    def __init__(self, exe: Path):
        self.proc = subprocess.Popen(
            [str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1, encoding="utf-8",
        )

    def _write(self, s: str) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(s + "\n")
        self.proc.stdin.flush()

    def _read(self) -> str:
        assert self.proc.stdout is not None
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("engine 意外退出")
        return line.rstrip("\r\n")

    def load(self, map_path: Path, seed: int, strict: bool = True,
             mask: int | None = None) -> tuple[str, int]:
        cmd = "LOAD" if strict else "LOADX"
        suffix = f" {mask}" if mask is not None else ""
        self._write(f"{cmd} {map_path.as_posix()} {seed}{suffix}")
        line = self._read()
        if line.startswith("ERR"):
            raise RuntimeError(f"engine 拒绝加载：{line[4:]}")
        _, digest, first = line.split()
        return digest, int(first)

    def step(self, a0: str, a1: str) -> dict:
        self._write(f"STEP {a0} | {a1}")
        line = self._read()
        if line.startswith("ERR"):
            raise RuntimeError(f"engine 报错：{line[4:]}")
        return parse_state(line)

    def grid(self) -> list[str]:
        self._write("GRID")
        rows = []
        while True:
            line = self._read()
            if line == "GEND":
                return rows
            if line.startswith("G "):
                rows.append(line[2:])
            elif line.startswith("ERR"):
                raise RuntimeError(f"engine 报错：{line[4:]}")

    def close(self) -> None:
        try:
            self._write("QUIT")
        except Exception:
            pass
        try:
            self.proc.wait(timeout=2)
        except Exception:
            self.proc.kill()


def parse_state(line: str) -> dict:
    p = line.split()
    st = {
        "round": int(p[1]), "wind": int(p[2]) - 32, "water": int(p[3]),
        "over": int(p[4]) == 1, "winner": int(p[5]),
        "hp": [int(p[6]), int(p[7])], "damage": [int(p[8]), int(p[9])],
        "reason": p[10], "bad": [int(p[11]) == 1, int(p[12]) == 1],
        "hash": p[13], "worm": [],
    }
    base = 14
    for i in range(6):
        o = base + i * 6
        st["worm"].append({
            "id": i, "x": int(p[o]), "y": int(p[o + 1]), "hp": int(p[o + 2]),
            "grenades": int(p[o + 3]), "drills": int(p[o + 4]), "alive": int(p[o + 5]) == 1,
        })
    return st


# ---------------------------------------------------------------- CPU 计时
# 单步预算按子进程 CPU 时间计，wall clock 只做兜底。
# Windows 用 GetProcessTimes；Linux 读 /proc/<pid>/stat；其他平台返回 None，
# 调用方自动退回 wall clock。

class ProcessCPU:
    def __init__(self, pid: int):
        self.pid = pid
        self._handle = None
        self._get = None
        self._ft = None
        self._clk = 0
        if os.name == "nt":
            try:
                import ctypes
                from ctypes import wintypes

                class FILETIME(ctypes.Structure):
                    _fields_ = [("dwLowDateTime", wintypes.DWORD),
                                ("dwHighDateTime", wintypes.DWORD)]

                k32 = ctypes.WinDLL("kernel32", use_last_error=True)
                k32.OpenProcess.restype = wintypes.HANDLE
                k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                h = k32.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
                if h:
                    k32.GetProcessTimes.argtypes = [
                        wintypes.HANDLE, ctypes.POINTER(FILETIME),
                        ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME),
                        ctypes.POINTER(FILETIME)]
                    k32.GetProcessTimes.restype = wintypes.BOOL
                    self._handle = h
                    self._get = k32.GetProcessTimes
                    self._ft = FILETIME
            except Exception:
                self._handle = None
        elif sys.platform.startswith("linux"):
            try:
                self._clk = os.sysconf("SC_CLK_TCK")
            except Exception:
                self._clk = 0
        # 采样精度：Windows GetProcessTimes 按调度 tick 更新（15.625 ms），
        # Linux /proc 按 USER_HZ 更新。判罚时加一个粒度，避免把“实际 50ms、
        # 采样到 62.5ms”的步误判成超时。
        if os.name == "nt":
            self.granularity_ms = 15.625
        elif self._clk:
            self.granularity_ms = 1000.0 / self._clk
        else:
            self.granularity_ms = 0.0

    def sample(self) -> float | None:
        """返回进程累计 CPU 秒数；不可用时返回 None。"""
        if os.name == "nt":
            if not self._handle:
                return None
            try:
                import ctypes
                a, b, c, d = self._ft(), self._ft(), self._ft(), self._ft()
                if not self._get(self._handle, ctypes.byref(a), ctypes.byref(b),
                                 ctypes.byref(c), ctypes.byref(d)):
                    return None
                total = ((c.dwHighDateTime << 32 | c.dwLowDateTime)
                         + (d.dwHighDateTime << 32 | d.dwLowDateTime))
                return total / 1e7
            except Exception:
                return None
        if self._clk:
            try:
                fields = Path(f"/proc/{self.pid}/stat").read_text().split()
                return (int(fields[13]) + int(fields[14])) / self._clk
            except Exception:
                return None
        return None

    def close(self) -> None:
        if self._handle:
            try:
                import ctypes
                ctypes.WinDLL("kernel32").CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None


MAX_LINE_BYTES = 65536   # 单行上限：超过就当噪音丢弃
QUEUE_MAX = 256          # 队列上限：满了反压到子进程管道


class Bot:
    """bot 子进程 + 后台读线程。Windows 上不能对管道用 select，只能靠线程喂队列。

    这里的三条防御都是被"恶意选手"实测打出来的（见 `tools/abuse.py`）：

    - **有界队列**：bot 可以无限刷 stdout。无界队列会把裁判内存吃光，堆到交换分区后整场卡死。
      有界队列把压力反推到子进程的管道上——它写不进去，就不会继续吃内存。
    - **单行上限**：`readline()` 不限长时，一行 300 KB 会先整个读进内存再判非法。
      改成限长读取，超长部分直接丢掉。
    - **清空积压**：一回合打两行，会让后面每个回合都错位一格（读到上一回合的答案）。
      `recv` 取第一行，其余丢弃并计入 `stale`。
    """

    def __init__(self, exe: Path):
        self.proc = subprocess.Popen(
            [str(exe)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            encoding="utf-8",
        )
        self.q: queue.Queue[str | None] = queue.Queue(maxsize=QUEUE_MAX)
        self.stale = 0        # 一回合多打的垃圾行数
        self.oversize = 0     # 超长行数
        self.reader = threading.Thread(target=self._pump, daemon=True)
        self.reader.start()
        self.cpu = ProcessCPU(self.proc.pid)
        self.last_cpu_ms: float | None = None
        self.last_wall_ms: float | None = None

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        try:
            while True:
                line = self.proc.stdout.readline(MAX_LINE_BYTES + 1)
                if not line:
                    break
                if len(line) > MAX_LINE_BYTES:
                    self.oversize += 1
                    # 把这条超长行的剩余部分读掉，避免下一行从半截开始
                    while line and not line.endswith("\n"):
                        line = self.proc.stdout.readline(MAX_LINE_BYTES)
                    continue
                try:
                    self.q.put(line.rstrip("\r\n"), timeout=5.0)
                except queue.Full:
                    self.stale += 1     # 队列满：丢掉，别把裁判拖死
        except Exception:
            pass
        try:
            self.q.put(None, timeout=5.0)
        except queue.Full:
            pass

    def send(self, text: str) -> bool:
        if self.proc.poll() is not None:
            return False
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.write(text)
            self.proc.stdin.flush()
            return True
        except Exception:
            return False

    def recv(self, timeout: float) -> str | None:
        try:
            line = self.q.get(timeout=max(0.001, timeout))
        except queue.Empty:
            return None
        # 把积压的清掉：只保留第一条，其余算噪音
        while True:
            try:
                extra = self.q.get_nowait()
            except queue.Empty:
                break
            if extra is not None:
                self.stale += 1
        return line

    def recv_limited(self, cpu_ms: float, wall_s: float) -> str | None:
        """先看队列；没有消息时按 CPU 预算轮询，wall 只做兜底。

        同时记录本步耗时（profile_turn 用）：last_cpu_ms / last_wall_ms。
        """
        t0 = time.time()
        base = self.cpu.sample()
        line = None
        if base is None:
            line = self.recv(wall_s)
        else:
            while True:
                try:
                    line = self.q.get_nowait()
                except queue.Empty:
                    pass
                else:
                    while True:
                        try:
                            extra = self.q.get_nowait()
                        except queue.Empty:
                            break
                        if extra is not None:
                            self.stale += 1
                    break
                now = self.cpu.sample()
                if (now is not None
                        and (now - base) * 1000.0 >= cpu_ms + self.cpu.granularity_ms):
                    break
                if time.time() - t0 >= wall_s:
                    break
                time.sleep(0.001)
        now = self.cpu.sample()
        self.last_cpu_ms = None if (base is None or now is None) else (now - base) * 1000.0
        self.last_wall_ms = (time.time() - t0) * 1000.0
        return line

    def alive(self) -> bool:
        return self.proc.poll() is None

    def close(self) -> None:
        self.cpu.close()
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


def load_grid(map_path: Path) -> list[str]:
    rows = []
    for line in map_path.read_text(encoding="utf-8").splitlines():
        line = line.rstrip("\r\n")
        if line:
            rows.append(line)
    if len(rows) != 54 or any(len(r) != 96 for r in rows):
        raise RuntimeError(f"地图 {map_path} 不是 96x54")
    return rows


def _init_text(seat: int, rules_hash: str, st: dict, grid: list[str], map_id: str) -> str:
    lines = [
        "INIT", f"game {GAME_ID}", f"version {GAME_VERSION}", f"rules_hash {rules_hash}",
        f"seat {seat}", f"rounds {ROUNDS}", "size 96 54",
        f"water {st['water']}", f"wind {st['wind']}", f"map {map_id}",
    ]
    for w in st["worm"]:
        side = "mine" if w["id"] // 3 == seat else "enemy"
        lines.append(f"worm {w['id']} {w['x']} {w['y']} {w['hp']} {w['grenades']} {w['drills']} {side}")
    lines += [f"grid {r}" for r in grid]
    lines.append("END")
    return "\n".join(lines) + "\n"


def _turn_text(seat: int, st: dict, grid: list[str]) -> str:
    enemy = 1 - seat
    alive_me = sum(1 for w in st["worm"] if w["alive"] and w["id"] // 3 == seat)
    alive_en = sum(1 for w in st["worm"] if w["alive"] and w["id"] // 3 == enemy)
    lines = [
        f"TURN {st['round']}", f"wind {st['wind']}", f"water {st['water']}",
        f"alive {alive_me} {alive_en}",
    ]
    for w in st["worm"]:
        if not w["alive"]:
            continue
        side = "mine" if w["id"] // 3 == seat else "enemy"
        lines.append(f"worm {w['id']} {w['x']} {w['y']} {w['hp']} {w['grenades']} {w['drills']} {side}")
    lines += [f"grid {r}" for r in grid]
    lines.append("END")
    return "\n".join(lines) + "\n"


def _parse_order(line: str | None, turn: int, seat: int, st: dict) -> tuple[str | None, str]:
    """返回 (可直接喂给引擎的指令串, 错误标记)。超时/格式错都返回 None。"""
    if line is None:
        return None, "timeout"
    line = line.strip()
    if not line or "|" in line:
        return None, "invalid"
    parts = line.split(maxsplit=1)
    if len(parts) < 2:
        return None, "invalid"
    if parts[0] != str(turn):
        return None, "invalid"
    body = parts[1].strip()
    act_tok = body.split()[0] if body.split() else ""
    try:
        act = int(act_tok)
    except ValueError:
        return None, "invalid"
    if act < seat * 3 or act >= seat * 3 + 3:
        return None, "invalid"
    return body, ""


def _play_one(engine: Engine, map_path: Path, map_id: str, rules_hash: str,
              bot_paths: dict[int, Path], seed: int,
              turn_ms: int, first_ms: int) -> dict:
    grid = load_grid(map_path)
    turns: list = []
    bots = {0: Bot(bot_paths[0]), 1: Bot(bot_paths[1])}

    try:
        digest, _ = engine.load(map_path, seed)
        live_grid = engine.grid()

        st = {
            "round": 1, "wind": 0, "water": 50, "over": False, "winner": -1,
            "hp": [300, 300], "damage": [0, 0], "reason": "RUNNING",
            "bad": [False, False], "hash": digest,
            "worm": [{"id": i, "x": 0, "y": 0, "hp": 100, "grenades": 2, "drills": 2,
                      "alive": True} for i in range(6)],
        }
        # 用 INFO 补齐初始位置
        engine._write("INFO")
        while True:
            line = engine._read()
            if line.startswith("INFO worm"):
                _, _, wid, x, y, hp = line.split()
                st["worm"][int(wid)].update({"x": int(x), "y": int(y), "hp": int(hp)})
            elif line.startswith("INFO water"):
                _, _, water, _, wind = line.split()
                st["water"] = int(water)
                st["wind"] = int(wind)
                break          # INFO 的最后一行，不 break 会一直等下去
            else:
                break

        for seat in (0, 1):
            bots[seat].send(_init_text(seat, rules_hash, st, live_grid, map_id))

        first = [True, True]
        faults = [0, 0]
        initial_state = {
            "t": st["round"], "state_hash": st["hash"],
            "state": _replay_state(st, live_grid, st["wind"], faults, []),
        }
        forfeit = -1
        turn_wall = max(turn_ms / 1000.0 * WALL_FACTOR, WALL_FLOOR_MS / 1000.0)

        while not st["over"]:
            bodies = {}
            for seat in (0, 1):
                if not bots[seat].send(_turn_text(seat, st, live_grid)):
                    forfeit = seat
                    break
                line = bots[seat].recv_limited(
                    turn_ms, first_ms / 1000.0 if first[seat] else turn_wall)
                first[seat] = False
                body, err = _parse_order(line, st["round"], seat, st)
                if err:
                    faults[seat] += 1
                    # A valid WAIT for one of this seat's worms: the engine must
                    # not add a second bad/fault on top of the timeout.
                    body = next((f"{i} WAIT" for i in range(seat * 3, seat * 3 + 3)
                                 if st["worm"][i]["alive"]), f"{seat * 3} WAIT")
                bodies[seat] = body
            if forfeit >= 0:
                break

            a0 = bodies[0] if bodies[0] else "-1 X"
            a1 = bodies[1] if bodies[1] else "-1 X"
            wind = st["wind"]
            shots = _shots_from_body(a0, st) + _shots_from_body(a1, st)
            st = engine.step(a0, a1)
            live_grid = engine.grid()

            if st["bad"][0]:
                faults[0] += 1
            if st["bad"][1]:
                faults[1] += 1

            # 先记录再判负：触发第 3 次故障的那一步也是有效结算，回放缺了它
            # 就会在重算时少走一步，结果字段对不上。
            turns.append({"type": "turn", "t": st["round"],
                          "orders": {str(0): a0, str(1): a1}, "state_hash": st["hash"],
                          "state": _replay_state(st, live_grid, wind, faults, shots),
                          "timing": {str(s): {"cpu_ms": bots[s].last_cpu_ms,
                                              "wall_ms": bots[s].last_wall_ms}
                                     for s in (0, 1)}})

            if faults[0] >= MAX_FAULTS:
                forfeit = 0
                break
            if faults[1] >= MAX_FAULTS:
                forfeit = 1
                break

            if st["round"] > ROUNDS:
                break
    finally:
        for b in bots.values():
            b.close()

    winner = st["winner"]
    if forfeit >= 0:
        winner = 1 - forfeit

    survivors = [sum(1 for w in st["worm"] if w["alive"] and w["id"] // 3 == seat)
                 for seat in (0, 1)]

    return {
        "map": map_id, "seed": seed, "winner": winner, "reason": st["reason"],
        "round": st["round"], "hp": st["hp"], "damage": st["damage"],
        "survivors": survivors, "faults": faults, "forfeit": forfeit,
        "turns": turns, "initial_state": initial_state,
    }


def play_match(engine_exe: Path, map_path: Path, map_id: str, rules_hash: str,
               bot_paths: dict[int, Path], seed: int = 0,
               turn_ms: int = 50, first_ms: int = 2000,
               replay_path: Path | None = None) -> dict:
    """一场 = 两局，第二局交换座位。

    同 tick 爆炸按轮次奇偶换序；两局再交换 bot 的地图座位，抵消地形和
    剩余顺序差。单局完全同分各记 0.5 分，不使用 seed 抓阄。
    """
    engine = Engine(engine_exe)
    games: list[dict] = []
    try:
        for i in range(2):
            if i == 0:
                seats = {0: bot_paths[0], 1: bot_paths[1]}
                seat_bots = [0, 1]
            else:
                seats = {0: bot_paths[1], 1: bot_paths[0]}
                seat_bots = [1, 0]
            g = _play_one(engine, map_path, map_id, rules_hash, seats,
                          seed, turn_ms, first_ms)
            g["seat_bots"] = seat_bots
            games.append(g)
    finally:
        engine.close()

    points = [0.0, 0.0]
    total_hp = [0, 0]
    total_damage = [0, 0]
    total_survivors = [0, 0]
    for g in games:
        for seat in (0, 1):
            bot = g["seat_bots"][seat]
            total_hp[bot] += g["hp"][seat]
            total_damage[bot] += g["damage"][seat]
            total_survivors[bot] += g["survivors"][seat]
        # 局分每局只记一次；上面那个循环是“按座位累加统计”
        if g["forfeit"] >= 0:
            points[g["seat_bots"][1 - g["forfeit"]]] += 1
        elif g["winner"] >= 0:
            points[g["seat_bots"][g["winner"]]] += 1
        else:
            points[0] += 0.5
            points[1] += 0.5

    if points[0] != points[1]:
        winner = 0 if points[0] > points[1] else 1
        decided_by = "points"
    elif total_hp[0] != total_hp[1]:
        winner = 0 if total_hp[0] > total_hp[1] else 1
        decided_by = "hp"
    elif total_damage[0] != total_damage[1]:
        winner = 0 if total_damage[0] > total_damage[1] else 1
        decided_by = "damage"
    elif total_survivors[0] != total_survivors[1]:
        winner = 0 if total_survivors[0] > total_survivors[1] else 1
        decided_by = "survivors"
    else:
        # 局分、剩余血量、总伤害、存活数全相同 —— 这就是平局。
        # 不拿 seed 抓阄：那会让座位偏差伪装成胜负。
        winner = -1
        decided_by = "draw"

    result = {
        "map": map_id, "seed": seed,
        "games": [{k: v for k, v in g.items()
                   if k not in {"turns", "initial_state"}} for g in games],
        "points": points,
        "total_hp": total_hp, "total_damage": total_damage,
        "total_survivors": total_survivors,
        "winner": winner, "decided_by": decided_by,
        "timing": [{"game": tag, "t": rec["t"], "seat": int(seat),
                    "cpu_ms": tv.get("cpu_ms"), "wall_ms": tv.get("wall_ms")}
                   for tag, g in zip("AB", games) for rec in g["turns"]
                   for seat, tv in (rec.get("timing") or {}).items()],
    }

    if replay_path is not None:
        replay_path.parent.mkdir(parents=True, exist_ok=True)
        meta = _replay_meta()
        with replay_path.open("w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"type": "header", "game": GAME_ID, "version": GAME_VERSION,
                                "rules_hash": meta.get("rules_hash", rules_hash),
                                "engine_hash": meta.get("engine_hash", "sha256:unknown"),
                                "seed": seed, "map": map_id}, ensure_ascii=False) + "\n")
            f.write(json.dumps({"type": "map", "id": map_id, "grid": load_grid(map_path)},
                               ensure_ascii=False) + "\n")
            for tag, g in zip("AB", games):
                f.write(json.dumps({"type": "game", "label": tag,
                                    "seat_bots": g["seat_bots"],
                                    "initial_state": g["initial_state"]},
                                   ensure_ascii=False) + "\n")
                for rec in g["turns"]:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.write(json.dumps({"type": "result",
                                **{k: v for k, v in result.items() if k != "timing"}},
                               ensure_ascii=False) + "\n")

    return result


def settle(result: dict, seed: int) -> int:
    return result["winner"]

def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="P01 BALLISTIC 本地裁判")
    ap.add_argument("--game-dir", required=True)
    ap.add_argument("--map", required=True)
    ap.add_argument("--bot0", required=True)
    ap.add_argument("--bot1", required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--replay")
    ap.add_argument("--json")
    args = ap.parse_args()

    game = Path(args.game_dir).resolve()
    engine_exe = build_engine(game)
    build_dir = game / "build"
    bots = {
        0: build_bot(resolve_spec(args.bot0, game), build_dir, game / "public"),
        1: build_bot(resolve_spec(args.bot1, game), build_dir, game / "public"),
    }
    map_path = Path(args.map).resolve()
    result = play_match(engine_exe, map_path, map_path.stem, "0" * 16, bots,
                        seed=args.seed,
                        replay_path=Path(args.replay) if args.replay else None)

    print(f"map {result['map']}  seed {result['seed']}")
    ga, gb = result["games"]
    print(f"A (seat0 first)  round {ga['round']}  reason {ga['reason']}  "
          f"hp {ga['hp']}  damage {ga['damage']}")
    print(f"B (seat1 first)  round {gb['round']}  reason {gb['reason']}  "
          f"hp {gb['hp']}  damage {gb['damage']}")
    print(f"points {result['points']}  total hp {result['total_hp']}  "
          f"decided by {result['decided_by']}")
    print(f"winner bot {result['winner']}")

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
