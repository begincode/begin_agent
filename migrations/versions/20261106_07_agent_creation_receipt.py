"""Persist scoped, immutable Agent creation receipts without changing existing entities."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261106_07"
down_revision = "20261029_06"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_creation_receipt",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("tenant_code", sa.String(40), nullable=False),
        sa.Column("receipt_code", sa.String(40), nullable=False, unique=True),
        sa.Column("created_by", sa.String(40), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("agent_code", sa.String(40), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("tenant_code", "created_by", "request_hash"),
        sa.ForeignKeyConstraint(
            ["tenant_code", "agent_code"], ["agent.tenant_code", "agent.agent_code"]
        ),
        sa.ForeignKeyConstraint(["created_by"], ["platform_user.user_code"]),
    )
    op.create_index(
        "ix_agent_creation_receipt_tenant_code", "agent_creation_receipt", ["tenant_code"]
    )
    op.execute("ALTER TABLE agent_creation_receipt ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agent_creation_receipt FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY tenant_isolation ON agent_creation_receipt
        USING (tenant_code = current_setting('app.tenant_code', true))
        WITH CHECK (tenant_code = current_setting('app.tenant_code', true))""")
    op.execute("""CREATE TRIGGER immutable_row BEFORE UPDATE OR DELETE ON agent_creation_receipt
        FOR EACH ROW EXECUTE FUNCTION reject_immutable_change()""")


def downgrade():
    op.drop_table("agent_creation_receipt")
