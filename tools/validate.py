#!/usr/bin/env python3
"""题库统一验证入口。

    python tools/validate.py                       # 跑所有题（默认 fuzz=0）
    python tools/validate.py --game p02            # 只跑一道
    python tools/validate.py --game p02,p08 --fuzz 100
    python tools/validate.py --json dist/validate.json

规则测试必然按题写（每题机制不同），所以真正的测试代码在各题的
`tests/validate.py`。这个入口只负责：找到它们、统一跑、统一汇报。

输出契约见 `tools/harness.py`。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="all",
                    help="题号或目录名，逗号分隔；all = 所有已实现的题")
    ap.add_argument("--fuzz", type=int, default=0, help="每题额外的 fuzz 局数")
    ap.add_argument("--json", help="汇总报告输出路径")
    ap.add_argument("--update-golden", action="store_true",
                    help="重写 golden 基线（仅单题、仅该题支持时有效）")
    args = ap.parse_args()

    if args.game.strip().lower() == "all":
        games = harness.all_games()
    else:
        games = [harness.game_dir(g.strip()) for g in args.game.split(",") if g.strip()]

    if not games:
        print("没有可验证的题目")
        return 1

    rows = []
    all_ok = True
    for g in games:
        print(f"\n=== {g.name} ===")
        extra = ["--update-golden"] if args.update_golden else []
        ok, passed, total = harness.run_game_validate(g, args.fuzz, None, extra)
        rows.append((g.name, ok, passed, total))
        all_ok = all_ok and ok

    print()
    width = max(len(n) for n, *_ in rows)
    print(f"{'game'.ljust(width)}  result      checks")
    print(f"{'-' * width}  ----------  ------")
    for name, ok, passed, total in rows:
        mark = "PASS" if ok else "FAIL"
        print(f"{name.ljust(width)}  {mark:<10}  {passed}/{total}")
    print()
    print(f"{sum(1 for _, ok, _, _ in rows if ok)}/{len(rows)} 道题通过验证")

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        import json
        p.write_text(json.dumps({
            "tool": "validate", "ok": all_ok,
            "games": [{"game": n, "ok": ok, "passed": pa, "total": to}
                      for n, ok, pa, to in rows],
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
