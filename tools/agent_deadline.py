#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run an agent command with a hard wall-clock answer deadline.

The child inherits ``P01_START_EPOCH`` and ``P01_BUDGET_S``; the agent's
build-phase code can call ``public/tools/timebudget.py``.  At the deadline the
whole child process tree is terminated.  This wrapper is the enforcement
layer; the in-package reporter is only a clock for the agent.

    python tools/agent_deadline.py --budget-min 90 --out runs/x/deadline.json -- \
        python agent.py
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

DEVNULL = subprocess.DEVNULL


def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       stdout=DEVNULL, stderr=DEVNULL)
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def main() -> int:
    ap = argparse.ArgumentParser(description="Run an agent with a hard deadline")
    ap.add_argument("--budget-min", type=float, default=90.0)
    ap.add_argument("--out", default="deadline.json")
    ap.add_argument("--warn-min", default="15,5",
                    help="comma-separated warning marks")
    ap.add_argument("command", nargs=argparse.REMAINDER)
    args = ap.parse_args()
    cmd = args.command
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        ap.error("missing command after --")
    if args.budget_min <= 0:
        ap.error("--budget-min must be > 0")

    start = time.time()
    budget = args.budget_min * 60.0
    deadline = start + budget
    marks = sorted({float(x) * 60.0 for x in args.warn_min.split(",")
                    if x.strip() and 0 < float(x) * 60.0 < budget}, reverse=True)
    env = dict(os.environ,
               P01_START_EPOCH=f"{start:.6f}",
               P01_BUDGET_S=f"{budget:.1f}")
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(cmd, env=env, cwd=str(Path.cwd()),
                            start_new_session=(os.name != "nt"),
                            creationflags=flags)
    status = "completed"
    warned = set()
    try:
        while proc.poll() is None:
            remaining = deadline - time.time()
            if remaining <= 0:
                status = "expired"
                kill_tree(proc)
                break
            for m in marks:
                if remaining <= m and m not in warned:
                    warned.add(m)
                    print(f"[deadline] {m / 60:.0f} minutes left", flush=True)
            time.sleep(1.0)
    except KeyboardInterrupt:
        status = "interrupted"
        kill_tree(proc)
    end = time.time()
    payload = {
        "tool": "agent_deadline",
        "status": status,
        "budget_s": round(budget, 1),
        "elapsed_s": round(end - start, 1),
        "start_epoch": round(start, 3),
        "deadline_epoch": round(deadline, 3),
        "returncode": proc.returncode,
        "command": cmd,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    if status != "completed":
        return 124
    return proc.returncode or 0


if __name__ == "__main__":
    sys.exit(main())
