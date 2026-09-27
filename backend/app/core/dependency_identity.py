from __future__ import annotations

from typing import Any


def dependency_instance(metadata: dict[str, Any] | None) -> str | None:
    """Build a stable endpoint identity from OpenTelemetry server attributes."""
    values = metadata or {}
    explicit = str(values.get("dependency.instance") or "").strip()
    if explicit:
        return explicit

    address = str(values.get("server.address") or "").strip()
    if not address:
        return None

    port_value = values.get("server.port")
    port = str(port_value).strip() if port_value not in {None, ""} else ""
    if not port:
        return address

    if address.startswith("[") and address.endswith("]"):
        return f"{address}:{port}"
    if ":" in address:
        return f"[{address}]:{port}"
    return f"{address}:{port}"
