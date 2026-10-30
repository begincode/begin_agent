import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field, replace

from fastapi import HTTPException
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as SchemaValidationError
from sqlalchemy import select

from app.core.config import get_settings
from app.core.credentials import CredentialCipher
from app.core.identity import new_code
from app.model_gateway.domain.chat import (
    ChatCompletionRequest,
    ChatMessage,
    ToolDefinition,
    ToolFunction,
)
from app.model_gateway.errors import ProviderRequestError
from app.model_gateway.gateway import ModelGateway
from app.models.platform import Agent, AgentRun, AgentVersion, RunMessage, RunStep, TaskArtifact
from app.models.workspace import TaskTurn
from app.repositories.platform import TenantRepository
from app.repositories.task_queue import LeaseLost
from app.runtime.progress import RunProgress
from app.services.model_profiles import ModelProfileService
from app.services.resources import require_resource
from app.tools.builtins import execute
from app.tools.mcp import mcp_request


class RuntimeFailure(Exception):
    def __init__(self, reason, retryable=False):
        self.reason, self.retryable = reason, retryable
        super().__init__(reason)


def redact(value, secrets=()):
    if isinstance(value, dict):
        return {
            k: "[REDACTED]"
            if k.lower()
            in {
                "api_key",
                "authorization",
                "password",
                "client_secret",
                "token",
                "verifier",
            }
            else redact(v, secrets)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return re.sub(
            r"(?:Bearer\s+\S+|(?:sess_|handoff_)[A-Za-z0-9_-]{43}|sk-[A-Za-z0-9_-]{16,})",
            "[REDACTED]",
            value,
        )
    return value


@dataclass
class Budget:
    remaining: int
    max_calls: int
    max_depth: int
    calls: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def reserve(self, prompt_tokens, wanted):
        async with self.lock:
            allowance = min(wanted, self.remaining - prompt_tokens)
            if allowance < 1:
                raise RuntimeFailure("BUDGET_EXCEEDED")
            reserved = prompt_tokens + allowance
            self.remaining -= reserved
            return allowance, reserved

    async def reconcile(self, reserved, used):
        async with self.lock:
            self.remaining += reserved - used
            if self.remaining < 0:
                raise RuntimeFailure("BUDGET_EXCEEDED")


class AgentRuntime:
    def __init__(self, queue, gateway=None):
        self.queue = queue
        self.gateway = gateway or ModelGateway(get_settings())

    async def stream_model(self, request, provider, progress):
        upstream = self.gateway.stream(request, provider)
        pending = None
        response = None
        try:
            while True:
                if pending is None:
                    pending = asyncio.create_task(anext(upstream))
                done, _ = await asyncio.wait({pending}, timeout=0.15)
                if not done:
                    if progress.pending:
                        await progress.flush(force=True)
                    continue
                try:
                    event = pending.result()
                except StopAsyncIteration:
                    break
                pending = None
                if event.type in {"content", "reasoning"} and event.delta:
                    await progress.add(event.type, event.delta)
                elif event.type == "done" and event.response:
                    response = event.response
                elif event.type == "error":
                    raise ProviderRequestError("模型流中断")
            if response is None:
                raise ProviderRequestError("模型流未完整结束")
            return response
        finally:
            if pending and not pending.done():
                pending.cancel()
            if pending:
                await asyncio.gather(pending, return_exceptions=True)
            await upstream.aclose()

    async def version(self, lease, code):
        async with self.queue.factory() as session:
            repo = TenantRepository(session, lease.tenant_code)
            version = await repo.get(AgentVersion, "version_code", code)
            agent = await repo.get(Agent, "agent_code", version.agent_code)
            if agent.status != 1:
                raise RuntimeFailure("CONFIG_DISABLED")
            return version

    async def run_task(self, lease):
        version = await self.version(lease, lease.version_code)
        config = version.execution_config
        budget = Budget(config["max_tokens"], config["max_sub_agent_calls"], config["max_depth"])
        async with self.queue.factory() as session:
            turns = (
                await session.scalars(
                    select(TaskTurn)
                    .where(
                        TaskTurn.tenant_code == lease.tenant_code,
                        TaskTurn.task_code == lease.task_code,
                        TaskTurn.turn_no < lease.turn_no,
                        TaskTurn.status == "SUCCEEDED",
                    )
                    .order_by(TaskTurn.turn_no.desc())
                    .limit(20)
                )
            ).all()
        history = []
        for turn in reversed(turns):
            history += [
                ChatMessage(role="user", content=turn.prompt),
                ChatMessage(
                    role="assistant", content=(turn.output or {}).get("text") or "（无文本输出）"
                ),
            ]
        try:
            async with asyncio.timeout(config["timeout_seconds"]):
                output, run_code = await self.run(
                    lease, version, lease.prompt, budget, history=history
                )
                await self.queue.write(
                    lease,
                    TaskArtifact(
                        tenant_code=lease.tenant_code,
                        artifact_code=new_code("art"),
                        task_code=lease.task_code,
                        run_code=run_code,
                        artifact_name="最终结果",
                        content={"text": output},
                        version_no=lease.turn_no,
                    ),
                )
                return {"text": output, "run_code": run_code}
        except TimeoutError as exc:
            raise RuntimeFailure("TIMEOUT") from exc

    async def run(
        self,
        lease,
        version,
        prompt,
        budget,
        parent=None,
        parent_step=None,
        root=None,
        depth=0,
        history=None,
    ):
        if depth > budget.max_depth:
            raise RuntimeFailure("MAX_DEPTH")
        run_code = new_code("run")
        await self.queue.write(
            lease,
            AgentRun(
                tenant_code=lease.tenant_code,
                run_code=run_code,
                task_code=lease.task_code,
                root_run_code=root or run_code,
                parent_run_code=parent,
                parent_step_code=parent_step,
                agent_code=version.agent_code,
                agent_version_code=version.version_code,
                lease_token=lease.token,
                run_depth=depth,
                turn_no=lease.turn_no,
                input=redact({"prompt": prompt}),
            ),
        )
        step_no, sequence = 0, 0
        secret_values = []
        progress = RunProgress(self.queue, lease, run_code, secret_values, redact)

        async def step(kind, name, data, output, error=None):
            nonlocal step_no
            step_no += 1
            row = RunStep(
                tenant_code=lease.tenant_code,
                step_code=new_code("stp"),
                run_code=run_code,
                step_no=step_no,
                step_type=kind,
                name=name,
                input=redact(data, secret_values),
                output=redact(output, secret_values),
                status="FAILED" if error else "SUCCEEDED",
                error_code=error,
            )
            await self.queue.write(lease, row)
            await progress.stage(kind, name, "FAILED" if error else "SUCCEEDED")
            return row.step_code

        async def message(item, step_code=None):
            nonlocal sequence
            sequence += 1
            meta = item.model_dump(exclude={"content", "role"}, exclude_none=True)
            await self.queue.write(
                lease,
                RunMessage(
                    tenant_code=lease.tenant_code,
                    message_code=new_code("msg"),
                    run_code=run_code,
                    step_code=step_code,
                    sequence_no=sequence,
                    role=item.role.upper(),
                    content=redact(item.content, secret_values),
                    metadata_=redact(meta, secret_values),
                ),
            )

        async def child_call(child, child_prompt):
            if budget.calls >= budget.max_calls:
                raise RuntimeFailure("BUDGET_EXCEEDED")
            if depth + 1 > budget.max_depth:
                raise RuntimeFailure("MAX_DEPTH")
            budget.calls += 1
            child_step = await step(
                "AGENT_CALL",
                child["agent_name"],
                {"prompt": child_prompt, "agent_version_code": child["version_code"]},
                {"phase": "dispatched"},
            )
            await progress.stage("AGENT_CALL", "等待子 Agent")
            child_version = await self.version(lease, child["version_code"])
            try:
                async with asyncio.timeout(
                    min(child["timeout_seconds"], child_version.execution_config["timeout_seconds"])
                ):
                    result, code = await self.run(
                        lease,
                        child_version,
                        child_prompt,
                        budget,
                        parent=run_code,
                        parent_step=child_step,
                        root=root or run_code,
                        depth=depth + 1,
                    )
            except TimeoutError as exc:
                raise RuntimeFailure("TIMEOUT") from exc
            await step(
                "RESULT_PROCESS",
                "子 Agent 返回",
                {"parent_step_code": child_step},
                {"text": result, "run_code": code},
            )
            return result

        try:
            async with self.queue.factory() as session:
                provider = await ModelProfileService(
                    session, CredentialCipher(get_settings()), lease.tenant_code
                ).resolve(version.profile_code)
            secret_values.append(provider.api_key)
            skills = version.dependency_snapshot.get("skills", [])
            async with self.queue.factory() as session:
                for skill in skills:
                    await require_resource(session, lease.tenant_code, "SKILL", skill["skill_code"])
            system_prompt = version.system_prompt + "".join(
                "\n\n### Skill: " + s["skill_name"] + "\n" + s["instructions"] for s in skills
            )
            # Keys can rotate, but the endpoint, provider and model are immutable version data.
            provider = replace(
                provider,
                default_model=version.model_snapshot["model_code"],
                base_url=version.model_snapshot["base_url"],
            )
            messages = [
                ChatMessage(role="system", content=system_prompt),
                *(history or []),
                ChatMessage(role="user", content=prompt),
            ]
            await step(
                "CONTEXT_BUILD",
                "构建上下文",
                {"version_code": version.version_code},
                {"message_count": 2},
            )
            for item in messages:
                await message(item)
            children = version.dependency_snapshot.get("children", [])
            pre_results = []
            for child in children:
                if child["call_mode"] == "SERIAL":
                    pre_results.append(await child_call(child, prompt))
            parallel = [c for c in children if c["call_mode"] == "PARALLEL"]
            if parallel:
                # TaskGroup cancels siblings and awaits cleanup when any child fails.
                async with asyncio.TaskGroup() as group:
                    jobs = [group.create_task(child_call(c, prompt)) for c in parallel]
                pre_results.extend(j.result() for j in jobs)
            if pre_results:
                item = ChatMessage(
                    role="user", content="子 Agent 结果：\n" + "\n\n".join(pre_results)
                )
                messages.append(item)
                await message(item)
            tool_map = {
                "tool_" + t["tool_code"]: t for t in version.dependency_snapshot.get("tools", [])
            }
            mcp_map = {}
            for connector in version.dependency_snapshot.get("mcps", []):
                for tool in connector["tools"]:
                    alias = (
                        "mcp_"
                        + hashlib.sha256(
                            (connector["mcp_code"] + ":" + tool["name"]).encode()
                        ).hexdigest()[:24]
                    )
                    mcp_map[alias] = (connector, tool)
            child_map = {
                "delegate_" + c["agent_code"]: c for c in children if c["call_mode"] == "ON_DEMAND"
            }
            definitions = [
                ToolDefinition(
                    function=ToolFunction(
                        name=name, description=t["tool_name"], parameters=t["input_schema"]
                    )
                )
                for name, t in tool_map.items()
            ]
            definitions += [
                ToolDefinition(
                    function=ToolFunction(
                        name=name,
                        description=tool["description"][:4096],
                        parameters=tool["input_schema"],
                    )
                )
                for name, (_, tool) in mcp_map.items()
            ]
            definitions += [
                ToolDefinition(
                    function=ToolFunction(
                        name=name,
                        description="调用子 Agent：" + c["agent_name"],
                        parameters={
                            "type": "object",
                            "properties": {"prompt": {"type": "string", "maxLength": 30000}},
                            "required": ["prompt"],
                            "additionalProperties": False,
                        },
                    )
                )
                for name, c in child_map.items()
            ]
            for _ in range(version.execution_config["max_steps"]):
                # UTF-8 bytes conservatively bound token use when provider usage is unavailable.
                prompt_size = len(
                    json.dumps(
                        [m.model_dump(exclude_none=True) for m in messages], ensure_ascii=False
                    ).encode()
                )
                allowance, reservation = await budget.reserve(
                    prompt_size, version.model_parameters["max_tokens"]
                )
                parameters = {**version.model_parameters, "max_tokens": allowance}
                request = ChatCompletionRequest(
                    profile_code=version.profile_code,
                    messages=messages,
                    tools=definitions,
                    **parameters,
                )
                try:
                    await progress.begin_model()
                    if not hasattr(self.gateway, "stream"):
                        # Compatibility for injected non-streaming gateways; never replay a stream.
                        response = await self.gateway.generate(request, provider)
                    else:
                        response = await self.stream_model(request, provider, progress)
                    await progress.complete(response)
                except ProviderRequestError as exc:
                    await progress.flush(force=True)
                    await step(
                        "MODEL_CALL",
                        "调用模型",
                        {"model": provider.default_model},
                        {},
                        "MODEL_FAILED",
                    )
                    raise RuntimeFailure(
                        "MODEL_FAILED",
                        retryable=exc.retryable
                        or exc.status_code in {408, 429, 500, 502, 503, 504},
                    ) from exc
                used = (response.usage or {}).get("total_tokens")
                if not isinstance(used, int) or used < 1:
                    used = prompt_size + len(response.message.model_dump_json().encode())
                await budget.reconcile(reservation, used)
                step_code = await step(
                    "MODEL_CALL",
                    "调用模型",
                    {"model": provider.default_model},
                    {"finish_reason": response.finish_reason, "usage": response.usage},
                )
                messages.append(response.message)
                await message(response.message, step_code)
                if response.finish_reason == "length":
                    raise RuntimeFailure("BUDGET_EXCEEDED")
                calls = response.message.tool_calls or []
                if not calls:
                    if response.finish_reason != "stop" or not response.message.content:
                        raise RuntimeFailure("NO_PROGRESS")
                    output = redact(response.message.content, secret_values)
                    await step("RESULT_PROCESS", "保存结果", {}, {"text": output})
                    await self.queue.finish_run(lease, run_code, {"text": output})
                    return output, run_code
                for call in calls:
                    name = call.function.name
                    await progress.stage("TOOL_CALL", "执行工具")
                    try:
                        arguments = json.loads(call.function.arguments)
                        if not isinstance(arguments, dict):
                            raise ValueError("arguments must be an object")
                        if name in child_map:
                            if (
                                set(arguments) != {"prompt"}
                                or not isinstance(arguments["prompt"], str)
                                or not 0 < len(arguments["prompt"]) <= 30000
                            ):
                                raise ValueError("invalid child input")
                            result = {
                                "text": await child_call(child_map[name], arguments["prompt"])
                            }
                        elif name in tool_map:
                            async with self.queue.factory() as session:
                                await require_resource(
                                    session, lease.tenant_code, "TOOL", tool_map[name]["tool_code"]
                                )
                            async with asyncio.timeout(30):
                                result = await execute(
                                    tool_map[name]["connection_config"]["implementation"], arguments
                                )
                        elif name in mcp_map:
                            connector, tool = mcp_map[name]
                            Draft202012Validator(tool["input_schema"]).validate(arguments)
                            async with self.queue.factory() as session:
                                current = await require_resource(
                                    session, lease.tenant_code, "MCP", connector["mcp_code"]
                                )
                                if current.endpoint != connector["endpoint"]:
                                    raise ValueError("MCP endpoint changed; republish required")
                                token = (
                                    CredentialCipher(get_settings()).decrypt(
                                        current.token_ciphertext
                                    )
                                    if current.token_ciphertext
                                    else None
                                )
                            if token:
                                secret_values.append(token)
                            try:
                                result = await mcp_request(
                                    connector["endpoint"], token, tool["name"], arguments
                                )
                            except Exception:
                                raise ValueError("MCP tool execution failed") from None
                        else:
                            raise ValueError("unauthorized tool")
                    except (
                        ValueError,
                        TypeError,
                        TimeoutError,
                        HTTPException,
                        SchemaValidationError,
                    ) as exc:
                        await step(
                            "TOOL_CALL",
                            name,
                            {"arguments": call.function.arguments},
                            {},
                            "TOOL_FAILED",
                        )
                        raise RuntimeFailure("TOOL_FAILED") from exc
                    result = redact(result, secret_values)
                    tool_step = await step("TOOL_CALL", name, arguments, result)
                    item = ChatMessage(
                        role="tool",
                        content=json.dumps(result, ensure_ascii=False),
                        tool_call_id=call.id,
                    )
                    messages.append(item)
                    await message(item, tool_step)
            raise RuntimeFailure("MAX_STEPS")
        except LeaseLost:
            raise
        except BaseException as exc:
            if isinstance(exc, BaseExceptionGroup):
                # Recover meaningful bounded-runtime errors from TaskGroup failures.
                failures = list(exc.exceptions)
                failure = next((e for e in failures if isinstance(e, RuntimeFailure)), None)
            else:
                failure = exc if isinstance(exc, RuntimeFailure) else None
            reason = (
                failure.reason
                if failure
                else "TIMEOUT"
                if isinstance(exc, (TimeoutError, asyncio.CancelledError))
                else "MODEL_FAILED"
            )
            await self.queue.finish_run(lease, run_code, reason=reason)
            if isinstance(exc, asyncio.CancelledError):
                raise
            if failure:
                raise failure
            raise RuntimeFailure(reason) from exc
