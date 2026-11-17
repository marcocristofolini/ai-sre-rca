from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.alert_grouping import (
    OPEN_STALE_AFTER,
    alert_fingerprint,
    build_report,
    group_key,
    occurrence_from_alert,
)
from app.db.repository import MemoryRepository
from app.main import app

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
WINDOW = 900


def occ(alertname="HighErrorRate", *, tenant="t1", cluster="prod", service="checkout",
        status="firing", start=T0, end=None, received=None, **labels):
    base = {"alertname": alertname, "cluster": cluster, "namespace": "shop", "severity": "critical"}
    if service:
        base["service"] = service
    base.update(labels)
    return occurrence_from_alert(
        tenant_id=tenant, labels=base, status=status, starts_at=start, ends_at=end,
        fingerprint=None, received_at=received or (end or start),
    )


def test_fingerprint_and_group_key_are_deterministic_and_scope_bound():
    labels = {"alertname": "A", "cluster": "prod", "service": "checkout"}
    assert alert_fingerprint(labels) == alert_fingerprint(dict(reversed(list(labels.items()))))
    assert alert_fingerprint(labels) != alert_fingerprint({**labels, "service": "cart"})
    key = group_key("t1", "prod", "shop", "service", "checkout")
    assert key == group_key("t1", "prod", "shop", "service", "checkout")
    assert len({key, group_key("t2", "prod", "shop", "service", "checkout"),
                group_key("t1", "stage", "shop", "service", "checkout"),
                group_key("t1", "prod", "other", "service", "checkout")}) == 4


def test_target_prefers_service_then_pod_then_node_then_alert_name():
    assert (occ().target_kind, occ().target) == ("service", "checkout")
    pod = occ(service=None, pod="checkout-abc")
    assert (pod.target_kind, pod.target) == ("pod", "checkout-abc")
    node = occ(service=None, node="node-1")
    assert (node.target_kind, node.target) == ("node", "node-1")
    bare = occ(service=None)
    assert (bare.target_kind, bare.target) == ("alert", "HighErrorRate")


async def test_alerts_on_one_scope_within_window_share_a_group():
    repo = MemoryRepository()
    first = await repo.record_alert_occurrence(occ("HighErrorRate"), WINDOW)
    second = await repo.record_alert_occurrence(
        occ("HighLatency", start=T0 + timedelta(minutes=5)), WINDOW)
    other_scope = await repo.record_alert_occurrence(
        occ("HighErrorRate", service="cart", start=T0 + timedelta(minutes=5)), WINDOW)
    assert first.group_id and first.group_id == second.group_id
    assert other_scope.group_id != first.group_id


async def test_grouping_never_crosses_tenant_or_cluster():
    repo = MemoryRepository()
    base = await repo.record_alert_occurrence(occ(), WINDOW)
    other_tenant = await repo.record_alert_occurrence(occ(tenant="t2"), WINDOW)
    other_cluster = await repo.record_alert_occurrence(occ(cluster="stage"), WINDOW)
    assert len({base.group_id, other_tenant.group_id, other_cluster.group_id}) == 3
    only_t1 = await repo.list_alert_occurrences(
        tenant_id="t1", allowed_clusters=frozenset({"prod"}), cluster=None, since=T0 - timedelta(hours=1))
    assert [item.id for item in only_t1] == [base.id]


async def test_resolved_alert_outside_window_starts_a_new_group_but_open_alert_keeps_it():
    repo = MemoryRepository()
    first = await repo.record_alert_occurrence(occ("A"), WINDOW)
    await repo.record_alert_occurrence(
        occ("A", status="resolved", end=T0 + timedelta(minutes=2)), WINDOW)
    later = await repo.record_alert_occurrence(occ("B", start=T0 + timedelta(hours=2)), WINDOW)
    assert later.group_id != first.group_id

    open_repo = MemoryRepository()
    still_firing = await open_repo.record_alert_occurrence(occ("A"), WINDOW)
    joined = await open_repo.record_alert_occurrence(occ("B", start=T0 + timedelta(hours=2)), WINDOW)
    assert joined.group_id == still_firing.group_id
    stale = await open_repo.record_alert_occurrence(
        occ("C", start=T0 + OPEN_STALE_AFTER + timedelta(hours=3)), WINDOW)
    assert stale.group_id != still_firing.group_id


async def test_repeat_and_resolution_update_one_occurrence_and_resolution_is_kept():
    repo = MemoryRepository()
    first = await repo.record_alert_occurrence(occ("A"), WINDOW)
    repeat = await repo.record_alert_occurrence(occ("A", received=T0 + timedelta(minutes=4)), WINDOW)
    resolved = await repo.record_alert_occurrence(
        occ("A", status="resolved", end=T0 + timedelta(minutes=6)), WINDOW)
    late_firing = await repo.record_alert_occurrence(occ("A", received=T0 + timedelta(minutes=7)), WINDOW)
    rows = await repo.list_alert_occurrences(
        tenant_id="t1", allowed_clusters=None, cluster=None, since=T0 - timedelta(hours=1))
    assert len(rows) == 1 and rows[0].id == first.id == repeat.id == resolved.id
    assert late_firing.status == "resolved" and late_firing.ends_at == T0 + timedelta(minutes=6)
    assert late_firing.notifications == 4


async def test_resolved_alert_never_seen_firing_is_still_recorded():
    repo = MemoryRepository()
    stored = await repo.record_alert_occurrence(
        occ("A", status="resolved", end=T0 + timedelta(minutes=1)), WINDOW)
    assert stored.status == "resolved" and stored.group_id


async def test_report_flags_short_lived_alerts_and_counts_co_firing():
    repo = MemoryRepository()
    for index in range(3):  # flapping alert: closes on its own within the threshold
        start = T0 + timedelta(hours=index * 2)
        await repo.record_alert_occurrence(occ("Flappy", service="cart", start=start), WINDOW)
        await repo.record_alert_occurrence(
            occ("Flappy", service="cart", status="resolved", start=start, end=start + timedelta(minutes=3)), WINDOW)
    await repo.record_alert_occurrence(occ("HighErrorRate"), WINDOW)
    await repo.record_alert_occurrence(occ("HighLatency", start=T0 + timedelta(minutes=1)), WINDOW)
    await repo.record_alert_occurrence(
        occ("HighLatency", status="resolved", start=T0 + timedelta(minutes=1), end=T0 + timedelta(hours=1)), WINDOW)
    rows = await repo.list_alert_occurrences(
        tenant_id="t1", allowed_clusters=None, cluster=None, since=T0 - timedelta(hours=1))
    report = build_report(rows, now=T0 + timedelta(days=1), since=T0 - timedelta(hours=1), noise_resolve_seconds=300)

    by_name = {item["alert_name"]: item for item in report["alerts"]}
    assert by_name["Flappy"] | {"median_open_seconds": None} == {
        "tenant_id": "t1", "cluster": "prod", "alert_name": "Flappy", "occurrences": 3,
        "resolved": 3, "short_lived": 3, "median_open_seconds": None, "noise_candidate": True}
    assert by_name["Flappy"]["median_open_seconds"] == 180
    assert by_name["HighLatency"]["noise_candidate"] is False
    assert by_name["HighErrorRate"]["resolved"] == 0 and by_name["HighErrorRate"]["noise_candidate"] is False
    assert report["co_firing"] == [
        {"tenant_id": "t1", "cluster": "prod", "alerts": ["HighErrorRate", "HighLatency"], "groups": 1}]
    assert report["totals"] == {
        "occurrences": 5, "alert_names": 3, "problems": 4, "open_problems": 1, "noise_candidates": 1}
    checkout = next(item for item in report["problems"] if item["target"] == "checkout")
    assert checkout["alert_names"] == ["HighErrorRate", "HighLatency"] and checkout["open"] is True


@pytest.fixture
def grouping_enabled():
    settings, original_repo = app.state.settings, app.state.repo
    saved = (settings.alert_grouping_enabled, settings.alertmanager_webhook_token)
    settings.alert_grouping_enabled, settings.alertmanager_webhook_token = True, None
    app.state.repo = MemoryRepository()
    try:
        yield
    finally:
        settings.alert_grouping_enabled, settings.alertmanager_webhook_token = saved
        app.state.repo = original_repo


def _payload(status, name, starts, ends=None):
    alert = {"status": status, "labels": {"alertname": name, "cluster": "demo", "namespace": "shop",
             "service": "checkout"}, "annotations": {}, "startsAt": starts}
    if ends:
        alert["endsAt"] = ends
    return {"status": status, "alerts": [alert]}


def test_webhook_records_groups_and_report_respects_flag(grouping_enabled):
    start = (datetime.now(timezone.utc) - timedelta(minutes=10)).replace(microsecond=0)
    with TestClient(app) as client:
        first = client.post("/api/v1/webhooks/alertmanager", json=_payload("firing", "HighErrorRate", start.isoformat()))
        second = client.post("/api/v1/webhooks/alertmanager",
                             json=_payload("firing", "HighLatency", (start + timedelta(minutes=1)).isoformat()))
        resolved = client.post("/api/v1/webhooks/alertmanager", json=_payload(
            "resolved", "HighLatency", (start + timedelta(minutes=1)).isoformat(), (start + timedelta(minutes=3)).isoformat()))
        assert first.status_code == second.status_code == resolved.status_code == 200
        # Grouping is bookkeeping only: every firing alert is still investigated.
        assert first.json()["accepted"] == 1 and second.json()["accepted"] == 1
        assert first.json()["investigations"][0]["group_id"] == second.json()["investigations"][0]["group_id"]
        assert resolved.json() == {"accepted": 0, "investigations": [], "recorded": 1}

        report = client.get("/api/v1/alerts/report").json()
        assert report["totals"]["occurrences"] == 2 and report["totals"]["problems"] == 1
        assert {item["alert_name"]: item["short_lived"] for item in report["alerts"]} == {
            "HighErrorRate": 0, "HighLatency": 1}

        app.state.settings.alert_grouping_enabled = False
        assert client.get("/api/v1/alerts/report").status_code == 404
        off = client.post("/api/v1/webhooks/alertmanager", json=_payload("firing", "HighErrorRate", start.isoformat()))
        assert "recorded" not in off.json() and "group_id" not in off.json()["investigations"][0]


def test_recording_failure_does_not_block_investigation(grouping_enabled):
    async def boom(*args, **kwargs):
        raise RuntimeError("storage unavailable")
    app.state.repo.record_alert_occurrence = boom
    start = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    with TestClient(app) as client:
        response = client.post("/api/v1/webhooks/alertmanager", json=_payload("firing", "HighErrorRate", start))
    assert response.status_code == 200
    assert response.json()["accepted"] == 1 and response.json()["recorded"] == 0
