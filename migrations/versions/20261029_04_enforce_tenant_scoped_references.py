"""Enforce tenant-scoped references; build unique targets before foreign keys."""

from alembic import op

revision = "20261029_04"
down_revision = "20261029_03"
branch_labels = None
depends_on = None

UNIQUES = (
    ("agent_run", "run_code"),
    ("model_profile", "profile_code"),
    ("run_step", "step_code"),
    ("tool_definition", "tool_code"),
)
REFERENCES = (
    ("agent_run", "agent_version_code", "agent_version", "version_code"),
    ("agent_run", "task_code", "agent_task", "task_code"),
    ("agent_run", "parent_run_code", "agent_run", "run_code"),
    ("agent_run", "root_run_code", "agent_run", "run_code"),
    ("agent_task", "agent_version_code", "agent_version", "version_code"),
    ("agent_tool_binding", "agent_version_code", "agent_version", "version_code"),
    ("agent_tool_binding", "tool_code", "tool_definition", "tool_code"),
    ("agent_version", "profile_code", "model_profile", "profile_code"),
    ("run_message", "step_code", "run_step", "step_code"),
    ("run_message", "run_code", "agent_run", "run_code"),
    ("run_step", "run_code", "agent_run", "run_code"),
    ("task_artifact", "run_code", "agent_run", "run_code"),
    ("task_artifact", "task_code", "agent_task", "task_code"),
)


def upgrade():
    for table, column in UNIQUES:
        op.create_unique_constraint(f"uk_{table}_tenant_{column}", table, ["tenant_code", column])
    for table, column, target, target_column in REFERENCES:
        op.create_foreign_key(
            f"fk_{table}_{column}",
            table,
            target,
            ["tenant_code", column],
            ["tenant_code", target_column],
        )


def downgrade():
    for table, column, _, _ in reversed(REFERENCES):
        op.drop_constraint(f"fk_{table}_{column}", table, type_="foreignkey")
    for table, column in reversed(UNIQUES):
        op.drop_constraint(f"uk_{table}_tenant_{column}", table, type_="unique")
