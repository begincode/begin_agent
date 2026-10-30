"""Exercise saved profiles through the running application, without reading API keys.

Run: python -m scripts.verify_generation_controls --output docs/generation-live-verification.json
"""

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx


async def verify(base_url: str) -> dict:
    async with httpx.AsyncClient(base_url=base_url, timeout=180, trust_env=False) as client:
        response = await client.get("/api/v1/model-profiles")
        response.raise_for_status()
        profiles = response.json()

        async def check_profile(profile):
            caps = profile["capabilities"]
            result = {
                "provider": profile["provider_code"],
                "model": profile["model_code"],
                "profile_code": profile["profile_code"],
                "checks": [],
            }
            cases = [
                ("enabled", effort, i % 2 == 0)
                for i, effort in enumerate(caps["reasoning_efforts"])
            ]
            if not cases:
                cases = [("auto", None, True), ("auto", None, False)]
            if "disabled" in caps["thinking_modes"]:
                cases += [("disabled", None, False), ("disabled", None, True)]
            for thinking, effort, streaming in cases:
                check = {"thinking": thinking, "reasoning_effort": effort, "streaming": streaming}
                result["checks"].append(check)
                body = {
                    "profile_code": profile["profile_code"],
                    "messages": [
                        {"role": "user", "content": "计算 17×23，只在最终回答给出数字结果。"}
                    ],
                    "thinking": thinking,
                    "max_tokens": 4096,
                }
                if effort:
                    body["reasoning_effort"] = effort
                try:
                    if streaming:
                        contents, reasoning, final = [], [], None
                        async with client.stream(
                            "POST", "/api/v1/chat/stream", json=body
                        ) as response:
                            response.raise_for_status()
                            async for line in response.aiter_lines():
                                if not line.startswith("data:"):
                                    continue
                                event = json.loads(line[5:])
                                assert event["type"] != "error", event.get("code")
                                if event["type"] == "reasoning":
                                    reasoning.append(event["delta"])
                                if event["type"] == "content":
                                    contents.append(event["delta"])
                                if event["type"] == "done":
                                    final = event["response"]
                        assert final
                        assert "".join(contents) == (final["message"]["content"] or "")
                        assert "".join(reasoning) == (
                            final["message"].get("reasoning_content") or ""
                        )
                        check.update(content_events=len(contents), reasoning_events=len(reasoning))
                    else:
                        response = await client.post("/api/v1/chat", json=body)
                        response.raise_for_status()
                        final = response.json()
                    assert final["finish_reason"] == "stop" and "391" in final["message"]["content"]
                    reasoning_text = final["message"].get("reasoning_content") or ""
                    assert bool(reasoning_text) == (thinking != "disabled")
                    check.update(
                        status="PASSED",
                        reasoning_characters=len(reasoning_text),
                        usage=final.get("usage"),
                        provider_request_id=final.get("provider_request_id"),
                    )
                except httpx.HTTPStatusError as exc:
                    check.update(status="FAILED", http_status=exc.response.status_code)
                except (AssertionError, KeyError, ValueError, httpx.HTTPError) as exc:
                    check.update(status="FAILED", error_type=type(exc).__name__)
            result["status"] = (
                "PASSED" if all(c["status"] == "PASSED" for c in result["checks"]) else "FAILED"
            )
            return result

        results = await asyncio.gather(*(check_profile(p) for p in profiles if p["enabled"]))
        for provider in ["deepseek", "glm", "minimax", "kimi"]:
            if not any(r["provider"] == provider for r in results):
                results.append(
                    {
                        "provider": provider,
                        "status": "BLOCKED",
                        "reason": "no_enabled_database_profile",
                    }
                )
        return {
            "checked_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            "kind": "live_application_generation_controls",
            "credential_source": "encrypted_database_profiles_via_application",
            "results": results,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18011")
    parser.add_argument(
        "--output", type=Path, default=Path("/tmp/begin-agent-generation-report.json")
    )
    args = parser.parse_args()
    report = asyncio.run(verify(args.base_url))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if all(r["status"] == "PASSED" for r in report["results"]) else 2)
