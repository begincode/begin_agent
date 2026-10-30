"""Isolated UI acceptance fixture. Build web first, then open http://127.0.0.1:18012/.

No database or provider credentials are used. Commands in the message box:
ui-audit-slow / ui-audit-error / ui-audit-stream-error / ui-audit-length.
Other messages produce Markdown with a wide table, long code and a long fence label.
"""

import json
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "app/static"
CAPS = {
    "thinking_modes": ["auto", "enabled", "disabled"],
    "reasoning_efforts": ["low", "high", "max"],
    "streaming": True,
    "reasoning_content": True,
}
BASE = {
    "provider_code": "deepseek",
    "model_code": "deepseek-flash",
    "base_url": "https://api.deepseek.com",
    "enabled": True,
    "capabilities": CAPS,
}
PROFILES = [
    {**BASE, "profile_code": "ui_normal", "display_name": "UI 验收模型"},
    {**BASE, "profile_code": "ui_long", "display_name": "LongModelProfile" * 8},
    {**BASE, "profile_code": "ui_disabled", "display_name": "已停用的模型", "enabled": False},
]
MARKDOWN = (
    """# 页面验收

## 功能与格式
- 支持 **Markdown**、`profile_code` 和嵌套列表
  - 子项目
1. 发送消息
2. 检查结果

> 这是一条测试引用，不调用外部模型。

| 模型名称 | 接口协议 | 流式输出 | 思考信息 | 工具调用 | 验证状态 |
| --- | --- | --- | --- | --- | --- |
| UI fixture | Chat Completions | 支持 | 支持 | 支持 | 测试数据 |

```python
endpoint = "https://example.test/enterprise-agent-platform/model-gateway/providers/deepseek/chat/completions?stream=true&reasoning_effort=high"
```

```"""
    + ("unknown_language_" * 18)
    + """
copy_this_exactly = "中文 <tag> & symbols"
```
"""
)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, *_args):
        pass

    def send_json(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/v1/models":
            self.send_json(
                [
                    {
                        "code": "deepseek",
                        "name": "DeepSeek",
                        "default_model": "deepseek-flash",
                        "base_url": BASE["base_url"],
                    }
                ]
            )
        elif self.path == "/api/v1/model-profiles":
            self.send_json(PROFILES)
        else:
            super().do_GET()

    def do_PATCH(self):
        self.do_POST()

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
        if self.path.endswith("/verify"):
            time.sleep(2)
            self.send_json({"passed": True, "finish_reason": "stop"})
            return
        if self.path.startswith("/api/v1/model-profiles"):
            time.sleep(2)
            code = self.path.rsplit("/", 1)[-1]
            profile = next(
                (p for p in PROFILES if p["profile_code"] == code),
                {**BASE, "profile_code": "ui_created"},
            )
            profile.update({k: v for k, v in data.items() if k in BASE or k == "display_name"})
            if profile not in PROFILES:
                PROFILES.append(profile)
            self.send_json(profile)
            return
        prompt = data.get("messages", [{}])[-1].get("content", "")
        if prompt == "ui-audit-error":
            self.send_json({"detail": "模拟连接失败，请重试"}, 503)
            return
        truncated = prompt == "ui-audit-length"
        content = "这是被截断的回复" if truncated else MARKDOWN
        reasoning = "正在检查格式、布局和控件。" if data.get("thinking") != "disabled" else ""
        result = {
            "message": {"role": "assistant", "content": content, "reasoning_content": reasoning},
            "finish_reason": "length" if truncated else "stop",
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 100,
                "reasoning_tokens": 10,
                "total_tokens": 120,
            },
        }
        if self.path == "/api/v1/chat":
            self.send_json(result)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def emit(event):
            self.wfile.write(("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode())
            self.wfile.flush()

        try:
            emit({"type": "start"})
            if reasoning:
                emit({"type": "reasoning", "delta": reasoning})
            if prompt == "ui-audit-slow":
                emit({"type": "content", "delta": "这部分内容应在停止后保留。\n"})
                for _ in range(80):
                    time.sleep(0.25)
                    emit({"type": "content", "delta": "仍在生成…\n"})
                return
            if prompt == "ui-audit-stream-error":
                emit({"type": "content", "delta": "失败前已返回的内容。"})
                emit({"type": "error", "message": "模拟上游失败", "code": "UI_TEST_FAILURE"})
                return
            for start in range(0, len(content), 90):
                emit({"type": "content", "delta": content[start : start + 90]})
                time.sleep(0.02)
            emit({"type": "done", "response": result})
        except (BrokenPipeError, ConnectionResetError):
            pass


if __name__ == "__main__":
    if not (ROOT / "index.html").exists():
        raise SystemExit("Build the frontend first: npm --prefix web run build")
    print(
        "Isolated UI fixture: http://127.0.0.1:18012/ (no database / no provider API)", flush=True
    )
    ThreadingHTTPServer(("127.0.0.1", 18012), Handler).serve_forever()
