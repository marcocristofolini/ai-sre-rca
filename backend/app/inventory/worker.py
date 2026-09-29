from __future__ import annotations

import asyncio
import logging

from app.awareness.kubernetes_inventory import reconcile_kubernetes_inventory
from app.config import get_settings
from app.db.repository import SQLRepository
from app.integrations.http_tools import ReadOnlyHTTPTools


logger = logging.getLogger(__name__)


async def run_once() -> int:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    if not settings.database_url:
        raise RuntimeError(
            "Kubernetes inventory reconciliation requires persistent DATABASE_URL"
        )

    repo = SQLRepository(settings.database_url)
    tools = ReadOnlyHTTPTools(settings)
    try:
        await repo.initialize()
        reconciliations = await reconcile_kubernetes_inventory(
            repo,
            tools,
            settings,
        )
    finally:
        await repo.close()

    snapshots = sum(len(item.results) for item in reconciliations)
    tombstoned_entities = sum(
        result.entities_tombstoned
        for item in reconciliations
        for result in item.results
    )
    tombstoned_relationships = sum(
        result.relationships_tombstoned
        for item in reconciliations
        for result in item.results
    )
    logger.info(
        "Kubernetes inventory reconciliation complete: "
        "clusters=%d snapshots=%d entities_tombstoned=%d "
        "relationships_tombstoned=%d",
        len(reconciliations),
        snapshots,
        tombstoned_entities,
        tombstoned_relationships,
    )
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(run_once()))


if __name__ == "__main__":
    main()
