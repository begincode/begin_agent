import asyncio
import logging
import os
import signal
import socket
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.directories import prepare_directories
from app.core.identity import tenant_context
from app.core.logging import configure_logging
from app.repositories.task_queue import LeaseLost, TaskQueue
from app.runtime.agent import AgentRuntime, RuntimeFailure
from app.workers.readiness import directory, report

logger = logging.getLogger(__name__)


async def serve() -> None:
    settings = get_settings()
    prepare_directories(settings)
    engine = create_async_engine(
        settings.worker_database_url or settings.database_url, pool_pre_ping=True
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    if settings.auth_mode != "local" and settings.environment not in {"local", "test"}:
        async with factory() as session:
            role = (
                await session.execute(
                    text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user")
                )
            ).one()
            if role.rolsuper or not role.rolbypassrls:
                raise RuntimeError("Worker 必须使用独立的非超级用户 BYPASSRLS 数据库角色")
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    queue = TaskQueue(factory, worker_id, settings.worker_lease_seconds)
    runtime = AgentRuntime(queue)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    logger.info("Agent worker ready: worker=%s", worker_id)
    try:
        while not stop.is_set():
            try:
                lease = await queue.claim()
                report(settings, worker_id)
                if lease:
                    ctx = tenant_context.set(lease.tenant_code)
                    try:
                        await process_lease(queue, runtime, lease, stop)
                    finally:
                        tenant_context.reset(ctx)
                else:
                    try:
                        await asyncio.wait_for(stop.wait(), settings.worker_poll_seconds)
                    except TimeoutError:
                        pass
            except Exception:
                logger.error("Worker polling failed; retrying (details redacted)")
                try:
                    await asyncio.wait_for(stop.wait(), settings.worker_poll_seconds)
                except TimeoutError:
                    pass
    finally:
        (directory(settings) / f"{os.getpid()}.json").unlink(missing_ok=True)
        await engine.dispose()


async def process_lease(queue, runtime, lease, stop=None):
    async def heartbeat():
        while True:
            await asyncio.sleep(queue.lease_seconds / 3)
            await queue.heartbeat(lease)
            report(get_settings(), queue.worker_id)

    execution = asyncio.create_task(runtime.run_task(lease))
    pulse = asyncio.create_task(heartbeat())
    shutdown = asyncio.create_task(stop.wait()) if stop else None
    try:
        watched = {execution, pulse} | ({shutdown} if shutdown else set())
        done, _ = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
        if pulse in done:
            await pulse  # A lost lease or DB failure must cancel upstream work immediately.
        if shutdown in done:
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
            await queue.finish(lease, "FAILED", reason="CANCELLED", retryable=True)
            return
        output = await execution
        await queue.finish(lease, "SUCCEEDED", output)
    except LeaseLost:
        logger.info("Task ownership ended: task=%s", lease.task_code)
    except RuntimeFailure as exc:
        await queue.finish(
            lease,
            "TIMEOUT" if exc.reason == "TIMEOUT" else "FAILED",
            reason=exc.reason,
            retryable=exc.retryable,
        )
    except Exception:
        try:
            await queue.finish(lease, "FAILED", reason="INTERNAL_ERROR", retryable=True)
        except LeaseLost:
            pass
        logger.error("Task failed (details redacted): task=%s", lease.task_code)
    finally:
        for task in (execution, pulse, shutdown):
            if task and not task.done():
                task.cancel()
        await asyncio.gather(
            *(t for t in (execution, pulse, shutdown) if t), return_exceptions=True
        )


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_dir)
    asyncio.run(serve())


if __name__ == "__main__":
    run()
