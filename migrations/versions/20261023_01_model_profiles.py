"""Database-backed encrypted model configurations."""

import sqlalchemy as sa
from alembic import op

revision = "20261023_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_profile",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("profile_code", sa.String(40), nullable=False, unique=True),
        sa.Column("provider_code", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("model_code", sa.String(128), nullable=False),
        sa.Column("base_url", sa.String(256), nullable=False),
        sa.Column("api_key_ciphertext", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("model_profile")
