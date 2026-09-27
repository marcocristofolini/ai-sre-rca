from __future__ import annotations

import re
from urllib.parse import quote


_RE2_META = re.compile(r'([\\.^$|?*+(){}\[\]])')


def quoted_string(value: str) -> str:
    """Escape a value embedded in a double-quoted PromQL/LogQL/TraceQL string."""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def re2_literal(value: str) -> str:
    """Escape only RE2 metacharacters; ordinary hostname characters stay literal."""
    return _RE2_META.sub(r'\\\1', str(value))


def prom_regex_literal(value: str) -> str:
    """Return an RE2 literal safely embedded inside a PromQL quoted matcher."""
    return quoted_string(re2_literal(value))


def path_segment(value: str) -> str:
    """Percent-encode a single URL path segment, including slashes."""
    return quote(str(value), safe="")
