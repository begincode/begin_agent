from datetime import UTC, datetime

CATALOG = {
    "text_stats": {
        "type": "object",
        "properties": {"text": {"type": "string", "maxLength": 30000}},
        "required": ["text"],
        "additionalProperties": False,
    },
    "export_text_file": {
        "type": "object",
        "properties": {
            "file_name": {"type": "string", "minLength": 1, "maxLength": 128},
            "content": {"type": "string", "minLength": 1, "maxLength": 30000},
        },
        "required": ["file_name", "content"],
        "additionalProperties": False,
    },
    "utc_now": {"type": "object", "properties": {}, "additionalProperties": False},
}


async def execute(implementation: str, arguments: dict):
    if implementation == "export_text_file" and set(arguments) == {"file_name", "content"}:
        import base64

        from app.domain.platform import TaskAttachment
        from app.services.task_inputs import parse_files

        files = parse_files(
            [
                TaskAttachment(
                    file_name=arguments["file_name"],
                    content_base64=base64.b64encode(arguments["content"].encode()).decode(),
                )
            ]
        )
        return {"generated_file": files[0]}
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
