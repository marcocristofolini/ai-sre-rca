import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import AuthManager
from app.config import get_settings
from app.db.repository import MemoryRepository, SQLRepository
from app.remediation.api import router as remediation_router
from app.remediation.executor import KubernetesScaleExecutor
from app.remediation.service import GuardedRemediationService

settings = get_settings()
logging.basicConfig(level=settings.log_level)

repo = SQLRepository(settings.database_url) if settings.database_url else MemoryRepository()
auth_manager = AuthManager(settings)
executor = KubernetesScaleExecutor(settings)
remediation_service = GuardedRemediationService(repo, executor, settings)


def validate_remediator_runtime(settings) -> None:
    if not settings.remediation_enabled:
        return
    if not settings.auth_enabled:
        raise RuntimeError(
            "remediation requires AUTH_ENABLED=true"
        )
    if not settings.database_url:
        raise RuntimeError(
            "remediation requires shared persistent DATABASE_URL"
        )
    if not (
        settings.oidc_issuer
        and settings.oidc_audience
        and settings.oidc_jwks_url
    ):
        raise RuntimeError(
            "remediation requires complete OIDC issuer/audience/JWKS configuration"
        )
    if settings.remediator_role in {
        settings.viewer_role,
        settings.investigator_role,
        settings.admin_role,
    }:
        raise RuntimeError(
            "REMEDIATOR_ROLE must be distinct from viewer/investigator/admin roles"
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_remediator_runtime(settings)
    await repo.initialize()
    yield
    await repo.close()


app = FastAPI(
    title="AI SRE RCA Remediator",
    version="0.8.0",
    description=(
        "Guarded remediation control plane with policy validation, Kubernetes "
        "server-side dry-run, explicit human approval, drift detection and rollback"
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

app.state.settings = settings
app.state.repo = repo
app.state.auth = auth_manager
app.state.remediation_service = remediation_service


@app.get("/healthz")
async def healthz():
    return {
        "status": "ok",
        "version": "0.8.0",
        "service": "remediator",
        "enabled": settings.remediation_enabled,
        "separate_identity": True,
        "allowed_actions": sorted(settings.remediation_allowed_action_set),
        "allowed_namespaces": sorted(settings.remediation_allowed_namespace_set),
        "configured_clusters": sorted(settings.remediation_cluster_config),
    }


app.include_router(remediation_router)
