from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Identity, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.identity import LOCAL_TENANT
from app.db.base import Base


class ModelProfile(Base):
    __tablename__ = "model_profile"
    __table_args__ = (UniqueConstraint("tenant_code", "profile_code"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    tenant_code: Mapped[str] = mapped_column(
        String(40), nullable=False, default=LOCAL_TENANT, index=True
    )
    profile_code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    provider_code: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    model_code: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str] = mapped_column(String(256), nullable=False)
    api_key_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
