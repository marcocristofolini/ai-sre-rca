from __future__ import annotations

from typing import Protocol

from app.schemas import IncidentInput, SimilarIncident


class IncidentMemory(Protocol):
    async def search_similar(self, incident: IncidentInput, limit: int = 3) -> list[SimilarIncident]: ...
