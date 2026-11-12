#!/usr/bin/env python3
"""P01 v2 · 4-seat map/state/rotation smoke validation."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GAME = HERE.parent
REPO = GAME.parents[1]
sys.path.insert(0, str(REPO / "tools"))
from harness import Reporter  # noqa: E402

_R = Reporter()
check = _R.check


def _judge():
    """加载 2 席判官：先 judge2.py（包里叫这个名），再退回 judge.py。

    照 judge4.py:30-43 的回落抄 — 硬写 judge.py 的话，一旦在打包布局
    （包里只有 judge2.py）里跑，这里就直接找不到文件。
    """
    for name in ("judge2.py", "judge.py"):
        cand = GAME / "public" / "judge" / name
        if cand.is_file():
            spec = importlib.util.spec_from_file_location("p01_judge", cand)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m
    raise FileNotFoundError(f"judge2.py / judge.py 都不在 {GAME / 'public' / 'judge'}")


def _judge4():
    spec = importlib.util.spec_from_file_location("p01_judge4", GAME / "public/judge/judge4.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def state(engine, cmd: str) -> str:
    engine._write(cmd)
    line = engine._read()
    if line.startswith("ERR"):
        raise RuntimeError(line)
    return line


def parse(line: str):
    p = line.split()
    assert p[0] == "STATE"
    return {"round": int(p[1]), "over": int(p[4]), "winner": int(p[5]),
            "hp": [int(x) for x in p[6:10]], "hash": p[19],
            "worm": [tuple(int(x) for x in p[20 + i * 6:26 + i * 6]) for i in range(12)]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    args = ap.parse_args()
    j = _judge()
    j4 = _judge4()
    exe = j.build_engine(GAME)
    engine = j.Engine(exe)
    maps = sorted((GAME / "public/maps4").glob("*.txt")) + sorted(
        (GAME / "private/maps4_hidden").glob("*.txt"))
    check("有 4 席地图", bool(maps), f"{len(maps)} 张")
    try:
        for mp in maps:
            engine.load(mp, 1, strict=True, mask=15)
            st = parse(state(engine, "STATE"))
            check(f"{mp.stem} mask15 12虫", all(w[5] for w in st["worm"]))
            engine.load(mp, 1, strict=True, mask=7)
            st = parse(state(engine, "STATE"))
            check(f"{mp.stem} mask7 9虫", sum(w[5] for w in st["worm"]) == 9)
            engine.load(mp, 1, strict=True, mask=5)
            st = parse(state(engine, "STATE"))
            check(f"{mp.stem} mask5 6虫", sum(w[5] for w in st["worm"]) == 6)
        # Determinism of a 4-seat load.
        engine.load(maps[0], 7, strict=True, mask=15)
        h1 = parse(state(engine, "STATE"))["hash"]
        engine.load(maps[0], 7, strict=True, mask=15)
        h2 = parse(state(engine, "STATE"))["hash"]
        check("4席 LOAD 确定性", h1 == h2, f"{h1} vs {h2}")
    finally:
        engine.close()

    # v2.1.0 ladder smoke: TIDE-HARD (public baseline) + three hidden challenges.
    #
    # 不硬依赖 build/：.gitignore 忽略了整棵 build/，干净 clone 上它永远是空的，
    # 老写法会直接抛 missing ladder bot，把整份验证变成"没跑成"。
    # 先 build/（本机编译产物），再退到随包发布的 public/baselines/bin 与
    # public/challenges/bin（Windows 无后缀目录 / Linux 的 linux/ 子目录）；
    # 两处都没有就打印 SKIP 跳过这一条，不 raise —— 它只是冒烟，不是正确性依据。
    def ladder_bot(n: str) -> Path:
        bins = GAME / "public" / "baselines" / "bin"
        chal = GAME / "public" / "challenges" / "bin"
        for cand in (GAME / "build" / f"{n}.exe", GAME / "build" / n,
                     bins / f"{n}.exe", bins / "linux" / n,
                     chal / f"{n}.exe", chal / "linux" / n):
            if cand.is_file():
                return cand
        raise FileNotFoundError(n)

    names = ["tide_hard", "chimera", "pseudo", "typhon"]
    try:
        bots = [ladder_bot(n) for n in names]
    except FileNotFoundError as exc:
        print(f"  SKIP  4席真实对局：找不到 ladder bot {exc.args[0]}"
              "（build/ 与 public/baselines/bin、public/challenges/bin 都没有）")
    else:
        r = j4.play_one(j, exe, maps[0], 1, bots, (0, 1, 2, 3), (0, 1, 2, 3), 0.5)
        check("4席真实对局 (TIDE/CHIMERA/PSEUDO/TYPHON)",
              bool(r.get("ok")) and r.get("faults", 1) == 0, str(r))

    # ---- 公平性地基（v2.2.0）：地图严格镜像 + 赛制邻接平衡 + 每个排列换 seed
    swap = {"0": "3", "3": "0", "1": "2", "2": "1"}
    asym = []
    for mp in maps:
        rows = mp.read_text(encoding="utf-8").splitlines()
        if any(swap.get(c, c) != row[95 - x]
               for row in rows for x, c in enumerate(row)):
            asym.append(mp.stem)
    check("全部 4 席地图严格镜像对称", not asym, str(asym[:3]))

    plan = j4.schedule((0, 1, 2, 3), 24, 2)
    check("赛制 = 24 排列 × 2 seed", len(plan) == 48, f"{len(plan)}")

    # ---- 并发补偿（v2.4.3）：预算 = 50 × s × c(jobs)，c 必须单调且有封顶
    c1, c20, c48 = (j4.concurrency_factor(x) for x in (1, 20, 48))
    check("并发因子 jobs=1 不加余量", c1 == 1.0, f"{c1}")
    check("并发因子单调递增", c1 < c20 <= c48, f"{c1:.2f} / {c20:.2f} / {c48:.2f}")
    check("并发因子有封顶", c48 == j4.concurrency_factor(200), f"{c48:.2f}")
    check("并发因子对齐实测（jobs=20 落在 1.4–1.7 倍）", 1.4 <= c20 <= 1.7, f"{c20:.2f}")
    # 第三个字段是绝对 seed 下标：48 局必须落在 48 个不同的 seed 上（driver 也只许用它）。
    abs_idx = {si for _, _, si in plan}
    check("每个排列换 seed（48 局 48 个 seed）",
          len({p for p, _, _ in plan}) == 24 and len(abs_idx) == 48
          and abs_idx == set(range(len(plan))),
          f"perms={len({p for p, _, _ in plan})} seeds={len(abs_idx)} "
          f"span={min(abs_idx) if abs_idx else '-'}..{max(abs_idx) if abs_idx else '-'}")

    counts: dict[int, dict[int, int]] = {b: {} for b in range(4)}
    for perm, _, _ in plan:
        for i, b in enumerate(perm):
            for side in ((i + 1) % 4, (i + 3) % 4):
                o = perm[side]
                counts[b][o] = counts[b].get(o, 0) + 1
    spread = max(max(v.values()) - min(v.values()) for v in counts.values())
    check("24 排列的邻居分布均衡", spread == 0, f"spread={spread} counts={counts}")

    # ---- 类型轴（v2.4.3：openness 已移除，只剩 cover × elevation = 9 种）
    # 108 张 = 每种 12 张。★ 为什么删 openness：实测它对专精探针的胜率影响只有 1.4pp
    # （cover 27.0 / elevation 25.3）—— 中央障碍挡的是地面行进，挡不住主流的高抛打法。
    # 它占了三分之一的组合却测不出效应，删掉后 27 类降到 9 类，图集反而更好读。
    import json as _json
    pub_man = GAME / "public" / "maps4" / "manifest.json"
    hid_man = GAME / "private" / "maps4_hidden" / "manifest.json"
    if pub_man.is_file() and hid_man.is_file():
        pm = _json.loads(pub_man.read_text(encoding="utf-8"))
        hm = _json.loads(hid_man.read_text(encoding="utf-8"))
        pt = {m["type_code"] for m in pm["maps"]}
        ht = {m["type_code"] for m in hm["maps"]}
        check("公开图类型轴 = 9 种组合", len(pt) == 9, f"{len(pt)}")
        check("隐藏图类型轴 = 9 种组合", len(ht) == 9, f"{len(ht)}")
        check("公开/隐藏类型集合一致", pt == ht, f"only_pub={sorted(pt - ht)[:3]}")
        import collections as _c
        pc = _c.Counter(m["type_code"] for m in pm["maps"])
        hc = _c.Counter(m["type_code"] for m in hm["maps"])
        bad = [t for t in pc if pc[t] != 12 or hc.get(t, 0) != 12]
        check("每种类型公开 12 张 / 隐藏 12 张", not bad, str(bad[:3]))
        # 两个在用的轴都真的分了三档；openness 已移除，它的断言随之删掉
        for axis, n in (("cover", 3), ("elevation", 3)):
            vals = {m["type"][axis] for m in pm["maps"]}
            check(f"类型轴 {axis} 有三档", len(vals) == n, str(sorted(vals)))
        # openness 必须**恒为 open**：删轴之后如果还有非 open 的值，说明生成器没改干净
        op_vals = {m["type"]["openness"] for m in pm["maps"]}
        check("openness 已移除（恒为 open）", op_vals == {"open"}, str(sorted(op_vals)))
        # 下面三条是回归保护：图集"108 张互不相同、公开隐藏不撞图"这件事
        # 以前没有任何断言，重生地图时最容易悄悄破掉。
        # ★ 旧图（v2.4.1）上这三条是 FAIL 的（隐藏 108 张只有 97 个不同 hash，
        #   且与公开图集撞图）；图重生之后才应该全绿 —— 别为了让它变绿去改断言。
        import hashlib as _h

        def shas(dirpath: Path, recs: list[dict]) -> dict[str, str]:
            """{文件名: sha256}，只收磁盘上真的存在的文件。"""
            out: dict[str, str] = {}
            for r in recs:
                p = dirpath / r["file"]
                if p.is_file():
                    out[r["file"]] = _h.sha256(p.read_bytes()).hexdigest()
            return out

        pub_sha = shas(GAME / "public" / "maps4", pm["maps"])
        hid_sha = shas(GAME / "private" / "maps4_hidden", hm["maps"])
        for label, recs, sh in (("公开", pm["maps"], pub_sha),
                                ("隐藏", hm["maps"], hid_sha)):
            # 同 split 内零重复：108 个文件 → 108 个不同 hash
            check(f"{label}图集 {len(recs)} 张 sha256 互不相同",
                  len(sh) == len(recs) and len(set(sh.values())) == len(recs),
                  f"files={len(sh)}/{len(recs)} unique={len(set(sh.values()))}")
            per: dict[str, set[str]] = {}
            for r in recs:
                if r["file"] in sh:
                    per.setdefault(r["type_code"], set()).add(sh[r["file"]])
            dup_types = [t for t, s in per.items() if len(s) != 12]
            check(f"{label}每种 type_code 的 12 张互不相同",
                  not dup_types, str(dup_types[:3]))
        overlap = set(pub_sha.values()) & set(hid_sha.values())
        check("公开/隐藏图集零重叠", not overlap, f"{len(overlap)} 张重复")
    elif pub_man.is_file() and not (GAME / "private").exists():
        # 私有内容没挂进来：只核对公开图集，隐藏图相关断言跳过
        import collections as _c
        import hashlib as _h
        pm = _json.loads(pub_man.read_text(encoding="utf-8"))
        pt = {m["type_code"] for m in pm["maps"]}
        check("公开图类型轴 = 9 种组合", len(pt) == 9, f"{len(pt)}")
        pc = _c.Counter(m["type_code"] for m in pm["maps"])
        bad = [t for t in pc if pc[t] != 12]
        check("每种类型公开 12 张", not bad, str(bad[:3]))
        for axis, n in (("cover", 3), ("elevation", 3)):
            vals = {m["type"][axis] for m in pm["maps"]}
            check(f"类型轴 {axis} 有三档", len(vals) == n, str(sorted(vals)))
        op_vals = {m["type"]["openness"] for m in pm["maps"]}
        check("openness 已移除（恒为 open）", op_vals == {"open"}, str(sorted(op_vals)))
        sh = {r["file"]: _h.sha256((GAME / "public" / "maps4" / r["file"]).read_bytes()).hexdigest()
              for r in pm["maps"] if (GAME / "public" / "maps4" / r["file"]).is_file()}
        check(f"公开图集 {len(pm['maps'])} 张 sha256 互不相同",
              len(sh) == len(pm["maps"]) and len(set(sh.values())) == len(pm["maps"]),
              f"files={len(sh)}/{len(pm['maps'])} unique={len(set(sh.values()))}")
        print("note: private/ 未挂载，隐藏图相关断言已跳过")
    else:
        check("类型轴 manifest 存在", False, "缺 manifest.json")

    print()
    print(_R.summary("p01-ballistic"))
    _R.emit(args.json, "p01-ballistic")
    return 0 if _R.ok else 1


if __name__ == "__main__":
    sys.exit(main())