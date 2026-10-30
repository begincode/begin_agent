from fastapi import HTTPException
from sqlalchemy import text

from app.core.identity import new_code
from app.model_gateway.domain.chat import ChatCompletionRequest, ChatMessage
from app.models.platform import Agent, AgentRelation, AgentToolBinding, AgentVersion
from app.repositories.platform import TenantRepository, public


class AgentService:
    def __init__(self, session, tenant_code):
        self.session = session
        self.repo = TenantRepository(session, tenant_code)
        self.tenant = tenant_code

    async def publish(self, code, data, activate=True):
        agent = await self.repo.get(Agent, "agent_code", code, lock=True)
        if agent.status != 1:
            raise HTTPException(409, "Agent 已停用")
        from app.services.resources import require_resource

        profile = await require_resource(self.session, self.tenant, "MODEL", data.profile_code)
        if not profile.enabled:
            raise HTTPException(409, "模型配置已停用")
        # Use the same model capability validation as actual requests.
        from app.api.chat import get_model_gateway
        from app.core.config import get_settings
        from app.core.credentials import CredentialCipher
        from app.services.model_profiles import ModelProfileService

        provider = await ModelProfileService(
            self.session, CredentialCipher(get_settings()), self.tenant
        ).resolve(profile.profile_code)
        get_model_gateway().validate(
            ChatCompletionRequest(
                profile_code=profile.profile_code,
                messages=[ChatMessage(role="user", content="validate")],
                **data.model_parameters.model_dump(exclude_none=True),
            ),
            provider,
        )
        tools = []
        for tool_code in data.tool_codes:
            tool = await require_resource(self.session, self.tenant, "TOOL", tool_code)
            if tool.status != 1:
                raise HTTPException(409, "工具已停用")
            tools.append(
                {
                    "tool_code": tool.tool_code,
                    "tool_name": tool.tool_name,
                    "input_schema": tool.input_schema,
                    "connection_config": tool.connection_config,
                }
            )
        relations = await self.repo.list(
            AgentRelation, limit=1000, master_agent_code=code, enabled=True
        )
        children = []
        for relation in relations:
            child = await self.repo.get(Agent, "agent_code", relation.sub_agent_code)
            if not child.current_version_code or child.status != 1:
                raise HTTPException(409, "子 Agent 必须启用且已发布版本")
            children.append(
                {
                    "agent_code": child.agent_code,
                    "version_code": child.current_version_code,
                    "agent_name": child.agent_name,
                    "call_mode": relation.call_mode,
                    "timeout_seconds": relation.timeout_seconds,
                }
            )
        previous = await self.session.scalar(
            self.repo.query(AgentVersion)
            .where(AgentVersion.agent_code == code)
            .order_by(AgentVersion.version_no.desc())
            .limit(1)
        )
        skills, mcps = [], []
        for skill_code in dict.fromkeys(data.skill_codes):
            row = await require_resource(self.session, self.tenant, "SKILL", skill_code)
            skills.append(
                {
                    "skill_code": row.skill_code,
                    "skill_name": row.skill_name,
                    "instructions": row.instructions,
                }
            )
        for mcp_code in dict.fromkeys(data.mcp_codes):
            row = await require_resource(self.session, self.tenant, "MCP", mcp_code)
            selected = data.mcp_tool_names.get(mcp_code)
            catalog = {t["name"]: t for t in row.tools}
            names = list(dict.fromkeys(selected)) if selected is not None else list(catalog)
            if not row.checked_at or not names or any(n not in catalog for n in names):
                raise HTTPException(409, "请先检测 MCP 连接，并选择已发现的工具")
            mcps.append(
                {
                    "mcp_code": row.mcp_code,
                    "mcp_name": row.mcp_name,
                    "endpoint": row.endpoint,
                    "tools": [catalog[n] for n in names],
                }
            )
        if sum(len(m["tools"]) for m in mcps) + len(tools) + len(children) > 64:
            raise HTTPException(422, "单个版本最多授权 64 个工具与子 Agent")
        version = AgentVersion(
            tenant_code=self.tenant,
            version_code=new_code("agv"),
            agent_code=code,
            version_no=(previous.version_no + 1) if previous else 1,
            system_prompt=data.system_prompt,
            profile_code=data.profile_code,
            model_snapshot={
                "provider_code": profile.provider_code,
                "model_code": profile.model_code,
                "base_url": profile.base_url,
            },
            model_parameters=data.model_parameters.model_dump(exclude_none=True),
            execution_config=data.execution_config.model_dump(),
            dependency_snapshot={
                "tools": tools,
                "children": children,
                "skills": skills,
                "mcps": mcps,
            },
            status="PUBLISHED" if activate else "DEBUG",
        )
        self.session.add(version)
        await self.session.flush()
        for tool in tools:
            self.session.add(
                AgentToolBinding(
                    tenant_code=self.tenant,
                    binding_code=new_code("bnd"),
                    agent_version_code=version.version_code,
                    tool_code=tool["tool_code"],
                    permission_config={
                        "allowed_operations": [tool["connection_config"]["implementation"]]
                    },
                )
            )
        if activate:
            agent.current_version_code = version.version_code
        await self.session.commit()
        return public(version)

    async def relate(self, code, data, relation_code=None):
        # Serialize graph edits for a tenant to prevent concurrent A->B / B->A races.
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:tenant, 0))"),
            {"tenant": self.tenant},
        )
        await self.repo.get(Agent, "agent_code", code)
        row = (
            await self.repo.get(AgentRelation, "relation_code", relation_code, lock=True)
            if relation_code
            else None
        )
        if row and row.master_agent_code != code:
            raise HTTPException(404, "关系不存在")
        target = row.sub_agent_code if row else data.sub_agent_code
        enabled = data.enabled if row is None or "enabled" in data.model_fields_set else row.enabled
        if target == code:
            raise HTTPException(409, "Agent 不能调用自己")
        child = await self.repo.get(Agent, "agent_code", target)
        if (enabled or row is None) and (child.status != 1 or not child.current_version_code):
            raise HTTPException(409, "子 Agent 必须启用且已发布版本")
        edges = (await self.session.scalars(self.repo.query(AgentRelation))).all()
        if row is None and any(
            edge.master_agent_code == code and edge.sub_agent_code == target for edge in edges
        ):
            raise HTTPException(409, "协作关系已存在，请编辑已有关系")
        graph = {}
        for edge in edges:
            if edge.enabled and edge.relation_code != relation_code:
                graph.setdefault(edge.master_agent_code, []).append(edge.sub_agent_code)
        queue, seen = [target] if enabled else [], set()
        while queue:
            node = queue.pop()
            if node == code:
                raise HTTPException(409, "Agent 调用关系不能形成循环")
            if node not in seen:
                seen.add(node)
                queue.extend(graph.get(node, []))
        if row is None:
            row = AgentRelation(
                tenant_code=self.tenant,
                relation_code=new_code("rel"),
                master_agent_code=code,
                **data.model_dump(),
            )
        else:
            for field, value in data.model_dump(exclude_unset=True).items():
                setattr(row, field, value)
        self.session.add(row)
        await self.session.commit()
        return public(row)
