"""Read-only, bounded local benchmark of task snapshot reads; invokes no model.

Use an explicitly selected database and an existing PENDING/RUNNING acceptance task.
"""

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.identity import Principal
from app.services import task_stream


def percentile(values, percent):
    return sorted(values)[min(len(values) - 1, int((len(values) - 1) * percent))]


async def benchmark(factory, principal, code, viewers, rounds, shared=False):
    cache = task_stream.SnapshotCache() if shared else None
    latencies, revisions = [], set()
    engine = factory.kw["bind"]
    statements = 0

    def count(*_args):
        nonlocal statements
        statements += 1

    event.listen(engine.sync_engine, "before_cursor_execute", count)
    try:
        started = time.perf_counter()
        for _ in range(rounds):

            async def read():
                before = time.perf_counter()
                value = await (
                    cache.get(factory, principal, code)
                    if cache
                    else task_stream.snapshot(factory, principal, code)
                )
                if value["terminal"]:
                    raise ValueError("benchmark_requires_nonterminal_task")
                revisions.add(value["revision"])
                latencies.append((time.perf_counter() - before) * 1000)

            await asyncio.gather(*(read() for _ in range(viewers)))
            await asyncio.sleep(task_stream.POLL_SECONDS)
        elapsed = time.perf_counter() - started
        return {
            "mode": "shared" if shared else "direct",
            "viewers": viewers,
            "rounds": rounds,
            "reads": len(latencies),
            "sql_statements": statements,
            "elapsed_seconds": round(elapsed, 3),
            "latency_ms": {
                "median": round(statistics.median(latencies), 3),
                "p95": round(percentile(latencies, 0.95), 3),
            },
            "snapshot_revisions": len(revisions),
            "authorization_revalidation_included": False,
        }
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count)
        if cache:
            await cache.close()


async def main(args):
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    principal = Principal("benchmark", "local-benchmark", True, args.tenant_code, "TENANT_ADMIN")
    results = []
    try:
        for mode in [False, True] if args.mode == "both" else [args.mode == "shared"]:
            results.append(
                await benchmark(factory, principal, args.task_code, args.viewers, args.rounds, mode)
            )
    finally:
        await engine.dispose()
    report = {
        "kind": "snapshot_read_microbenchmark",
        "creates_tasks": False,
        "provider_calls": 0,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-code", required=True)
    parser.add_argument("--task-code", required=True)
    parser.add_argument("--viewers", type=int, default=30, choices=range(1, 101))
    parser.add_argument("--rounds", type=int, default=10, choices=range(1, 31))
    parser.add_argument("--mode", choices=["baseline", "shared", "both"], default="both")
    parser.add_argument("--output", type=Path, required=True)
    try:
        asyncio.run(main(parser.parse_args()))
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "reason": type(exc).__name__}))
        raise SystemExit(2) from None
