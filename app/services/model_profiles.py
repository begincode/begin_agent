import secrets
import time
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr, computed_field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.credentials import CredentialCipher
from app.core.identity import tenant_context
from app.model_gateway.capabilities import GenerationCapabilities, generation_capabilities
from app.model_gateway.domain.chat import ProviderCode
from app.model_gateway.errors import InvalidModelRequest, ProviderNotConfiguredError
from app.model_gateway.registry import ProviderConfig, validate_endpoint
from app.models.model_profile import ModelProfile
from app.services.resources import resource_enabled


class ProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_code: ProviderCode
    display_name: str = Field(min_length=1, max_length=128)
    model_code: str = Field(min_length=1, max_length=128)
    base_url: str = Field(min_length=1, max_length=256)
    api_key: SecretStr = Field(min_length=1, max_length=4096)
    enabled: bool = True

    @field_validator("display_name", "model_code", "base_url")
    @classmethod
    def strip_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("字段不能为空")
        return value.strip()


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    model_code: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = Field(default=None, min_length=1, max_length=256)
    api_key: SecretStr | None = Field(default=None, min_length=1, max_length=4096)
    enabled: bool | None = None


class ProfileInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    profile_code: str
    provider_code: ProviderCode
    display_name: str
    model_code: str
    base_url: str
    enabled: bool
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def capabilities(self) -> GenerationCapabilities:
        return generation_capabilities(self.provider_code, self.model_code)


def new_profile_code() -> str:
    # 48-bit millisecond timestamp + 80 random bits, encoded as a Crockford ULID.
    number = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    return "mdl_" + "".join(alphabet[(number >> shift) & 31] for shift in range(125, -1, -5))


class ModelProfileService:
    def __init__(
        self, session: AsyncSession, cipher: CredentialCipher, tenant_code: str | None = None
    ) -> None:
        self.session = session
        self.cipher = cipher
        self.tenant_code = tenant_code or tenant_context.get()

    async def list(self) -> list[ProfileInfo]:
        rows = (
            await self.session.scalars(
                select(ModelProfile)
                .where(ModelProfile.tenant_code == self.tenant_code)
                .order_by(ModelProfile.created_at)
            )
        ).all()
        return [ProfileInfo.model_validate(row) for row in rows]

    async def _get(self, code: str) -> ModelProfile:
        row = await self.session.scalar(
            select(ModelProfile).where(
                ModelProfile.profile_code == code, ModelProfile.tenant_code == self.tenant_code
            )
        )
        if row is None:
            raise ProviderNotConfiguredError("模型配置不存在")
        return row

    async def create(self, data: ProfileCreate) -> ProfileInfo:
        endpoint = validate_endpoint(data.provider_code, data.base_url)
        secret = data.api_key.get_secret_value().strip()
        if not secret or any(character.isspace() for character in secret):
            raise InvalidModelRequest("API Key 不能为空")
        row = ModelProfile(
            tenant_code=self.tenant_code,
            profile_code=new_profile_code(),
            provider_code=data.provider_code,
            display_name=data.display_name,
            model_code=data.model_code,
            base_url=endpoint,
            api_key_ciphertext=self.cipher.encrypt(secret),
            enabled=data.enabled,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return ProfileInfo.model_validate(row)

    async def update(self, code: str, data: ProfileUpdate) -> ProfileInfo:
        row = await self._get(code)
        changes = data.model_dump(exclude_unset=True, exclude_none=True, exclude={"api_key"})
        if "base_url" in changes:
            changes["base_url"] = validate_endpoint(ProviderCode(row.provider_code), data.base_url)
        for key in ("display_name", "model_code"):
            if key in changes:
                changes[key] = changes[key].strip()
                if not changes[key]:
                    raise InvalidModelRequest("字段不能为空")
        if data.api_key is not None:
            value = data.api_key.get_secret_value().strip()
            if not value or any(character.isspace() for character in value):
                raise InvalidModelRequest("API Key 不能为空；不修改密钥请省略该字段")
            changes["api_key_ciphertext"] = self.cipher.encrypt(value)
        for key, value in changes.items():
            setattr(row, key, value)
        await self.session.commit()
        await self.session.refresh(row)
        return ProfileInfo.model_validate(row)

    async def resolve(self, code: str) -> ProviderConfig:
        row = await self._get(code)
        if not await resource_enabled(self.session, self.tenant_code, "MODEL", code):
            raise ProviderNotConfiguredError("模型已被租户撤销授权")
        if not row.enabled:
            raise ProviderNotConfiguredError("模型配置已停用")
        provider = ProviderCode(row.provider_code)
        return ProviderConfig(
            code=provider,
            base_url=validate_endpoint(provider, row.base_url),
            default_model=row.model_code,
            api_key=self.cipher.decrypt(row.api_key_ciphertext),
        )
