#!/usr/bin/env python3
"""scripts/check_magnets.py — Trigger a magnet liveness check on the running backend.

Thin HTTP wrapper around POST /api/magnets/check: all DB writes go through the
backend's serial write queue, avoiding cross-process DuckDB write locks.
Requires the backend (star-archive-backend) to be running.

Usage:
    .venv/bin/python scripts/check_magnets.py                # scope=changed (default)
    .venv/bin/python scripts/check_magnets.py --scope all    # full historical sweep
    .venv/bin/python scripts/check_magnets.py --scope dead   # retry dead ones
"""

from __future__ import annotations

import argparse
import datetime
import sys
import time

import httpx


def _today_password() -> str:
    """Daily rotating password, same rule as backend/routers/auth.py."""
    d = datetime.datetime.now(datetime.timezone.utc)
    return f"rn{d.strftime('%y%m%d')}{d.day % 2}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Trigger magnet liveness check")
    parser.add_argument("--host", default="http://localhost:8765")
    parser.add_argument("--scope", default="changed", choices=["all", "dead", "unchecked", "changed"])
    parser.add_argument("--no-wait", action="store_true", help="start the check and exit")
    args = parser.parse_args()

    client = httpx.Client(base_url=args.host, timeout=30.0)

    resp = client.post("/api/auth", json={"password": _today_password()})
    if not resp.json().get("ok"):
        print("auth failed: wrong daily password?", file=sys.stderr)
        return 1

    resp = client.post("/api/magnets/check", json={"scope": args.scope})
    body = resp.json()
    print(f"start: {resp.status_code} {body}")
    if args.no_wait or body.get("status") == "running":
        return 0

    while True:
        time.sleep(10)
        state = client.get("/api/magnets/check").json()
        print(
            f"\r{state['done']}/{state['total']} "
            f"alive={state['alive']} dead={state['dead']} swapped={state['swapped']} "
            f"elapsed={state.get('elapsed')}s",
            end="", flush=True,
        )
        if not state["running"]:
            print()
            break

    print(
        f"done: total={state['total']} alive={state['alive']} "
        f"dead={state['dead']} swapped={state['swapped']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
