import tempfile
from pathlib import Path


def prepare_directories(settings):
    for purpose, value in (("日志", settings.log_dir), ("持久数据", settings.data_dir)):
        if not value:
            continue
        path = Path(value)
        try:
            path.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=path) as handle:
                handle.write(b"directory-check")
                handle.flush()
                handle.seek(0)
                assert handle.read() == b"directory-check"
        except OSError as exc:
            raise ValueError(f"{purpose}目录不可读写：{path}") from exc
