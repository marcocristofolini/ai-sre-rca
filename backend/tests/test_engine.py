from datetime import datetime, timezone

import pytest

from app.core.engine import InvestigationEngine
from app.integrations.demo import DemoTools
from app.schemas import HypothesisStatus, IncidentInput


@pytest.mark.asyncio
async def test_ephemeral_storage_eviction_is_confirmed():
    engine = InvestigationEngine(DemoTools())
    result = await engine.investigate(IncidentInput(
        title="Pod evicted",
        cluster="prod",
        namespace="exchange",
        pod="landing-page-7d8b6d9b-x9k2p",
        node="ip-10-0-1-42",
        alert_name="KubePodEvicted",
        starts_at=datetime(2026, 9, 24, 20, 47, tzinfo=timezone.utc),
    ))

    assert result.status == "resolved"
    assert result.root_cause.hypothesis_id == "disk-pressure"
    assert result.root_cause.evidence_strength == "strong"
    by_id = {h.id: h for h in result.hypotheses}
    assert by_id["disk-pressure"].status == HypothesisStatus.CONFIRMED
    assert by_id["memory-pressure"].status == HypothesisStatus.RULED_OUT
    assert by_id["pid-pressure"].status == HypothesisStatus.RULED_OUT
    assert by_id["missing-storage-reservations"].status != HypothesisStatus.RULED_OUT
