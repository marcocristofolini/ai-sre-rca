from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "AI SRE RCA"
    demo_mode: bool = True
    log_level: str = "INFO"
    database_url: str | None = None

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
