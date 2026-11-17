"""Deterministic alert grouping and noise reporting.

Grouping records which alerts describe the same problem; it never changes an
investigation, a verdict or which alerts are investigated. No model is involved.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
from itertools import combinations
import json
from statistics import median
from typing import Iterable

from app.schemas import AlertOccurrence

# A firing occurrence that has not been re-notified for this long is treated as
# closed for grouping, so a lost "resolved" notification cannot keep a group open forever.
OPEN_STALE_AFTER = timedelta(hours=24)

_TARGET_LABELS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("service", ("service", "service_name", "service.name", "app")),
    ("pod", ("pod",)),
    ("node", ("node",)),
)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def alert_fingerprint(labels: dict[str, str]) -> str:
    """Stable identity of one alert series, derived from its full label set."""
    canonical = json.dumps(sorted(labels.items()), separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


def group_target(labels: dict[str, str]) -> tuple[str, str]:
    """Most specific stable scope an alert names: service, then pod, then node."""
    for kind, keys in _TARGET_LABELS:
        for key in keys:
            value = labels.get(key)
            if value:
                return kind, value[:255]
    return "alert", labels.get("alertname", "PrometheusAlert")[:255]


def group_key(tenant_id: str, cluster: str, namespace: str | None, target_kind: str, target: str) -> str:
    """Tenant and cluster are part of the key, so groups never span either."""
    canonical = json.dumps([tenant_id, cluster, namespace or "", target_kind, target], separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def occurrence_from_alert(
    *,
    tenant_id: str,
    labels: dict[str, str],
    status: str,
    starts_at: datetime | None,
    ends_at: datetime | None,
    fingerprint: str | None,
    received_at: datetime,
) -> AlertOccurrence:
    received_at = _utc(received_at)
    started = _utc(starts_at) if starts_at else received_at
    resolved = status == "resolved"
    ended = None
    if resolved:
        ended = _utc(ends_at) if ends_at else received_at
        if ended < started:
            ended = started
    cluster = labels.get("cluster", "unknown")[:255]
    namespace = (labels.get("namespace") or None)
    namespace = namespace[:255] if namespace else None
    target_kind, target = group_target(labels)
    return AlertOccurrence(
        tenant_id=tenant_id,
        cluster=cluster,
        namespace=namespace,
        target_kind=target_kind,
        target=target,
        group_key=group_key(tenant_id, cluster, namespace, target_kind, target),
        alert_name=labels.get("alertname", "PrometheusAlert")[:255],
        severity=labels.get("severity", "warning")[:64],
        fingerprint=(fingerprint or alert_fingerprint(labels))[:64],
        status="resolved" if resolved else "firing",
        starts_at=started,
        ends_at=ended,
        received_at=received_at,
        last_received_at=received_at,
    )


def last_activity(item: AlertOccurrence) -> datetime:
    return max(_utc(item.starts_at), _utc(item.ends_at)) if item.ends_at else _utc(item.starts_at)


def merge_alert_notification(existing: AlertOccurrence, incoming: AlertOccurrence) -> AlertOccurrence:
    """Fold a repeated notification for the same occurrence into the stored one.

    A resolution is kept once seen: a late firing repeat does not reopen it.
    """
    update: dict = {
        "last_received_at": max(_utc(existing.last_received_at), _utc(incoming.last_received_at)),
        "notifications": existing.notifications + 1,
    }
    if incoming.status == "resolved":
        update.update(status="resolved", ends_at=incoming.ends_at)
    return existing.model_copy(update=update)


def select_group(
    candidates: Iterable[AlertOccurrence],
    occurrence: AlertOccurrence,
    window: timedelta,
) -> str | None:
    """Return the group an occurrence joins, or None when it starts a new one.

    Candidates must already share the occurrence's tenant and group key. An
    occurrence joins a group that is still open (a recently notified firing
    alert) or whose last activity is within ``window`` of its own start.
    """
    started = _utc(occurrence.starts_at)
    best: tuple[datetime, str] | None = None
    for item in candidates:
        if item.tenant_id != occurrence.tenant_id or item.group_key != occurrence.group_key:
            continue
        activity = last_activity(item)
        still_open = (
            item.status == "firing"
            and _utc(occurrence.received_at) - _utc(item.last_received_at) <= OPEN_STALE_AFTER
        )
        if still_open or abs(started - activity) <= window:
            if best is None or activity > best[0]:
                best = (activity, item.group_id)
    return best[1] if best else None


def build_report(
    occurrences: list[AlertOccurrence],
    *,
    now: datetime,
    since: datetime,
    noise_resolve_seconds: int,
) -> dict:
    """Summarize grouped problems, short-lived alerts and alerts that fire together."""
    groups: dict[tuple[str, str], list[AlertOccurrence]] = defaultdict(list)
    by_alert: dict[tuple[str, str, str], list[AlertOccurrence]] = defaultdict(list)
    for item in occurrences:
        groups[(item.tenant_id, item.group_id)].append(item)
        by_alert[(item.tenant_id, item.cluster, item.alert_name)].append(item)

    problems = []
    co_firing: Counter[tuple[str, str, str, str]] = Counter()
    for (tenant_id, group_id), items in groups.items():
        first = min(items, key=lambda entry: _utc(entry.starts_at))
        names = sorted({entry.alert_name for entry in items})
        problems.append({
            "group_id": group_id,
            "tenant_id": tenant_id,
            "cluster": first.cluster,
            "namespace": first.namespace,
            "target_kind": first.target_kind,
            "target": first.target,
            "alert_names": names,
            "occurrences": len(items),
            "first_seen": _utc(first.starts_at).isoformat(),
            "last_seen": max(last_activity(entry) for entry in items).isoformat(),
            "open": any(entry.status == "firing" for entry in items),
        })
        for left, right in combinations(names, 2):
            co_firing[(tenant_id, first.cluster, left, right)] += 1

    alerts = []
    for (tenant_id, cluster, alert_name), items in by_alert.items():
        durations = [
            (_utc(entry.ends_at) - _utc(entry.starts_at)).total_seconds()
            for entry in items
            if entry.status == "resolved" and entry.ends_at is not None
        ]
        short_lived = sum(duration <= noise_resolve_seconds for duration in durations)
        alerts.append({
            "tenant_id": tenant_id,
            "cluster": cluster,
            "alert_name": alert_name,
            "occurrences": len(items),
            "resolved": len(durations),
            "short_lived": short_lived,
            "median_open_seconds": median(durations) if durations else None,
            # At least half of all occurrences closed on their own within the threshold.
            "noise_candidate": short_lived > 0 and short_lived * 2 >= len(items),
        })

    problems.sort(key=lambda entry: (entry["last_seen"], entry["group_id"]), reverse=True)
    alerts.sort(key=lambda entry: (-entry["short_lived"], -entry["occurrences"], entry["cluster"], entry["alert_name"]))
    pairs = [
        {"tenant_id": tenant_id, "cluster": cluster, "alerts": [left, right], "groups": count}
        for (tenant_id, cluster, left, right), count in co_firing.items()
    ]
    pairs.sort(key=lambda entry: (-entry["groups"], entry["cluster"], entry["alerts"]))
    return {
        "generated_at": _utc(now).isoformat(),
        "since": _utc(since).isoformat(),
        "noise_resolve_seconds": noise_resolve_seconds,
        "totals": {
            "occurrences": len(occurrences),
            "alert_names": len(by_alert),
            "problems": len(problems),
            "open_problems": sum(entry["open"] for entry in problems),
            "noise_candidates": sum(entry["noise_candidate"] for entry in alerts),
        },
        "problems": problems,
        "alerts": alerts,
        "co_firing": pairs,
    }
