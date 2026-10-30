from datetime import UTC, datetime

CATALOG = {
    "text_stats": {
        "type": "object",
        "properties": {"text": {"type": "string", "maxLength": 30000}},
        "required": ["text"],
        "additionalProperties": False,
    },
    "utc_now": {"type": "object", "properties": {}, "additionalProperties": False},
}


async def execute(implementation: str, arguments: dict):
    if implementation == "utc_now" and arguments == {}:
        return {"utc": datetime.now(UTC).isoformat()}
    if implementation == "text_stats" and set(arguments) == {"text"}:
        text = arguments["text"]
        if isinstance(text, str) and len(text) <= 30000:
            return {
                "characters": len(text),
                "words": len(text.split()),
                "lines": len(text.splitlines()),
            }
    raise ValueError("工具参数不合法或实现未授权")
