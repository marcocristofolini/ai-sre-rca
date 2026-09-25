from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.db.models import Base, InvestigationRecord
from app.memory.embedding import cosine_similarity, hash_embedding
from app.schemas import IncidentInput, InvestigationResult, SimilarIncident


class InvestigationRepository(Protocol):
    async def initialize(self) -> None: ...
    async def save(self, result: InvestigationResult) -> None: ...
    async def get(self, incident_id: str) -> InvestigationResult | None: ...
    async def search_similar(self, incident: IncidentInput, limit: int = 3) -> list[SimilarIncident]: ...
    async def close(self) -> None: ...


def incident_fingerprint(incident: IncidentInput, root_cause: str | None = None) -> str:
    labels = " ".join(f"{k}={v}" for k, v in sorted(incident.labels.items()))
    return " ".join(filter(None, [
        incident.alert_name,
        incident.title,
        incident.cluster,
        incident.namespace or "",
        incident.pod or "",
        incident.node or "",
        labels,
        root_cause or "",
    ]))


class MemoryRepository:
    def __init__(self):
        self._items: dict[str, InvestigationResult] = {}

    async def initialize(self) -> None:
        return None

    async def save(self, result: InvestigationResult) -> None:
        self._items[result.incident_id] = result

    async def get(self, incident_id: str) -> InvestigationResult | None:
        return self._items.get(incident_id)

    async def search_similar(self, incident: IncidentInput, limit: int = 3) -> list[SimilarIncident]:
        query = hash_embedding(incident_fingerprint(incident))
        ranked: list[tuple[float, InvestigationResult]] = []
        for result in self._items.values():
            vector = hash_embedding(incident_fingerprint(result.incident, result.root_cause.title))
            ranked.append((cosine_similarity(query, vector), result))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [
            SimilarIncident(
                incident_id=result.incident_id,
                title=result.incident.title,
                root_cause=result.root_cause.title,
                similarity=round(max(0.0, score), 4),
                occurred_at=result.created_at,
                cluster=result.incident.cluster,
                namespace=result.incident.namespace,
            )
            for score, result in ranked[:limit]
            if score > 0.05
        ]

    async def close(self) -> None:
        return None


class SQLRepository:
    def __init__(self, database_url: str):
        self.engine: AsyncEngine = create_async_engine(database_url, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def initialize(self) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
            # Lightweight forward migration for volumes created by v0.1.
            await conn.execute(text("ALTER TABLE investigations ADD COLUMN IF NOT EXISTS fingerprint_text TEXT"))
            await conn.execute(text("ALTER TABLE investigations ADD COLUMN IF NOT EXISTS embedding vector(128)"))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS investigations_embedding_hnsw "
                "ON investigations USING hnsw (embedding vector_cosine_ops)"
            ))

    async def save(self, result: InvestigationResult) -> None:
        async with self.sessions() as session:
            existing = await session.get(InvestigationRecord, result.incident_id)
            payload = result.model_dump(mode="json")
            fingerprint = incident_fingerprint(result.incident, result.root_cause.title)
            embedding = hash_embedding(fingerprint)
            if existing:
                existing.status = result.status
                existing.root_cause = result.root_cause.title
                existing.fingerprint_text = fingerprint
                existing.embedding = embedding
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
                    fingerprint_text=fingerprint,
                    embedding=embedding,
                    payload=payload,
                ))
            await session.commit()

    async def get(self, incident_id: str) -> InvestigationResult | None:
        async with self.sessions() as session:
            record = await session.scalar(select(InvestigationRecord).where(InvestigationRecord.id == incident_id))
            if not record:
                return None
            return InvestigationResult.model_validate(record.payload)

    async def search_similar(self, incident: IncidentInput, limit: int = 3) -> list[SimilarIncident]:
        vector = hash_embedding(incident_fingerprint(incident))
        distance = InvestigationRecord.embedding.cosine_distance(vector).label("distance")
        statement = select(InvestigationRecord, distance).order_by(distance).limit(limit)
        async with self.sessions() as session:
            rows = (await session.execute(statement)).all()
        output: list[SimilarIncident] = []
        for record, dist in rows:
            similarity = max(0.0, 1.0 - float(dist or 0.0))
            if similarity <= 0.05:
                continue
            occurred_at = record.created_at or datetime.now(timezone.utc)
            output.append(SimilarIncident(
                incident_id=record.id,
                title=record.title,
                root_cause=record.root_cause,
                similarity=round(similarity, 4),
                occurred_at=occurred_at,
                cluster=record.cluster,
                namespace=record.namespace,
            ))
        return output

    async def close(self) -> None:
        await self.engine.dispose()
