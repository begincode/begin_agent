"""Bounded, lease-fenced run previews; never a source of successful task context."""

import re
import time
from datetime import UTC, datetime

LIMIT = 65536


def preview_text(value, secrets, redact, final=False):
    # Hold suffixes that may become credentials in the next upstream frame.
    if not final:
        held = 0
        for secret in [*secrets, "Bearer ", "sk-", "sess_", "handoff_"]:
            for n in range(min(len(secret), len(value)), 0, -1):
                if value.endswith(secret[:n]):
                    held = max(held, n)
                    break
        if held:
            value = value[:-held]
    value = redact(value, secrets)
    return re.sub(r"Bearer\s+\S*|(?:sk-|sess_|handoff_)[A-Za-z0-9_-]*", "[REDACTED]", value)


class RunProgress:
    def __init__(self, queue, lease, run_code, secrets, redact):
        self.queue, self.lease, self.run_code = queue, lease, run_code
        self.secrets, self.redact = secrets, redact
        self.content, self.reasoning = "", ""
        self.call_no, self.truncated = 0, False
        self.phase = {}
        self.last_flush, self.pending = 0.0, 0

    async def stage(self, kind, name, status="RUNNING"):
        self.phase = {"type": kind, "name": name, "status": status}
        await self.flush(force=True)

    async def begin_model(self):
        self.call_no += 1
        self.content, self.reasoning = "", ""
        self.truncated = False
        await self.stage("MODEL_CALL", "调用模型")

    async def add(self, kind, delta):
        first = not (self.content or self.reasoning)
        field = "content" if kind == "content" else "reasoning"
        text = getattr(self, field) + delta
        if len(text) > LIMIT:
            self.truncated = True
        setattr(self, field, text[:LIMIT])
        self.pending += len(delta)
        await self.flush(force=first)

    async def flush(self, force=False, final=False):
        now = time.monotonic()
        if not force and self.pending < 1024 and now - self.last_flush < 0.15:
            return
        value = {
            "call_no": self.call_no,
            "content": preview_text(self.content, self.secrets, self.redact, final),
            "reasoning": preview_text(self.reasoning, self.secrets, self.redact, final),
            "phase": self.redact(self.phase, self.secrets),
            "truncated": self.truncated,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        await self.queue.update_live(self.lease, self.run_code, value)
        self.last_flush, self.pending = now, 0

    async def complete(self, response):
        self.content = (response.message.content or "")[:LIMIT]
        self.reasoning = (response.message.reasoning_content or "")[:LIMIT]
        self.truncated |= len(response.message.content or "") > LIMIT
        self.truncated |= len(response.message.reasoning_content or "") > LIMIT
        await self.flush(force=True, final=not self.truncated)
