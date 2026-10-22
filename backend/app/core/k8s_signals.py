from __future__ import annotations

from typing import Any

from app.schemas import IncidentInput


def container_statuses(pod: dict[str, Any]) -> list[dict[str, Any]]:
    status = pod.get("status", {})
    return [
        *status.get("initContainerStatuses", []),
        *status.get("containerStatuses", []),
        *status.get("ephemeralContainerStatuses", []),
    ]


def terminated_states(pod: dict[str, Any]) -> list[dict[str, Any]]:
    states: list[dict[str, Any]] = []
    for container in container_statuses(pod):
        current = container.get("state", {}).get("terminated")
        previous = container.get("lastState", {}).get("terminated")
        if current:
            states.append({"container": container.get("name"), "source": "state", **current})
        if previous:
            states.append({"container": container.get("name"), "source": "lastState", **previous})
    return states


def waiting_states(pod: dict[str, Any]) -> list[dict[str, Any]]:
    states: list[dict[str, Any]] = []
    for container in container_statuses(pod):
        waiting = container.get("state", {}).get("waiting")
        if waiting:
            states.append({"container": container.get("name"), **waiting})
    return states


def restart_count(pod: dict[str, Any]) -> int:
    return sum(int(item.get("restartCount", 0) or 0) for item in container_statuses(pod))


def event_text(events: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"{item.get('reason', '')} {item.get('message', '')}".strip()
        for item in events
    ).lower()


def classify_pod_scenario(
    incident: IncidentInput,
    pod: dict[str, Any],
    events: list[dict[str, Any]],
) -> str:
    alert = incident.alert_name.lower()
    if "keda" in alert:
        return "keda"
    if "hpa" in alert or "horizontalpodautoscaler" in alert:
        return "hpa"

    reason = str(pod.get("status", {}).get("reason", "")).lower()
    message = str(pod.get("status", {}).get("message", "")).lower()
    text = event_text(events)

    if reason == "evicted" or "reason=evicted" in alert or any(item.get("reason") == "Evicted" for item in events):
        return "eviction"

    if "oom" in alert or any(str(item.get("reason", "")).lower() == "oomkilled" for item in terminated_states(pod)):
        return "oomkilled"

    repeated_exit = restart_count(pod) > 0 and any(int(item.get('exitCode', 0) or 0) != 0 for item in terminated_states(pod))
    if "crashloop" in alert or repeated_exit or any(str(item.get("reason", "")).lower() == "crashloopbackoff" for item in waiting_states(pod)):
        return "crashloop"

    if (
        "failedscheduling" in alert
        or any(item.get("reason") == "FailedScheduling" for item in events)
        or (
            str(pod.get("status", {}).get("phase", "")).lower() == "pending"
            and not pod.get("spec", {}).get("nodeName")
            and ("unschedul" in message or "failedscheduling" in text)
        )
    ):
        return "failed-scheduling"

    return "generic-pod"


def condition(resource: dict[str, Any], condition_type: str) -> dict[str, Any] | None:
    for item in resource.get("status", {}).get("conditions", []):
        if item.get("type") == condition_type:
            return item
    return None


def resource_memory_limits(pod: dict[str, Any]) -> dict[str, str]:
    output: dict[str, str] = {}
    for container in pod.get("spec", {}).get("containers", []):
        limit = container.get("resources", {}).get("limits", {}).get("memory")
        if limit:
            output[str(container.get("name", "container"))] = str(limit)
    return output


def scheduler_categories(message: str) -> set[str]:
    lower = message.lower()
    categories: set[str] = set()
    if any(token in lower for token in ("insufficient cpu", "insufficient memory", "too many pods", "insufficient ephemeral-storage")):
        categories.add("insufficient-resources")
    if "untolerated taint" in lower or "taint" in lower and "tolerat" in lower:
        categories.add("taint-toleration-mismatch")
    if any(token in lower for token in ("node affinity", "node selector", "didn't match", "topology spread")):
        categories.add("affinity-mismatch")
    if any(token in lower for token in ("persistentvolumeclaim", "unbound immediate persistentvolumeclaims", "volume node affinity", "volume binding")):
        categories.add("volume-binding")
    return categories


def karpenter_capacity_signal(events: list[dict[str, Any]]) -> bool:
    text = event_text(events)
    return any(
        token in text
        for token in (
            "insufficientcapacity",
            "insufficient capacity",
            "failedprovisioning",
            "failed provisioning",
            "no instance type satisfied",
            "no instance type met",
        )
    )
