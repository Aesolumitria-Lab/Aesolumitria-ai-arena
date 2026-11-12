#!/usr/bin/env python3
"""校验选手包：manifest.json 里的每个文件，sha256 是否对得上。

    python tools/verify_package.py --path dist/p07-maze-hunt.zip
    python tools/verify_package.py --path 某个解压后的选手包目录/

存在的理由（借自 `meow-ai-arena/workspace/MANIFEST.json` 的做法）：
选手拿到的包必须能自证完整。否则"我本地跑出来和你线上不一样"永远说不清——
可能是规则版本不同、引擎被改过，也可能是包在传输中被截断。

校验三件事：
1. manifest 里列的每个文件都在，且 sha256 与记录一致；
2. 包里没有 manifest 之外的多余文件（防止把 private/ 之类的东西带进来）；
3. manifest 的 engine_hash / rules_hash 与该题当前 `meta.json` 一致——
   对不上说明**这份包是旧版引擎打的**，回放不能拿来对账。

第 3 条是有意设计成"会失败"的：改了引擎就该重打包，不该让旧包悄悄流通。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
import harness  # noqa: E402


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_entries(path: Path) -> tuple[dict[str, bytes], dict]:
    """返回 ({包内路径: 内容}, manifest)。支持 zip 与解压目录。"""
    if path.is_dir():
        files = {p.relative_to(path).as_posix(): p.read_bytes()
                 for p in sorted(path.rglob("*")) if p.is_file()}
    else:
        with zipfile.ZipFile(path) as zf:
            files = {n: zf.read(n) for n in zf.namelist() if not n.endswith("/")}
    if "manifest.json" not in files:
        raise SystemExit("包里没有 manifest.json，无法校验")
    manifest = json.loads(files.pop("manifest.json").decode("utf-8"))
    return files, manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", required=True)
    ap.add_argument("--json")
    args = ap.parse_args()

    path = Path(args.path).resolve()
    if not path.exists():
        print(f"找不到 {path}", file=sys.stderr)
        return 2

    files, manifest = load_entries(path)
    fails: list[str] = []
    warns: list[str] = []

    listed = {e["path"]: e for e in manifest.get("files", [])}

    for rel, entry in listed.items():
        if rel not in files:
            fails.append(f"缺文件：{rel}")
            continue
        got = digest(files[rel])
        if got != entry["sha256"]:
            fails.append(f"内容不一致：{rel}（manifest {entry['sha256'][:12]} vs 实际 {got[:12]}）")
        if len(files[rel]) != entry.get("size", len(files[rel])):
            fails.append(f"大小不一致：{rel}")

    extra = sorted(set(files) - set(listed))
    for rel in extra:
        fails.append(f"多余文件：{rel}")

    # 版本对账：包必须由当前引擎打出
    game = manifest.get("game", "")
    try:
        cur = json.loads((harness.game_dir(game) / "meta.json").read_text(encoding="utf-8"))
    except SystemExit:
        warns.append(f"找不到题目 {game}，跳过版本对账")
        cur = {}
    for key in ("rules_hash", "engine_hash"):
        if cur and manifest.get(key) != cur.get(key):
            fails.append(f"{key} 过期：包内 {manifest.get(key)} vs 当前 {cur.get(key)}"
                         "（这份包是旧版打的，需重新打包）")

    for w in warns:
        print(f"[WARN] {w}")
    for f in fails:
        print(f"[FAIL] {f}")

    summary = f"{len(listed)} 文件校验，{len(fails)} fail"
    if not fails:
        print(f"PASS  {summary}，{manifest.get('game')} {manifest.get('version')}")
    else:
        print(f"\n{summary}")

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"tool": "verify_package", "ok": not fails,
                                 "game": game, "version": manifest.get("version"),
                                 "files": len(listed), "fails": fails, "warns": warns},
                                ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
