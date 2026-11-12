#!/usr/bin/env python3
"""把 meta.json 里的 rules_hash / engine_hash / arena_hash / 地图数量对齐到当前文件。

    python tools/refresh_meta.py                 # 全部题
    python tools/refresh_meta.py --game p06      # 单题
    python tools/refresh_meta.py --dry-run       # 只报差异，不写

存在的理由：改了 RULES.md、engine.cpp 或加了地图却忘了更新 meta，是题库里最常见的隐性漂移。
`tools/acceptance.py` 会把它判成 FAIL，这个工具用来修。它只改可以从文件系统确定的字段，
version/status 之类的语义字段必须人来决定。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
import harness  # noqa: E402

# 一个 hash 可以有多个候选文件：P01 用 arena4.hpp，P06/P07/P08 用 arena.hpp。
# 谁存在就哈希谁；都不存在就跳过（字段保持原样，不写 null）。
FIELDS = [("rules_hash", ("RULES.md",)),
          ("engine_hash", ("public/engine/engine.cpp",)),
          # arena_hash：选手真正逐行依赖的是这份公开头文件，但旧版 pack.py 读
          # meta.get("arena_hash") 永远是 null —— 包指纹里缺了最该绑的那一份。
          ("arena_hash", ("public/arena4.hpp", "public/arena.hpp"))]
MAP_FIELDS = [("public_maps", "public/maps"),
              ("hidden_maps", "private/maps_hidden"),
              ("public_maps4", "public/maps4"),
              ("hidden_maps4", "private/maps4_hidden")]


def short_hash(p: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="all")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    games = harness.all_games() if args.game.strip().lower() == "all" \
        else [harness.game_dir(g.strip()) for g in args.game.split(",") if g.strip()]

    changed = 0
    for g in games:
        mp = g / "meta.json"
        if not mp.exists():
            continue
        m = json.loads(mp.read_text(encoding="utf-8"))
        diff = []
        for key, rels in FIELDS:
            f = next((g / rel for rel in rels if (g / rel).exists()), None)
            if f is None:
                continue
            want = short_hash(f)
            if m.get(key) != want:
                diff.append((key, m.get(key), want))
                m[key] = want
        for key, rel in MAP_FIELDS:
            d = g / rel
            if not d.is_dir():
                continue
            want = len(list(d.glob("*.txt")))
            if m.get(key) != want:
                diff.append((key, m.get(key), want))
                m[key] = want
        if not diff:
            print(f"{g.name}: 一致")
            continue
        for key, old, new in diff:
            print(f"{g.name}: {key} {old} -> {new}")
        changed += 1
        if not args.dry_run:
            mp.write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.dry_run:
        print(f"\n{changed} 道题需要刷新（未写入）")
    else:
        print(f"\n{changed} 道题已刷新")
    return 0


if __name__ == "__main__":
    sys.exit(main())
