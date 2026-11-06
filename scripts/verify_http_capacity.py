"""Bounded loopback HTTP read benchmark. No mutations or supplier calls."""

import argparse
import asyncio
import json
import re
import stat
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def validate_target(url, requests, concurrency):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("only explicit loopback HTTP targets are accepted")
    if not 1 <= requests <= 2000 or not 1 <= concurrency <= 100:
        raise ValueError("bounded workload required")


async def run(args):
    validate_target(args.base_url, args.requests, args.concurrency)
    path = Path(args.session_file)
    if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("private session file required")
    token = path.read_text().strip()
    if not token or len(token) > 256:
        raise ValueError("invalid session")
    if not re.fullmatch(r"ten_[0-9A-Z]{26}", args.tenant):
        raise ValueError("invalid tenant")
    limit = asyncio.Semaphore(args.concurrency)
    latencies, statuses = [], {}
    async with httpx.AsyncClient(
        base_url=args.base_url,
        trust_env=False,
        timeout=15,
        cookies={"begin_agent_session": token},
        headers={"X-Tenant-Code": args.tenant},
        limits=httpx.Limits(
            max_connections=args.concurrency, max_keepalive_connections=args.concurrency
        ),
    ) as client:

        async def one():
            async with limit:
                started = time.perf_counter()
                try:
                    status = (await client.get("/api/v1/tasks?limit=20")).status_code
                except httpx.HTTPError:
                    status = 0
                latencies.append((time.perf_counter() - started) * 1000)
                statuses[status] = statuses.get(status, 0) + 1

        started = time.perf_counter()
        await asyncio.gather(*(one() for _ in range(args.requests)))
        duration = time.perf_counter() - started
    ordered = sorted(latencies)
    return {
        "requests": args.requests,
        "concurrency": args.concurrency,
        "duration_seconds": round(duration, 3),
        "p50_ms": round(ordered[len(ordered) // 2], 2),
        "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 2),
        "statuses": statuses,
        "passed": statuses == {200: args.requests},
        "scope": "single_host_read_only_http",
        "provider_calls": 0,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-url", required=True)
    p.add_argument("--session-file", required=True)
    p.add_argument("--tenant", required=True)
    p.add_argument("--requests", type=int, default=100)
    p.add_argument("--concurrency", type=int, default=10)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    try:
        result = asyncio.run(run(args))
    except Exception:
        result = {"passed": False, "error": "configuration_or_connection_failed"}
    Path(args.output).write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
