import os
import uuid

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.credentials import CredentialCipher
from app.db.base import Base
from app.model_gateway.errors import ProviderNotConfiguredError
from app.models.model_profile import ModelProfile
from app.services.model_profiles import ModelProfileService, ProfileCreate, ProfileUpdate


@pytest_asyncio.fixture
async def database():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires isolated PostgreSQL TEST_DATABASE_URL")
    schema = "model_test_" + uuid.uuid4().hex
    admin = create_async_engine(url)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


def profile_input():
    return ProfileCreate(
        provider_code="deepseek",
        display_name="测试 DeepSeek",
        model_code="deepseek-flash",
        base_url="https://api.deepseek.com",
        api_key="database-only-secret",
    )


@pytest.mark.asyncio
async def test_database_ciphertext_restart_rotation_and_disable(database, tmp_path):
    settings = Settings(
        _env_file=None, auth_mode="local", credential_key_file=str(tmp_path / "key")
    )
    async with database() as session:
        service = ModelProfileService(session, CredentialCipher(settings))
        profile = await service.create(profile_input())
        code = profile.profile_code
        assert code.startswith("mdl_") and len(code) == 30
        row = await session.scalar(select(ModelProfile))
        ciphertext = row.api_key_ciphertext
        assert "database-only-secret" not in ciphertext
        assert "api_key" not in profile.model_dump()
        assert "ciphertext" not in profile.model_dump_json()
    # Independent service/cipher instance reproduces a process restart.
    async with database() as session:
        service = ModelProfileService(session, CredentialCipher(settings))
        resolved = await service.resolve(code)
        assert resolved.api_key == "database-only-secret"
        assert resolved.api_key not in repr(resolved)
        await service.update(code, ProfileUpdate(display_name="rename"))
        assert (await service.resolve(code)).api_key == "database-only-secret"
        await service.update(code, ProfileUpdate(api_key="rotated-model-key"))
        assert (await service.resolve(code)).api_key == "rotated-model-key"
        await service.update(code, ProfileUpdate(enabled=False))
        with pytest.raises(ProviderNotConfiguredError):
            await service.resolve(code)
    assert (tmp_path / "key").stat().st_mode & 0o777 == 0o600


def test_missing_or_wrong_master_key_does_not_silently_replace_it(tmp_path):
    path = tmp_path / "master"
    settings = Settings(_env_file=None, auth_mode="local", credential_key_file=str(path))
    cipher = CredentialCipher(settings)
    value = cipher.encrypt("secret")
    path.unlink()
    with pytest.raises(ProviderNotConfiguredError):
        CredentialCipher(settings).decrypt(value)
    assert not path.exists()
    wrong = CredentialCipher(
        Settings(_env_file=None, credential_encryption_key=Fernet.generate_key().decode())
    )
    with pytest.raises(ProviderNotConfiguredError):
        wrong.decrypt(value)


@pytest.mark.asyncio
async def test_configuration_api_encrypts_and_never_echoes_key(database, tmp_path, monkeypatch):
    from app.api import chat
    from app.api.main import create_app

    settings = Settings(
        _env_file=None, auth_mode="local", credential_key_file=str(tmp_path / "key")
    )
    from app.api import auth

    monkeypatch.setattr(auth, "session_factory", database)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(chat, "session_factory", database)
    monkeypatch.setattr(chat, "get_settings", lambda: settings)
    transport = httpx.ASGITransport(app=create_app(), client=("127.0.0.1", 1234))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        data = profile_input().model_dump(mode="json")
        data["api_key"] = "database-only-secret"
        response = await client.post("/api/v1/model-profiles", json=data)
        assert response.status_code == 201, response.text
        code = response.json()["profile_code"]
        for raw in [response.text, (await client.get("/api/v1/model-profiles")).text]:
            assert "database-only-secret" not in raw
            assert "api_key" not in raw
            assert "ciphertext" not in raw
        response = await client.patch(
            f"/api/v1/model-profiles/{code}", json={"display_name": "new"}
        )
        assert response.status_code == 200
        data["display_name"] = ""
        response = await client.post("/api/v1/model-profiles", json=data)
        assert response.status_code == 422
        assert "database-only-secret" not in response.text
        assert "input" not in response.text
        response = await client.post(
            "/api/v1/model-profiles",
            json={**data, "display_name": "valid", "base_url": "https://attacker.example/v1"},
        )
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_remote_configuration_write_requires_admin_token(database, tmp_path, monkeypatch):
    from app.api import auth, chat
    from app.api.main import create_app

    settings = Settings(
        _env_file=None, auth_mode="local", credential_key_file=str(tmp_path / "key")
    )
    monkeypatch.setattr(auth, "session_factory", database)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(chat, "session_factory", database)
    monkeypatch.setattr(chat, "get_settings", lambda: settings)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    transport = httpx.ASGITransport(app=create_app(), client=("198.51.100.1", 1234))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        data = profile_input().model_dump(mode="json")
        data["api_key"] = "test-model-key"
        assert (await client.post("/api/v1/model-profiles", json=data)).status_code == 403
        settings = Settings(
            _env_file=None,
            auth_mode="local",
            model_admin_token="admin-test-token",
            credential_key_file=str(tmp_path / "key"),
        )
        assert (
            await client.post(
                "/api/v1/model-profiles",
                json=data,
                headers={"Authorization": "Bearer admin-test-token"},
            )
        ).status_code == 201


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["deepseek", "glm", "minimax", "kimi"])
async def test_api_to_database_to_provider_stream_and_full_response(
    database, tmp_path, monkeypatch, provider
):
    from app.api import chat
    from app.api.main import create_app
    from app.model_gateway.gateway import ModelGateway

    settings = Settings(
        _env_file=None, auth_mode="local", credential_key_file=str(tmp_path / "key")
    )
    seen = []

    def upstream(request):
        body = __import__("json").loads(request.content)
        seen.append(body)
        assert request.headers["authorization"] == "Bearer stored-test-key"
        message = {"content": "connected", "reasoning_content": "preserved"}
        if body["stream"]:
            payload = {
                "choices": [{"delta": message, "finish_reason": "stop"}],
                "usage": {"total_tokens": 4},
            }
            data = "data: " + __import__("json").dumps(payload) + "\n\ndata: [DONE]\n\n"
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=data)
        return httpx.Response(
            200, json={"choices": [{"message": message, "finish_reason": "stop"}]}
        )

    from app.api import auth

    monkeypatch.setattr(auth, "session_factory", database)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(chat, "session_factory", database)
    monkeypatch.setattr(chat, "get_settings", lambda: settings)
    monkeypatch.setattr(
        chat, "get_model_gateway", lambda: ModelGateway(settings, httpx.MockTransport(upstream))
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(), client=("127.0.0.1", 1234)),
        base_url="http://test",
    ) as client:
        profile = await client.post(
            "/api/v1/model-profiles",
            json={
                "provider_code": provider,
                "display_name": provider,
                "model_code": getattr(settings, f"{provider}_model"),
                "base_url": getattr(settings, f"{provider}_base_url"),
                "api_key": "stored-test-key",
            },
        )
        assert profile.status_code == 201
        assert profile.json()["capabilities"]["reasoning_efforts"]
        data = {
            "profile_code": profile.json()["profile_code"],
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": "enabled",
            "reasoning_effort": "max",
        }
        full = await client.post("/api/v1/chat", json=data)
        assert full.status_code == 200 and full.json()["message"]["content"] == "connected"
        assert full.json()["message"]["reasoning_content"] == "preserved"
        stream = await client.post("/api/v1/chat/stream", json=data)
        assert stream.status_code == 200 and '"type": "done"' in stream.text
        assert "preserved" in stream.text and "stored-test-key" not in stream.text
        assert len(seen) == 2
        assert seen[0]["stream"] is False and seen[1]["stream"] is True
        assert all(body["reasoning_effort"] == "max" for body in seen)
