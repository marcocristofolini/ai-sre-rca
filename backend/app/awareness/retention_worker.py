from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from app.awareness.retention import AwarenessRetentionResult
from app.config import Settings, get_settings
from app.db.repository import InvestigationRepository, SQLRepository


logger = logging.getLogger(__name__)
RETENTION_ACTOR = "system:awareness-retention-worker"


async def run_retention(
    repo: InvestigationRepository,
    settings: Settings,
    *,
    as_of: datetime | None = None,
) -> list[AwarenessRetentionResult]:
    clusters = settings.awareness_retention_cluster_list
    if not clusters:
        raise RuntimeError("AWARENESS_RETENTION_CLUSTERS must be configured")

    return [
        await repo.purge_awareness_retention(
            tenant_id=settings.awareness_retention_tenant_id,
            cluster=cluster,
            observation_days=settings.awareness_observation_retention_days,
            tombstone_days=settings.awareness_tombstone_retention_days,
            actor=RETENTION_ACTOR,
            dry_run=False,
            as_of=as_of,
        )
        for cluster in clusters
    ]


async def run_once() -> int:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    if not settings.database_url:
        raise RuntimeError(
            "Scheduled Awareness retention requires persistent DATABASE_URL"
        )

    repo = SQLRepository(settings.database_url)
    try:
        await repo.initialize()
        results = await run_retention(repo, settings)
    finally:
        await repo.close()

    logger.info(
        "Awareness retention complete: clusters=%d observations_deleted=%d "
        "relationships_deleted=%d entities_deleted=%d",
        len(results),
        sum(item.observations_deleted for item in results),
        sum(item.tombstoned_relationships_deleted for item in results),
        sum(item.tombstoned_entities_deleted for item in results),
    )
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(run_once()))


if __name__ == "__main__":
    main()
