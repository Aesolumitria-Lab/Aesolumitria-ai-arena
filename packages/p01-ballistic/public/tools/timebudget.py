#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""P01 answer-time budget reporter.

The evaluation harness normally sets ``P01_START_EPOCH`` (Unix seconds) and
``P01_BUDGET_S`` before the agent starts.  Not every harness injects them.

Fallback (sanctioned): if both variables are absent, the tool creates a local
start marker on first use and uses the system clock with a 90-minute default
budget.  If the harness provides a timer/watchdog, it is authoritative; otherwise the local fallback is self-managed.
The submitted bot itself must not read time, and must not depend on this tool.

Exit codes: 0 = ok, 3 = warning/critical, 4 = expired, 2 = invalid arguments.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

WARN_S = 15 * 60
CRIT_S = 5 * 60
DEFAULT_BUDGET_S = 90 * 60


def human(sec: float) -> str:
    sec = max(0, int(sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def phase(elapsed: float, budget: float) -> str:
    if budget <= 0:
        return "unknown"
    p = elapsed / budget
    if p < 0.12:
        return "read rules / compile starter / verify protocol"
    if p < 0.40:
        return "exact simulator + protocol hardening"
    if p < 0.72:
        return "search + strategy + self-play"
    if p < 0.88:
        return "public-map validation; fix faults"
    return "freeze; final compile + 0-fault selfcheck + submit"


def main() -> int:
    ap = argparse.ArgumentParser(description="P01 answer-time budget reporter")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--start-epoch", type=float, default=None,
                    help="override start time (Unix seconds) when the harness did not inject it")
    ap.add_argument("--budget-s", type=float, default=None,
                    help="override budget seconds (default 5400 when not injected)")
    ap.add_argument("--state", default=".p01_time_start",
                    help="local marker file used only when P01_START_EPOCH is absent")
    ap.add_argument("--reset", action="store_true",
                    help="reset the local marker to now (fallback mode only)")
    args = ap.parse_args()

    raw_start = os.environ.get("P01_START_EPOCH", "")
    raw_budget = os.environ.get("P01_BUDGET_S", "")
    warnings: list[str] = []
    source = "harness"

    if args.start_epoch is not None:
        start = args.start_epoch
        source = "cli"
    elif raw_start:
        try:
            start = float(raw_start)
        except ValueError:
            print("timebudget: P01_START_EPOCH must be numeric.", file=sys.stderr)
            return 2
    else:
        state = Path(args.state)
        if args.reset or not state.is_file():
            start = time.time()
            state.write_text(str(start), encoding="utf-8")
            source = "local-new"
            warnings.append(
                f"P01_START_EPOCH/P01_BUDGET_S not injected; wrote local start marker {state}")
        else:
            try:
                start = float(state.read_text(encoding="utf-8").strip())
            except ValueError:
                print(f"timebudget: bad local marker {state}; use --reset.", file=sys.stderr)
                return 2
            source = "local"
            warnings.append(
                f"P01_START_EPOCH/P01_BUDGET_S not injected; using local start marker {state}")

    if args.budget_s is not None:
        budget = args.budget_s
    elif raw_budget:
        try:
            budget = float(raw_budget)
        except ValueError:
            print("timebudget: P01_BUDGET_S must be numeric.", file=sys.stderr)
            return 2
    else:
        budget = DEFAULT_BUDGET_S
        warnings.append("P01_BUDGET_S not injected; using 5400 s (90 min)")

    if budget <= 0:
        print("timebudget: budget must be > 0.", file=sys.stderr)
        return 2
    if start > time.time() + 60:
        print("timebudget: start time is in the future; check --start-epoch.", file=sys.stderr)
        return 2

    now = time.time()
    elapsed = max(0.0, now - start)
    remaining = budget - elapsed
    if remaining <= 0:
        status, code = "expired", 4
    elif remaining <= CRIT_S:
        status, code = "critical", 3
    elif remaining <= WARN_S:
        status, code = "warning", 3
    else:
        status, code = "ok", 0

    deadline = start + budget
    note = "answer/build budget only; official match runtime is separate"
    if warnings:
        note += "; fallback timer is self-managed; use any harness timer if present"
    payload = {
        "tool": "timebudget",
        "status": status,
        "source": source,
        "warning": " | ".join(warnings) if warnings else None,
        "budget_s": round(budget, 1),
        "elapsed_s": round(elapsed, 1),
        "remaining_s": round(max(0.0, remaining), 1),
        "elapsed": human(elapsed),
        "remaining": human(max(0.0, remaining)),
        "start_utc": datetime.fromtimestamp(start, timezone.utc)
        .isoformat().replace("+00:00", "Z"),
        "deadline_utc": datetime.fromtimestamp(deadline, timezone.utc)
        .isoformat().replace("+00:00", "Z"),
        "phase": phase(elapsed, budget),
        "note": note,
    }
    if warnings and not args.json:
        for w in warnings:
            print(f"timebudget: {w}", file=sys.stderr)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"[P01 time] {payload['elapsed']} elapsed | "
              f"{payload['remaining']} left | {status}")
        print(f"[P01 time] phase: {payload['phase']}")
        print(f"[P01 time] deadline: {payload['deadline_utc']}")
        if status in ("warning", "critical"):
            print("[P01 time] stop tuning; reserve the last 10 minutes for "
                  "final compile + 0-fault selfcheck.")
        elif status == "expired":
            print("[P01 time] budget expired; freeze and submit the best "
                  "working version now.")
    return code


if __name__ == "__main__":
    sys.exit(main())
