import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.identity import new_code


@pytest.mark.asyncio
async def test_real_migrations_rls_and_immutable_rows_with_nonprivileged_role():
    url = os.environ.get("MIGRATED_TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires explicitly migrated isolated PostgreSQL database")
    engine = create_async_engine(url)
    role = "agent_rls_test_" + uuid.uuid4().hex
    tenant_a, tenant_b = new_code("ten"), new_code("ten")
    code_a, code_b = new_code("agt"), new_code("agt")
    try:
        async with engine.connect() as conn:
            txn = await conn.begin()
            try:
                assert (
                    await conn.scalar(text("SELECT version_num FROM alembic_version"))
                    == "20261029_06"
                )
                await conn.execute(text(f'CREATE ROLE "{role}" NOLOGIN NOBYPASSRLS'))
                await conn.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
                await conn.execute(
                    text(f'GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO "{role}"')
                )
                await conn.execute(
                    text(f'GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO "{role}"')
                )
                for tenant, code in ((tenant_a, code_a), (tenant_b, code_b)):
                    await conn.execute(
                        text(
                            "INSERT INTO agent "
                            "(tenant_code, agent_code, agent_name, agent_type, status) "
                            "VALUES (:t,:c,'RLS test','NORMAL',1)"
                        ),
                        {"t": tenant, "c": code},
                    )
                await conn.execute(text(f'SET LOCAL ROLE "{role}"'))
                await conn.execute(
                    text("SELECT set_config('app.tenant_code', :t, true)"), {"t": tenant_a}
                )
                rows = (await conn.execute(text("SELECT agent_code FROM agent"))).scalars().all()
                assert rows == [code_a]  # Intentionally omit the application tenant WHERE.
                assert not (
                    await conn.execute(
                        text("SELECT * FROM agent WHERE agent_code=:c"), {"c": code_b}
                    )
                ).all()
                savepoint = await conn.begin_nested()
                with pytest.raises(DBAPIError, match="row-level security"):
                    await conn.execute(
                        text(
                            "INSERT INTO agent "
                            "(tenant_code,agent_code,agent_name,agent_type,status) "
                            "VALUES (:t,:c,'bad','NORMAL',1)"
                        ),
                        {"t": tenant_b, "c": new_code("agt")},
                    )
                await savepoint.rollback()
                await conn.execute(
                    text("SELECT set_config('app.tenant_code', :t, true)"), {"t": tenant_b}
                )
                assert (
                    await conn.execute(text("SELECT agent_code FROM agent"))
                ).scalars().all() == [code_b]
                await conn.execute(text("RESET ROLE"))
                # Immutable triggers apply even to the migration owner.
                function = await conn.scalar(
                    text(
                        "SELECT count(*) FROM pg_trigger "
                        "WHERE tgname='immutable_row' AND NOT tgisinternal"
                    )
                )
                assert function == 6
            finally:
                await txn.rollback()  # Includes test data, grants and the temporary role.
    finally:
        await engine.dispose()
