import os
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete

from app.api.copilot_investigations import InvestigationConfirmation, InvestigationRequest, confirm_investigation, propose_investigation
from app.db.models import AuditEventRecord, InvestigationRecord, OrchestrationJobRecord, OrchestrationProgressEventRecord
from app.db.repository import SQLRepository
from test_copilot_v1 import make_result
from test_copilot_v2 import interactive_api


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv('TEST_DATABASE_URL'), reason='TEST_DATABASE_URL is required for Copilot persistence coverage')
async def test_proposal_confirmation_survives_postgres_repository_restart():
    app, principal = interactive_api()
    tenant = 'copilot-' + uuid4().hex
    owner = replace(principal.value, tenant_id=tenant)
    source = make_result(tenant=tenant)
    source.incident_id = 'inc-' + uuid4().hex[:12]
    request = SimpleNamespace(app=app)
    repo = SQLRepository(os.environ['TEST_DATABASE_URL'])
    await repo.initialize()
    app.state.repo = repo
    try:
        await repo.save(source)
        proposal = await propose_investigation(source.incident_id, InvestigationRequest(question='Investigate again'), request, owner)
        confirmation = InvestigationConfirmation(question='Investigate again', proposal_id=proposal.proposal_id, confirmed=True)
        first = await confirm_investigation(source.incident_id, confirmation, request, owner)
        await repo.close()
        repo = SQLRepository(os.environ['TEST_DATABASE_URL'])
        await repo.initialize()
        app.state.repo = repo
        retry = await confirm_investigation(source.incident_id, confirmation, request, owner)
        assert retry.id == first.id
        stored = await repo.get_orchestration_job(first.id, tenant)
        assert stored['payload']['incident'] == proposal.incident.model_dump(mode='json')
        assert await repo.get_orchestration_job(first.id, 'another-tenant') is None
        with pytest.raises(HTTPException) as denied:
            await confirm_investigation(source.incident_id, confirmation, request, replace(owner, tenant_id='another-tenant'))
        assert denied.value.status_code == 404
    finally:
        async with repo.sessions() as session:
            for model in (OrchestrationProgressEventRecord, OrchestrationJobRecord, AuditEventRecord, InvestigationRecord):
                await session.execute(delete(model).where(model.tenant_id == tenant))
            await session.commit()
        await repo.close()
