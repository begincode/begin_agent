"""Conditional configuration writes share graph -> Agent lock ordering."""

import hashlib
import json

from fastapi import HTTPException

from app.models.platform import Agent, AgentRelation, AgentVersion
from app.models.workspace import AgentDraft
from app.repositories.platform import public


def tag(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return '"' + hashlib.sha256(encoded).hexdigest() + '"'


def metadata(agent):
    return {
        key: getattr(agent, key)
        for key in ("agent_code", "agent_name", "agent_type", "status", "current_version_code")
    }


def agent_public(agent):
    return {**public(agent), "edit_etag": tag(metadata(agent))}


def match(expected, current):
    if expected is None:
        raise HTTPException(428, "请先读取配置，再携带 If-Match 保存")
    if expected != current:
        raise HTTPException(412, "配置已被其他操作更新，请重新加载后再保存")


async def graph_lock(session, tenant):
    from sqlalchemy import text

    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:tenant, 0))"), {"tenant": tenant}
    )


async def relation_snapshot(repo, code):
    rows = (
        await repo.session.execute(
            repo.query(AgentRelation)
            .join(
                Agent,
                (Agent.agent_code == AgentRelation.sub_agent_code)
                & (Agent.tenant_code == AgentRelation.tenant_code),
            )
            .outerjoin(
                AgentVersion,
                (AgentVersion.version_code == Agent.current_version_code)
                & (AgentVersion.tenant_code == Agent.tenant_code),
            )
            .add_columns(
                Agent.agent_name, Agent.status, Agent.current_version_code, AgentVersion.version_no
            )
            .where(AgentRelation.master_agent_code == code)
            .order_by(AgentRelation.created_at, AgentRelation.relation_code)
            .limit(1001)
        )
    ).all()
    if len(rows) > 1000:
        raise HTTPException(409, "协作关系超过读取上限，请联系管理员核对")
    return [
        {
            **public(row),
            "sub_agent_name": name,
            "sub_agent_status": status,
            "sub_agent_version_code": version,
            "sub_agent_version_no": number,
        }
        for row, name, status, version, number in rows
    ]


async def snapshot(repo, code, agent=None, *, include_versions=True):
    agent = agent or await repo.get(Agent, "agent_code", code, lock=True)
    draft = await repo.session.scalar(repo.query(AgentDraft).where(AgentDraft.agent_code == code))
    versions = await repo.list(AgentVersion, agent_code=code) if include_versions else []
    published = next((v for v in versions if v.version_code == agent.current_version_code), None)
    if include_versions and published is None and agent.current_version_code:
        published = await repo.session.scalar(
            repo.query(AgentVersion).where(AgentVersion.version_code == agent.current_version_code)
        )
    relations = await relation_snapshot(repo, code)
    draft_value = public(draft) if draft else {"revision": 0, "config": None}
    return {
        "agent": agent_public(agent),
        "draft": draft_value,
        "versions": [public(v) for v in versions],
        "published": public(published) if published else None,
        "relations": relations,
        "relation_etag": tag(relations),
        "etag": tag({"agent": metadata(agent), "draft": draft_value, "relations": relations}),
    }
