#!/usr/bin/env python3
"""题库总验收：一条命令跑完所有题的所有硬性检查。

    python tools/acceptance.py                 # 全部题，fuzz=0
    python tools/acceptance.py --fuzz 100      # 带 fuzz
    python tools/acceptance.py --game p01      # 单题
    python tools/acceptance.py --json out.json

检查项对应 `docs/acceptance.md` 的 DoD：

    docs      RULES / README / CONDITIONS / prompt / meta 齐全
    meta      version/status 已填、无 TODO、两个 hash 与实际文件一致
    maps      meta 的 public_maps / hidden_maps 与实际地图文件数一致
    validate  tests/validate.py 全过（D7）
    validate4 tests/validate4.py 全过（有该脚本的题才跑）
    pack      两次打包 sha256 一致（D8）
    leak      选手包 0 fail（D8）
    package   manifest 自校验：每个文件的 sha256 对得上（D8）
    ranking   可选：唯一有效开局的相邻 anchor 档位（--ranking）

写文件、跑对局、比哈希都在这里；规则判定不在这里——那是各题 tests 的事。
默认 PASS 只表示工程通过；--ranking 的总体 PASS 才包含排名门槛。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
import harness  # noqa: E402

REQUIRED_DOCS = ["RULES.md", "README.md", "CONDITIONS.md", "prompt.md", "meta.json"]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def check_docs(game: Path) -> tuple[bool, str]:
    missing = [n for n in REQUIRED_DOCS if not (game / n).exists()]
    if missing:
        return False, "缺 " + ",".join(missing)
    return True, ""


def check_meta(game: Path) -> tuple[bool, str]:
    p = game / "meta.json"
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return False, f"meta 读不出来：{exc}"

    problems = []
    if not m.get("version") or str(m.get("version")).startswith("0."):
        problems.append(f"version={m.get('version')}")
    if m.get("status") in (None, "", "draft"):
        problems.append(f"status={m.get('status')}")

    raw = p.read_text(encoding="utf-8")
    if "TODO" in raw:
        problems.append("meta 里还有 TODO")

    # hash 必须指向真实文件：改了却没更新 meta，是题库最容易出现的隐性漂移
    # v2.4.3 补 arena_hash：选手真正逐行依赖的是 public/arena4.hpp，而以前没人校验它
    # （refresh_meta 会写、pack manifest 会带，但 acceptance 不查 —— 改了头文件没人报错）。
    # 每个 hash 允许的候选文件：P01 是 arena4.hpp，其他题用 arena.hpp。
    # 字段本身可选（有的题没有这一份头文件），但【一旦写了就必须对】。
    HASH_FILES = {
        "rules_hash": ("RULES.md",),
        "engine_hash": ("public/engine/engine.cpp",),
        "arena_hash": ("public/arena4.hpp", "public/arena.hpp"),
    }
    for key, rels in HASH_FILES.items():
        want = m.get(key)
        f = next((game / rel for rel in rels if (game / rel).exists()), None)
        if not want:
            if f is not None:
                problems.append(f"{key} 缺失（有 {f.relative_to(game).as_posix()} 但 meta 没写）")
            continue
        if f is None:
            problems.append(f"{key} 指向的文件不存在（试过 " + ", ".join(rels) + "）")
            continue
        got = "sha256:" + sha256(f)[:16]
        if want != got:
            problems.append(f"{key} 过期（meta {want} vs 实际 {got}）")

    return (not problems), "; ".join(problems)


def check_maps(game: Path) -> tuple[bool, str]:
    """地图数量必须和 meta 一致；加图后忘了改计数会在这里暴露。"""
    try:
        m = json.loads((game / "meta.json").read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return False, f"meta 读不出来：{exc}"
    problems = []
    counts = []
    labels = {"public_maps": "public", "hidden_maps": "hidden",
              "public_maps4": "public4", "hidden_maps4": "hidden4"}
    for key, rel in (("public_maps", "public/maps"),
                     ("hidden_maps", "private/maps_hidden"),
                     ("public_maps4", "public/maps4"),
                     ("hidden_maps4", "private/maps4_hidden")):
        want = m.get(key)
        d = game / rel
        if rel.startswith("private/") and not (game / "private").exists():
            # 私有内容没挂进来（tools/link_private.py）：隐藏图数量无从核对，跳过而不是判失败
            counts.append(f"{labels[key]}=skipped(no private)")
            continue
        got = len(list(d.glob("*.txt"))) if d.is_dir() else 0
        if want is None:
            if d.is_dir():
                problems.append(f"{key} 缺失")
            continue
        if int(want) != got:
            problems.append(f"{key}={want} 但实际 {got} 张")
        counts.append(f"{labels[key]}={got}")
    return (not problems), "; ".join(problems) or ", ".join(counts)


def check_validate(game: Path, fuzz: int) -> tuple[bool, str]:
    script = game / "tests" / "validate.py"
    if not script.exists():
        return False, "没有 tests/validate.py"
    tmp = Path(tempfile.mkdtemp(prefix="acc-val-"))
    report = tmp / "v.json"
    cmd = [sys.executable, str(script), "--json", str(report), "--fuzz", str(fuzz)]
    r = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, encoding="utf-8")
    if not report.exists():
        return False, f"验证脚本没产出报告（exit={r.returncode}）"
    d = json.loads(report.read_text(encoding="utf-8"))
    if d.get("ok"):
        return True, f"{d['passed']}/{d['total']}"
    bad = [x["name"] for x in d.get("results", []) if not x["ok"]]
    return False, f"{d['passed']}/{d['total']}，失败：{'; '.join(bad[:3])}"


def check_validate4(game: Path) -> tuple[bool, str]:
    script = game / "tests" / "validate4.py"
    if not script.exists():
        return True, "n/a"
    tmp = Path(tempfile.mkdtemp(prefix="acc-val4-"))
    report = tmp / "v4.json"
    r = subprocess.run([sys.executable, str(script), "--json", str(report)],
                       cwd=str(REPO), capture_output=True, text=True, encoding="utf-8")
    if not report.exists():
        return False, f"4席验证脚本没产出报告（exit={r.returncode}）"
    d = json.loads(report.read_text(encoding="utf-8"))
    if d.get("ok"):
        return True, f"{d['passed']}/{d['total']}"
    bad = [x["name"] for x in d.get("results", []) if not x["ok"]]
    return False, f"{d['passed']}/{d['total']}，失败：{'; '.join(bad[:3])}"


def check_pack(game: Path) -> tuple[bool, str]:
    tmp = Path(tempfile.mkdtemp(prefix="acc-pack-"))
    a, b = tmp / "a.zip", tmp / "b.zip"
    outs = []
    for out in (a, b):
        r = subprocess.run([sys.executable, str(HERE / "pack.py"), "--game-dir", str(game),
                            "--out", str(out)],
                           cwd=str(REPO), capture_output=True, text=True, encoding="utf-8")
        if r.returncode != 0 or not out.exists():
            return False, f"打包失败：{r.stderr.strip()[:120]}"
        outs.append(sha256(out))
    if outs[0] != outs[1]:
        return False, f"两次打包不一致 {outs[0][:12]} vs {outs[1][:12]}"
    return True, outs[0][:12]


def check_package(game: Path) -> tuple[bool, str]:
    """打一次包，再用 verify_package 自校验——保证发出去的包能自证完整。"""
    tmp = Path(tempfile.mkdtemp(prefix="acc-pkg-"))
    z = tmp / "pkg.zip"
    r = subprocess.run([sys.executable, str(HERE / "pack.py"), "--game-dir", str(game),
                        "--out", str(z)],
                       cwd=str(REPO), capture_output=True, text=True, encoding="utf-8")
    if not z.exists():
        return False, "打包失败"
    r = subprocess.run([sys.executable, str(HERE / "verify_package.py"), "--path", str(z)],
                       cwd=str(REPO), capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        fails = [ln for ln in r.stdout.splitlines() if ln.startswith("[FAIL]")]
        return False, "; ".join(fails[:2])
    return True, "manifest ok"


def check_leak(game: Path) -> tuple[bool, str]:
    tmp = Path(tempfile.mkdtemp(prefix="acc-leak-"))
    z = tmp / "pkg.zip"
    r = subprocess.run([sys.executable, str(HERE / "pack.py"), "--game-dir", str(game),
                        "--out", str(z)],
                       cwd=str(REPO), capture_output=True, text=True, encoding="utf-8")
    if not z.exists():
        return False, "打包失败，无法检查泄漏"
    r = subprocess.run([sys.executable, str(HERE / "leakcheck.py"), "--path", str(z)],
                       cwd=str(REPO), capture_output=True, text=True, encoding="utf-8")
    tail = r.stdout.strip().splitlines()
    summary = tail[-1] if tail else ""
    if r.returncode != 0:
        fails = [ln for ln in tail if ln.startswith("[FAIL]")]
        return False, f"{summary} {'; '.join(fails[:2])}"
    return True, summary or "ok"


def check_ranking(game: Path, matches: int) -> tuple[bool, str]:
    tmp = Path(tempfile.mkdtemp(prefix="acc-ranking-"))
    report = tmp / "ranking.json"
    r = subprocess.run([sys.executable, str(HERE / "anchor_audit.py"),
                        "--game", game.name, "--matches", str(matches),
                        "--json", str(report)],
                       cwd=str(REPO), capture_output=True, text=True, encoding="utf-8")
    if not report.exists():
        return False, f"档位审计没产出报告（exit={r.returncode}）"
    d = json.loads(report.read_text(encoding="utf-8"))
    rows = d.get("rows", [])
    if not rows:
        return False, "没有相邻档位结果"
    bad = [x for x in rows if not x.get("ok")]
    detail = f"{len(rows) - len(bad)}/{len(rows)}档通过"
    if bad:
        x = bad[0]
        detail += f"；{x['upper']} vs {x['lower']} {x['wins']}-{x['losses']}-{x['draws']}"
    return not bad, detail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="all")
    ap.add_argument("--fuzz", type=int, default=0)
    ap.add_argument("--ranking", action="store_true", help="同时要求相邻anchor档位通过")
    ap.add_argument("--ranking-matches", type=int, default=12, help="每档请求场数；重复有效开局会跳过")
    ap.add_argument("--json")
    args = ap.parse_args()

    if args.game.strip().lower() == "all":
        games = harness.all_games()
    else:
        games = [harness.game_dir(g.strip()) for g in args.game.split(",") if g.strip()]

    checks = [("docs", check_docs),
              ("meta", check_meta),
              ("maps", check_maps),
              ("validate", lambda g: check_validate(g, args.fuzz)),
              ("validate4", check_validate4),
              ("pack", check_pack),
              ("leak", check_leak),
              ("package", check_package)]
    engineering_names = [name for name, _fn in checks]
    if args.ranking:
        checks.append(("ranking", lambda g: check_ranking(g, args.ranking_matches)))

    rows = []
    for g in games:
        print(f"\n=== {g.name} ===")
        row = {"game": g.name, "results": {}}
        for name, fn in checks:
            ok, detail = fn(g)
            row["results"][name] = {"ok": ok, "detail": detail}
            print(f"  {'PASS' if ok else 'FAIL'}  {name:<9} {detail}")
        row["engineering_ok"] = all(row["results"][name]["ok"] for name in engineering_names)
        row["ok"] = all(v["ok"] for v in row["results"].values())
        rows.append(row)

    print()
    heads = [n for n, _ in checks]
    width = max(len(r["game"]) for r in rows)
    print(f"{'game'.ljust(width)}  " + "  ".join(h.ljust(8) for h in heads) + "  overall")
    print("-" * (width + 2 + 10 * len(heads) + 8))
    for r in rows:
        cells = "  ".join(("PASS" if r["results"][h]["ok"] else "FAIL").ljust(8) for h in heads)
        print(f"{r['game'].ljust(width)}  {cells}  {'PASS' if r['ok'] else 'FAIL'}")

    passed = sum(1 for r in rows if r["ok"])
    print()
    print(f"{passed}/{len(rows)} 道题通过{'工程+排名验收' if args.ranking else '工程验收'}")

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"tool": "acceptance", "ok": passed == len(rows),
                                 "games": rows}, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
