"""Read-only acceptance of existing tasks: model -> child -> MCP -> final artifact.

Never creates tasks or invokes a model. Use an external session token file (0600).
python -m scripts.verify_runtime_integrations --base-url http://127.0.0.1:18030 \
    --token-file /private/session --tenant-code ten_... \
    --task-code tsk_... --output /tmp/report.json
"""

import argparse
import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx


def summarize(detail, traces, snapshots, expected_sum=391, require_child=True, require_mcp=True):
    runs = detail.get("runs", [])
    steps = [step for trace in traces for step in trace.get("steps", [])]
    models = [s for s in steps if s["step_type"] == "MODEL_CALL"]
    mcps = [
        s
        for s in steps
        if s["step_type"] == "TOOL_CALL"
        and s.get("input", {}).get("source", {}).get("kind") == "MCP"
    ]
    text = (detail.get("output") or {}).get("text", "")
    receipts = sorted(
        set(re.findall(r"receipt_[0-9a-f]{16}", json.dumps([s.get("output", {}) for s in mcps])))
    )
    conditions = {
        "task_succeeded": detail["status"] == "SUCCEEDED",
        "model_calls_succeeded": bool(models) and all(s["status"] == "SUCCEEDED" for s in models),
        "provider_request_ids_recorded": bool(models)
        and all(s.get("output", {}).get("provider_request_id") for s in models),
        "successful_text_artifact": any(
            a["artifact_type"] == "TEXT" and a.get("content", {}).get("text") == text and text
            for a in detail.get("artifacts", [])
        ),
    }
    if require_child:
        conditions["child_succeeded"] = any(
            r.get("parent_run_code") and r["status"] == "SUCCEEDED" for r in runs
        )
    if require_mcp:
        conditions["mcp_step_succeeded"] = bool(mcps) and all(
            s["status"] == "SUCCEEDED" for s in mcps
        )
        conditions["server_receipt_in_final_output"] = bool(receipts) and any(
            r in text for r in receipts
        )
        conditions["expected_sum_in_final_output"] = bool(re.search(rf"\b{expected_sum}\b", text))
    return {
        "task_code": detail["task_code"],
        "task_status": detail["status"],
        "status": "PASSED" if all(conditions.values()) else "FAILED",
        "checks": conditions,
        "run_count": len(runs),
        "mcp_calls": len(mcps),
        "server_receipts": receipts,
        "live_preview_seen_before_terminal": any(
            s.get("status") == "RUNNING"
            and (s.get("preview", {}).get("content") or s.get("preview", {}).get("reasoning"))
            for s in snapshots
            if s.get("preview")
        ),
        "model_calls": [
            {
                "provider": s.get("input", {}).get("provider_code"),
                "model": s.get("input", {}).get("model"),
                "request_id": s.get("output", {}).get("provider_request_id"),
                "usage": s.get("output", {}).get("usage"),
                "diagnostic_code": s.get("output", {}).get("diagnostic_code"),
            }
            for s in models
        ],
    }


async def observe(client, code, seconds):
    snapshots = []
    async with asyncio.timeout(seconds):
        while True:
            async with client.stream("GET", f"api/v1/tasks/{code}/stream") as response:
                response.raise_for_status()
                event, data = "", []
                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data.append(line[5:].lstrip())
                    elif not line and data:
                        value = json.loads("\n".join(data))
                        if event == "snapshot":
                            snapshots.append(value)
                        elif event == "complete":
                            return snapshots
                        elif event in {"access_error", "unavailable"}:
                            raise ValueError("task_stream_unavailable")
                        event, data = "", []


async def verify(args):
    url = urlsplit(args.base_url)
    if (
        (
            url.scheme != "https"
            and not (url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost", "::1"})
        )
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError("invalid_application_origin")
    token = args.token_file.read_text().strip()
    report = {
        "checked_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "kind": "existing_task_runtime_acceptance",
        "creates_tasks": False,
        "results": [],
    }
    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/") + "/",
        trust_env=False,
        follow_redirects=False,
        headers={"Authorization": "Bearer " + token, "X-Tenant-Code": args.tenant_code},
        timeout=httpx.Timeout(30, connect=5),
    ) as client:
        for code in args.task_code:
            try:
                snapshots = await observe(client, code, args.wait_seconds)
                response = await client.get(f"api/v1/tasks/{code}")
                response.raise_for_status()
                detail = response.json()
                traces = []
                for run in detail.get("runs", []):
                    response = await client.get("api/v1/runs/" + run["run_code"])
                    response.raise_for_status()
                    traces.append(response.json())
                result = summarize(detail, traces, snapshots, args.expected_sum)
            except httpx.HTTPStatusError as exc:
                result = {
                    "task_code": code,
                    "status": "FAILED",
                    "reason": "HTTP_" + str(exc.response.status_code),
                }
            except Exception as exc:
                result = {"task_code": code, "status": "FAILED", "reason": type(exc).__name__}
            report["results"].append(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(r["status"] == "PASSED" for r in report["results"]) else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--tenant-code", required=True)
    parser.add_argument("--task-code", action="append", required=True)
    parser.add_argument("--expected-sum", type=int, default=391)
    parser.add_argument("--wait-seconds", type=int, default=180, choices=range(1, 601))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        return asyncio.run(verify(args))
    except Exception as exc:
        # A URL/token/request error must never print its untrusted message.
        print(json.dumps({"status": "FAILED", "reason": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
