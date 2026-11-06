import pytest

from scripts.deployment_preflight import runtime_checks
from scripts.verify_http_capacity import validate_target


def test_production_target_requires_python312_and_linux():
    checks = runtime_checks((3, 12, 13), "Linux")
    assert all(c["passed"] for c in checks)
    assert not all(c["passed"] for c in runtime_checks((3, 14, 0), "Darwin"))
    local = runtime_checks((3, 12, 13), "Darwin", True)
    assert not local[1]["passed"] and not local[1]["required"]


@pytest.mark.parametrize(
    "url",
    [
        "https://remote.example",
        "http://example.com",
        "http://user:pass@127.0.0.1:18029",
        "http://localhost/?token=secret",
    ],
)
def test_load_probe_rejects_remote_or_credential_urls(url):
    with pytest.raises(ValueError):
        validate_target(url, 100, 10)


def test_load_probe_is_bounded():
    validate_target("http://127.0.0.1:18033", 100, 10)
    with pytest.raises(ValueError):
        validate_target("http://localhost", 2001, 10)
    with pytest.raises(ValueError):
        validate_target("http://localhost", 100, 101)
