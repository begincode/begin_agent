"""Safe MCP error categories; never expose remote exception messages or credentials."""

import httpx2

MESSAGES = {
    "MCP_AUTH_FAILED": "MCP 鉴权失败，请检查服务器凭据",
    "MCP_RATE_LIMITED": "MCP 服务限流，请稍后重试",
    "MCP_TIMEOUT": "MCP 请求超时",
    "MCP_NETWORK_ERROR": "MCP 服务连接失败",
    "MCP_UNAVAILABLE": "MCP 服务暂不可用",
    "MCP_TOOL_ERROR": "MCP 工具返回执行失败",
    "MCP_SCHEMA_INVALID": "MCP 工具目录或参数定义无效",
    "MCP_PROTOCOL_ERROR": "MCP 协议响应无效",
}


class McpRequestError(ValueError):
    def __init__(self, code, status_code=None):
        self.code = code if code in MESSAGES else "MCP_PROTOCOL_ERROR"
        self.status_code = status_code
        super().__init__(MESSAGES[self.code])


def classify_mcp_error(error, http_status=None):
    if isinstance(http_status, int) and http_status >= 400:
        code = (
            "MCP_AUTH_FAILED"
            if http_status in {401, 403}
            else "MCP_RATE_LIMITED"
            if http_status == 429
            else "MCP_UNAVAILABLE"
            if http_status >= 500
            else "MCP_PROTOCOL_ERROR"
        )
        return McpRequestError(code, http_status)
    pending = [error]
    leaves = []
    while pending:
        item = pending.pop()
        if isinstance(item, BaseExceptionGroup):
            pending.extend(item.exceptions)
        else:
            leaves.append(item)
    for item in leaves:
        if isinstance(item, McpRequestError):
            return item
    for item in leaves:
        if isinstance(item, httpx2.HTTPStatusError):
            status = item.response.status_code
            code = (
                "MCP_AUTH_FAILED"
                if status in {401, 403}
                else "MCP_RATE_LIMITED"
                if status == 429
                else "MCP_UNAVAILABLE"
                if status >= 500
                else "MCP_PROTOCOL_ERROR"
            )
            return McpRequestError(code, status)
    if any(isinstance(item, (TimeoutError, httpx2.TimeoutException)) for item in leaves):
        return McpRequestError("MCP_TIMEOUT")
    if any(isinstance(item, httpx2.TransportError) for item in leaves):
        return McpRequestError("MCP_NETWORK_ERROR")
    return McpRequestError("MCP_PROTOCOL_ERROR")
