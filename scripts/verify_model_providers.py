"""Real API checks using encrypted model profiles from the configured database.

Run from the repository root: python -m scripts.verify_model_providers --output /tmp/report.json
No fake credential fallback; missing profiles are BLOCKED, never PASSED.
"""

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.credentials import CredentialCipher
from app.db.session import engine, session_factory
from app.model_gateway.domain.chat import ChatCompletionRequest, ChatMessage, ProviderCode
from app.model_gateway.errors import ProviderNotConfiguredError, ProviderRequestError
from app.model_gateway.gateway import ModelGateway
from app.services.model_profiles import ModelProfileService

TOOL = {
    "type": "function",
    "function": {
        "name": "add_numbers",
        "description": "计算两个整数之和。必须调用此工具取得答案。",
        "parameters": {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
            "additionalProperties": False,
        },
    },
}


async def verify(args) -> int:
    settings = get_settings()
    gateway = ModelGateway(settings)
    report = {
        "checked_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "kind": "live_provider_api",
        "credential_source": "database",
        "results": [],
    }

    def save():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    async def check_profile(profile, config):
        result = {
            "provider": config.code.value,
            "profile_code": profile.profile_code,
            "model": config.default_model,
            "checks": [],
            "status": "RUNNING",
        }
        report["results"].append(result)
        save()
        history = [ChatMessage(role="user", content="记住验证码 Q7A9。现在只回复 OK。")]

        def request(messages, **extra):
            return ChatCompletionRequest(
                profile_code=profile.profile_code,
                messages=messages,
                max_tokens=args.max_tokens,
                **extra,
            )

        async def run_check(name, operation):
            entry = {"name": name, "status": "RUNNING"}
            result["checks"].append(entry)
            try:
                response = await operation()
                entry.update(
                    status="PASSED",
                    finish_reason=response.finish_reason,
                    usage=response.usage,
                    provider_request_id=response.provider_request_id,
                )
                return response
            except ProviderRequestError as exc:
                entry.update(status="FAILED", error_code=exc.code, http_status=exc.status_code)
            except AssertionError:
                entry.update(status="FAILED", error_code="RESPONSE_ASSERTION_FAILED")
            except Exception:
                entry.update(status="FAILED", error_code="UNEXPECTED_ERROR")
            finally:
                save()
            return None

        async def text_check():
            response = await gateway.generate(request(history), config)
            assert response.finish_reason == "stop" and "OK" in (response.message.content or "")
            return response

        response = await run_check("text", text_check)
        if response:

            async def multi_check():
                response2 = await gateway.generate(
                    request(
                        [
                            *history,
                            response.message,
                            ChatMessage(role="user", content="刚才的验证码是什么？只回复验证码。"),
                        ]
                    ),
                    config,
                )
                assert response2.finish_reason == "stop" and "Q7A9" in (
                    response2.message.content or ""
                )
                return response2

            await run_check("multi_turn_with_reasoning_replay", multi_check)
        else:
            result["checks"].append(
                {
                    "name": "multi_turn_with_reasoning_replay",
                    "status": "BLOCKED",
                    "reason": "text_check_failed",
                }
            )

        async def stream_check():
            events = [
                event
                async for event in gateway.stream(
                    request([ChatMessage(role="user", content="只回复 STREAM_OK。")]), config
                )
            ]
            assert events[-1].type == "done"
            streamed = "".join(event.delta for event in events if event.type == "content")
            response3 = events[-1].response
            assert response3.finish_reason == "stop" and "STREAM_OK" in streamed
            assert response3.message.content == streamed
            return response3

        await run_check("stream", stream_check)

        async def tool_check():
            messages = [
                ChatMessage(
                    role="user", content="必须调用 add_numbers 工具计算 2 加 3，不要自行计算。"
                )
            ]
            tool_response = await gateway.generate(request(messages, tools=[TOOL]), config)
            calls = tool_response.message.tool_calls
            assert tool_response.finish_reason == "tool_calls" and calls
            replay = [*messages, tool_response.message]
            for call in calls:
                assert call.function.name == "add_numbers"
                values = json.loads(call.function.arguments)
                assert type(values.get("a")) is int and type(values.get("b")) is int
                assert sorted([values["a"], values["b"]]) == [2, 3]
                replay.append(
                    ChatMessage(
                        role="tool",
                        tool_call_id=call.id,
                        content=json.dumps({"result": values["a"] + values["b"]}),
                    )
                )
            final = await gateway.generate(request(replay), config)
            assert final.finish_reason == "stop" and "5" in (final.message.content or "")
            return final

        await run_check("tool_call_and_result_round_trip", tool_check)
        result["status"] = (
            "PASSED" if all(c["status"] == "PASSED" for c in result["checks"]) else "FAILED"
        )
        save()

    try:
        async with session_factory() as session:
            service = ModelProfileService(session, CredentialCipher(settings))
            profiles = [
                p
                for p in await service.list()
                if p.enabled and (not args.profile_code or p.profile_code == args.profile_code)
            ]
        requested = (
            {p.provider_code for p in profiles}
            if args.profile_code and profiles
            else set(ProviderCode)
        )
        for code in ProviderCode:
            if code not in requested:
                continue
            found = [p for p in profiles if p.provider_code == code]
            if not found:
                report["results"].append(
                    {
                        "provider": code.value,
                        "status": "BLOCKED",
                        "reason": "no_enabled_database_profile",
                        "checks": [],
                    }
                )
                save()
                continue
            for profile in found:
                try:
                    async with session_factory() as session:
                        config = await ModelProfileService(
                            session, CredentialCipher(settings)
                        ).resolve(profile.profile_code)
                    await check_profile(profile, config)
                except ProviderNotConfiguredError:
                    report["results"].append(
                        {
                            "provider": code.value,
                            "profile_code": profile.profile_code,
                            "status": "BLOCKED",
                            "reason": "credential_unavailable",
                            "checks": [],
                        }
                    )
                    save()
    except (SQLAlchemyError, OSError):
        report["results"] = [
            {
                "provider": code.value,
                "status": "BLOCKED",
                "reason": "database_unavailable_or_not_migrated",
                "checks": [],
            }
            for code in ProviderCode
        ]
    finally:
        await engine.dispose()
    save()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["results"] and all(r["status"] == "PASSED" for r in report["results"]) else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path("/tmp/begin-agent-live-model-report.json")
    )
    parser.add_argument("--profile-code")
    parser.add_argument("--max-tokens", type=int, default=4096, choices=range(1024, 8193))
    raise SystemExit(asyncio.run(verify(parser.parse_args())))
