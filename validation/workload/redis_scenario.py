"""Bounded writes to the disposable validation Redis, never investigator tools."""
from __future__ import annotations

import socket
from typing import BinaryIO

REDIS_HOST = "cache"
REDIS_PORT = 6379
KEY_PREFIX = "ai-sre-validation:"
MAX_KEYS = 256
VALUE_BYTES = 64 * 1024
TARGET_UTILIZATION = 0.93


class RedisCommandError(RuntimeError):
    pass


def read_response(stream: BinaryIO) -> bytes | str | int | None:
    line = stream.readline(4096)
    if not line.endswith(b"\r\n"):
        raise RuntimeError("truncated or oversized Redis response")
    marker, value = line[:1], line[1:-2]
    if marker == b"-":
        raise RedisCommandError(value.decode(errors="replace"))
    if marker == b"+":
        return value.decode()
    if marker == b":":
        return int(value)
    if marker == b"$":
        length = int(value)
        if length == -1:
            return None
        if not 0 <= length <= 4 * 1024 * 1024:
            raise RuntimeError("invalid Redis bulk response length")
        body = stream.read(length)
        if len(body) != length or stream.read(2) != b"\r\n":
            raise RuntimeError("truncated Redis bulk response")
        return body
    raise RuntimeError("unsupported Redis response type")


def command(*parts: str | bytes) -> bytes | str | int | None:
    encoded = [p if isinstance(p, bytes) else p.encode() for p in parts]
    request = f"*{len(encoded)}\r\n".encode() + b"".join(
        f"${len(p)}\r\n".encode() + p + b"\r\n" for p in encoded
    )
    with socket.create_connection((REDIS_HOST, REDIS_PORT), timeout=5) as conn:
        conn.sendall(request)
        with conn.makefile("rb") as stream:
            return read_response(stream)


def memory_state() -> dict[str, float]:
    raw = command("INFO", "memory")
    if not isinstance(raw, bytes):
        raise RuntimeError("Redis INFO did not return a bulk response")
    fields = dict(
        line.split(":", 1) for line in raw.decode().splitlines()
        if ":" in line and not line.startswith("#")
    )
    used, maximum = float(fields["used_memory"]), float(fields["maxmemory"])
    if maximum <= 0:
        raise RuntimeError("validation Redis requires bounded maxmemory")
    return {"used_bytes": used, "max_bytes": maximum, "utilization": used / maximum}


def load_memory() -> dict[str, float]:
    for index in range(MAX_KEYS):
        state = memory_state()
        if state["utilization"] >= TARGET_UTILIZATION:
            return state
        command("SET", f"{KEY_PREFIX}{index}", b"x" * VALUE_BYTES)
    raise RuntimeError("bounded Redis loader did not reach memory pressure")


def fail_write() -> None:
    try:
        command("SET", f"{KEY_PREFIX}probe", b"x" * (2 * 1024 * 1024))
    except RedisCommandError as exc:
        if not str(exc).startswith("OOM"):
            raise
        raise RedisCommandError("OOM: validation write rejected at maxmemory") from exc
    raise RuntimeError("validation Redis unexpectedly accepted the failure probe")


def recover_memory() -> dict[str, float]:
    # Delete only the bounded, fixed validation key set, never FLUSHDB/FLUSHALL.
    command("DEL", *(f"{KEY_PREFIX}{i}" for i in range(MAX_KEYS)), f"{KEY_PREFIX}probe")
    return memory_state()


def healthy_request() -> None:
    command("SET", f"{KEY_PREFIX}healthy", "ok")
    if command("GET", f"{KEY_PREFIX}healthy") != b"ok":
        raise RuntimeError("Redis recovery read did not return the written value")
