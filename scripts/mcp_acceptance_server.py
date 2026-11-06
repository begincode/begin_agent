"""Loopback-only authenticated MCP service for live Agent integration acceptance.

python -m scripts.mcp_acceptance_server --token-file /private/token --port 18033
No tool has business side effects. Output contains only fixed arithmetic test data and receipts.
"""

import argparse
import json
import secrets
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import uvicorn
from mcp.server.mcpserver import MCPServer
from starlette.responses import JSONResponse


def create_server(token, event_file=None, control_file=None):
    if len(token) < 24 or any(char.isspace() for char in token):
        raise ValueError("验收 Token 至少 24 位且不能包含空白")
    server = MCPServer("begin-agent-live-acceptance")
    calls = []

    @server.tool()
    def acceptance_add(a: int, b: int) -> dict:
        """Read-only arithmetic acceptance tool. Return the sum and a fresh server receipt.

        Call this tool to obtain the receipt; a language model cannot infer it in advance.
        """
        if not (-1000000 <= a <= 1000000 and -1000000 <= b <= 1000000):
            raise ValueError("验收数值超出范围")
        result = {"sum": a + b, "receipt": "receipt_" + secrets.token_hex(8)}
        event = {
            "tool": "acceptance_add",
            "result": result,
            "at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        }
        calls.append(event)
        if event_file:
            with Path(event_file).open("a") as output:
                output.write(json.dumps(event) + "\n")
        return result

    application = server.streamable_http_app(stateless_http=True, json_response=True)

    class Authenticated:
        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                status = 0
                if control_file and Path(control_file).is_file():
                    control = json.loads(Path(control_file).read_text())
                    status = control.get("http_status", 0)
                    if status not in {0, 401, 403, 429, 503}:
                        status = 503
                headers = dict(scope["headers"])
                if not secrets.compare_digest(
                    headers.get(b"authorization", b""),
                    ("Bearer " + token).encode(),
                ):
                    status = 401
                if status:
                    # Deliberately contains no token, submitted arguments or arbitrary error text.
                    return await JSONResponse(
                        {"error": "acceptance_service_unavailable"},
                        status_code=status,
                    )(scope, receive, send)
            await application(scope, receive, send)

    return Authenticated(), calls


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--events", type=Path)
    parser.add_argument("--control", type=Path)
    parser.add_argument("--port", type=int, default=18033)
    args = parser.parse_args()
    application, _ = create_server(args.token_file.read_text().strip(), args.events, args.control)
    uvicorn.run(
        application, host="127.0.0.1", port=args.port, access_log=False, log_level="warning"
    )


if __name__ == "__main__":
    main()
