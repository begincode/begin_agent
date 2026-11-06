"""Database worker observation and tenant-scoped diagnostic acknowledgments."""

import sqlalchemy as sa
from alembic import op

revision = "20261106_08"
down_revision = "20261106_07"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "worker_pulse",
        sa.Column("instance_code", sa.String(40), primary_key=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_worker_pulse_heartbeat_at", "worker_pulse", ["heartbeat_at"])
    op.create_table(
        "diagnostic_acknowledgment",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("tenant_code", sa.String(40), nullable=False),
        sa.Column("user_code", sa.String(40), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("tenant_code", "user_code", "fingerprint"),
        sa.ForeignKeyConstraint(["tenant_code"], ["tenant.tenant_code"]),
        sa.ForeignKeyConstraint(["user_code"], ["platform_user.user_code"]),
    )
    op.create_index(
        "ix_diagnostic_acknowledgment_tenant_code", "diagnostic_acknowledgment", ["tenant_code"]
    )
    op.execute("ALTER TABLE diagnostic_acknowledgment ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE diagnostic_acknowledgment FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY tenant_isolation ON diagnostic_acknowledgment
        USING (tenant_code = current_setting('app.tenant_code', true))
        WITH CHECK (tenant_code = current_setting('app.tenant_code', true))""")


def downgrade():
    op.drop_table("diagnostic_acknowledgment")
    op.drop_table("worker_pulse")
