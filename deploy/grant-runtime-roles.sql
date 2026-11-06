-- Execute with the migration role after alembic upgrade head.
-- psql -v api_role=YOUR_API_ROLE -v worker_role=YOUR_WORKER_ROLE -f this-file
-- Roles must already exist; credentials are managed outside this file.
\set ON_ERROR_STOP on
BEGIN;
ALTER ROLE :"api_role" NOSUPERUSER NOBYPASSRLS;
ALTER ROLE :"worker_role" NOSUPERUSER BYPASSRLS;
GRANT USAGE ON SCHEMA public TO :"api_role", :"worker_role";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO :"api_role";
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO :"api_role", :"worker_role";
REVOKE INSERT, UPDATE, DELETE ON worker_pulse FROM :"api_role";
REVOKE UPDATE, DELETE ON agent_version, run_step, run_message, task_artifact, agent_creation_receipt FROM :"api_role";
GRANT SELECT ON alembic_version, tenant, agent, agent_version, agent_relation, model_profile,
  tool_definition, mcp_connection, skill_definition, resource_grant TO :"worker_role";
GRANT SELECT, INSERT, UPDATE ON agent_task, task_turn, agent_run, worker_pulse TO :"worker_role";
GRANT SELECT, INSERT ON run_step, run_message, task_artifact TO :"worker_role";
COMMIT;
