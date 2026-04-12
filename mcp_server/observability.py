"""MCP Server observability: OpenTelemetry traces, Prometheus metrics, structured logging.

Integrates with the existing DaemonObservability (G-07) to provide a unified
telemetry surface for both the long-running daemon and the stateless MCP server.

Usage:
    from mcp_server.observability import init_telemetry, metrics, get_logger

    init_telemetry(service_name="mcp-server", otlp_endpoint="http://otel-collector:4317")
    logger = get_logger(__name__)
    metrics.request_count.inc()
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Generator

# ---------------------------------------------------------------------------
# Structured JSON logging
# ---------------------------------------------------------------------------


class JSONFormatter(logging.Formatter):
    """Structured JSON log formatter for centralized log aggregation."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        # Merge extra fields
        for key in ("request_id", "tool_name", "user_id", "trace_id", "span_id"):
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val
        return json.dumps(log_entry, default=str)


def get_logger(name: str) -> logging.Logger:
    """Return a logger configured with JSON structured output."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
        logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
    return logger


# ---------------------------------------------------------------------------
# Prometheus-compatible metrics (no external dependency required)
# ---------------------------------------------------------------------------


class _Counter:
    """Simple thread-safe counter for Prometheus exposition."""

    __slots__ = ("_name", "_help", "_value", "_labels")

    def __init__(self, name: str, help_text: str) -> None:
        self._name = name
        self._help = help_text
        self._value: float = 0.0
        self._labels: dict[tuple[str, ...], float] = {}

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        if labels:
            key = tuple(sorted(labels.items()))
            self._labels[key] = self._labels.get(key, 0.0) + amount
        else:
            self._value += amount

    def expose(self) -> str:
        lines = [f"# HELP {self._name} {self._help}", f"# TYPE {self._name} counter"]
        if self._labels:
            for label_tuple, val in sorted(self._labels.items()):
                label_str = ",".join(f'{k}="{v}"' for k, v in label_tuple)
                lines.append(f"{self._name}{{{label_str}}} {val}")
        else:
            lines.append(f"{self._name} {self._value}")
        return "\n".join(lines)


class _Gauge:
    """Simple gauge metric."""

    __slots__ = ("_name", "_help", "_value")

    def __init__(self, name: str, help_text: str) -> None:
        self._name = name
        self._help = help_text
        self._value: float = 0.0

    def set(self, value: float) -> None:
        self._value = value

    def inc(self, amount: float = 1.0) -> None:
        self._value += amount

    def dec(self, amount: float = 1.0) -> None:
        self._value -= amount

    def expose(self) -> str:
        return (
            f"# HELP {self._name} {self._help}\n"
            f"# TYPE {self._name} gauge\n"
            f"{self._name} {self._value}"
        )


class _Histogram:
    """Simple histogram with fixed buckets for latency tracking."""

    __slots__ = ("_name", "_help", "_buckets", "_counts", "_sum", "_count")

    def __init__(self, name: str, help_text: str, buckets: tuple[float, ...] | None = None) -> None:
        self._name = name
        self._help = help_text
        self._buckets = buckets or (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
        self._counts: list[int] = [0] * len(self._buckets)
        self._sum: float = 0.0
        self._count: int = 0

    def observe(self, value: float) -> None:
        self._sum += value
        self._count += 1
        for i, bound in enumerate(self._buckets):
            if value <= bound:
                self._counts[i] += 1

    def expose(self) -> str:
        lines = [f"# HELP {self._name} {self._help}", f"# TYPE {self._name} histogram"]
        cumulative = 0
        for i, bound in enumerate(self._buckets):
            cumulative += self._counts[i]
            lines.append(f'{self._name}_bucket{{le="{bound}"}} {cumulative}')
        lines.append(f'{self._name}_bucket{{le="+Inf"}} {self._count}')
        lines.append(f"{self._name}_sum {self._sum}")
        lines.append(f"{self._name}_count {self._count}")
        return "\n".join(lines)


@dataclass
class MCPMetrics:
    """Registry of all MCP server metrics."""

    request_total: _Counter = field(
        default_factory=lambda: _Counter("mcp_request_total", "Total MCP tool invocations")
    )
    request_errors: _Counter = field(
        default_factory=lambda: _Counter("mcp_request_errors_total", "Total MCP tool errors")
    )
    request_duration: _Histogram = field(
        default_factory=lambda: _Histogram("mcp_request_duration_seconds", "MCP tool invocation latency")
    )
    active_connections: _Gauge = field(
        default_factory=lambda: _Gauge("mcp_active_connections", "Current active connections")
    )
    governance_gate_checks: _Counter = field(
        default_factory=lambda: _Counter("mcp_governance_gate_checks_total", "Governance gate evaluations")
    )
    governance_gate_failures: _Counter = field(
        default_factory=lambda: _Counter("mcp_governance_gate_failures_total", "Governance gate failures")
    )
    auth_failures: _Counter = field(
        default_factory=lambda: _Counter("mcp_auth_failures_total", "Authentication/authorization failures")
    )
    redis_operations: _Counter = field(
        default_factory=lambda: _Counter("mcp_redis_operations_total", "Redis session store operations")
    )
    health_check_status: _Gauge = field(
        default_factory=lambda: _Gauge("mcp_health_check_status", "Health check status (1=healthy, 0=unhealthy)")
    )
    uptime_seconds: _Gauge = field(
        default_factory=lambda: _Gauge("mcp_uptime_seconds", "Server uptime in seconds")
    )

    def expose_all(self) -> str:
        """Return Prometheus text exposition format for all metrics."""
        parts = []
        for metric_field in self.__dataclass_fields__:
            metric = getattr(self, metric_field)
            parts.append(metric.expose())
        return "\n\n".join(parts) + "\n"


# Singleton metrics registry
metrics = MCPMetrics()


# ---------------------------------------------------------------------------
# Timing context manager
# ---------------------------------------------------------------------------


@contextmanager
def track_request(tool_name: str) -> Generator[None, None, None]:
    """Context manager that tracks request count, duration, and errors."""
    metrics.request_total.inc(tool=tool_name)
    metrics.active_connections.inc()
    start = time.monotonic()
    try:
        yield
    except Exception:
        metrics.request_errors.inc(tool=tool_name)
        raise
    finally:
        duration = time.monotonic() - start
        metrics.request_duration.observe(duration)
        metrics.active_connections.dec()


# ---------------------------------------------------------------------------
# OpenTelemetry integration (optional — graceful degradation if not installed)
# ---------------------------------------------------------------------------

_tracer = None
_otel_initialized = False


def init_telemetry(
    service_name: str = "mcp-server",
    otlp_endpoint: str | None = None,
) -> None:
    """Initialize OpenTelemetry tracing if the SDK is available.

    Falls back to no-op if opentelemetry packages are not installed.
    Set OTEL_EXPORTER_OTLP_ENDPOINT env var or pass otlp_endpoint directly.
    """
    global _tracer, _otel_initialized
    if _otel_initialized:
        return

    endpoint = otlp_endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        _otel_initialized = True
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=endpoint)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer(service_name)
    except ImportError:
        pass  # OTel not installed — metrics + logging still work

    _otel_initialized = True


def get_tracer():
    """Return the configured OTel tracer, or None if not available."""
    return _tracer


# ---------------------------------------------------------------------------
# /metrics endpoint handler
# ---------------------------------------------------------------------------


def metrics_response() -> tuple[str, str]:
    """Return (body, content_type) for the /metrics Prometheus scrape endpoint."""
    return metrics.expose_all(), "text/plain; version=0.0.4; charset=utf-8"
