"""题库公共驱动：路径、报告器、验证脚本的统一输出格式。

每道题的规则不同，测试必然不同——但**入口、报告格式、失败语义**可以统一。
这个模块只做这三件事，不碰任何规则。

统一输出契约（所有 tests/validate.py 必须遵守）：

    {
      "tool": "validate",
      "game": "p02-breach-lite",
      "ok": true,
      "passed": 24,
      "total": 24,
      "results": [{"name": "...", "ok": true, "detail": ""}, ...]
    }

`tools/validate.py` 读这个结构做汇总；`tools/acceptance.py` 再叠上打包与泄漏检查。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROBLEMS = REPO / "problems"


def exe_name(stem: str) -> str:
    """按平台补可执行后缀。"""
    return stem + ".exe" if os.name == "nt" else stem


def game_dir(game: str) -> Path:
    """题号或目录名 -> 题目目录。支持 p02 / p02-breach-lite / 绝对路径。

    只给题号时按前缀匹配，必须唯一——歧义时报错而不是猜。
    """
    p = Path(game)
    if p.is_absolute() and p.is_dir():
        return p.resolve()
    if (PROBLEMS / game).is_dir():
        return (PROBLEMS / game).resolve()
    hits = [d for d in PROBLEMS.iterdir() if d.is_dir() and d.name.startswith(game)]
    hits = [d for d in hits if not d.name.startswith("_")]
    if len(hits) == 1:
        return hits[0].resolve()
    if not hits:
        raise SystemExit(f"找不到题目：{game}")
    raise SystemExit(f"题号有歧义：{game} -> {[d.name for d in hits]}")


def all_games() -> list[Path]:
    """所有已实现的题：有 public/engine 的才算，_template 与只有简报的排除。"""
    out = []
    for d in sorted(PROBLEMS.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        if (d / "public" / "engine").is_dir():
            out.append(d.resolve())
    return out


class Reporter:
    """收集 check 结果。不打印 PASS，只打印 FAIL——全过的时候输出应该很短。"""

    def __init__(self) -> None:
        self.results: list[dict] = []

    def check(self, name: str, cond: bool, detail: str = "") -> None:
        ok = bool(cond)
        self.results.append({"name": name, "ok": ok, "detail": detail})
        if not ok:
            print(f"  FAIL  {name}  {detail}")

    def extend(self, results: list[dict]) -> None:
        self.results.extend(results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r["ok"])

    @property
    def failed(self) -> list[dict]:
        return [r for r in self.results if not r["ok"]]

    @property
    def ok(self) -> bool:
        return not self.failed and bool(self.results)

    def summary(self, game: str) -> str:
        bad = self.failed
        head = f"{game}: {self.passed}/{len(self.results)} pass"
        if not bad:
            return head
        lines = [head, "  失败项："]
        for r in bad:
            lines.append(f"    - {r['name']}: {r['detail']}")
        return "\n".join(lines)

    def emit(self, path: str | None, game: str, tool: str = "validate") -> None:
        if not path:
            return
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "tool": tool, "game": game, "ok": self.ok,
            "passed": self.passed, "total": len(self.results),
            "results": self.results,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_module(path: Path):
    """按路径加载 tests/validate.py。题目之间同名模块会互相覆盖，所以不能用 import。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(f"_bank_{path.parent.parent.name}", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"无法加载 {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def run_game_validate(game: Path, fuzz: int, json_out: str | None,
                      extra: list[str] | None = None) -> tuple[bool, int, int]:
    """以子进程跑某题的 tests/validate.py，读它写出的 json。

    用子进程而不是 import：每道题的验证脚本都会拉起引擎、改写全局状态，
    同进程连跑多题时残留状态会互相污染（P02 就踩过一次 static 变量的坑）。
    """
    script = game / "tests" / "validate.py"
    if not script.exists():
        print(f"  SKIP  {game.name}: 没有 tests/validate.py")
        return (True, 0, 0)

    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="bank-val-"))
    report = Path(json_out) if json_out else tmp / f"{game.name}.json"
    cmd = [sys.executable, str(script), "--json", str(report), "--fuzz", str(fuzz)]
    if extra:
        cmd += extra
    r = subprocess.run(cmd, cwd=str(REPO), text=True, capture_output=True, encoding="utf-8")
    if r.stdout:
        print(r.stdout.rstrip())
    if r.returncode != 0 and r.stderr:
        print(r.stderr.rstrip()[-2000:])

    if not report.exists():
        print(f"  FAIL  {game.name}: 未产出验证报告（exit={r.returncode}）")
        return (False, 0, 0)
    data = json.loads(report.read_text(encoding="utf-8"))
    return (bool(data.get("ok")), int(data.get("passed", 0)), int(data.get("total", 0)))
