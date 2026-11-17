import os
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.db.models import AlertOccurrenceRecord
from app.db.repository import SQLRepository
from test_alert_grouping_v09 import T0, WINDOW, occ

TEST_DATABASE_URL = os.getenv('TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason='TEST_DATABASE_URL is required for PostgreSQL integration coverage')


@pytest.mark.asyncio
async def test_postgres_alert_grouping_upsert_isolation_and_listing():
    repo = SQLRepository(TEST_DATABASE_URL)
    await repo.initialize()
    tenant, other = f'alerts-{uuid4().hex}', f'alerts-{uuid4().hex}'
    try:
        first = await repo.record_alert_occurrence(occ('HighErrorRate', tenant=tenant), WINDOW)
        second = await repo.record_alert_occurrence(
            occ('HighLatency', tenant=tenant, start=T0 + timedelta(minutes=5)), WINDOW)
        foreign = await repo.record_alert_occurrence(occ('HighErrorRate', tenant=other), WINDOW)
        stage = await repo.record_alert_occurrence(occ('HighErrorRate', tenant=tenant, cluster='stage'), WINDOW)
        assert first.group_id == second.group_id
        assert len({first.group_id, foreign.group_id, stage.group_id}) == 3

        resolved = await repo.record_alert_occurrence(
            occ('HighErrorRate', tenant=tenant, status='resolved', end=T0 + timedelta(minutes=2)), WINDOW)
        assert resolved.id == first.id and resolved.status == 'resolved' and resolved.notifications == 2
        assert resolved.ends_at == T0 + timedelta(minutes=2)

        scoped = await repo.list_alert_occurrences(
            tenant_id=tenant, allowed_clusters=frozenset({'prod'}), cluster=None, since=T0 - timedelta(hours=1))
        assert {item.alert_name for item in scoped} == {'HighErrorRate', 'HighLatency'}
        assert all(item.tenant_id == tenant and item.cluster == 'prod' for item in scoped)
        assert await repo.list_alert_occurrences(
            tenant_id=tenant, allowed_clusters=None, cluster=None, since=T0 + timedelta(days=1)) == []
    finally:
        async with repo.sessions() as session:
            await session.execute(delete(AlertOccurrenceRecord).where(AlertOccurrenceRecord.tenant_id.in_([tenant, other])))
            await session.commit()
        await repo.engine.dispose()
