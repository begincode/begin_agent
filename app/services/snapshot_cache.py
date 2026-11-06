"""Short, bounded, per-application snapshot reuse; authorization is never cached."""

import asyncio
import json
import time
from collections import OrderedDict

from fastapi.encoders import jsonable_encoder

from app.services.permissions import allowed, require


class SnapshotCache:
    def __init__(self, ttl=0.25, max_entries=64, max_bytes=512 * 1024, loader=None):
        self.ttl, self.max_entries, self.max_bytes = ttl, max_entries, max_bytes
        self.loader = loader
        self.values = OrderedDict()
        self.pending = {}
        self.closed = False

    async def get(self, factory, principal, code):
        require(principal, "tasks.read")
        if self.closed:
            raise RuntimeError("snapshot cache closed")
        key = (factory, principal.tenant_code, code, allowed(principal, "runs.read"))
        now = time.monotonic()
        for stale in [k for k, (expiry, _) in self.values.items() if expiry <= now]:
            self.values.pop(stale, None)
        if key in self.values:
            self.values.move_to_end(key)
            return self.values[key][1]
        if key not in self.pending:
            if len(self.pending) >= self.max_entries:
                return await self.read(factory, principal, code)
            task = asyncio.create_task(self.load(key, factory, principal, code))
            self.pending[key] = task
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        # One disconnected viewer must not cancel the other viewers' shared read.
        return await asyncio.shield(self.pending[key])

    async def read(self, factory, principal, code):
        from app.services.task_stream import snapshot

        return await (self.loader or snapshot)(factory, principal, code)

    async def load(self, key, factory, principal, code):
        try:
            value = await self.read(factory, principal, code)
            if (
                not self.closed
                and not value["terminal"]
                and len(json.dumps(jsonable_encoder(value)).encode()) <= self.max_bytes
            ):
                self.values[key] = (time.monotonic() + self.ttl, value)
                while len(self.values) > self.max_entries:
                    self.values.popitem(last=False)
            return value
        finally:
            self.pending.pop(key, None)

    async def close(self):
        self.closed = True
        jobs = list(self.pending.values())
        for task in jobs:
            task.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        self.pending.clear()
        self.values.clear()
