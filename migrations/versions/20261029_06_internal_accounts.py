"""Built-in password accounts and revocable opaque sessions; preserve legacy users."""

import sqlalchemy as sa
from alembic import op

revision = "20261029_06"
down_revision = "20261029_05"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("platform_user", sa.Column("username", sa.String(32), nullable=True))
    op.add_column("platform_user", sa.Column("password_hash", sa.Text(), nullable=True))
    op.add_column(
        "platform_user",
        sa.Column("platform_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("platform_user", "platform_admin", server_default=None)
    op.create_unique_constraint("uq_platform_user_username", "platform_user", ["username"])
    op.create_table(
        "account_session",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_code", sa.String(40), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_account_session_user_code", "account_session", ["user_code"])
    op.create_table(
        "login_bucket",
        sa.Column("bucket_key", sa.String(64), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("login_bucket")
    op.drop_table("account_session")
    op.drop_constraint("uq_platform_user_username", "platform_user", type_="unique")
    for column in ("platform_admin", "password_hash", "username"):
        op.drop_column("platform_user", column)
