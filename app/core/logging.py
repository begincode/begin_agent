import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


class RedactLoginQuery(logging.Filter):
    def filter(self, record):
        if isinstance(record.args, tuple) and len(record.args) == 5:
            args = list(record.args)
            if "/sso/" in str(args[2]):
                args[2] = str(args[2]).split("?", 1)[0]
                record.args = tuple(args)
        return True


def configure_logging(level: str, log_dir: str | None = None) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("uvicorn.access").addFilter(RedactLoginQuery())
    # HTTP client INFO logs include callback / credential-bearing URLs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    if log_dir:
        path = Path(log_dir)
        path.mkdir(parents=True, exist_ok=True)
        target = str(path / "agent.log")
        root = logging.getLogger()
        if not any(getattr(h, "baseFilename", None) == target for h in root.handlers):
            handler = RotatingFileHandler(target, maxBytes=10_000_000, backupCount=10)
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
            )
            root.addHandler(handler)
