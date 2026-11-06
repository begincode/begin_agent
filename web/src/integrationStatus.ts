const messages: Record<string, string> = {
  MCP_AUTH_FAILED: "鉴权失败，请检查已保存的凭据后重新检测。",
  MCP_RATE_LIMITED: "服务限流，请稍后重新检测。",
  MCP_TIMEOUT: "请求超时，请检查服务状态后重新检测。",
  MCP_NETWORK_ERROR: "无法连接服务，请检查地址与网络。",
  MCP_UNAVAILABLE: "服务暂不可用，请稍后重新检测。",
  MCP_TOOL_ERROR: "工具返回执行失败，请检查工具配置。",
  MCP_SCHEMA_INVALID: "工具目录或参数定义无效，请检查服务端实现。",
  MCP_PROTOCOL_ERROR: "协议响应无效，请检查 Streamable HTTP 接入方式。",
};
export function mcpFailure(code: string) {
  return messages[code] ?? "最近检测失败，请重新检测连接。";
}
