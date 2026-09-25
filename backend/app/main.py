import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.incidents import router as incidents_router
from app.api.webhooks import router as webhooks_router
from app.config import get_settings
from app.core.engine import InvestigationEngine
from app.db.repository import MemoryRepository, SQLRepository
from app.integrations.demo import DemoTools
from app.integrations.http_tools import ReadOnlyHTTPTools

settings = get_settings()
logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)

repo = SQLRepository(settings.database_url) if settings.database_url else MemoryRepository()
tools = DemoTools() if settings.demo_mode else ReadOnlyHTTPTools(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await repo.initialize()
    yield
    await repo.close()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Evidence-first, read-only SRE incident investigator",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

app.state.tools = tools
app.state.engine = InvestigationEngine(tools)
app.state.repo = repo


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "demo_mode": settings.demo_mode, "persistence": "postgres" if settings.database_url else "memory"}


app.include_router(incidents_router)
app.include_router(webhooks_router)
