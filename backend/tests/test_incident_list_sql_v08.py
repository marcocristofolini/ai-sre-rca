import os
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.db.models import InvestigationRecord
from app.db.repository import SQLRepository
from test_incident_list_v08 import result

TEST_DATABASE_URL = os.getenv('TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason='TEST_DATABASE_URL is required for PostgreSQL integration coverage')


@pytest.mark.asyncio
async def test_postgres_list_summary_scope_literal_search_and_pagination():
    repo = SQLRepository(TEST_DATABASE_URL)
    await repo.initialize()
    tenant = f'list-{uuid4().hex}'
    ids = [f'inc-{uuid4().hex[:12]}' for _ in range(3)]
    try:
        for id, cluster, title in zip(ids, ['prod', 'prod', 'secret'], ['Literal %_ issue', 'Checkout issue', 'Hidden']):
            await repo.save(result(id, tenant=tenant, cluster=cluster, title=title))
        scoped = dict(tenant_id=tenant, allowed_clusters=frozenset({'prod'}))
        rows = await repo.list_incidents(**scoped, limit=1)
        assert len(rows) == 1
        assert rows[0].incident_id == ids[1]
        assert rows[0].severity == 'warning'
        assert rows[0].investigation_mode == 'classic'
        assert (await repo.list_incidents(**scoped, limit=1, offset=1))[0].incident_id == ids[0]
        assert [item.incident_id for item in await repo.list_incidents(**scoped, query='%_')] == [ids[0]]
        assert await repo.list_incidents(tenant_id='other', allowed_clusters=None) == []
        assert await repo.list_incidents(tenant_id=tenant, allowed_clusters=frozenset()) == []
    finally:
        async with repo.sessions() as session:
            await session.execute(delete(InvestigationRecord).where(InvestigationRecord.id.in_(ids)))
            await session.commit()
        await repo.close()
