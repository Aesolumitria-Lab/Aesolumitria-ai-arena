#!/usr/bin/env python3
"""Bot registry for P01 v2 submissions.

    entries/<id>/meta.json + bot.cpp
    id = model@harness#run        e.g. some-model@some-harness#1

The registry is the stable identity layer for official matches.  An id is a
lower-case ASCII slug, ``model_key`` is the grouping key, ``display_name`` is
for humans, and ``run`` starts at 1.  Changing code must bump ``run``; old ids
are never reused.

CLI (standard library only):

    python tools/bot_registry.py validate <entries_dir> [--json [PATH]] [--check-build]
    python tools/bot_registry.py list <entries_dir> [--json]
    python tools/bot_registry.py resolve <entries_dir> <id> [--json]

``judge4.py --entries`` imports :func:`resolve_entry` from this module.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCHEMA = 1
MAX_ID_LEN = 80
MAX_DISPLAY_LEN = 120
REQUIRED_META = (
    "schema", "id", "display_name", "model", "model_key", "harness",
    "run", "entry", "language", "code_sha256",
)
OPTIONAL_META = ("build", "notes")

# model / harness / model_key are ASCII slugs; a single leading/trailing
# separator and empty segments (a..b, a-, -a) are rejected.
SLUG = r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
ID_RE = re.compile(rf"^{SLUG}@{SLUG}#[1-9][0-9]*$")
SLUG_RE = re.compile(rf"^{SLUG}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CPP_LANGUAGES = {"cpp", "c++", "cxx", "c++17"}


class BotRegistryError(RuntimeError):
    """Registry integrity or resolution error."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _has_control(text: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in text)


def parse_bot_id(bot_id: str) -> dict:
    """Parse ``model@harness#run`` or raise :class:`BotRegistryError`."""
    if not isinstance(bot_id, str):
        raise BotRegistryError(f"bot id 必须是字符串：{bot_id!r}")
    if not bot_id:
        raise BotRegistryError("bot id 不能为空")
    if len(bot_id) > MAX_ID_LEN:
        raise BotRegistryError(f"bot id 超长（{len(bot_id)} > {MAX_ID_LEN}）：{bot_id!r}")
    if _has_control(bot_id):
        raise BotRegistryError(f"bot id 含控制字符：{bot_id!r}")
    if "/" in bot_id or "\\" in bot_id:
        raise BotRegistryError(f"bot id 含路径分隔符：{bot_id!r}")
    if not ID_RE.fullmatch(bot_id):
        raise BotRegistryError(
            f"bot id 语法非法：{bot_id!r}（要求 model@harness#run，"
            "小写 ASCII slug，run 从 1 开始）"
        )
    left, run_text = bot_id.rsplit("#", 1)
    model, harness = left.split("@", 1)
    return {"model": model, "harness": harness, "run": int(run_text)}


def is_valid_id(bot_id: str) -> bool:
    try:
        parse_bot_id(bot_id)
        return True
    except BotRegistryError:
        return False


def _slug_problem(value, name: str) -> str | None:
    if not isinstance(value, str) or not value:
        return f"{name} 必须是非空字符串"
    if len(value) > MAX_ID_LEN:
        return f"{name} 超长：{len(value)} > {MAX_ID_LEN}"
    if _has_control(value) or "/" in value or "\\" in value:
        return f"{name} 含控制字符或路径分隔符：{value!r}"
    if not SLUG_RE.fullmatch(value):
        return f"{name} 不是小写 ASCII slug：{value!r}"
    return None


def _entry_rel_problem(raw) -> str | None:
    if not isinstance(raw, str) or not raw:
        return "entry 必须是非空相对路径"
    if _has_control(raw):
        return f"entry 含控制字符：{raw!r}"
    if raw.startswith(("/", "\\")) or ":" in raw:
        return f"entry 必须是相对路径（禁止绝对路径/盘符）：{raw!r}"
    parts = re.split(r"[\\/]+", raw)
    if any(part in ("", ".", "..") for part in parts):
        return f"entry 含空段/./..：{raw!r}"
    return None


def _subdirs(entries_dir: Path) -> list[Path]:
    if not entries_dir.is_dir():
        return []
    return sorted((d for d in entries_dir.iterdir()
                   if d.is_dir() and not d.name.startswith(".")),
                  key=lambda d: d.name)


def _load_meta(entry_dir: Path) -> tuple[dict | None, list[str]]:
    errors: list[str] = []
    meta_path = entry_dir / "meta.json"
    if not meta_path.is_file():
        return None, [f"缺少 meta.json：{meta_path}"]
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - report, do not crash
        return None, [f"meta.json 读不出来：{exc}"]
    if not isinstance(meta, dict):
        return None, ["meta.json 顶层必须是对象"]
    return meta, errors


def _validate_entry(entry_dir: Path, meta: dict, *,
                    check_build: bool = False,
                    registry_root: Path | None = None) -> dict:
    """Validate one ``entries/<dir>`` and return a normalized record."""
    errors: list[str] = []
    warnings: list[str] = []

    for key in REQUIRED_META:
        if key not in meta:
            errors.append(f"meta 缺少字段：{key}")

    schema = meta.get("schema")
    if schema != SCHEMA:
        errors.append(f"schema 必须是 {SCHEMA}（实际 {schema!r}）")

    bot_id = meta.get("id")
    parsed = None
    if isinstance(bot_id, str):
        try:
            parsed = parse_bot_id(bot_id)
        except BotRegistryError as exc:
            errors.append(str(exc))
    else:
        errors.append(f"id 必须是字符串：{bot_id!r}")

    display = meta.get("display_name")
    if not isinstance(display, str) or not display.strip():
        errors.append("display_name 必须是非空字符串")
    elif len(display) > MAX_DISPLAY_LEN:
        errors.append(f"display_name 超长：{len(display)} > {MAX_DISPLAY_LEN}")
    elif _has_control(display):
        errors.append("display_name 含控制字符")

    model = meta.get("model")
    if (problem := _slug_problem(model, "model")) is not None:
        errors.append(problem)
    harness = meta.get("harness")
    if (problem := _slug_problem(harness, "harness")) is not None:
        errors.append(problem)
    model_key = meta.get("model_key")
    if (problem := _slug_problem(model_key, "model_key")) is not None:
        errors.append(problem)

    run = meta.get("run")
    if isinstance(run, bool) or not isinstance(run, int) or run < 1:
        errors.append(f"run 必须是 >=1 的整数：{run!r}")

    if parsed is not None:
        expected = f"{model}@{harness}#{run}"
        if bot_id != expected:
            errors.append(f"id 与 model/harness/run 不一致：{bot_id!r} != {expected!r}")
        elif isinstance(run, int) and not isinstance(run, bool) and run != parsed["run"]:
            errors.append(f"id 的 run={parsed['run']} 与 meta.run={run} 不一致")

    language = meta.get("language")
    if not isinstance(language, str) or not language.strip():
        errors.append("language 必须是非空字符串")
    elif language.strip().lower() not in CPP_LANGUAGES:
        errors.append(
            f"language={language!r} 不受支持（可选：{', '.join(sorted(CPP_LANGUAGES))}）"
        )

    entry_abs: Path | None = None
    entry_rel = ""
    raw_entry = meta.get("entry")
    if (problem := _entry_rel_problem(raw_entry)) is not None:
        errors.append(problem)
    else:
        root = entry_dir.resolve()
        candidate = (entry_dir / raw_entry).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            errors.append(f"entry 逃出提交目录：{raw_entry!r}")
        else:
            if not candidate.is_file():
                errors.append(f"entry 文件不存在：{raw_entry!r}")
            else:
                entry_abs = candidate
                entry_rel = candidate.relative_to(root).as_posix()

    actual_sha = ""
    code_sha = meta.get("code_sha256")
    if not isinstance(code_sha, str) or not SHA256_RE.fullmatch(code_sha):
        errors.append("code_sha256 必须是 64 位小写十六进制")
    elif entry_abs is not None:
        actual_sha = sha256_file(entry_abs)
        if actual_sha != code_sha:
            errors.append(
                f"code_sha256 不匹配：meta={code_sha} 实际={actual_sha}"
            )

    build_opt = meta.get("build")
    if build_opt is not None and not isinstance(build_opt, dict):
        errors.append("可选 build 字段必须是对象")
    notes = meta.get("notes")
    if notes is not None and not isinstance(notes, str):
        errors.append("可选 notes 字段必须是字符串")

    if isinstance(bot_id, str) and entry_dir.name != bot_id:
        warnings.append(
            f"目录名 {entry_dir.name!r} 与 id {bot_id!r} 不同（建议 entries/<id>/）"
        )

    record = {
        "dir": entry_dir.name,
        "id": bot_id if isinstance(bot_id, str) else "",
        "display_name": display if isinstance(display, str) else "",
        "model": model if isinstance(model, str) else "",
        "model_key": model_key if isinstance(model_key, str) else "",
        "harness": harness if isinstance(harness, str) else "",
        "run": run if isinstance(run, int) and not isinstance(run, bool) else None,
        "entry": str(entry_abs) if entry_abs is not None else "",
        "entry_rel": entry_rel,
        "language": language if isinstance(language, str) else "",
        "code_sha256": code_sha if isinstance(code_sha, str) else "",
        "sha256_actual": actual_sha,
        "meta": meta,
        "errors": errors,
        "warnings": warnings,
        "ok": not errors,
    }

    if check_build and not errors:
        build_error = _check_build(record, entry_dir, registry_root or entry_dir.parent)
        if build_error:
            record["errors"].append(build_error)
            record["ok"] = False
        else:
            record["build_ok"] = True
    return record


def _check_build(record: dict, entry_dir: Path, registry_root: Path) -> str | None:
    """Compile an entry once into a private temp dir.  Returns an error string."""
    gxx = shutil.which("g++")
    if gxx is None:
        return "--check-build 需要 g++，但 PATH 中找不到"
    cxx = [gxx, "-O2", "-std=c++17"]
    if os.name == "nt":
        cxx.append("-static")
    with tempfile.TemporaryDirectory(prefix="bot-registry-", dir=None) as td:
        out = Path(td) / ("bot.exe" if os.name == "nt" else "bot")
        cmd = [*cxx, "-o", str(out), record["entry"],
               "-I", str(entry_dir), "-I", str(registry_root)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            return f"{record['id']}: 编译超时（180s）"
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-8:]
            return f"{record['id']}: 编译失败\n" + "\n".join(tail)
    return None


def validate_entries(entries_dir: str | Path, *,
                     check_build: bool = False) -> dict:
    """Validate every child directory of ``entries_dir``.

    Returns a JSON-serializable report.  ``ok`` is false on any error.  The
    report never raises for entry-level problems; only a missing registry root
    is reported in ``errors`` as well.
    """
    root = Path(entries_dir)
    report = {
        "tool": "bot_registry",
        "schema": SCHEMA,
        "command": "validate",
        "entries_dir": str(root),
        "ok": False,
        "count": 0,
        "entries": [],
        "errors": [],
    }
    if not root.is_dir():
        report["errors"].append({"what": "entries_dir", "detail": f"目录不存在：{root}"})
        return report

    dirs = _subdirs(root)
    if not dirs:
        report["errors"].append({"what": "entries_dir", "detail": "没有任何 entry 子目录"})
        return report

    records: list[dict] = []
    for entry_dir in dirs:
        meta, load_errors = _load_meta(entry_dir)
        if meta is None:
            records.append({
                "dir": entry_dir.name, "id": "", "display_name": "", "model": "",
                "model_key": "", "harness": "", "run": None, "entry": "",
                "entry_rel": "", "language": "", "code_sha256": "",
                "sha256_actual": "", "meta": {}, "errors": load_errors,
                "warnings": [], "ok": False,
            })
            continue
        records.append(_validate_entry(entry_dir, meta, check_build=check_build,
                                       registry_root=root))

    by_id: dict[str, list[str]] = {}
    by_entry: dict[str, list[str]] = {}
    for rec in records:
        if rec["id"]:
            by_id.setdefault(rec["id"], []).append(rec["dir"])
        if rec["entry"]:
            by_entry.setdefault(rec["entry"], []).append(rec["dir"])
    for bot_id, dirs_for_id in by_id.items():
        if len(dirs_for_id) > 1:
            report["errors"].append({
                "what": "duplicate_id", "id": bot_id, "dirs": dirs_for_id,
                "detail": f"ID 重复：{bot_id} -> {dirs_for_id}",
            })
    for entry_path, dirs_for_entry in by_entry.items():
        if len(dirs_for_entry) > 1:
            report["errors"].append({
                "what": "duplicate_entry", "entry": entry_path,
                "dirs": dirs_for_entry,
                "detail": f"entry 文件被多个提交共用：{entry_path} -> {dirs_for_entry}",
            })

    report["entries"] = records
    report["count"] = len(records)
    report["ok"] = not report["errors"] and all(r["ok"] for r in records)
    return report


def list_entries(entries_dir: str | Path) -> dict:
    """Return the same report shape as :func:`validate_entries` (no build)."""
    report = validate_entries(entries_dir, check_build=False)
    report["command"] = "list"
    return report


def _find_entry_dirs(entries_dir: Path, bot_id: str) -> list[Path]:
    hits: list[Path] = []
    for entry_dir in _subdirs(entries_dir):
        meta, _ = _load_meta(entry_dir)
        if meta is not None and meta.get("id") == bot_id:
            hits.append(entry_dir)
    return hits


def resolve_entry(entries_dir: str | Path, bot_id: str) -> dict:
    """Resolve one registry id to a validated entry record.

    Raises :class:`BotRegistryError` for an invalid id, a missing/duplicate id,
    or a broken entry (missing file, hash mismatch, path escape, ...).
    """
    parse_bot_id(bot_id)
    root = Path(entries_dir)
    if not root.is_dir():
        raise BotRegistryError(f"entries 目录不存在：{root}")
    hits = _find_entry_dirs(root, bot_id)
    if not hits:
        raise BotRegistryError(f"registry 中找不到 bot id：{bot_id}")
    if len(hits) > 1:
        raise BotRegistryError(
            f"registry 中 bot id 重复：{bot_id} -> {[h.name for h in hits]}"
        )
    meta, load_errors = _load_meta(hits[0])
    if meta is None:
        raise BotRegistryError("; ".join(load_errors))
    record = _validate_entry(hits[0], meta, check_build=False, registry_root=root)
    if not record["ok"]:
        raise BotRegistryError(
            f"bot {bot_id} 校验失败：" + "; ".join(record["errors"])
        )
    return record


# Backwards-friendly alias for callers that expect a short name.
resolve = resolve_entry


def _emit_json(data: dict, target: str | None) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if target in (None, "-"):
        sys.stdout.write(text)
    else:
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _print_validate(report: dict) -> None:
    print(f"{report['entries_dir']}: {report['count']} entries, "
          f"{'OK' if report['ok'] else 'FAIL'}")
    for rec in report["entries"]:
        status = "ok  " if rec["ok"] else "FAIL"
        print(f"  {status} {rec['id'] or rec['dir']:<32} "
              f"{rec['display_name']:<20} {rec['entry_rel'] or '-'}")
        for warning in rec["warnings"]:
            print(f"       WARN {warning}")
        for error in rec["errors"]:
            print(f"       ERR  {error}")
    for error in report["errors"]:
        print(f"  ERR  {error['detail']}")


def _print_list(report: dict) -> None:
    print("id\tdisplay_name\tmodel\tharness\tsha256\tentry")
    for rec in report["entries"]:
        sha = rec["code_sha256"][:12] if rec["code_sha256"] else "-"
        print(f"{rec['id'] or rec['dir']}\t{rec['display_name'] or '-'}\t"
              f"{rec['model'] or '-'}\t{rec['harness'] or '-'}\t{sha}\t"
              f"{rec['entry_rel'] or '-'}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="P01 v2 bot registry (entries/<id>)")
    sub = ap.add_subparsers(dest="command", required=True)

    v = sub.add_parser("validate", help="检查 registry 目录")
    v.add_argument("entries_dir")
    v.add_argument("--json", nargs="?", const="-", default=None,
                   metavar="PATH", help="输出 JSON（省略 PATH 写到 stdout）")
    v.add_argument("--check-build", action="store_true",
                   help="用 g++ 在临时目录编译每个 entry（不写共享 build）")

    ls = sub.add_parser("list", help="列出 registry 条目")
    ls.add_argument("entries_dir")
    ls.add_argument("--json", action="store_true", help="输出 JSON")

    r = sub.add_parser("resolve", help="把 ID 解析成 entry 路径与元数据")
    r.add_argument("entries_dir")
    r.add_argument("id")
    r.add_argument("--json", action="store_true", help="输出 JSON")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "validate":
        report = validate_entries(args.entries_dir, check_build=args.check_build)
        if args.json is not None:
            _emit_json(report, args.json)
        if args.json is None:
            _print_validate(report)
        return 0 if report["ok"] else 1

    if args.command == "list":
        report = list_entries(args.entries_dir)
        if args.json:
            _emit_json(report, "-")
        else:
            _print_list(report)
        return 0 if report["ok"] else 1

    try:
        rec = resolve_entry(args.entries_dir, args.id)
    except BotRegistryError as exc:
        print(f"resolve failed: {exc}", file=sys.stderr)
        return 1
    payload = {
        "ok": True,
        "id": rec["id"],
        "display_name": rec["display_name"],
        "model": rec["model"],
        "model_key": rec["model_key"],
        "harness": rec["harness"],
        "run": rec["run"],
        "entry": rec["entry"],
        "entry_rel": rec["entry_rel"],
        "language": rec["language"],
        "code_sha256": rec["code_sha256"],
    }
    if args.json:
        _emit_json(payload, "-")
    else:
        print(f"{rec['id']} -> {rec['entry']}")
        print(f"  display={rec['display_name']} model={rec['model']} "
              f"model_key={rec['model_key']} harness={rec['harness']} run={rec['run']}")
        print(f"  sha256={rec['code_sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
