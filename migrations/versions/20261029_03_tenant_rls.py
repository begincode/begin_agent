"""Enforce tenant RLS and immutable published configurations / trace entries."""

from alembic import op

revision = "20261029_03"
down_revision = "20261029_02"
branch_labels = None
depends_on = None

SCOPED = (
    "model_profile",
    "agent",
    "agent_version",
    "agent_relation",
    "tool_definition",
    "agent_tool_binding",
    "agent_task",
    "agent_run",
    "run_step",
    "run_message",
    "task_artifact",
)
IMMUTABLE = ("agent_version", "agent_tool_binding", "run_step", "run_message", "task_artifact")


def upgrade():
    for table in SCOPED:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'''CREATE POLICY tenant_isolation ON "{table}"
            USING (tenant_code = current_setting('app.tenant_code', true))
            WITH CHECK (tenant_code = current_setting('app.tenant_code', true))''')
    op.execute("""CREATE FUNCTION reject_immutable_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Published configuration and trace rows are immutable'; END; $$""")
    for table in IMMUTABLE:
        op.execute(f'''CREATE TRIGGER immutable_row BEFORE UPDATE OR DELETE ON "{table}"
            FOR EACH ROW EXECUTE FUNCTION reject_immutable_change()''')


def downgrade():
    for table in IMMUTABLE:
        op.execute(f'DROP TRIGGER immutable_row ON "{table}"')
    op.execute("DROP FUNCTION reject_immutable_change()")
    for table in SCOPED:
        op.execute(f'DROP POLICY tenant_isolation ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
