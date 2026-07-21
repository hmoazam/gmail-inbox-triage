"""MLflow experiment tracking for inbox triage runs.

All public functions are best-effort: an MLflow or Databricks SDK auth failure
never crashes the app or the classify run. If tracking is unavailable the triage
still completes normally; the sidebar shows "MLflow unavailable: <reason>".

This is entirely optional — it only does anything if you point it at a Databricks
workspace. Configure via env vars:
  MLFLOW_EXPERIMENT    experiment path (e.g. /Users/you@example.com/gmail_inbox_triage)
  CLEANUP_DATABRICKS_PROFILE   Databricks CLI profile to authenticate with
If MLFLOW_EXPERIMENT is unset, tracking is skipped and the app runs normally.

IMPORTANT: MLFLOW_ENABLE_ASYNC_TRACE_LOGGING=false is set in configure() before
any span is created. MLflow reads this flag lazily at the first span call, so the
order configure() → first span is sufficient to guarantee sync export.
"""
from __future__ import annotations

import os
import contextlib
import logging
from datetime import datetime, timezone
from typing import Iterator

import mlflow
from mlflow.entities import SpanType

from models import EmailThread, ThreadDecision

log = logging.getLogger(__name__)

EXPERIMENT_NAME = os.environ.get("MLFLOW_EXPERIMENT", "")
PROFILE = os.environ.get("CLEANUP_DATABRICKS_PROFILE", "DEFAULT")

# Populated by configure() on success; None means MLflow is unavailable.
_experiment_id: str | None = None
_host: str | None = None
_configure_error: str | None = None


def configure() -> None:
    """Set tracking URI and experiment. Errors stored, not raised."""
    global _experiment_id, _host, _configure_error
    if not EXPERIMENT_NAME:
        _configure_error = "MLFLOW_EXPERIMENT not set — tracking disabled."
        return
    os.environ.setdefault("DATABRICKS_CONFIG_PROFILE", PROFILE)
    os.environ["MLFLOW_ENABLE_ASYNC_TRACE_LOGGING"] = "false"
    try:
        mlflow.set_tracking_uri("databricks")
        exp = mlflow.set_experiment(EXPERIMENT_NAME)
        _experiment_id = exp.experiment_id
        # Resolve host once here so run_url/experiment_url don't need SDK calls.
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient(profile=PROFILE)
        _host = w.config.host.rstrip("/")
    except Exception as exc:  # noqa: BLE001
        _configure_error = f"{type(exc).__name__}: {exc}"
        log.warning("MLflow tracking unavailable: %s", _configure_error)


def is_available() -> bool:
    return _experiment_id is not None


def unavailable_reason() -> str | None:
    return _configure_error


@contextlib.contextmanager
def triage_run(query: str, total_threads: int,
               backend: str, model: str) -> Iterator[str | None]:
    """Wrap one triage session in an MLflow run.

    Yields run_id (str) on success, or None if MLflow is unavailable.
    Any MLflow error inside the body is caught and logged — the classify
    run always completes regardless.
    """
    if not is_available():
        yield None
        return

    run_name = f"triage {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC"
    try:
        with mlflow.start_run(run_name=run_name) as run:
            try:
                mlflow.log_params({
                    "query": query,
                    "total_threads": total_threads,
                    "backend": backend,
                    "model": model or "cli_default",
                })
            except Exception as exc:
                log.warning("MLflow log_params failed: %s", exc)
            yield run.info.run_id
    except Exception as exc:  # noqa: BLE001
        log.warning("MLflow start_run failed: %s", exc)
        yield None


def log_thread_span(thread: EmailThread, decision: ThreadDecision,
                    input_tokens: int = 0, output_tokens: int = 0,
                    cost_usd: float | None = None,
                    quick_triaged: bool = False) -> None:
    """Log one thread as a child span. Best-effort — never raises."""
    if not is_available():
        return
    try:
        span_name = f"{'[quick] ' if quick_triaged else ''}{thread.subject[:60]}"
        with mlflow.start_span(name=span_name, span_type=SpanType.LLM) as span:
            span.set_inputs({
                "thread_id": thread.thread_id,
                "subject": thread.subject,
                "participants": thread.participants[:6],
                "message_count": len(thread.messages),
                "unread": thread.unread,
                "has_unsubscribe": bool(thread.unsubscribe),
                "quick_triaged": quick_triaged,
            })
            out: dict = {
                "category": decision.category,
                "customer_related": decision.customer_related,
                "internal_only": decision.internal_only,
                "needs_response": decision.needs_response,
                "action_on_me": decision.action_on_me,
                "summary": decision.summary,
                "confidence": decision.confidence,
            }
            if not quick_triaged:
                out["input_tokens"] = input_tokens
                out["output_tokens"] = output_tokens
                out["total_tokens"] = input_tokens + output_tokens
                if cost_usd is not None:
                    out["cost_usd"] = cost_usd
            span.set_outputs(out)
    except Exception as exc:  # noqa: BLE001
        log.warning("MLflow log_thread_span failed: %s", exc)


def log_run_summary(usage: dict, quick_count: int) -> None:
    """Log aggregate metrics. Best-effort."""
    if not is_available():
        return
    try:
        mlflow.log_metrics({
            "claude_calls": usage["claude_calls"],
            "quick_triaged": quick_count,
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "total_tokens": usage["total_tokens"],
            **({"cost_usd": usage["cost_usd"]} if usage["cost_usd"] is not None else {}),
        })
    except Exception as exc:  # noqa: BLE001
        log.warning("MLflow log_run_summary failed: %s", exc)


def experiment_url() -> str | None:
    """Deep link to the experiment, or None if unavailable."""
    if _host and _experiment_id:
        return f"{_host}/ml/experiments/{_experiment_id}"
    return None


def run_url(run_id: str | None) -> str | None:
    """Deep link to a specific run, or None if unavailable."""
    if _host and _experiment_id and run_id:
        return f"{_host}/ml/experiments/{_experiment_id}/runs/{run_id}"
    return None
