import json
from functools import lru_cache
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "AI SRE RCA"
    demo_mode: bool = True
    log_level: str = "INFO"
    database_url: str | None = None

    # Operational productization
    auth_enabled: bool = False
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    oidc_algorithms: str = "RS256,ES256"
    oidc_roles_claim: str = "roles"
    oidc_tenant_claim: str = "tenant"
    oidc_clusters_claim: str = "clusters"
    default_tenant_id: str = "default"
    viewer_role: str = "viewer"
    investigator_role: str = "investigator"
    admin_role: str = "admin"
    remediator_role: str = "remediator"
    alertmanager_webhook_token: str | None = None
    alertmanager_tenant_id: str = "default"

    slack_webhook_url: str | None = None
    pagerduty_routing_key: str | None = None
    pagerduty_events_url: str = "https://events.pagerduty.com/v2/enqueue"
    notifications_enabled: bool = False
    notification_min_severity: str = "warning"

    incident_retention_days: int = 30
    audit_retention_days: int = 90

    query_budget_enabled: bool = True
    query_budget_max_calls: int = 80
    query_budget_max_calls_per_source: int = 30
    query_budget_max_wall_seconds: float = 45.0

    # Guarded remediation plane. Disabled by default and intentionally uses
    # a separate cluster credential registry from the read-only investigator.
    remediation_enabled: bool = False
    remediation_require_distinct_approver: bool = True
    remediation_plan_ttl_minutes: int = 30
    remediation_max_incident_age_minutes: int = 120
    remediation_allowed_actions: str = "scale_deployment"
    remediation_allowed_namespaces: str = ""
    # Exact deterministic hypothesis IDs allowed to justify Deployment scaling.
    # Empty by default so enabling the remediator alone never authorizes scale.
    remediation_scale_hypothesis_allowlist: str = ""
    remediation_min_replicas: int = 1
    remediation_max_replicas: int = 20
    remediation_max_replica_delta: int = 3
    remediation_cluster_config_json: str = "{}"

    # Optional JSON map keyed by logical cluster name. Each entry can override
    # kubernetes_api_url/token_file/ca_file and Prometheus/Loki/Tempo/Argo CD URLs.
    cluster_config_json: str = "{}"

    kubernetes_api_url: str = "https://kubernetes.default.svc"
    kubernetes_token_file: str = "/var/run/secrets/kubernetes.io/serviceaccount/token"
    kubernetes_ca_file: str = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"

    prometheus_url: str = "http://prometheus-operated.monitoring.svc:9090"
    loki_url: str = "http://loki-gateway.logging.svc:3100"
    tempo_url: str = "http://tempo-query-frontend.tracing.svc:3200"
    tempo_tenant_id: str | None = None
    trace_analysis_enabled: bool = True
    trace_window_minutes: int = 15
    trace_search_limit: int = 8
    trace_fetch_limit: int = 4
    trace_slow_span_min_ms: float = 500.0
    trace_slow_span_root_fraction: float = 0.5

    stateful_dependency_analysis_enabled: bool = True
    stateful_dependency_max_targets: int = 4
    dependency_window_minutes: int = 15

    postgres_connection_utilization_critical: float = 0.90
    postgres_locks_critical: float = 50.0
    postgres_access_exclusive_locks_critical: float = 1.0
    postgres_deadlocks_delta_critical: float = 1.0
    postgres_replication_lag_bytes_critical: float = 16 * 1024 * 1024
    postgres_long_tx_seconds_critical: float = 60.0

    kafka_consumer_lag_critical: float = 10000.0
    kafka_under_replicated_partitions_critical: float = 1.0
    kafka_offline_partitions_critical: float = 1.0
    kafka_consumer_group_members_min: float = 1.0

    redis_memory_utilization_critical: float = 0.90
    redis_evictions_delta_critical: float = 1.0
    redis_blocked_clients_critical: float = 1.0
    redis_client_utilization_critical: float = 0.90
    redis_command_latency_seconds_critical: float = 0.05

    argocd_url: str = "http://argocd-server.argocd.svc"
    argocd_token: str | None = None

    github_api_url: str = "https://api.github.com"
    github_token: str | None = None

    change_correlation_window_minutes: int = 180
    service_graph_window_minutes: int = 15

    llm_enabled: bool = False
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str | None = None
    llm_model: str = "local-model"
    llm_max_hypotheses: int = 4

    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [v.strip() for v in self.cors_origins.split(",") if v.strip()]

    @property
    def oidc_algorithm_list(self) -> list[str]:
        return [v.strip() for v in self.oidc_algorithms.split(",") if v.strip()]

    @staticmethod
    def _validate_cluster_registry(raw: str, name: str) -> str:
        value = (raw or "{}").strip() or "{}"
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{name} must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"{name} must be a JSON object")
        if any(not isinstance(config, dict) for config in parsed.values()):
            raise ValueError(f"each {name} cluster entry must be a JSON object")
        return value

    @field_validator("cluster_config_json")
    @classmethod
    def validate_cluster_config_json(cls, raw: str) -> str:
        return cls._validate_cluster_registry(raw, "CLUSTER_CONFIG_JSON")

    @field_validator("remediation_cluster_config_json")
    @classmethod
    def validate_remediation_cluster_config_json(cls, raw: str) -> str:
        return cls._validate_cluster_registry(raw, "REMEDIATION_CLUSTER_CONFIG_JSON")

    @property
    def cluster_config(self) -> dict[str, dict[str, Any]]:
        value = json.loads(self.cluster_config_json or "{}")
        return {
            str(name): dict(config)
            for name, config in value.items()
        }

    @property
    def remediation_cluster_config(self) -> dict[str, dict[str, Any]]:
        value = json.loads(self.remediation_cluster_config_json or "{}")
        return {
            str(name): dict(config)
            for name, config in value.items()
        }

    @property
    def remediation_allowed_action_set(self) -> frozenset[str]:
        return frozenset(
            item.strip()
            for item in self.remediation_allowed_actions.split(",")
            if item.strip()
        )

    @property
    def remediation_allowed_namespace_set(self) -> frozenset[str]:
        return frozenset(
            item.strip()
            for item in self.remediation_allowed_namespaces.split(",")
            if item.strip()
        )

    @property
    def remediation_scale_hypothesis_set(self) -> frozenset[str]:
        return frozenset(
            item.strip()
            for item in self.remediation_scale_hypothesis_allowlist.split(",")
            if item.strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
