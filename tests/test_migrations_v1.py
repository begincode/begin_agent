import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.identity import new_code
from app.db.schema import SCHEMA_REVISION


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
                    == SCHEMA_REVISION
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
                assert function == 7
            finally:
                await txn.rollback()  # Includes test data, grants and the temporary role.
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_creation_receipts_force_rls_and_reject_updates_and_deletes():
    url = os.environ.get("MIGRATED_TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires explicitly migrated isolated PostgreSQL database")
    engine = create_async_engine(url)
    role = "receipt_rls_" + uuid.uuid4().hex
    tenant_a, tenant_b, owner = new_code("ten"), new_code("ten"), new_code("usr")
    agent_a, agent_b = new_code("agt"), new_code("agt")
    try:
        async with engine.connect() as conn:
            transaction = await conn.begin()
            try:
                await conn.execute(text(f'CREATE ROLE "{role}" NOLOGIN NOBYPASSRLS'))
                await conn.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
                await conn.execute(
                    text(
                        "GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES "
                        f'IN SCHEMA public TO "{role}"'
                    )
                )
                await conn.execute(
                    text(f'GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO "{role}"')
                )
                await conn.execute(
                    text(
                        "INSERT INTO platform_user "
                        "(user_code,external_user_id,display_name,status,platform_admin) "
                        "VALUES (:u,:e,'receipt owner',1,false)"
                    ),
                    {"u": owner, "e": "receipt-" + uuid.uuid4().hex},
                )
                for tenant, code in ((tenant_a, agent_a), (tenant_b, agent_b)):
                    await conn.execute(
                        text(
                            "INSERT INTO agent "
                            "(tenant_code,agent_code,agent_name,agent_type,status) "
                            "VALUES (:t,:a,'receipt RLS','NORMAL',1)"
                        ),
                        {"t": tenant, "a": code},
                    )
                    await conn.execute(
                        text(
                            "INSERT INTO agent_creation_receipt "
                            "(tenant_code,receipt_code,created_by,"
                            "request_hash,payload_hash,agent_code,result) "
                            "VALUES (:t,:r,:u,:h,:p,:a,'{}')"
                        ),
                        {
                            "t": tenant,
                            "r": new_code("acr"),
                            "u": owner,
                            "h": "a" * 64,
                            "p": "b" * 64,
                            "a": code,
                        },
                    )
                flags = (
                    await conn.execute(
                        text(
                            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
                            "WHERE oid='agent_creation_receipt'::regclass"
                        )
                    )
                ).one()
                assert flags == (True, True)
                await conn.execute(text(f'SET LOCAL ROLE "{role}"'))
                await conn.execute(
                    text("SELECT set_config('app.tenant_code',:t,true)"), {"t": tenant_a}
                )
                assert (
                    await conn.execute(text("SELECT agent_code FROM agent_creation_receipt"))
                ).scalars().all() == [agent_a]
                for statement in (
                    "UPDATE agent_creation_receipt SET payload_hash='changed'",
                    "DELETE FROM agent_creation_receipt",
                ):
                    savepoint = await conn.begin_nested()
                    with pytest.raises(DBAPIError, match="immutable"):
                        await conn.execute(text(statement))
                    await savepoint.rollback()
                await conn.execute(
                    text("SELECT set_config('app.tenant_code',:t,true)"), {"t": tenant_b}
                )
                assert (
                    await conn.execute(text("SELECT agent_code FROM agent_creation_receipt"))
                ).scalars().all() == [agent_b]
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_diagnostic_acknowledgment_rls_on_migrated_database():
    url = os.environ.get("MIGRATED_TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires explicitly migrated isolated PostgreSQL database")
    engine = create_async_engine(url)
    role = "diagnostic_rls_" + uuid.uuid4().hex
    a, b, user = new_code("ten"), new_code("ten"), new_code("usr")
    try:
        async with engine.connect() as conn:
            tx = await conn.begin()
            try:
                await conn.execute(text(f'CREATE ROLE "{role}" NOLOGIN NOBYPASSRLS'))
                await conn.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
                await conn.execute(
                    text(f'GRANT SELECT,INSERT ON diagnostic_acknowledgment TO "{role}"')
                )
                await conn.execute(
                    text(f'GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO "{role}"')
                )
                await conn.execute(
                    text(
                        "INSERT INTO platform_user "
                        "(user_code,external_user_id,display_name,status,platform_admin) "
                        "VALUES (:u,:e,'diag owner',1,false)"
                    ),
                    {"u": user, "e": uuid.uuid4().hex},
                )
                for tenant in (a, b):
                    await conn.execute(
                        text(
                            "INSERT INTO tenant (tenant_code,tenant_name,status) "
                            "VALUES (:t,'diag tenant',1)"
                        ),
                        {"t": tenant},
                    )
                    await conn.execute(
                        text(
                            "INSERT INTO diagnostic_acknowledgment "
                            "(tenant_code,user_code,fingerprint) VALUES (:t,:u,:f)"
                        ),
                        {"t": tenant, "u": user, "f": "a" * 64},
                    )
                await conn.execute(text(f'SET LOCAL ROLE "{role}"'))
                await conn.execute(text("SELECT set_config('app.tenant_code',:t,true)"), {"t": a})
                assert (
                    await conn.execute(text("SELECT tenant_code FROM diagnostic_acknowledgment"))
                ).scalars().all() == [a]
                savepoint = await conn.begin_nested()
                with pytest.raises(DBAPIError, match="row-level security"):
                    await conn.execute(
                        text(
                            "INSERT INTO diagnostic_acknowledgment "
                            "(tenant_code,user_code,fingerprint) VALUES (:t,:u,:f)"
                        ),
                        {"t": b, "u": user, "f": "b" * 64},
                    )
                await savepoint.rollback()
            finally:
                await tx.rollback()
    finally:
        await engine.dispose()
