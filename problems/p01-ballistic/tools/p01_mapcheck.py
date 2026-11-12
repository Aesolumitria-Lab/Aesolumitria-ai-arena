#!/usr/bin/env python3
"""P01 4 席地图集的跨平台 hash 金标（v2.4.2 新增）。

为什么需要它（2026-11-10）：v2.4.2 重生了 216 张 4 席地图，而 `tools/crosscheck.py`
的 p01 用例只覆盖 `public/maps/`（2 席遗留图），**完全没碰 maps4**。
地图内容会进 state_hash（recompute_hash 把整张 grid 混进去），所以「地图换了」
和「hash 变了」是一回事 —— 换了图就必须重跑一次跨平台对账才算数。

做法尽量小：每张图只做 `LOAD <file> 1 15` + `STATE`，取开局 hash。
它验证的是 fopen/读行/字符解析/水线与网格哈希这条链路在两个平台上逐字节一致，
而不是整局对局（整局另有 docs/crossplatform-and-host-speed.md §3 的 144 局对账）。

    python problems/p01-ballistic/tools/p01_mapcheck.py --json dist/_map_win.json
    python3 problems/p01-ballistic/tools/p01_mapcheck.py --json dist/_map_linux.json
    python problems/p01-ballistic/tools/p01_mapcheck.py --compare dist/_map_win.json dist/_map_linux.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
GAME = REPO / "problems" / "p01-ballistic"
HEX16 = re.compile(r"\b[0-9a-f]{16}\b")


def engine_path() -> Path:
    for name in ("engine.exe", "engine"):
        p = GAME / "build" / name
        if p.is_file():
            return p
    raise SystemExit("找不到 build/engine(.exe)：先编译一次")


def collect() -> dict:
    eng = engine_path()
    out = {}
    for split in ("public/maps4", "private/maps4_hidden"):
        d = GAME / split
        for mp in sorted(d.glob("*.txt")):
            proc = subprocess.run(
                [str(eng)],
                input="LOAD %s 1 15\nSTATE\nGRID\nQUIT\n" % mp.as_posix(),
                capture_output=True, text=True, timeout=30, cwd=str(GAME))
            if proc.returncode != 0 or not proc.stdout.startswith("OK "):
                raise SystemExit("engine failed on %s: %s" % (mp.name, proc.stdout[:200]))
            rows = [ln[2:] for ln in proc.stdout.splitlines() if ln.startswith("G ")]
            if len(rows) != 54:
                raise SystemExit("GRID rows %d for %s" % (len(rows), mp.name))
            hashes = HEX16.findall(proc.stdout.split("GRID")[0])
            if not hashes:
                raise SystemExit("no hash for %s" % mp.name)
            out["%s/%s" % (split, mp.name)] = {"hash": hashes[-1],
                                               "grid_sha": _sha("\n".join(rows))}
    return out


def _sha(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    ap.add_argument("--compare", nargs=2, metavar=("A.json", "B.json"))
    args = ap.parse_args()
    if args.compare:
        a = json.loads(Path(args.compare[0]).read_text(encoding="utf-8"))
        b = json.loads(Path(args.compare[1]).read_text(encoding="utf-8"))
        if set(a) != set(b):
            print("FAIL: case set differs (%d vs %d)" % (len(a), len(b)))
            return 1
        bad = [k for k in a if a[k]["hash"] != b[k]["hash"]]
        badg = [k for k in a if a[k]["grid_sha"] != b[k]["grid_sha"]]
        if bad or badg:
            print("FAIL: %d hash mismatches, %d grid mismatches" % (len(bad), len(badg)))
            for k in (bad + badg)[:5]:
                print("  %s\n    A %s\n    B %s" % (k, a[k], b[k]))
            return 1
        print("PASS  %d maps, spawn hash + grid sha identical on both platforms" % len(a))
        return 0

    data = collect()
    print("%d maps, %d distinct spawn hashes" % (len(data), len({v["hash"] for v in data.values()})))
    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print("wrote %s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
