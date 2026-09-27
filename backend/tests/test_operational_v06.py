from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.auth import AuthManager, Principal
from app.config import Settings
from app.core.engine import InvestigationEngine
from app.core.integrity import verify_evidence_hash
from app.core.query_budget import (
    begin_query_budget,
    consume_query,
    end_query_budget,
    query_budget_snapshot,
)
from app.db.repository import MemoryRepository
from app.integrations.demo import DemoTools
from app.integrations.http_tools import ReadOnlyHTTPTools
from app.main import app
from app.notifications import NotificationManager
from app.schemas import Evidence, IncidentInput


def test_evidence_integrity_hash_detects_tampering():
    evidence = Evidence(
        source="prometheus",
        category="metric",
        summary="memory pressure",
        facts={"value": 0.97},
        strength="strong",
    )

    assert evidence.integrity_sha256
    assert verify_evidence_hash(evidence)

    evidence.facts["value"] = 0.20
    assert not verify_evidence_hash(evidence)


def test_query_budget_denies_over_limit_and_reports_reason():
    settings = Settings(
        query_budget_enabled=True,
        query_budget_max_calls=2,
        query_budget_max_calls_per_source=1,
        query_budget_max_wall_seconds=60,
    )
    token = begin_query_budget(settings)
    try:
        assert consume_query("prometheus")
        assert not consume_query("prometheus")
        assert consume_query("kubernetes")
        assert not consume_query("loki")
        snapshot = query_budget_snapshot()
        assert snapshot is not None
        assert snapshot["total_calls"] == 2
        assert {item["reason"] for item in snapshot["denied"]} == {
            "source_calls",
            "total_calls",
        }
    finally:
        final = end_query_budget(token)

    assert final is not None
    assert final["total_calls"] == 2


def test_principal_role_hierarchy_and_cluster_scope():
    settings = Settings()
    principal = Principal(
        subject="user-1",
        tenant_id="tenant-a",
        roles=frozenset({"investigator"}),
        clusters=frozenset({"prod-a"}),
    )

    assert principal.has_role("viewer", settings)
    assert principal.has_role("investigator", settings)
    assert not principal.has_role("admin", settings)
    assert principal.can_access_cluster("prod-a")
    assert not principal.can_access_cluster("prod-b")
    assert principal.can_access_tenant("tenant-a")
    assert not principal.can_access_tenant("tenant-b")


def test_cluster_registry_fails_closed_for_unknown_cluster():
    settings = Settings(
        cluster_config_json='{"prod-a":{"prometheus_url":"http://prom-a:9090"}}'
    )
    tools = ReadOnlyHTTPTools(settings)

    with pytest.raises(ValueError, match="cluster is not configured"):
        tools.set_cluster("prod-b")


@pytest.mark.asyncio
async def test_unknown_cluster_failure_resets_query_budget_context():
    settings = Settings(
        cluster_config_json='{"prod-a":{"prometheus_url":"http://prom-a:9090"}}',
        query_budget_enabled=True,
    )
    tools = ReadOnlyHTTPTools(settings)
    engine = InvestigationEngine(tools, settings=settings)

    with pytest.raises(ValueError, match="cluster is not configured"):
        await engine.investigate(
            IncidentInput(title="unknown cluster", cluster="prod-b")
        )

    assert query_budget_snapshot() is None


def test_cluster_registry_overrides_endpoints_without_cross_request_state():
    settings = Settings(
        cluster_config_json=(
            '{"prod-a":{"prometheus_url":"http://prom-a:9090"},'
            '"prod-b":{"prometheus_url":"http://prom-b:9090"}}'
        )
    )
    tools = ReadOnlyHTTPTools(settings)

    token = tools.set_cluster("prod-a")
    try:
        assert tools._cluster_value("prometheus_url", "fallback") == "http://prom-a:9090"
    finally:
        tools.reset_cluster(token)

    assert tools._cluster_value("prometheus_url", "fallback") == "fallback"


@pytest.mark.asyncio
async def test_oidc_authentication_extracts_roles_tenant_and_cluster_scope():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings = Settings(
        auth_enabled=True,
        oidc_issuer="https://issuer.example",
        oidc_audience="ai-sre-rca",
        oidc_jwks_url="https://issuer.example/jwks",
    )
    manager = AuthManager(settings)

    class FakeJWKClient:
        def get_signing_key_from_jwt(self, token):
            return SimpleNamespace(key=private_key.public_key())

    manager._jwk_client = FakeJWKClient()
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": "user-42",
            "iss": settings.oidc_issuer,
            "aud": settings.oidc_audience,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
            "roles": ["investigator"],
            "tenant": "tenant-a",
            "clusters": ["prod-a", "prod-b"],
        },
        private_key,
        algorithm="RS256",
    )

    principal = await manager.authenticate(token)

    assert principal.subject == "user-42"
    assert principal.tenant_id == "tenant-a"
    assert principal.roles == frozenset({"investigator"})
    assert principal.clusters == frozenset({"prod-a", "prod-b"})


@pytest.mark.asyncio
async def test_budget_denied_prometheus_query_does_not_touch_network():
    settings = Settings(
        query_budget_enabled=True,
        query_budget_max_calls=0,
    )
    tools = ReadOnlyHTTPTools(settings)
    budget_token = begin_query_budget(settings)
    try:
        payload = await tools.prometheus_query("up")
    finally:
        snapshot = end_query_budget(budget_token)

    assert payload["_budget_denied"] is True
    assert payload["data"]["result"] == []
    assert snapshot["total_calls"] == 0
    assert snapshot["denied"][0]["reason"] == "total_calls"


def test_notification_payloads_do_not_expose_credentials():
    settings = Settings(
        notifications_enabled=True,
        slack_webhook_url="https://hooks.slack.test/secret",
        pagerduty_routing_key="routing-secret",
    )
    manager = NotificationManager(settings)

    incident = IncidentInput(
        title="Checkout failure",
        tenant_id="tenant-a",
        cluster="prod-a",
        severity="critical",
    )
    result = __import__("asyncio").run(
        InvestigationEngine(DemoTools()).investigate(incident)
    )

    slack = manager.slack_payload(result)
    pagerduty = manager.pagerduty_payload(result)

    assert "hooks.slack.test" not in str(slack)
    assert "routing-secret" not in str(pagerduty["payload"])
    assert pagerduty["routing_key"] == "routing-secret"
    assert result.incident_id == pagerduty["dedup_key"]


@pytest.mark.asyncio
async def test_memory_repository_is_tenant_scoped_and_retention_works():
    repo = MemoryRepository()
    engine = InvestigationEngine(DemoTools(), memory=repo)

    first = await engine.investigate(IncidentInput(
        title="Tenant A incident",
        tenant_id="tenant-a",
        cluster="prod",
        namespace="exchange",
        pod="landing-page-7d8b6d9b-x9k2p",
        node="ip-10-0-1-42",
        alert_name="KubePodEvicted",
    ))
    first.created_at = datetime.now(timezone.utc) - timedelta(days=10)
    await repo.save(first)

    second = await engine.investigate(IncidentInput(
        title="Tenant B incident",
        tenant_id="tenant-b",
        cluster="prod",
        namespace="exchange",
        pod="landing-page-7d8b6d9b-x9k2p",
        node="ip-10-0-1-42",
        alert_name="KubePodEvicted",
    ))
    await repo.save(second)

    similar = await repo.search_similar(
        IncidentInput(title="Tenant A incident", tenant_id="tenant-a", cluster="prod"),
        limit=10,
    )
    assert all(item.incident_id != second.incident_id for item in similar)

    deleted = await repo.purge_expired(incident_days=5, audit_days=90)
    assert deleted["incidents"] == 1
    assert await repo.get(first.incident_id) is None
    assert await repo.get(second.incident_id) is not None


def test_demo_api_exposes_audit_export_budget_and_capabilities():
    with TestClient(app) as client:
        demo = client.get("/api/v1/incidents/demo")
        assert demo.status_code == 200
        payload = demo.json()
        incident_id = payload["incident_id"]

        assert payload["incident"]["tenant_id"] == "default"
        assert payload["query_budget"]["enabled"] is True
        assert all(item["integrity_sha256"] for item in payload["evidence"])

        audit = client.get(f"/api/v1/incidents/{incident_id}/audit")
        assert audit.status_code == 200
        actions = {item["action"] for item in audit.json()}
        assert "investigation.completed" in actions

        exported = client.get(f"/api/v1/incidents/{incident_id}/export")
        assert exported.status_code == 200
        exported_payload = exported.json()
        assert exported_payload["schema_version"] == "ai-sre-rca.export.v1"
        assert exported_payload["evidence_integrity_ok"] is True
        assert len(exported_payload["evidence_hashes"]) == len(payload["evidence"])

        me = client.get("/api/v1/system/me")
        assert me.status_code == 200
        assert me.json()["tenant_id"] == "default"

        capabilities = client.get(
            "/api/v1/system/capabilities",
            params={"cluster": "prod-india-exchange"},
        )
        assert capabilities.status_code == 200
        assert all(item["status"] == "healthy" for item in capabilities.json()["sources"])



def test_alertmanager_machine_token_is_constant_time_gate():
    original = app.state.settings.alertmanager_webhook_token
    app.state.settings.alertmanager_webhook_token = "machine-secret"
    payload = {
        "status": "resolved",
        "alerts": [{
            "status": "resolved",
            "labels": {"alertname": "ResolvedAlert"},
            "annotations": {},
        }],
    }
    try:
        with TestClient(app) as client:
            denied = client.post("/api/v1/webhooks/alertmanager", json=payload)
            assert denied.status_code == 401

            accepted = client.post(
                "/api/v1/webhooks/alertmanager",
                json=payload,
                headers={"X-Webhook-Token": "machine-secret"},
            )
            assert accepted.status_code == 200
            assert accepted.json()["accepted"] == 0
    finally:
        app.state.settings.alertmanager_webhook_token = original
