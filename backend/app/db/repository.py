from __future__ import annotations

from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.db.models import Base, InvestigationRecord
from app.schemas import InvestigationResult


class InvestigationRepository(Protocol):
    async def initialize(self) -> None: ...
    async def save(self, result: InvestigationResult) -> None: ...
    async def get(self, incident_id: str) -> InvestigationResult | None: ...
    async def close(self) -> None: ...


class MemoryRepository:
    def __init__(self):
        self._items: dict[str, InvestigationResult] = {}

    async def initialize(self) -> None:
        return None

    async def save(self, result: InvestigationResult) -> None:
        self._items[result.incident_id] = result

    async def get(self, incident_id: str) -> InvestigationResult | None:
        return self._items.get(incident_id)

    async def close(self) -> None:
        return None


class SQLRepository:
    def __init__(self, database_url: str):
        self.engine: AsyncEngine = create_async_engine(database_url, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def initialize(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def save(self, result: InvestigationResult) -> None:
        async with self.sessions() as session:
            existing = await session.get(InvestigationRecord, result.incident_id)
            payload = result.model_dump(mode="json")
            if existing:
                existing.status = result.status
                existing.root_cause = result.root_cause.title
                existing.payload = payload
            else:
                session.add(InvestigationRecord(
                    id=result.incident_id,
                    status=result.status,
                    title=result.incident.title,
                    alert_name=result.incident.alert_name,
                    cluster=result.incident.cluster,
                    namespace=result.incident.namespace,
                    pod=result.incident.pod,
                    root_cause=result.root_cause.title,
                    payload=payload,
                ))
            await session.commit()

    async def get(self, incident_id: str) -> InvestigationResult | None:
        async with self.sessions() as session:
            record = await session.scalar(select(InvestigationRecord).where(InvestigationRecord.id == incident_id))
            if not record:
                return None
            return InvestigationResult.model_validate(record.payload)

    async def close(self) -> None:
        await self.engine.dispose()
