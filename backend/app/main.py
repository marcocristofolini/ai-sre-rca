import asyncio
import logging
from uuid import uuid4
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.api.awareness import router as awareness_router
from app.api.incidents import router as incidents_router
from app.api.copilot_investigations import router as copilot_investigations_router
from app.api.planner import router as planner_router
from app.api.planner import orchestration_worker
from app.api.alerts import router as alerts_router
from app.api.webhooks import router as webhooks_router
from app.api.system import router as system_router
from app.auth import AuthManager
from app.config import get_settings
from app.core.engine import InvestigationEngine
from app.db.repository import MemoryRepository, SQLRepository
from app.integrations.demo import DemoTools
from app.integrations.http_tools import ReadOnlyHTTPTools, UnknownClusterError
from app.llm.hypotheses import NullHypothesisGenerator, OpenAICompatibleHypothesisGenerator
from app.llm.planning import NullPlanningAdvisor, OpenAICompatiblePlanningAdvisor
from app.notifications import NotificationManager

settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)

repo = SQLRepository(settings.database_url) if settings.database_url else MemoryRepository()
tools = DemoTools() if settings.demo_mode else ReadOnlyHTTPTools(settings)
hypothesis_generator = OpenAICompatibleHypothesisGenerator(settings) if settings.llm_enabled else NullHypothesisGenerator()
planning_advisor = (
    OpenAICompatiblePlanningAdvisor(settings)
    if settings.llm_enabled and settings.agentic_model_planner_enabled
    else NullPlanningAdvisor()
)
auth_manager = AuthManager(settings)
notification_manager = NotificationManager(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await repo.initialize()
    await repo.purge_expired(
        settings.incident_retention_days,
        settings.audit_retention_days,
    )
    worker = asyncio.create_task(orchestration_worker(app))
    yield
    worker.cancel()
    try:
        await worker
    except asyncio.CancelledError:
        pass
    await repo.close()


app = FastAPI(
    title=settings.app_name,
    version="0.8.0-rc.3",
    description="Evidence-verified SRE investigation platform with deterministic RCA, persistent temporal awareness graph, trace-aware causality, stateful dependency analysis and guarded remediation",
    lifespan=lifespan,
)
@app.exception_handler(UnknownClusterError)
async def unknown_cluster_handler(request: Request, exc: UnknownClusterError):
    return JSONResponse(
        status_code=422,
        content={"detail": str(exc)},
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

app.state.settings = settings
app.state.tools = tools
app.state.repo = repo
app.state.auth = auth_manager
app.state.notifications = notification_manager
app.state.planning_advisor = planning_advisor
app.state.orchestration_worker_id = f"worker-{uuid4().hex}"
app.state.engine = InvestigationEngine(
    tools=tools,
    memory=repo,
    hypothesis_generator=hypothesis_generator,
    settings=settings,
)


@app.get("/healthz")
async def healthz():
    return {
        "status": "ok",
        "version": "0.8.0-rc.3",
        "demo_mode": settings.demo_mode,
        "persistence": "postgres+pgvector" if settings.database_url else "memory",
        "llm_hypotheses": settings.llm_enabled,
        "trace_analysis": settings.trace_analysis_enabled,
        "stateful_dependency_rca": settings.stateful_dependency_analysis_enabled,
        "auth_enabled": settings.auth_enabled,
        "notifications_enabled": settings.notifications_enabled,
        "query_budget_enabled": settings.query_budget_enabled,
        "persistent_awareness_graph": True,
        "persistent_change_events": True,
        "authoritative_inventory_reconciliation": True,
        "agentic_planner_enabled": settings.agentic_planner_enabled,
        "agentic_execution_enabled": settings.agentic_execution_enabled,
        "orchestration_execution_mode": (
            "experimental-postgres-leased-workers" if settings.database_url
            else "experimental-in-process-memory"
        ),
        "agentic_model_planner_enabled": (
            settings.llm_enabled and settings.agentic_model_planner_enabled
        ),
        "awareness_max_traversal_depth": settings.awareness_max_traversal_depth,
        "configured_clusters": sorted(settings.cluster_config),
    }


app.include_router(incidents_router)
app.include_router(copilot_investigations_router)
app.include_router(awareness_router)
app.include_router(planner_router)
app.include_router(webhooks_router)
app.include_router(alerts_router)
app.include_router(system_router)
