"""Agent and immutable receipt commit together; replay never rewrites current configuration."""

import hashlib
import json
import re

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select, text

from app.core.identity import new_code
from app.models.platform import Agent
from app.models.workspace import AgentCreationReceipt
from app.services.configuration import agent_public, metadata, tag

KEY = re.compile(r"[A-Za-z0-9_.-]{16,128}")


def request_hash(key):
    if not KEY.fullmatch(key):
        raise HTTPException(422, "创建请求标识格式无效")
    return hashlib.sha256(key.encode()).hexdigest()


async def lookup(session, principal, key):
    return await session.scalar(
        select(AgentCreationReceipt).where(
            AgentCreationReceipt.tenant_code == principal.tenant_code,
            AgentCreationReceipt.created_by == principal.user_code,
            AgentCreationReceipt.request_hash == request_hash(key),
        )
    )


async def create(session, principal, data, key=None):
    digest = None
    payload_hash = hashlib.sha256(
        json.dumps(data.model_dump(), sort_keys=True).encode()
    ).hexdigest()
    if key is not None:
        digest = request_hash(key)
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": f"agent-create:{principal.tenant_code}:{principal.user_code}:{digest}"},
        )
        receipt = await lookup(session, principal, key)
        if receipt:
            if receipt.payload_hash != payload_hash:
                raise HTTPException(409, "相同创建请求的内容已变化，请先找回原创建结果")
            return receipt.result, True
    row = Agent(tenant_code=principal.tenant_code, agent_code=new_code("agt"), **data.model_dump())
    session.add(row)
    await session.flush()
    result = jsonable_encoder(
        {
            **agent_public(row),
            "configuration_etag": tag(
                {"agent": metadata(row), "draft": {"revision": 0, "config": None}, "relations": []}
            ),
        }
    )
    if digest:
        session.add(
            AgentCreationReceipt(
                tenant_code=principal.tenant_code,
                receipt_code=new_code("acr"),
                created_by=principal.user_code,
                request_hash=digest,
                payload_hash=payload_hash,
                agent_code=row.agent_code,
                result=result,
            )
        )
    await session.commit()
    return result, False
