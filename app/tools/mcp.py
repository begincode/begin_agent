"""Official SDK transport; endpoint allow-list is set by the server operator."""

import asyncio
import json
from urllib.parse import urlsplit

import httpx2
from fastapi import HTTPException
from jsonschema import Draft202012Validator
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from app.core.config import get_settings


def validate_mcp_endpoint(endpoint):
    url = urlsplit(endpoint)
    settings = get_settings()
    local = settings.environment == "local" and url.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        not url.hostname
        or url.username
        or url.password
        or url.fragment
        or url.query
        or (url.scheme != "https" and not (local and url.scheme == "http"))
    ):
        raise HTTPException(422, "MCP 地址须使用 HTTPS；本机开发可使用 loopback HTTP")
    if url.hostname not in settings.mcp_allowed_hosts:
        raise HTTPException(422, "MCP 主机尚未加入服务器允许列表 AGENT_MCP_ALLOWED_HOSTS")
    return endpoint.rstrip("/")


def validate_schema(schema):
    if not isinstance(schema, dict) or len(json.dumps(schema)) > 100000:
        raise ValueError("MCP 工具参数 Schema 过大或格式不合法")
    pending = [schema]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if "$ref" in item and not str(item["$ref"]).startswith("#"):
                raise ValueError("MCP 工具 Schema 不允许远程引用")
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    Draft202012Validator.check_schema(schema)


async def mcp_request(endpoint, token=None, tool_name=None, arguments=None):
    validate_mcp_endpoint(endpoint)
    headers = {"Authorization": "Bearer " + token} if token else {}
    # SDK performs protocol negotiation and restricts cross-origin redirects.
    async with (
        asyncio.timeout(30),
        httpx2.AsyncClient(
            headers=headers, timeout=httpx2.Timeout(10, read=25), trust_env=False
        ) as http,
    ):
        transport = streamable_http_client(endpoint, http_client=http, max_sse_event_size=1_000_000)
        async with Client(transport) as client:
            if tool_name is not None:
                result = await client.call_tool(tool_name, arguments or {})
                if result.is_error:
                    raise ValueError("MCP 工具执行失败")
                return result.model_dump(mode="json", exclude_none=True)
            tools, cursor = [], None
            for _ in range(10):
                result = await client.list_tools(cursor=cursor)
                for tool in result.tools:
                    validate_schema(tool.input_schema)
                    tools.append(
                        {
                            "name": tool.name,
                            "description": tool.description or "",
                            "input_schema": tool.input_schema,
                        }
                    )
                if len(tools) > 100:
                    raise ValueError("MCP 工具数超过 100，请拆分连接")
                cursor = result.next_cursor
                if not cursor:
                    return tools
            raise ValueError("MCP 工具分页超过上限")
