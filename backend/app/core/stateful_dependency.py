from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.core.scoring import score_hypothesis
from app.integrations.base import InvestigationTools
from app.schemas import (
    Entity,
    Evidence,
    Hypothesis,
    HypothesisStatus,
    InvestigationResult,
    Remediation,
    RootCause,
    StatefulDependencyAnalysis,
    StatefulDependencySignal,
    TimelineEvent,
)


@dataclass
class DependencyTarget:
    entity: Entity
    kind: str
    target: str | None
    topic: str | None = None
    consumer_group: str | None = None
    instance_hint: str | None = None
    causal_role: str = "context"
    trace_evidence_ids: list[str] | None = None

    def __post_init__(self) -> None:
        if self.trace_evidence_ids is None:
            self.trace_evidence_ids = []


def _metric(metrics: dict[str, Any], name: str) -> float | None:
    try:
        value = metrics.get("signals", {}).get(name, {}).get("value")
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _query(metrics: dict[str, Any], name: str) -> str | None:
    value = metrics.get("signals", {}).get(name, {}).get("query")
    return str(value) if value else None


def _system_from_entity(entity: Entity) -> str:
    metadata = entity.metadata
    explicit = (
        metadata.get("db.system")
        or metadata.get("messaging.system")
        or metadata.get("dependency_system")
    )
    if explicit:
        return str(explicit).lower()

    prefix = entity.name.split("/", 1)[0].lower()
    return prefix


def _classify(entity: Entity) -> tuple[str | None, dict[str, Any]]:
    system = _system_from_entity(entity)
    metadata = entity.metadata
    suffix = entity.name.split("/", 1)[1] if "/" in entity.name else None

    if system in {"postgres", "postgresql"}:
        target = metadata.get("db.namespace") or metadata.get("db.name") or suffix or "postgres"
        return "postgresql", {"target": str(target)}

    if system in {"kafka", "apache_kafka"}:
        topic = (
            metadata.get("messaging.destination.name")
            or metadata.get("messaging.destination")
            or suffix
        )
        group = (
            metadata.get("messaging.consumer.group.name")
            or metadata.get("messaging.kafka.consumer.group")
        )
        return "kafka", {
            "target": str(topic) if topic else None,
            "topic": str(topic) if topic else None,
            "consumer_group": str(group) if group else None,
        }

    if system in {"redis", "valkey"}:
        target = metadata.get("db.namespace") or metadata.get("db.name") or suffix or entity.name
        instance_hint = metadata.get("server.address") or metadata.get("peer.service")
        return "redis", {
            "target": str(target) if target else None,
            "instance_hint": str(instance_hint) if instance_hint else None,
        }

    name = entity.name.lower()
    if name.startswith(("postgresql/", "postgres/")):
        return "postgresql", {"target": suffix or "postgres"}
    if name.startswith("kafka/"):
        return "kafka", {"target": suffix, "topic": suffix, "consumer_group": None}
    if name.startswith(("redis/", "valkey/")):
        return "redis", {"target": suffix or entity.name, "instance_hint": None}

    return None, {}


def _trace_origin_evidence(result: InvestigationResult, dependency_name: str) -> list[str]:
    output: list[str] = []
    for evidence in result.evidence:
        if evidence.category != "trace-causal-chain":
            continue
        if str(evidence.facts.get("candidate_service") or "") == dependency_name:
            output.append(evidence.id)
    return output


def _trace_chain_contains(result: InvestigationResult, dependency_name: str) -> bool:
    return any(
        hop.service == dependency_name
        for analysis in result.trace_analyses
        for hop in analysis.causal_chain
    )


def _targets(result: InvestigationResult) -> list[DependencyTarget]:
    targets: list[DependencyTarget] = []
    seen: set[tuple[str, str | None]] = set()

    for entity in result.entities:
        if entity.kind != "TraceDependency":
            continue
        kind, values = _classify(entity)
        if not kind:
            continue

        key = (kind, values.get("target"))
        if key in seen:
            continue
        seen.add(key)

        trace_ids = _trace_origin_evidence(result, entity.name)
        causal_role = "origin" if trace_ids else ("supporting" if _trace_chain_contains(result, entity.name) else "context")
        targets.append(
            DependencyTarget(
                entity=entity,
                kind=kind,
                target=values.get("target"),
                topic=values.get("topic"),
                consumer_group=values.get("consumer_group"),
                instance_hint=values.get("instance_hint"),
                causal_role=causal_role,
                trace_evidence_ids=trace_ids,
            )
        )

    if not targets:
        labels = result.incident.labels
        explicit = (
            labels.get("dependency_kind")
            or labels.get("db.system")
            or labels.get("messaging.system")
        )
        if explicit:
            explicit = explicit.lower()
            if explicit in {"postgres", "postgresql"}:
                database = labels.get("database") or labels.get("db.namespace") or "postgres"
                entity = Entity(
                    id=f"dependency:postgresql/{database}",
                    kind="TraceDependency",
                    name=f"postgresql/{database}",
                    metadata={"db.system": "postgresql", "db.namespace": database},
                )
                result.entities.append(entity)
                targets.append(DependencyTarget(entity, "postgresql", database, causal_role="origin"))
            elif explicit == "kafka":
                topic = labels.get("topic") or labels.get("messaging.destination.name")
                group = labels.get("consumer_group") or labels.get("messaging.consumer.group.name")
                entity = Entity(
                    id=f"dependency:kafka/{topic or 'cluster'}",
                    kind="TraceDependency",
                    name=f"kafka/{topic or 'cluster'}",
                    metadata={
                        "messaging.system": "kafka",
                        "messaging.destination.name": topic,
                        "messaging.consumer.group.name": group,
                    },
                )
                result.entities.append(entity)
                targets.append(
                    DependencyTarget(
                        entity,
                        "kafka",
                        topic,
                        topic=topic,
                        consumer_group=group,
                        causal_role="origin",
                    )
                )
            elif explicit in {"redis", "valkey"}:
                name = labels.get("redis_instance") or labels.get("db.namespace") or "redis"
                entity = Entity(
                    id=f"dependency:redis/{name}",
                    kind="TraceDependency",
                    name=f"redis/{name}",
                    metadata={"db.system": "redis", "db.namespace": name},
                )
                result.entities.append(entity)
                targets.append(
                    DependencyTarget(entity, "redis", name, instance_hint=labels.get("instance"), causal_role="origin")
                )

    targets.sort(key=lambda item: {"origin": 0, "supporting": 1, "context": 2}[item.causal_role])
    return targets


def _signal(
    *,
    name: str,
    value: float | None,
    unit: str | None,
    threshold: float | None,
    critical: bool | None,
    query: str | None,
    description: str,
) -> StatefulDependencySignal:
    if value is None:
        status = "unknown"
    elif critical is True:
        status = "critical"
    elif critical is False:
        status = "normal"
    else:
        status = "warning"
    return StatefulDependencySignal(
        name=name,
        value=value,
        unit=unit,
        status=status,
        threshold=threshold,
        query=query,
        description=description,
    )


def _evidence(
    *,
    source: str,
    category: str,
    summary: str,
    facts: dict[str, Any],
    query: str | None,
    strong: bool,
) -> Evidence:
    return Evidence(
        source=source,
        category=category,
        summary=summary,
        facts=facts,
        query=query,
        raw_reference="prometheus:/api/v1/query",
        strength="strong" if strong else "medium",
    )


def _attach_and_score(
    result: InvestigationResult,
    hypothesis: Hypothesis,
    support: list[Evidence],
    contradict: list[Evidence],
    trace_ids: list[str],
    direct_anomaly: bool,
) -> Hypothesis:
    for item in support + contradict:
        result.evidence.append(item)

    hypothesis.supporting_evidence.extend(item.id for item in support)
    hypothesis.contradicting_evidence.extend(item.id for item in contradict)
    if direct_anomaly:
        hypothesis.supporting_evidence.extend(trace_ids)

    score_hypothesis(hypothesis, {item.id: item for item in result.evidence})
    result.hypotheses.append(hypothesis)
    return hypothesis


def _health(signals: list[StatefulDependencySignal]) -> str:
    states = {signal.status for signal in signals}
    if "critical" in states:
        return "critical"
    if "warning" in states:
        return "degraded"
    if "normal" in states:
        return "healthy"
    return "unknown"


async def _postgres(
    result: InvestigationResult,
    target: DependencyTarget,
    tools: InvestigationTools,
    settings: Settings,
) -> StatefulDependencyAnalysis:
    database = target.target or "postgres"
    metrics = await tools.postgres_dependency_metrics(
        database,
        result.incident.starts_at,
        settings.dependency_window_minutes,
    )

    connections = _metric(metrics, "connections")
    active = _metric(metrics, "active_connections")
    idle_tx = _metric(metrics, "idle_in_transaction")
    max_connections = _metric(metrics, "max_connections")
    locks = _metric(metrics, "locks")
    exclusive = _metric(metrics, "access_exclusive_locks")
    deadlocks = _metric(metrics, "deadlocks_delta")
    lag = _metric(metrics, "replication_lag_bytes")
    long_tx = _metric(metrics, "long_tx_seconds")

    utilization = (
        connections / max_connections
        if connections is not None and max_connections not in {None, 0}
        else None
    )

    signals = [
        _signal(
            name="connection_utilization",
            value=utilization,
            unit="ratio",
            threshold=settings.postgres_connection_utilization_critical,
            critical=utilization is not None and utilization >= settings.postgres_connection_utilization_critical,
            query=_query(metrics, "connections"),
            description="PostgreSQL sessions divided by max_connections.",
        ),
        _signal(
            name="active_connections",
            value=active,
            unit="connections",
            threshold=None,
            critical=None if active is not None else None,
            query=_query(metrics, "active_connections"),
            description="Active PostgreSQL sessions.",
        ),
        _signal(
            name="idle_in_transaction",
            value=idle_tx,
            unit="connections",
            threshold=None,
            critical=None if idle_tx is not None else None,
            query=_query(metrics, "idle_in_transaction"),
            description="Sessions idle in transaction.",
        ),
        _signal(
            name="locks",
            value=locks,
            unit="locks",
            threshold=settings.postgres_locks_critical,
            critical=locks is not None and locks >= settings.postgres_locks_critical,
            query=_query(metrics, "locks"),
            description="Total PostgreSQL locks for the database.",
        ),
        _signal(
            name="access_exclusive_locks",
            value=exclusive,
            unit="locks",
            threshold=settings.postgres_access_exclusive_locks_critical,
            critical=exclusive is not None and exclusive >= settings.postgres_access_exclusive_locks_critical,
            query=_query(metrics, "access_exclusive_locks"),
            description="AccessExclusiveLock count.",
        ),
        _signal(
            name="deadlocks_delta",
            value=deadlocks,
            unit="deadlocks",
            threshold=settings.postgres_deadlocks_delta_critical,
            critical=deadlocks is not None and deadlocks >= settings.postgres_deadlocks_delta_critical,
            query=_query(metrics, "deadlocks_delta"),
            description=f"Deadlocks observed in the last {settings.dependency_window_minutes} minutes.",
        ),
        _signal(
            name="replication_lag_bytes",
            value=lag,
            unit="bytes",
            threshold=settings.postgres_replication_lag_bytes_critical,
            critical=lag is not None and lag >= settings.postgres_replication_lag_bytes_critical,
            query=_query(metrics, "replication_lag_bytes"),
            description="Maximum WAL replay lag exposed by postgres_exporter.",
        ),
        _signal(
            name="long_transaction_seconds",
            value=long_tx,
            unit="seconds",
            threshold=settings.postgres_long_tx_seconds_critical,
            critical=long_tx is not None and long_tx >= settings.postgres_long_tx_seconds_critical,
            query=_query(metrics, "long_tx_seconds"),
            description="Longest transaction age when the optional collector is enabled.",
        ),
    ]

    hypotheses: list[Hypothesis] = []

    conn_anomaly = utilization is not None and utilization >= settings.postgres_connection_utilization_critical
    conn_support: list[Evidence] = []
    conn_contradict: list[Evidence] = []
    if utilization is not None:
        ev = _evidence(
            source="postgresql",
            category="connection-utilization",
            summary=(
                f"PostgreSQL {database} is using {utilization:.1%} of max_connections "
                f"({connections:.0f}/{max_connections:.0f})."
            ),
            facts={
                "database": database,
                "connections": connections,
                "max_connections": max_connections,
                "utilization": utilization,
                "active_connections": active,
                "idle_in_transaction": idle_tx,
            },
            query=_query(metrics, "connections"),
            strong=True,
        )
        (conn_support if conn_anomaly else conn_contradict).append(ev)
    hypotheses.append(_attach_and_score(
        result,
        Hypothesis(
            id=f"postgres-connection-exhaustion:{target.entity.id}",
            statement=f"PostgreSQL {database} connection capacity is exhausted or near exhaustion.",
        ),
        conn_support,
        conn_contradict,
        target.trace_evidence_ids,
        conn_anomaly,
    ))

    lock_anomaly = bool(
        (exclusive is not None and exclusive >= settings.postgres_access_exclusive_locks_critical)
        or (locks is not None and locks >= settings.postgres_locks_critical)
    )
    lock_support: list[Evidence] = []
    lock_contradict: list[Evidence] = []
    if locks is not None or exclusive is not None:
        ev = _evidence(
            source="postgresql",
            category="lock-contention",
            summary=(
                f"PostgreSQL {database} reports {locks if locks is not None else 'n/a'} locks "
                f"and {exclusive if exclusive is not None else 'n/a'} AccessExclusiveLock entries."
            ),
            facts={"database": database, "locks": locks, "access_exclusive_locks": exclusive},
            query=_query(metrics, "access_exclusive_locks") or _query(metrics, "locks"),
            strong=True,
        )
        (lock_support if lock_anomaly else lock_contradict).append(ev)
    hypotheses.append(_attach_and_score(
        result,
        Hypothesis(
            id=f"postgres-lock-contention:{target.entity.id}",
            statement=f"PostgreSQL {database} lock contention is blocking application work.",
        ),
        lock_support,
        lock_contradict,
        target.trace_evidence_ids,
        lock_anomaly,
    ))

    deadlock_anomaly = deadlocks is not None and deadlocks >= settings.postgres_deadlocks_delta_critical
    dead_support: list[Evidence] = []
    dead_contradict: list[Evidence] = []
    if deadlocks is not None:
        ev = _evidence(
            source="postgresql",
            category="deadlocks",
            summary=f"PostgreSQL {database} observed {deadlocks:.0f} deadlocks in the incident window.",
            facts={"database": database, "deadlocks_delta": deadlocks},
            query=_query(metrics, "deadlocks_delta"),
            strong=True,
        )
        (dead_support if deadlock_anomaly else dead_contradict).append(ev)
    hypotheses.append(_attach_and_score(
        result,
        Hypothesis(
            id=f"postgres-deadlocks:{target.entity.id}",
            statement=f"PostgreSQL {database} deadlocks are causing request failures or retries.",
        ),
        dead_support,
        dead_contradict,
        target.trace_evidence_ids,
        deadlock_anomaly,
    ))

    lag_anomaly = lag is not None and lag >= settings.postgres_replication_lag_bytes_critical
    lag_support: list[Evidence] = []
    lag_contradict: list[Evidence] = []
    if lag is not None:
        ev = _evidence(
            source="postgresql",
            category="replication-lag",
            summary=f"PostgreSQL replication lag reached approximately {lag / 1024 / 1024:.1f} MiB.",
            facts={"database": database, "replication_lag_bytes": lag},
            query=_query(metrics, "replication_lag_bytes"),
            strong=True,
        )
        (lag_support if lag_anomaly else lag_contradict).append(ev)
    hypotheses.append(_attach_and_score(
        result,
        Hypothesis(
            id=f"postgres-replication-lag:{target.entity.id}",
            statement=f"PostgreSQL replication lag is affecting reads or failover readiness for {database}.",
        ),
        lag_support,
        lag_contradict,
        target.trace_evidence_ids,
        lag_anomaly,
    ))

    long_tx_anomaly = long_tx is not None and long_tx >= settings.postgres_long_tx_seconds_critical
    tx_support: list[Evidence] = []
    tx_contradict: list[Evidence] = []
    if long_tx is not None:
        ev = _evidence(
            source="postgresql",
            category="long-transaction",
            summary=f"PostgreSQL longest transaction age is {long_tx:.1f}s.",
            facts={"database": database, "long_transaction_seconds": long_tx},
            query=_query(metrics, "long_tx_seconds"),
            strong=True,
        )
        (tx_support if long_tx_anomaly else tx_contradict).append(ev)
    hypotheses.append(_attach_and_score(
        result,
        Hypothesis(
            id=f"postgres-long-transaction:{target.entity.id}",
            statement=f"A long-running PostgreSQL transaction is contributing to saturation or blocking in {database}.",
        ),
        tx_support,
        tx_contradict,
        target.trace_evidence_ids,
        long_tx_anomaly,
    ))

    confirmed = sorted(
        (item for item in hypotheses if item.status == HypothesisStatus.CONFIRMED),
        key=lambda item: item.score,
        reverse=True,
    )
    primary = confirmed[0] if confirmed else None

    return StatefulDependencyAnalysis(
        dependency_id=target.entity.id,
        kind="postgresql",
        name=target.entity.name,
        target=database,
        health=_health(signals),
        causal_role=target.causal_role,
        cause=primary.statement if primary else None,
        cause_strength="strong" if primary else "weak",
        hypothesis_id=primary.id if primary else None,
        evidence_ids=list(primary.supporting_evidence) if primary else [],
        signals=signals,
        metadata={"database": database},
    )


async def _kafka(
    result: InvestigationResult,
    target: DependencyTarget,
    tools: InvestigationTools,
    settings: Settings,
) -> StatefulDependencyAnalysis:
    metrics = await tools.kafka_dependency_metrics(
        target.topic,
        target.consumer_group,
        result.incident.starts_at,
        settings.dependency_window_minutes,
    )

    lag = _metric(metrics, "consumer_lag")
    urp = _metric(metrics, "under_replicated_partitions")
    offline = _metric(metrics, "offline_partitions")
    members = _metric(metrics, "consumer_group_members")

    signals = [
        _signal(
            name="consumer_lag",
            value=lag,
            unit="messages",
            threshold=settings.kafka_consumer_lag_critical,
            critical=lag is not None and lag >= settings.kafka_consumer_lag_critical,
            query=_query(metrics, "consumer_lag"),
            description="Approximate consumer group lag for the traced topic/group.",
        ),
        _signal(
            name="under_replicated_partitions",
            value=urp,
            unit="partitions",
            threshold=settings.kafka_under_replicated_partitions_critical,
            critical=urp is not None and urp >= settings.kafka_under_replicated_partitions_critical,
            query=_query(metrics, "under_replicated_partitions"),
            description="Partitions whose ISR is below the replica set.",
        ),
        _signal(
            name="offline_partitions",
            value=offline,
            unit="partitions",
            threshold=settings.kafka_offline_partitions_critical,
            critical=offline is not None and offline >= settings.kafka_offline_partitions_critical,
            query=_query(metrics, "offline_partitions"),
            description="Partitions without a valid leader.",
        ),
        _signal(
            name="consumer_group_members",
            value=members,
            unit="members",
            threshold=settings.kafka_consumer_group_members_min,
            critical=members is not None and members < settings.kafka_consumer_group_members_min,
            query=_query(metrics, "consumer_group_members"),
            description="Active members in the traced consumer group.",
        ),
    ]

    hypotheses: list[Hypothesis] = []
    specs = [
        (
            "consumer-lag",
            lag,
            lag is not None and lag >= settings.kafka_consumer_lag_critical,
            "consumer-lag",
            f"Kafka consumer lag for {target.topic or 'the traced topic'} reached {lag if lag is not None else 'n/a'} messages.",
            f"Kafka consumer lag is delaying processing for {target.topic or target.entity.name}.",
        ),
        (
            "under-replicated",
            urp,
            urp is not None and urp >= settings.kafka_under_replicated_partitions_critical,
            "under-replicated-partitions",
            f"Kafka reports {urp if urp is not None else 'n/a'} under-replicated partitions.",
            f"Kafka replication health is degraded for {target.topic or target.entity.name}.",
        ),
        (
            "offline-partitions",
            offline,
            offline is not None and offline >= settings.kafka_offline_partitions_critical,
            "offline-partitions",
            f"Kafka reports {offline if offline is not None else 'n/a'} partitions without a valid leader.",
            f"Kafka leader availability is causing failures for {target.topic or target.entity.name}.",
        ),
        (
            "consumer-members",
            members,
            members is not None and members < settings.kafka_consumer_group_members_min,
            "consumer-group-members",
            f"Kafka consumer group {target.consumer_group or 'unknown'} has {members if members is not None else 'n/a'} members.",
            f"Kafka consumer group {target.consumer_group or target.entity.name} has no active members.",
        ),
    ]

    for suffix, value, anomaly, category, summary, statement in specs:
        support: list[Evidence] = []
        contradict: list[Evidence] = []
        if value is not None:
            ev = _evidence(
                source="kafka",
                category=category,
                summary=summary,
                facts={
                    "topic": target.topic,
                    "consumer_group": target.consumer_group,
                    category.replace("-", "_"): value,
                },
                query=_query(metrics, {
                    "consumer-lag": "consumer_lag",
                    "under-replicated": "under_replicated_partitions",
                    "offline-partitions": "offline_partitions",
                    "consumer-members": "consumer_group_members",
                }[suffix]),
                strong=True,
            )
            (support if anomaly else contradict).append(ev)
        hypotheses.append(_attach_and_score(
            result,
            Hypothesis(id=f"kafka-{suffix}:{target.entity.id}", statement=statement),
            support,
            contradict,
            target.trace_evidence_ids,
            anomaly,
        ))

    confirmed = sorted(
        (item for item in hypotheses if item.status == HypothesisStatus.CONFIRMED),
        key=lambda item: item.score,
        reverse=True,
    )
    primary = confirmed[0] if confirmed else None

    return StatefulDependencyAnalysis(
        dependency_id=target.entity.id,
        kind="kafka",
        name=target.entity.name,
        target=target.topic,
        health=_health(signals),
        causal_role=target.causal_role,
        cause=primary.statement if primary else None,
        cause_strength="strong" if primary else "weak",
        hypothesis_id=primary.id if primary else None,
        evidence_ids=list(primary.supporting_evidence) if primary else [],
        signals=signals,
        metadata={"topic": target.topic, "consumer_group": target.consumer_group},
    )


async def _redis(
    result: InvestigationResult,
    target: DependencyTarget,
    tools: InvestigationTools,
    settings: Settings,
) -> StatefulDependencyAnalysis:
    metrics = await tools.redis_dependency_metrics(
        target.instance_hint,
        result.incident.starts_at,
        settings.dependency_window_minutes,
    )

    used = _metric(metrics, "memory_used_bytes")
    max_memory = _metric(metrics, "memory_max_bytes")
    config_max = _metric(metrics, "config_maxmemory_bytes")
    memory_cap = max_memory if max_memory not in {None, 0} else config_max
    memory_utilization = used / memory_cap if used is not None and memory_cap not in {None, 0} else None
    evictions = _metric(metrics, "evictions_delta")
    blocked = _metric(metrics, "blocked_clients")
    clients = _metric(metrics, "connected_clients")
    max_clients = _metric(metrics, "max_clients")
    client_utilization = clients / max_clients if clients is not None and max_clients not in {None, 0} else None
    latency = _metric(metrics, "command_latency_seconds")

    signals = [
        _signal(
            name="memory_utilization",
            value=memory_utilization,
            unit="ratio",
            threshold=settings.redis_memory_utilization_critical,
            critical=memory_utilization is not None and memory_utilization >= settings.redis_memory_utilization_critical,
            query=_query(metrics, "memory_used_bytes"),
            description="Redis used memory divided by configured maxmemory.",
        ),
        _signal(
            name="evictions_delta",
            value=evictions,
            unit="keys",
            threshold=settings.redis_evictions_delta_critical,
            critical=evictions is not None and evictions >= settings.redis_evictions_delta_critical,
            query=_query(metrics, "evictions_delta"),
            description=f"Keys evicted in the last {settings.dependency_window_minutes} minutes.",
        ),
        _signal(
            name="blocked_clients",
            value=blocked,
            unit="clients",
            threshold=settings.redis_blocked_clients_critical,
            critical=blocked is not None and blocked >= settings.redis_blocked_clients_critical,
            query=_query(metrics, "blocked_clients"),
            description="Clients blocked on Redis operations.",
        ),
        _signal(
            name="client_utilization",
            value=client_utilization,
            unit="ratio",
            threshold=settings.redis_client_utilization_critical,
            critical=client_utilization is not None and client_utilization >= settings.redis_client_utilization_critical,
            query=_query(metrics, "connected_clients"),
            description="Connected clients divided by maxclients.",
        ),
        _signal(
            name="command_latency_seconds",
            value=latency,
            unit="seconds",
            threshold=settings.redis_command_latency_seconds_critical,
            critical=latency is not None and latency >= settings.redis_command_latency_seconds_critical,
            query=_query(metrics, "command_latency_seconds"),
            description="Maximum exporter-derived average command execution latency.",
        ),
    ]

    hypotheses: list[Hypothesis] = []

    memory_anomaly = memory_utilization is not None and memory_utilization >= settings.redis_memory_utilization_critical
    memory_support: list[Evidence] = []
    memory_contradict: list[Evidence] = []
    if memory_utilization is not None:
        ev = _evidence(
            source="redis",
            category="memory-pressure",
            summary=(
                f"Redis memory utilization is {memory_utilization:.1%} "
                f"({used / 1024 / 1024:.1f} MiB of {memory_cap / 1024 / 1024:.1f} MiB)."
            ),
            facts={
                "memory_used_bytes": used,
                "memory_cap_bytes": memory_cap,
                "utilization": memory_utilization,
                "evictions_delta": evictions,
            },
            query=_query(metrics, "memory_used_bytes"),
            strong=True,
        )
        (memory_support if memory_anomaly else memory_contradict).append(ev)
    if evictions is not None and evictions >= settings.redis_evictions_delta_critical:
        memory_support.append(_evidence(
            source="redis",
            category="evictions",
            summary=f"Redis evicted {evictions:.0f} keys during the incident window.",
            facts={"evictions_delta": evictions},
            query=_query(metrics, "evictions_delta"),
            strong=True,
        ))
    hypotheses.append(_attach_and_score(
        result,
        Hypothesis(
            id=f"redis-memory-pressure:{target.entity.id}",
            statement=f"Redis memory pressure and eviction are degrading {target.target or target.entity.name}.",
        ),
        memory_support,
        memory_contradict,
        target.trace_evidence_ids,
        memory_anomaly,
    ))

    blocked_anomaly = blocked is not None and blocked >= settings.redis_blocked_clients_critical
    blocked_support: list[Evidence] = []
    blocked_contradict: list[Evidence] = []
    if blocked is not None:
        ev = _evidence(
            source="redis",
            category="blocked-clients",
            summary=f"Redis reports {blocked:.0f} blocked clients.",
            facts={"blocked_clients": blocked},
            query=_query(metrics, "blocked_clients"),
            strong=True,
        )
        (blocked_support if blocked_anomaly else blocked_contradict).append(ev)
    hypotheses.append(_attach_and_score(
        result,
        Hypothesis(
            id=f"redis-blocked-clients:{target.entity.id}",
            statement=f"Blocked Redis clients are stalling requests to {target.target or target.entity.name}.",
        ),
        blocked_support,
        blocked_contradict,
        target.trace_evidence_ids,
        blocked_anomaly,
    ))

    client_anomaly = client_utilization is not None and client_utilization >= settings.redis_client_utilization_critical
    client_support: list[Evidence] = []
    client_contradict: list[Evidence] = []
    if client_utilization is not None:
        ev = _evidence(
            source="redis",
            category="client-saturation",
            summary=f"Redis client utilization is {client_utilization:.1%} ({clients:.0f}/{max_clients:.0f}).",
            facts={"connected_clients": clients, "max_clients": max_clients, "utilization": client_utilization},
            query=_query(metrics, "connected_clients"),
            strong=True,
        )
        (client_support if client_anomaly else client_contradict).append(ev)
    hypotheses.append(_attach_and_score(
        result,
        Hypothesis(
            id=f"redis-client-saturation:{target.entity.id}",
            statement=f"Redis client capacity is saturated for {target.target or target.entity.name}.",
        ),
        client_support,
        client_contradict,
        target.trace_evidence_ids,
        client_anomaly,
    ))

    latency_anomaly = latency is not None and latency >= settings.redis_command_latency_seconds_critical
    latency_support: list[Evidence] = []
    latency_contradict: list[Evidence] = []
    if latency is not None:
        ev = _evidence(
            source="redis",
            category="command-latency",
            summary=f"Redis command latency reached approximately {latency * 1000:.1f} ms.",
            facts={"command_latency_seconds": latency},
            query=_query(metrics, "command_latency_seconds"),
            strong=True,
        )
        (latency_support if latency_anomaly else latency_contradict).append(ev)
    hypotheses.append(_attach_and_score(
        result,
        Hypothesis(
            id=f"redis-command-latency:{target.entity.id}",
            statement=f"Redis command execution latency is degrading {target.target or target.entity.name}.",
        ),
        latency_support,
        latency_contradict,
        target.trace_evidence_ids,
        latency_anomaly,
    ))

    confirmed = sorted(
        (item for item in hypotheses if item.status == HypothesisStatus.CONFIRMED),
        key=lambda item: item.score,
        reverse=True,
    )
    primary = confirmed[0] if confirmed else None

    return StatefulDependencyAnalysis(
        dependency_id=target.entity.id,
        kind="redis",
        name=target.entity.name,
        target=target.target,
        health=_health(signals),
        causal_role=target.causal_role,
        cause=primary.statement if primary else None,
        cause_strength="strong" if primary else "weak",
        hypothesis_id=primary.id if primary else None,
        evidence_ids=list(primary.supporting_evidence) if primary else [],
        signals=signals,
        metadata={"instance_hint": target.instance_hint},
    )


def _remediation(analysis: StatefulDependencyAnalysis) -> list[Remediation]:
    hypothesis = analysis.hypothesis_id or ""
    if "postgres-connection-exhaustion" in hypothesis:
        return [
            Remediation(priority="P1", action="Reduce connection demand or increase PostgreSQL connection capacity only after validating pool sizing.", reason="The dependency RCA shows connection utilization at or above the configured critical threshold."),
            Remediation(priority="P1", action="Inspect application pool concurrency, idle-in-transaction sessions and leaked connections.", reason="Connection exhaustion is usually driven by pool behavior or long-lived sessions, not only server capacity."),
            Remediation(priority="P2", action="Review PgBouncer or equivalent pooling and per-service connection budgets.", reason="Explicit connection budgets reduce cross-service contention."),
        ]
    if "postgres-lock-contention" in hypothesis:
        return [
            Remediation(priority="P1", action="Identify blocking and blocked PostgreSQL sessions before terminating any session.", reason="The RCA found lock pressure on the traced database."),
            Remediation(priority="P1", action="Correlate AccessExclusiveLock activity with migrations and recent schema changes.", reason="Exclusive locks commonly create broad request stalls."),
            Remediation(priority="P2", action="Reduce transaction scope and validate lock_timeout / statement_timeout policy.", reason="Shorter transactions limit future lock blast radius."),
        ]
    if "postgres-deadlocks" in hypothesis:
        return [
            Remediation(priority="P1", action="Inspect deadlock graphs and normalize transaction lock ordering.", reason="The incident window contains a direct deadlock signal."),
            Remediation(priority="P2", action="Validate application retry policy for SQLSTATE 40P01.", reason="Safe bounded retries reduce user-visible impact while the lock ordering is fixed."),
        ]
    if "postgres-replication-lag" in hypothesis:
        return [
            Remediation(priority="P1", action="Inspect replica apply throughput, WAL generation and replica resource saturation.", reason="The RCA found replication lag above the configured threshold."),
            Remediation(priority="P2", action="Route consistency-sensitive reads away from lagging replicas until recovery.", reason="Prevents stale or inconsistent read paths during lag."),
        ]
    if "postgres-long-transaction" in hypothesis:
        return [
            Remediation(priority="P1", action="Identify the oldest PostgreSQL transactions and their owning service before cancellation.", reason="Long-lived transactions can retain locks and delay vacuum."),
            Remediation(priority="P2", action="Enforce transaction duration budgets in application and database policy.", reason="Prevents recurrence of long transaction saturation."),
        ]
    if "kafka-consumer-lag" in hypothesis:
        return [
            Remediation(priority="P1", action="Inspect consumer throughput, processing latency and partition assignment for the affected group.", reason="Consumer lag is above the configured threshold on the traced Kafka path."),
            Remediation(priority="P2", action="Scale consumers only after confirming partition parallelism and downstream capacity.", reason="Adding consumers cannot exceed partition concurrency and can overload dependencies."),
        ]
    if "kafka-under-replicated" in hypothesis or "kafka-offline-partitions" in hypothesis:
        return [
            Remediation(priority="P1", action="Inspect broker health, ISR shrink events and partition leadership for the affected topic.", reason="Kafka replication or leader health is degraded."),
            Remediation(priority="P1", action="Avoid reassignment or broker restart until current leader/ISR state is understood.", reason="Uncoordinated changes can increase partition unavailability."),
        ]
    if "kafka-consumer-members" in hypothesis:
        return [
            Remediation(priority="P1", action="Restore healthy consumer-group membership and inspect rebalance/crash loops.", reason="The traced consumer group has no active members."),
            Remediation(priority="P2", action="Validate session timeout, heartbeat and deployment rollout behavior.", reason="Frequent rebalances can create prolonged consumption gaps."),
        ]
    if "redis-memory-pressure" in hypothesis:
        return [
            Remediation(priority="P1", action="Identify Redis memory growth, largest key classes and eviction policy before raising maxmemory.", reason="The RCA shows memory pressure on the traced Redis dependency."),
            Remediation(priority="P1", action="Validate TTL coverage and cache retention for high-volume keys.", reason="Unbounded key retention is a common driver of memory pressure."),
            Remediation(priority="P2", action="Review maxmemory and eviction policy against workload semantics.", reason="Capacity changes should preserve the intended cache/data-loss behavior."),
        ]
    if "redis-blocked-clients" in hypothesis:
        return [
            Remediation(priority="P1", action="Inspect blocking Redis commands and slow operations before restarting the instance.", reason="Blocked clients are directly visible in the incident window."),
            Remediation(priority="P2", action="Remove or isolate blocking command patterns from latency-sensitive request paths.", reason="Prevents recurrent queueing behind long-running commands."),
        ]
    if "redis-client-saturation" in hypothesis:
        return [
            Remediation(priority="P1", action="Inspect connection-pool reuse and leaked Redis clients before increasing maxclients.", reason="Client utilization is at the configured critical threshold."),
            Remediation(priority="P2", action="Set explicit per-service Redis connection budgets.", reason="Connection budgets prevent noisy-neighbor saturation."),
        ]
    if "redis-command-latency" in hypothesis:
        return [
            Remediation(priority="P1", action="Inspect command-level latency and slowlog for the traced Redis instance.", reason="Exporter-derived command duration is above the configured threshold."),
            Remediation(priority="P2", action="Review expensive command patterns, payload sizes and hot keys.", reason="These are common sources of elevated Redis command latency."),
        ]
    return []


def _can_promote(result: InvestigationResult, analysis: StatefulDependencyAnalysis) -> bool:
    if analysis.causal_role != "origin" or not analysis.hypothesis_id:
        return False
    return (
        result.status == "inconclusive"
        or result.root_cause.hypothesis_id in {None, "trace-causal-propagation"}
    )


async def enrich_with_stateful_dependencies(
    result: InvestigationResult,
    *,
    tools: InvestigationTools,
    settings: Settings,
) -> InvestigationResult:
    if not settings.stateful_dependency_analysis_enabled:
        return result

    targets = _targets(result)
    if not targets:
        return result

    analyses: list[StatefulDependencyAnalysis] = []
    for target in targets[: settings.stateful_dependency_max_targets]:
        try:
            if target.kind == "postgresql":
                analysis = await _postgres(result, target, tools, settings)
            elif target.kind == "kafka":
                analysis = await _kafka(result, target, tools, settings)
            elif target.kind == "redis":
                analysis = await _redis(result, target, tools, settings)
            else:
                continue
        except Exception as exc:
            result.evidence.append(Evidence(
                source=target.kind,
                category="dependency-telemetry-error",
                summary=f"Stateful RCA could not collect {target.kind} telemetry for {target.entity.name}.",
                facts={"dependency": target.entity.name, "error": str(exc)},
                strength="weak",
            ))
            continue

        analyses.append(analysis)

    result.stateful_dependencies.extend(analyses)

    promotable = [
        analysis
        for analysis in analyses
        if _can_promote(result, analysis)
    ]
    if not promotable:
        return result

    hypothesis_by_id = {item.id: item for item in result.hypotheses}
    promotable.sort(
        key=lambda analysis: hypothesis_by_id.get(analysis.hypothesis_id or "", Hypothesis(id="none", statement="")).score,
        reverse=True,
    )
    primary = promotable[0]
    hypothesis = hypothesis_by_id.get(primary.hypothesis_id or "")
    if not hypothesis or hypothesis.status != HypothesisStatus.CONFIRMED:
        return result

    result.status = "resolved"
    result.root_cause = RootCause(
        title=primary.cause or f"{primary.kind} dependency failure",
        summary=(
            f"Trace causality identifies {primary.name} as the downstream origin and the "
            f"{primary.kind} adapter independently confirms dependency-specific runtime evidence. "
            "The stateful dependency evidence is therefore more specific than the trace-only origin."
        ),
        hypothesis_id=hypothesis.id,
        evidence_strength="strong",
        supporting_signals=len(hypothesis.supporting_evidence),
        contradicting_signals=len(hypothesis.contradicting_evidence),
    )
    result.remediation = _remediation(primary)
    result.timeline.append(TimelineEvent(
        timestamp=result.incident.starts_at,
        source=primary.kind,
        message=f"Stateful dependency RCA confirmed: {primary.cause}.",
    ))
    result.timeline.sort(key=lambda item: item.timestamp)
    return result
