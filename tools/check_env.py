#!/usr/bin/env python3
"""检查本机是否满足题库的选手/评测环境基线。

    python tools/check_env.py
    python tools/check_env.py --game p01-ballistic
    python tools/check_env.py --package dist/contestant/p01-ballistic.zip
    python tools/check_env.py --eval --path dist/eval

检查项：
1. Python >= 3.10；
2. g++ 可执行；
3. C++17 能编译并运行；
4. 指定题目的 template.cpp 能编译（--game，可 all）；
5. 选手包通过 verify_package，且不含 private/ 或构建产物（--package）；
6. 评测包目录结构正确（anchors/manifest.json + 二进制）（--eval）。

node 不在检查项里：本题库是 C++17 + Python，不需要 node。
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
import harness  # noqa: E402


def _cxx(gxx: str) -> list[str]:
    """Windows 静态链接，避免运行时解析到 PATH 里另一份 libstdc++。"""
    return [gxx, "-O2", "-std=c++17"] + (["-static"] if sys.platform == "win32" else [])


class Report:
    def __init__(self) -> None:
        self.items: list[dict] = []
        self.ok = True

    def add(self, name: str, ok: bool, detail: str = "", warn: bool = False) -> None:
        self.items.append({"name": name, "ok": ok, "detail": detail, "warn": warn})
        if not ok and not warn:
            self.ok = False
        mark = "PASS" if ok else ("WARN" if warn else "FAIL")
        print(f"{mark}  {name}" + (f"  {detail}" if detail else ""))

    def emit(self, path: str | None) -> None:
        if not path:
            return
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"tool": "check_env", "ok": self.ok,
                                 "platform": f"{platform.system().lower()}-{platform.machine().lower()}",
                                 "items": self.items}, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")


def cxx17_probe(gxx: str) -> tuple[bool, str]:
    src = ("#include <fstream>\n#include <sstream>\n#include <string>\n"
           "#include <optional>\n#include <variant>\n"
           "int main(){ std::istringstream in(\"42 hello\"); int n = 0; std::string s;\n"
           "in >> n >> s; std::ifstream missing(\"env-probe-missing.txt\");\n"
           "std::optional<int> v = 17; std::variant<int, std::string> x = 3;\n"
           "return (v.has_value() && std::get<int>(x) == 3 && n == 42 && s == \"hello\"\n"
           "        && !missing) ? 0 : 1; }\n")
    with tempfile.TemporaryDirectory(prefix="env-cxx-") as td:
        p = Path(td) / "probe.cpp"
        p.write_text(src, encoding="utf-8", newline="\n")
        exe = Path(td) / "probe.exe"
        r = subprocess.run(_cxx(gxx) + ["-o", str(exe), str(p)],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return False, r.stderr.strip().splitlines()[-1] if r.stderr else "compile failed"
        run = subprocess.run([str(exe)], capture_output=True, timeout=10)
        return run.returncode == 0, ""


def compile_template(gxx: str, game: Path) -> tuple[bool, str]:
    src = game / "public" / "template.cpp"
    if not src.exists():
        return False, "缺少 public/template.cpp"
    with tempfile.TemporaryDirectory(prefix="env-tpl-") as td:
        out = Path(td) / "template.exe"
        r = subprocess.run(_cxx(gxx) + ["-I", str(game / "public"),
                            "-o", str(out), str(src)],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            tail = r.stderr.strip().splitlines()
            return False, tail[-1] if tail else "compile failed"
    return True, ""


def check_package(r: Report, path: Path) -> None:
    if not path.exists():
        r.add(f"package {path.name}", False, "找不到文件")
        return
    if path.suffix.lower() != ".zip":
        r.add(f"package {path.name}", False, "不是 .zip")
        return
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
    except zipfile.BadZipFile:
        r.add(f"package {path.name}", False, "坏 zip")
        return

    problems = []
    if "manifest.json" not in names:
        problems.append("缺 manifest.json")
    if "CONTESTANT_RULES.md" not in names:
        problems.append("缺 CONTESTANT_RULES.md")
    for n in names:
        if n.startswith("private/"):
            problems.append(f"含 {n}")
        if Path(n).suffix.lower() in {".exe", ".o", ".obj", ".pyc"}:
            problems.append(f"含构建产物 {n}")

    if problems:
        r.add(f"package {path.name}", False, "; ".join(problems[:3]))
        return

    vp = subprocess.run([sys.executable, str(HERE / "verify_package.py"),
                         "--path", str(path)], capture_output=True, text=True,
                        encoding="utf-8")
    if vp.returncode != 0:
        r.add(f"package {path.name}", False, "verify_package 未通过")
    else:
        r.add(f"package {path.name}", True, f"{len(names)} 文件")


def check_eval(r: Report, path: Path) -> None:
    if not path.exists() or not path.is_dir():
        r.add(f"eval {path}", False, "目录不存在")
        return
    zips = sorted(path.glob("*.zip"))
    if not zips:
        r.add(f"eval {path}", False, "没有评测包")
        return
    bad = []
    for z in zips:
        try:
            with zipfile.ZipFile(z) as zf:
                names = zf.namelist()
        except zipfile.BadZipFile:
            bad.append(f"{z.name}: 坏 zip")
            continue
        if "manifest.json" not in names:
            bad.append(f"{z.name}: 缺 manifest.json")
        if "anchors/manifest.json" not in names:
            bad.append(f"{z.name}: 缺 anchors/manifest.json")
        bins = [n for n in names if n.startswith("anchors/") and n != "anchors/manifest.json"]
        if not bins:
            bad.append(f"{z.name}: 没有 anchor 二进制")
    if bad:
        r.add(f"eval {path}", False, "; ".join(bad[:3]))
    else:
        r.add(f"eval {path}", True, f"{len(zips)} 个评测包")


def main() -> int:
    ap = argparse.ArgumentParser(description="题库环境基线检查")
    ap.add_argument("--game", default="", help="题号或 all；编译该题的 template.cpp")
    ap.add_argument("--package", action="append", default=[], help="选手包 zip，可多次")
    ap.add_argument("--eval", action="store_true", help="检查评测包目录结构")
    ap.add_argument("--path", default="", help="评测包目录；默认 dist/eval")
    ap.add_argument("--json")
    args = ap.parse_args()

    r = Report()
    print(f"environment  {platform.system().lower()}-{platform.machine().lower()}")

    py_ok = sys.version_info >= (3, 10)
    r.add("python >= 3.10", py_ok, platform.python_version())

    gxx = shutil.which("g++")
    if not gxx:
        r.add("g++", False, "找不到 g++")
    else:
        ver = subprocess.run([gxx, "--version"], capture_output=True, text=True,
                             encoding="utf-8").stdout.splitlines()
        r.add("g++", True, ver[0].strip() if ver else gxx)
        ok, detail = cxx17_probe(gxx)
        r.add("c++17 编译/运行", ok, detail)
        if args.game:
            games = harness.all_games() if args.game.strip().lower() == "all" \
                else [harness.game_dir(args.game)]
            for game in games:
                ok, detail = compile_template(gxx, game)
                r.add(f"template {game.name}", ok, detail)

    if args.package:
        for p in args.package:
            check_package(r, Path(p).resolve())

    if args.eval:
        check_eval(r, Path(args.path).resolve() if args.path
                   else REPO / "dist" / "eval")

    node = shutil.which("node")
    r.add("node", True, "不需要（未安装是正常的）" if not node else "已安装，但本题库不需要",
          warn=False)

    print()
    print(f"{sum(1 for x in r.items if x['ok'])}/{len(r.items)} 项通过")
    if not r.ok:
        print("有 FAIL 项，按上面的提示修环境")
    r.emit(args.json)
    return 0 if r.ok else 1


if __name__ == "__main__":
    sys.exit(main())
