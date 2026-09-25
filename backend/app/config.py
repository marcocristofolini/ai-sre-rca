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
    argocd_url: str = "http://argocd-server.argocd.svc"
    argocd_token: str | None = None

    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [v.strip() for v in self.cors_origins.split(",") if v.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
