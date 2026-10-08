from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.incidents import router
from app.api.system import router as system_router
from app.auth import Principal, get_principal
from app.config import Settings
from app.db.repository import MemoryRepository
from app.schemas import IncidentInput, InvestigationResult, RootCause


def result(incident_id='inc-a', tenant='tenant-a', cluster='prod', title='Checkout failure', status='resolved'):
    return InvestigationResult(
        incident_id=incident_id, status=status,
        incident=IncidentInput(title=title, tenant_id=tenant, cluster=cluster),
        entities=[], evidence=[], hypotheses=[], timeline=[], remediation=[],
        root_cause=RootCause(title='Insufficient capacity', summary='test fixture', evidence_strength='weak', supporting_signals=0, contradicting_signals=0),
        created_at=datetime(2026, 10, 8, 18, 0, tzinfo=timezone.utc),
    )


def api():
    app = FastAPI()
    app.state.settings = Settings()
    app.state.repo = MemoryRepository()
    user = SimpleNamespace(principal=Principal(subject='viewer', tenant_id='tenant-a', roles=frozenset({'viewer'}), clusters=frozenset({'prod'})))
    app.dependency_overrides[get_principal] = lambda: user.principal
    app.include_router(router)
    app.include_router(system_router)
    for item in [result('inc-a'), result('inc-c', status='inconclusive'), result('inc-b', title='Literal %_ issue'), result('hidden-tenant', tenant='tenant-b'), result('hidden-cluster', cluster='secret')]:
        app.state.repo._items[item.incident_id] = item
    return app, user


def test_listing_scopes_before_pagination_filters_and_returns_only_summaries():
    app, user = api()
    with TestClient(app) as client:
        response = client.get('/api/v1/incidents?limit=2')
        assert response.status_code == 200
        data = response.json()
        assert [item['incident_id'] for item in data['items']] == ['inc-c', 'inc-b']
        assert data['next_offset'] == 2
        assert not any('evidence' in item or 'agentic' in item for item in data['items'])
        second = client.get('/api/v1/incidents?limit=2&offset=2').json()
        assert [item['incident_id'] for item in second['items']] == ['inc-a']
        assert second['next_offset'] is None
        filtered = client.get('/api/v1/incidents?status=inconclusive').json()
        assert [item['incident_id'] for item in filtered['items']] == ['inc-c']
        assert [item['incident_id'] for item in client.get('/api/v1/incidents?q=%25_').json()['items']] == ['inc-b']
        assert len(client.get('/api/v1/incidents?q=CHECKOUT').json()['items']) == 2
        assert client.get('/api/v1/incidents?cluster=secret').status_code == 403
        assert client.get('/api/v1/incidents?limit=101').status_code == 422
        assert client.get('/api/v1/incidents?offset=10001').status_code == 422
        assert client.get('/api/v1/incidents?status=healthy').status_code == 422
        user.principal = Principal(subject='no-clusters', tenant_id='tenant-a', roles=frozenset({'viewer'}), clusters=frozenset())
        assert client.get('/api/v1/incidents').json()['items'] == []
        user.principal = Principal(subject='global', tenant_id='*', roles=frozenset({'admin'}), clusters=frozenset({'prod'}))
        assert len(client.get('/api/v1/incidents').json()['items']) == 4
        user.principal = Principal(subject='no-role', tenant_id='tenant-a', roles=frozenset(), clusters=frozenset({'prod'}))
        assert client.get('/api/v1/incidents').status_code == 403


def test_identity_reports_server_owned_investigation_permissions_and_flags():
    app, user = api()
    with TestClient(app) as client:
        identity = client.get('/api/v1/system/me').json()
        assert identity['can_investigate'] is False
        assert identity['agentic_execution_enabled'] is False
        app.state.settings.agentic_execution_enabled = True
        user.principal = Principal(subject='investigator', tenant_id='tenant-a', roles=frozenset({'investigator'}), clusters=frozenset({'prod'}))
        identity = client.get('/api/v1/system/me').json()
        assert identity['can_investigate'] is True
        assert identity['agentic_execution_enabled'] is True


def test_manual_classic_creation_overrides_tenant_and_supports_concrete_global_scope():
    app, user = api()
    async def investigate(incident):
        return result('inc-new', tenant=incident.tenant_id, cluster=incident.cluster)
    app.state.engine = SimpleNamespace(investigate=AsyncMock(side_effect=investigate))
    app.state.notifications = SimpleNamespace(send_incident=AsyncMock(return_value=[]))
    payload = {'title': 'Manual investigation', 'tenant_id': 'tenant-b', 'cluster': 'prod'}
    with TestClient(app) as client:
        assert client.post('/api/v1/incidents/investigate', json=payload).status_code == 403
        user.principal = Principal(subject='investigator', tenant_id='tenant-a', roles=frozenset({'investigator'}), clusters=frozenset({'prod'}))
        created = client.post('/api/v1/incidents/investigate', json=payload)
        assert created.status_code == 200
        assert created.json()['incident']['tenant_id'] == 'tenant-a'
        user.principal = Principal(subject='global', tenant_id='*', roles=frozenset({'admin'}), clusters=frozenset({'prod'}))
        assert client.post('/api/v1/incidents/investigate', json=payload).json()['incident']['tenant_id'] == 'tenant-b'
        payload['tenant_id'] = '*'
        assert client.post('/api/v1/incidents/investigate', json=payload).status_code == 422
        payload['tenant_id'] = 'tenant-a'
        payload['cluster'] = 'secret'
        assert client.post('/api/v1/incidents/investigate', json=payload).status_code == 403
