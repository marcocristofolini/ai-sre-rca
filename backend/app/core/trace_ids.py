"""Canonical wire identity for OpenTelemetry's nonzero 128-bit trace IDs."""
import re


def canonical_trace_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-fA-F]{1,32}', value) or int(value,16) == 0:
        return None
    return value.lower().zfill(32)
