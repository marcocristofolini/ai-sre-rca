from __future__ import annotations

import asyncio
import base64
from collections import Counter
from typing import Any

from app.config import Settings
from app.core.dependency_identity import dependency_instance
from app.core.query_safety import quoted_string
from app.core.scoring import score_hypothesis
from app.core.trace_ids import canonical_trace_id
from app.integrations.base import InvestigationTools
from app.integrations.telemetry import ServiceRed, TempoTracesSource
from app.schemas import (
    Entity,
    Evidence,
    GraphEdge,
    Hypothesis,
    HypothesisStatus,
    IncidentInput,
    InvestigationResult,
    Remediation,
    RootCause,
    TraceAnalysis,
    TraceCausalHop,
)


def _otel_value(value: dict[str, Any]) -> Any:
    if not isinstance(value, dict):
        return value
    for key in (
        "stringValue",
        "intValue",
        "doubleValue",
        "boolValue",
        "bytesValue",
    ):
        if key in value:
            return value[key]
    if "arrayValue" in value:
        return [_otel_value(item) for item in value["arrayValue"].get("values", [])]
    if "kvlistValue" in value:
        return {
            item.get("key", ""): _otel_value(item.get("value", {}))
            for item in value["kvlistValue"].get("values", [])
        }
    return None


def _attributes(items: list[dict[str, Any]] | None) -> dict[str, Any]:
    return {
        str(item.get("key")): _otel_value(item.get("value", {}))
        for item in (items or [])
        if item.get("key")
    }


def _id_to_hex(value: str | None) -> str:
    if not value:
        return ""
    value = str(value)
    if len(value) in (16,32) and all(char in '0123456789abcdefABCDEF' for char in value):
        return value.lower()
    try:
        raw = base64.b64decode(value, validate=True)
        if raw:
            return raw.hex()
    except Exception:
        pass
    return value.lower()


def _status(span: dict[str, Any], attrs: dict[str, Any]) -> str:
    code = span.get("status", {}).get("code")
    if code in {"STATUS_CODE_ERROR", "ERROR", 2, "2"}:
        return "error"
    http_status = attrs.get("http.response.status_code", attrs.get("http.status_code"))
    try:
        if int(http_status) >= 500:
            return "error"
    except (TypeError, ValueError):
        pass
    if code in {"STATUS_CODE_OK", "OK", 1, "1"}:
        return "ok"
    return "unset"


def _duration_ms(span: dict[str, Any]) -> float:
    try:
        start = int(span.get("startTimeUnixNano", 0) or 0)
        end = int(span.get("endTimeUnixNano", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, (end - start) / 1_000_000)


def _dependency_metadata(attrs: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "db.system",
        "db.namespace",
        "db.name",
        "server.address",
        "server.port",
        "peer.service",
        "messaging.system",
        "messaging.destination.name",
        "messaging.destination",
        "messaging.consumer.group.name",
        "messaging.kafka.consumer.group",
    )
    return {
        key: attrs.get(key)
        for key in keys
        if attrs.get(key) not in {None, ""}
    }


def _peer_target(attrs: dict[str, Any]) -> tuple[str | None, str | None]:
    database = attrs.get("db.namespace") or attrs.get("db.name")
    db_system = attrs.get("db.system")
    if database or db_system:
        name = "/".join(str(v) for v in (db_system, database) if v)
        return name or "database", "database"

    destination = attrs.get("messaging.destination.name") or attrs.get("messaging.destination")
    messaging = attrs.get("messaging.system")
    if destination or messaging:
        name = "/".join(str(v) for v in (messaging, destination) if v)
        return name or "messaging", "messaging"

    peer = attrs.get("peer.service")
    if peer:
        return str(peer), "external-service"

    address = attrs.get("server.address")
    if address:
        port = attrs.get("server.port")
        return f"{address}:{port}" if port else str(address), "external-service"

    return None, None


def _normalize_spans(payload: dict[str, Any]) -> list[dict[str, Any]]:
    trace = payload.get("trace", payload)
    resource_spans = trace.get("resourceSpans", [])
    normalized: list[dict[str, Any]] = []

    for resource_group in resource_spans:
        resource_attrs = _attributes(resource_group.get("resource", {}).get("attributes"))
        service = str(resource_attrs.get("service.name") or "unknown-service")
        for scope in resource_group.get("scopeSpans", []):
            for span in scope.get("spans", []):
                span_attrs = _attributes(span.get("attributes"))
                target, target_kind = _peer_target(span_attrs)
                normalized.append(
                    {
                        "span_id": _id_to_hex(span.get("spanId")),
                        "parent_span_id": _id_to_hex(span.get("parentSpanId")) or None,
                        "trace_id": _id_to_hex(span.get("traceId")),
                        "service": service,
                        "operation": str(span.get("name") or "unnamed-span"),
                        "kind": str(span.get("kind") or "SPAN_KIND_UNSPECIFIED").removeprefix("SPAN_KIND_").lower(),
                        "status": _status(span, span_attrs),
                        "duration_ms": _duration_ms(span),
                        "start_ns": int(span.get("startTimeUnixNano", 0) or 0),
                        "attributes": {**resource_attrs, **span_attrs},
                        "target": target,
                        "target_kind": target_kind,
                        "dependency_metadata": _dependency_metadata(span_attrs),
                    }
                )
    return [span for span in normalized if span["span_id"]]


def _depth(span_id: str, by_id: dict[str, dict[str, Any]]) -> int:
    depth = 0
    current = by_id.get(span_id)
    visited: set[str] = set()
    while current and current.get("parent_span_id") and current["span_id"] not in visited:
        visited.add(current["span_id"])
        current = by_id.get(str(current["parent_span_id"]))
        depth += 1
    return depth


def _path_to_root(span: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    path = [span]
    visited = {span["span_id"]}
    current = span
    while current.get("parent_span_id"):
        parent = by_id.get(str(current["parent_span_id"]))
        if not parent or parent["span_id"] in visited:
            break
        path.append(parent)
        visited.add(parent["span_id"])
        current = parent
    path.reverse()
    return path


def _has_error_descendant(span_id: str, children: dict[str, list[dict[str, Any]]]) -> bool:
    stack = list(children.get(span_id, []))
    while stack:
        item = stack.pop()
        if item["status"] == "error":
            return True
        stack.extend(children.get(item["span_id"], []))
    return False


def analyze_trace(
    payload: dict[str, Any],
    *,
    trace_id: str,
    source_query: str | None,
    incident_service: str,
    settings: Settings,
) -> TraceAnalysis | None:
    spans = _normalize_spans(payload)
    if not spans:
        return None

    by_id = {span["span_id"]: span for span in spans}
    children: dict[str, list[dict[str, Any]]] = {}
    for span in spans:
        if span.get("parent_span_id"):
            children.setdefault(str(span["parent_span_id"]), []).append(span)

    roots = [span for span in spans if not span.get("parent_span_id") or span["parent_span_id"] not in by_id]
    root = max(roots or spans, key=lambda item: item["duration_ms"])
    errors = [span for span in spans if span["status"] == "error"]

    candidate: dict[str, Any] | None = None
    candidate_reason: str | None = None
    strength = "weak"

    leaf_errors = [span for span in errors if not _has_error_descendant(span["span_id"], children)]
    if leaf_errors:
        candidate = sorted(
            leaf_errors,
            key=lambda item: (-_depth(item["span_id"], by_id), item["start_ns"], -item["duration_ms"]),
        )[0]
        target = candidate.get("target")
        if target and candidate.get("kind") in {"client", "producer"}:
            candidate_reason = f"downstream {candidate.get('target_kind', 'dependency')} call failed"
        else:
            candidate_reason = "deepest error span with no errored descendant"
        strength = "strong" if _depth(candidate["span_id"], by_id) > 0 else "medium"
    else:
        non_root = [span for span in spans if span["span_id"] != root["span_id"]]
        if non_root:
            slowest = max(non_root, key=lambda item: item["duration_ms"])
            threshold = max(
                settings.trace_slow_span_min_ms,
                root["duration_ms"] * settings.trace_slow_span_root_fraction,
            )
            if slowest["duration_ms"] >= threshold:
                candidate = slowest
                candidate_reason = (
                    f"span consumed {slowest['duration_ms']:.1f} ms of "
                    f"{root['duration_ms']:.1f} ms trace duration"
                )
                strength = "medium"

    chain: list[TraceCausalHop] = []
    candidate_service: str | None = None
    candidate_operation: str | None = None
    candidate_instance: str | None = None

    if candidate:
        path = _path_to_root(candidate, by_id)
        for item in path:
            role = "context"
            if item["span_id"] == candidate["span_id"]:
                role = "origin"
            elif item["status"] == "error":
                role = "propagated"
            chain.append(
                TraceCausalHop(
                    service=item["service"],
                    operation=item["operation"],
                    span_id=item["span_id"],
                    parent_span_id=item.get("parent_span_id"),
                    kind=item["kind"],
                    status=item["status"],
                    duration_ms=round(item["duration_ms"], 3),
                    role=role,
                    reason=(
                        candidate_reason
                        if item["span_id"] == candidate["span_id"]
                        else "upstream span on the same trace path"
                    ),
                    metadata=item.get("dependency_metadata", {}),
                )
            )

        candidate_instance = dependency_instance(candidate.get("dependency_metadata"))
        target = candidate.get("target")
        if target and candidate.get("kind") in {"client", "producer"}:
            if chain:
                chain[-1].role = "propagated"
            chain.append(
                TraceCausalHop(
                    service=str(target),
                    operation=candidate["operation"],
                    span_id=f"{candidate['span_id']}:peer",
                    parent_span_id=candidate["span_id"],
                    kind=str(candidate.get("target_kind") or "dependency"),
                    status=candidate["status"],
                    duration_ms=round(candidate["duration_ms"], 3),
                    role="origin",
                    reason=candidate_reason or "downstream dependency failure",
                    metadata=candidate.get("dependency_metadata", {}),
                )
            )
            candidate_service = str(target)
        else:
            candidate_service = candidate["service"]
        candidate_operation = candidate["operation"]

        path_services = {hop.service for hop in chain}
        if incident_service not in path_services and strength == "strong":
            strength = "medium"
            candidate_reason = (
                (candidate_reason + "; " if candidate_reason else "")
                + f"incident service {incident_service} is not on the reconstructed causal path"
            )
            for hop in chain:
                if hop.role == "origin":
                    hop.reason = candidate_reason

    services = sorted({str(span["service"]) for span in spans})
    deployment_context: dict[str, dict[str, Any]] = {}
    context_keys = (
        "service.version",
        "deployment.environment.name",
        "deployment.environment",
        "k8s.namespace.name",
        "k8s.deployment.name",
        "k8s.pod.name",
        "k8s.node.name",
    )
    for span in spans:
        service_name = str(span["service"])
        attrs = span.get("attributes", {})
        context = {
            key: attrs.get(key)
            for key in context_keys
            if attrs.get(key) not in {None, ""}
        }
        if context:
            deployment_context.setdefault(service_name, {}).update(context)

    return TraceAnalysis(
        trace_id=trace_id,
        root_service=root["service"],
        root_operation=root["operation"],
        duration_ms=round(root["duration_ms"], 3),
        error_count=len(errors),
        services=services,
        causal_chain=chain,
        candidate_service=candidate_service,
        candidate_operation=candidate_operation,
        candidate_instance=candidate_instance,
        candidate_reason=candidate_reason,
        candidate_strength=strength,
        source_query=source_query,
        deployment_context=deployment_context,
    )


def _service_for_incident(incident: IncidentInput, result: InvestigationResult) -> str | None:
    if incident.service:
        return incident.service
    for key in ("service", "service_name", "service.name", "app", "job"):
        if incident.labels.get(key):
            return incident.labels[key]
    for entity in result.entities:
        if entity.kind == "Service":
            return entity.name
    return None


async def _trace_red_evidence(
    tools: InvestigationTools,
    service: str,
    incident: IncidentInput,
) -> Evidence | None:
    traces = TempoTracesSource(tools)
    request = ServiceRed(service)
    start, end = incident.query_window(10)
    try:
        red = await traces.red(request, start, end, "30s")
    except Exception:
        return None

    rate_values = list(red.rate)
    error_values = list(red.errors)
    p95_values = list(red.p95)
    if not rate_values and not error_values and not p95_values:
        return None

    latest_rate = rate_values[-1] if rate_values else None
    latest_errors = error_values[-1] if error_values else None
    latest_p95 = p95_values[-1] if p95_values else None
    summary = (
        f"TraceQL RED metrics for {service}: "
        f"rate={latest_rate if latest_rate is not None else 'n/a'} spans/s, "
        f"error_rate={latest_errors if latest_errors is not None else 'n/a'} spans/s, "
        f"p95={latest_p95 if latest_p95 is not None else 'n/a'} seconds."
    )
    return Evidence(
        source="tempo",
        category="trace-red",
        summary=summary,
        facts={
            "service": service,
            "rate_spans_per_second": latest_rate,
            "error_spans_per_second": latest_errors,
            "p95_seconds": latest_p95,
        },
        query=red.provenance.native_query,
        raw_reference=red.provenance.raw_reference,
        strength="medium",
    )


async def enrich_with_trace_causality(
    result: InvestigationResult,
    *,
    tools: InvestigationTools,
    settings: Settings,
) -> InvestigationResult:
    if not settings.trace_analysis_enabled:
        return result

    service = _service_for_incident(result.incident, result)
    if not service:
        return result

    traces = TempoTracesSource(tools)
    start, end = result.incident.query_window(settings.trace_window_minutes)
    try:
        search_results = await traces.search(
            service,
            start,
            end,
            settings.trace_search_limit,
        )
    except Exception:
        return result

    if not search_results:
        return result

    selected = search_results[: settings.trace_fetch_limit]
    payloads = await asyncio.gather(
        *[
            traces.get_trace(
                str(item.get("traceID")),
                start,
                end,
            )
            for item in selected
            if item.get("traceID")
        ],
        return_exceptions=True,
    )

    analyses: list[TraceAnalysis] = []
    verified_trace_ids: set[str] = set()
    trace_evidence: list[Evidence] = []
    selected_with_id = [item for item in selected if item.get("traceID")]

    for item, payload in zip(selected_with_id, payloads):
        if isinstance(payload, Exception) or not payload:
            continue
        if result.incident.workload is not None:
            expected_id = canonical_trace_id(str(item.get('traceID') or ''))
            spans = _normalize_spans(payload)
            if not expected_id or not spans or any(canonical_trace_id(span['trace_id']) != expected_id for span in spans) or not any(span['service'] == service for span in spans):
                continue
            if expected_id in verified_trace_ids:
                continue
            verified_trace_ids.add(expected_id)
        trace_id = expected_id if result.incident.workload is not None else str(item.get("traceID"))
        analysis = analyze_trace(
            payload,
            trace_id=trace_id,
            source_query=str(item.get("_source_query") or ""),
            incident_service=service,
            settings=settings,
        )
        if not analysis:
            continue
        analyses.append(analysis)
        chain_text = " -> ".join(
            f"{hop.service}:{hop.operation}[{hop.status}]"
            for hop in analysis.causal_chain
        ) or "no deterministic causal candidate"
        ev = Evidence(
            source="tempo",
            category="trace-causal-chain",
            summary=(
                f"Trace {trace_id[:16]} root={analysis.root_service}:{analysis.root_operation}; "
                f"errors={analysis.error_count}; causal path={chain_text}."
            ),
            facts={
                "trace_id": trace_id,
                "root_service": analysis.root_service,
                "root_operation": analysis.root_operation,
                "duration_ms": analysis.duration_ms,
                "error_count": analysis.error_count,
                "candidate_service": analysis.candidate_service,
                "candidate_operation": analysis.candidate_operation,
                "candidate_instance": analysis.candidate_instance,
                "candidate_reason": analysis.candidate_reason,
                "causal_chain": [hop.model_dump() for hop in analysis.causal_chain],
            },
            query=analysis.source_query,
            raw_reference=f"tempo://trace/{trace_id}",
            strength=analysis.candidate_strength,
        )
        trace_evidence.append(ev)
        result.evidence.append(ev)

    if not analyses:
        return result

    result.trace_analyses.extend(analyses)
    search_ev = Evidence(
        source="tempo",
        category="trace-search",
        summary=(
            f"Tempo returned {len(search_results)} representative traces for service {service}; "
            f"{len(analyses)} were fetched and structurally analyzed."
        ),
        facts={
            "service": service,
            "searched": len(search_results),
            "analyzed": len(analyses),
            "trace_ids": [analysis.trace_id for analysis in analyses],
        },
        query=f'{{ resource.service.name = "{quoted_string(service)}" }}',
        raw_reference="tempo:/api/search",
        strength="medium",
    )
    result.evidence.append(search_ev)

    red_ev = await _trace_red_evidence(tools, service, result.incident)
    if red_ev:
        result.evidence.append(red_ev)

    graph_red_edges: list[dict[str, Any]] = []
    if not any(ev.category == "service-graph" for ev in result.evidence):
        try:
            graph_red_edges = await tools.service_graph(service, result.incident.ends_at or result.incident.starts_at)
        except Exception:
            graph_red_edges = []
        if graph_red_edges:
            graph_red_ev = Evidence(
                source="opentelemetry",
                category="service-graph",
                summary=(
                    f"Trace-derived service graph exposed {len(graph_red_edges)} dependency edges "
                    f"with request, failure and p95 latency signals around {service}."
                ),
                facts={"service": service, "edges": graph_red_edges},
                query=graph_red_edges[0].get("query"),
                raw_reference="prometheus:traces_service_graph_*",
                strength="medium",
            )
            result.evidence.append(graph_red_ev)

    entity_ids = {entity.id for entity in result.entities}
    existing_edges = {
        (edge.source, edge.target, edge.relationship, tuple(edge.evidence_ids))
        for edge in result.graph_edges
    }

    def dependency_entity_id(service_name: str, metadata: dict[str, Any] | None = None) -> str:
        instance = dependency_instance(metadata)
        return f"dependency:{service_name}@{instance}" if instance else f"dependency:{service_name}"

    def ensure_entity(entity_id: str, name: str, kind: str, metadata: dict[str, Any] | None = None) -> None:
        clean_metadata = {
            key: value
            for key, value in (metadata or {}).items()
            if value is not None
        }
        if entity_id in entity_ids:
            existing = next((entity for entity in result.entities if entity.id == entity_id), None)
            if existing:
                existing.metadata.update(clean_metadata)
            return
        result.entities.append(
            Entity(id=entity_id, kind=kind, name=name, metadata=clean_metadata)
        )
        entity_ids.add(entity_id)

    evidence_by_trace = {
        str(ev.facts.get("trace_id")): ev
        for ev in trace_evidence
        if ev.facts.get("trace_id")
    }

    def traced_dependency_instances(service_name: str) -> set[str]:
        return {
            instance
            for analysis in analyses
            for hop in analysis.causal_chain
            if hop.service == service_name
            and hop.kind in {"database", "messaging", "external-service"}
            and (instance := dependency_instance(hop.metadata))
        }

    if graph_red_edges:
        graph_ev = next(
            (
                ev for ev in result.evidence
                if ev.category == "service-graph" and ev.source == "opentelemetry"
            ),
            None,
        )
        for edge in graph_red_edges:
            client = str(edge.get("client") or "")
            server = str(edge.get("server") or "")
            if not client or not server:
                continue
            client_id = f"service:{client}"
            server_id = f"service:{server}"
            ensure_entity(client_id, client, "Service")
            connection_type = str(edge.get("connection_type") or "unset")
            server_kind = "TraceDependency" if connection_type in {"database", "messaging_system", "virtual_node"} else "Service"
            server_metadata: dict[str, Any] = {"connection_type": connection_type}
            if server_kind == "TraceDependency":
                instances = traced_dependency_instances(server)
                if len(instances) == 1:
                    instance = next(iter(instances))
                    server_metadata["dependency.instance"] = instance
                server_entity_id = dependency_entity_id(server, server_metadata)
            else:
                server_entity_id = server_id
            ensure_entity(server_entity_id, server, server_kind, server_metadata)
            evidence_ids = [graph_ev.id] if graph_ev else []
            key = (client_id, server_entity_id, "calls", tuple(evidence_ids))
            if key in existing_edges:
                continue
            result.graph_edges.append(
                GraphEdge(
                    source=client_id,
                    target=server_entity_id,
                    relationship="calls",
                    metadata={
                        "requests": edge.get("requests"),
                        "failures": edge.get("failures"),
                        "error_ratio": edge.get("error_ratio"),
                        "p95_seconds": edge.get("p95_seconds"),
                        "connection_type": connection_type,
                    },
                    evidence_ids=evidence_ids,
                )
            )
            existing_edges.add(key)

    existing_deployments = {
        entity.name: entity.id
        for entity in result.entities
        if entity.kind == "Deployment"
    }
    argo_revision = next(
        (
            str(ev.facts.get("revision") or "")
            for ev in result.evidence
            if ev.source == "argocd" and ev.category == "change-correlation"
        ),
        "",
    )

    for analysis in analyses:
        for traced_service, context in analysis.deployment_context.items():
            deployment_name = str(context.get("k8s.deployment.name") or "")
            namespace = str(context.get("k8s.namespace.name") or "")
            service_version = str(context.get("service.version") or "")
            if not deployment_name and not service_version:
                continue

            matches_revision = bool(
                service_version
                and argo_revision
                and (
                    argo_revision.startswith(service_version)
                    or service_version.startswith(argo_revision)
                )
            )
            correlation_ev = Evidence(
                source="tempo",
                category="trace-deployment-correlation",
                summary=(
                    f"Trace resource attributes map service {traced_service} "
                    + (
                        f"to Deployment {namespace + '/' if namespace else ''}{deployment_name}"
                        if deployment_name
                        else "to a deployed service version"
                    )
                    + (f" at service.version={service_version}" if service_version else "")
                    + (" matching the Argo CD revision." if matches_revision else ".")
                ),
                facts={
                    "trace_id": analysis.trace_id,
                    "service": traced_service,
                    "deployment": deployment_name or None,
                    "namespace": namespace or None,
                    "service_version": service_version or None,
                    "argo_revision": argo_revision or None,
                    "matches_argo_revision": matches_revision,
                    "resource_attributes": context,
                },
                raw_reference=f"tempo://trace/{analysis.trace_id}",
                strength="strong" if deployment_name else "medium",
            )
            result.evidence.append(correlation_ev)

            source_id = f"service:{traced_service}"
            ensure_entity(source_id, traced_service, "Service")
            if deployment_name:
                deployment_id = existing_deployments.get(deployment_name)
                if not deployment_id:
                    deployment_id = f"deployment:{namespace or 'unknown'}:{deployment_name}"
                    ensure_entity(
                        deployment_id,
                        deployment_name,
                        "Deployment",
                        {"namespace": namespace or None, "source": "trace-resource"},
                    )
                    existing_deployments[deployment_name] = deployment_id
                key = (source_id, deployment_id, "runs_as", (correlation_ev.id,))
                if key not in existing_edges:
                    result.graph_edges.append(
                        GraphEdge(
                            source=source_id,
                            target=deployment_id,
                            relationship="runs_as",
                            metadata={
                                "namespace": namespace or None,
                                "service_version": service_version or None,
                                "matches_argo_revision": matches_revision,
                            },
                            evidence_ids=[correlation_ev.id],
                        )
                    )
                    existing_edges.add(key)

    for analysis in analyses:
        ev = evidence_by_trace.get(analysis.trace_id)
        for hop in analysis.causal_chain:
            kind = "TraceDependency" if hop.kind in {"database", "messaging", "external-service"} else "Service"
            entity_id = (
                dependency_entity_id(hop.service, hop.metadata)
                if kind == "TraceDependency"
                else f"service:{hop.service}"
            )
            ensure_entity(
                entity_id,
                hop.service,
                kind,
                {
                    "trace_role": hop.role,
                    "operation": hop.operation,
                    "dependency_kind": hop.kind if kind == "TraceDependency" else None,
                    **hop.metadata,
                },
            )
        for source_hop, target_hop in zip(analysis.causal_chain, analysis.causal_chain[1:]):
            if source_hop.service == target_hop.service:
                continue
            source_id = (
                dependency_entity_id(source_hop.service, source_hop.metadata)
                if source_hop.kind in {"database", "messaging", "external-service"}
                else f"service:{source_hop.service}"
            )
            target_id = (
                dependency_entity_id(target_hop.service, target_hop.metadata)
                if target_hop.kind in {"database", "messaging", "external-service"}
                else f"service:{target_hop.service}"
            )
            evidence_ids = [ev.id] if ev else []
            key = (source_id, target_id, "trace_calls", tuple(evidence_ids))
            if key in existing_edges:
                continue
            result.graph_edges.append(
                GraphEdge(
                    source=source_id,
                    target=target_id,
                    relationship="trace_calls",
                    metadata={
                        "trace_id": analysis.trace_id,
                        "source_operation": source_hop.operation,
                        "target_operation": target_hop.operation,
                        "target_status": target_hop.status,
                        "target_duration_ms": target_hop.duration_ms,
                    },
                    evidence_ids=evidence_ids,
                )
            )
            existing_edges.add(key)

    signatures = [
        (analysis.candidate_service, analysis.candidate_operation, analysis.candidate_instance)
        for analysis in analyses
        if analysis.candidate_service and analysis.candidate_operation
    ]
    counts = Counter(signatures)
    if not counts:
        return result

    (candidate_service, candidate_operation, candidate_instance), repeated = counts.most_common(1)[0]
    matching_evidence = [
        ev
        for analysis, ev in zip(analyses, trace_evidence)
        if (
            analysis.candidate_service == candidate_service
            and analysis.candidate_operation == candidate_operation
            and analysis.candidate_instance == candidate_instance
        )
    ]

    candidate_display = (
        f"{candidate_service}@{candidate_instance}"
        if candidate_instance
        else candidate_service
    )
    hypothesis = Hypothesis(
        id="trace-causal-propagation",
        statement=(
            f"Failures originating at {candidate_display}:{candidate_operation} "
            f"propagated through distributed traces to {service}."
        ),
        required_checks=[
            "repeat the same downstream failure across representative traces",
            "verify parent/child span ancestry",
            "verify error status on the origin span",
        ],
        supporting_evidence=[ev.id for ev in matching_evidence],
    )
    if red_ev and repeated >= 2:
        hypothesis.supporting_evidence.append(red_ev.id)

    evidence_by_id = {ev.id: ev for ev in result.evidence}
    score_hypothesis(hypothesis, evidence_by_id)
    hypothesis.rationale = (
        f"{repeated} of {len(analyses)} analyzed traces converged on "
        f"{candidate_display}:{candidate_operation}. "
        + (
            "The same error origin repeats across independent traces and follows explicit parent/child span ancestry."
            if repeated >= 2
            else "A single trace is not enough to promote trace correlation to a confirmed root cause."
        )
    )
    result.hypotheses.append(hypothesis)

    if (
        result.status == "inconclusive"
        and not any(ev.category == "verified-workload-root-conflict" for ev in result.evidence)
        and hypothesis.status == HypothesisStatus.CONFIRMED
    ):
        supporting = len(hypothesis.supporting_evidence)
        result.status = "resolved"
        result.root_cause = RootCause(
            title=f"Downstream trace failure propagated from {candidate_display}",
            summary=(
                f"{repeated} representative traces independently identify "
                f"{candidate_display}:{candidate_operation} as the deepest failing dependency/operation "
                f"and show the error propagating through parent/child spans to {service}. "
                "This conclusion is trace-derived and does not rely on temporal coincidence alone."
            ),
            hypothesis_id=hypothesis.id,
            evidence_strength="strong",
            supporting_signals=supporting,
            contradicting_signals=0,
        )
        result.remediation = [
            Remediation(
                priority="P1",
                action=f"Inspect {candidate_display}:{candidate_operation} and its direct dependency health before changing upstream capacity.",
                reason="Independent traces place the deepest repeated failure at this downstream hop.",
            ),
            Remediation(
                priority="P1",
                action="Use the affected trace IDs to inspect correlated logs and request attributes for the failing operation.",
                reason="Trace ancestry identifies where the failure propagated; logs provide the operation-specific failure detail.",
            ),
            Remediation(
                priority="P2",
                action="Validate retry, timeout, circuit-breaker and graceful-degradation behavior on the upstream call path.",
                reason="Limits blast radius when the downstream dependency fails again.",
            ),
        ]

    return result
