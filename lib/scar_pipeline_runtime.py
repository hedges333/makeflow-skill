"""Shared CLI, tracing, circuit-breaking, and JSON formatting runtime for scar pipelines (GH-345).

Composed by ``ImportSyntheticScarsScript`` and ``WriteScarProbesScript`` to keep
``AgySubagentRunner`` minimal while providing validated numeric CLI flags,
per-scar buffered verbose traces, lock-protected stream output, consecutive
transient error circuit-breaking, and post-commit JSON row serialization.
"""

import argparse
import json
import math
import threading
from typing import Any, Dict, List, Optional, TextIO, Tuple

from lib.scar_subagent import AgySubagentError, parse_positive_workers

DEFAULT_TIMEOUT_SEC = 120.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_MAX_ERRORS = 5

JSON_LIST_COLUMNS: Tuple[str, ...] = (
    "workflow_phases",
    "ss_targets",
    "mf_targets",
    "keywords",
    "languages",
    "frameworks",
    "diagnostic_codes",
)


def parse_positive_timeout(value: str) -> float:
  """Validate a CLI ``--timeout`` argument as a finite number greater than zero.

  Args:
    value: Raw CLI argument string supplied to ``--timeout``.

  Returns:
    Parsed positive finite floating-point timeout in seconds.

  Raises:
    argparse.ArgumentTypeError: Raised when ``value`` is non-numeric, NaN,
      infinite, or ``<= 0``.
  """
  try:
    parsed = float(value)
  except (TypeError, ValueError) as exc:
    raise argparse.ArgumentTypeError(
        f"--timeout must be a finite number > 0, got {value!r}."
    ) from exc
  if not math.isfinite(parsed) or parsed <= 0.0:
    raise argparse.ArgumentTypeError(
        f"--timeout must be a finite number > 0, got {value!r}."
    )
  return parsed


def parse_non_negative_int(value: str) -> int:
  """Validate a CLI integer argument as greater than or equal to zero.

  Args:
    value: Raw CLI argument string.

  Returns:
    Parsed integer ``>= 0``.

  Raises:
    argparse.ArgumentTypeError: Raised when ``value`` is not an integer ``>= 0``.
  """
  try:
    parsed = int(value)
  except (TypeError, ValueError) as exc:
    raise argparse.ArgumentTypeError(
        f"must be an integer >= 0, got {value!r}."
    ) from exc
  if parsed < 0:
    raise argparse.ArgumentTypeError(
        f"must be an integer >= 0, got {parsed}."
    )
  return parsed


def parse_positive_int(value: str) -> int:
  """Validate a CLI integer argument as greater than or equal to one.

  Args:
    value: Raw CLI argument string.

  Returns:
    Parsed integer ``>= 1``.

  Raises:
    argparse.ArgumentTypeError: Raised when ``value`` is not an integer ``>= 1``.
  """
  try:
    parsed = int(value)
  except (TypeError, ValueError) as exc:
    raise argparse.ArgumentTypeError(
        f"must be a positive integer >= 1, got {value!r}."
    ) from exc
  if parsed < 1:
    raise argparse.ArgumentTypeError(
        f"must be a positive integer >= 1, got {parsed}."
    )
  return parsed


def add_scar_pipeline_cli_args(
    parser: argparse.ArgumentParser,
    default_timeout: float = DEFAULT_TIMEOUT_SEC,
    default_max_retries: int = DEFAULT_MAX_RETRIES,
    default_max_errors: int = DEFAULT_MAX_ERRORS,
) -> None:
  """Register shared CLI options for ``import-scars`` and ``probe-scars``.

  Args:
    parser: Target ArgumentParser instance to configure.
    default_timeout: Default per-call timeout in seconds.
    default_max_retries: Default retry budget per scar.
    default_max_errors: Default consecutive transient error abort threshold.
  """
  parser.add_argument(
      "--model",
      default=None,
      help="Optional model identifier forwarded verbatim to agy --model.",
  )
  parser.add_argument(
      "--db-path",
      default=None,
      help="Optional path to SQLite scars registry database.",
  )
  parser.add_argument(
      "--workers",
      type=parse_positive_workers,
      default=1,
      help="Maximum concurrent single-scar agy workers (positive integer, default 1).",
  )
  parser.add_argument(
      "--timeout",
      type=parse_positive_timeout,
      default=default_timeout,
      help=(
          "Per-call agy subprocess timeout in seconds "
          f"(finite number > 0, default {default_timeout})."
      ),
  )
  parser.add_argument(
      "--max-retries",
      type=parse_non_negative_int,
      default=default_max_retries,
      help=(
          "Maximum retries per scar for validation or transient agy errors "
          f"(integer >= 0, default {default_max_retries})."
      ),
  )
  parser.add_argument(
      "--max-errors",
      type=parse_positive_int,
      default=default_max_errors,
      help=(
          "Consecutive transient failed scars allowed before aborting batch "
          f"(integer >= 1, default {default_max_errors})."
      ),
  )
  parser.add_argument(
      "--verbose",
      action="store_true",
      default=False,
      help="Emit buffered per-scar attempt diagnostics to stderr.",
  )
  parser.add_argument(
      "--print-result",
      action="store_true",
      default=False,
      help="Print each committed SQLite scar row as indented JSON to stdout.",
  )


class ThreadSafeStreamWriter:
  """Lock-protected adapter writing complete text blocks atomically to a stream."""

  def __init__(
      self,
      stream: TextIO,
      lock: Optional[threading.Lock] = None,
  ) -> None:
    """Initialize ThreadSafeStreamWriter around a target text stream.

    Args:
      stream: Destination text stream (such as ``stdout`` or ``stderr``).
      lock: Optional pre-existing threading.Lock to synchronize writes.
    """
    self._stream = stream
    self._lock = lock if lock is not None else threading.Lock()

  def write(self, text: str) -> None:
    """Write ``text`` atomically under the writer lock.

    Args:
      text: Exact text payload to write.
    """
    if not text:
      return
    with self._lock:
      self._stream.write(text)
      flush_fn = getattr(self._stream, "flush", None)
      if callable(flush_fn):
        flush_fn()

  def write_block(self, text: str) -> None:
    """Write a complete multi-line block atomically with a trailing newline.

    Args:
      text: Buffered diagnostic or JSON text block.
    """
    if not text:
      return
    payload = text if text.endswith("\n") else f"{text}\n"
    self.write(payload)


class ScarAttemptTrace:
  """Per-scar in-memory diagnostic collector for atomic ``--verbose`` output."""

  def __init__(self, scar_id: str, max_attempts: int) -> None:
    """Initialize an isolated attempt trace buffer for one scar.

    Args:
      scar_id: Canonical Makeflow scar ID being processed.
      max_attempts: Total allowed attempts (``max_retries + 1``).
    """
    self.scar_id = scar_id
    self.max_attempts = max_attempts
    self._entries: List[str] = []
    self._total_elapsed_sec: float = 0.0
    self._final_status: str = ""

  def record_attempt(
      self,
      attempt: int,
      outcome: str,
      elapsed_sec: float,
      detail: str = "",
  ) -> None:
    """Append one attempt record to the scar's private memory buffer.

    Args:
      attempt: 1-based attempt number.
      outcome: Short outcome token (for example ``ok``, ``transient_error``,
        ``validation_error``, ``cli_error``, ``aborted``, ``interrupted``).
      elapsed_sec: Monotonic duration of this attempt in seconds.
      detail: Optional error or status detail string. Multiline continuation
        lines are normalized and indented beneath the attempt entry.
    """
    safe_elapsed = max(0.0, float(elapsed_sec))
    self._total_elapsed_sec += safe_elapsed
    self._final_status = outcome
    line = (
        f"  [{self.scar_id}] attempt {attempt}/{self.max_attempts} "
        f"{outcome} ({safe_elapsed:.2f}s)"
    )
    if detail:
      normalized = (
          detail.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
      )
      if normalized:
        indented_detail = "\n".join(
            part if idx == 0 else (f"    {part}" if part else "")
            for idx, part in enumerate(normalized.split("\n"))
        )
        line = f"{line}: {indented_detail}"
    self._entries.append(line)

  def render_block(self, status: Optional[str] = None) -> str:
    """Render all buffered attempts for this scar as one grouped text block.

    Args:
      status: Optional final status label overriding the last recorded outcome.

    Returns:
      Multi-line diagnostic block ending with a newline, or empty string if no
      attempts were recorded.
    """
    if not self._entries:
      return ""
    final_label = status or self._final_status or "done"
    header = (
        f"[verbose] {self.scar_id} status={final_label} "
        f"attempts={len(self._entries)}/{self.max_attempts} "
        f"elapsed={self._total_elapsed_sec:.2f}s"
    )
    return "\n".join([header, *self._entries]) + "\n"


class ConsecutiveErrorBreaker:
  """Circuit breaker tracking consecutive transient failed scars across a batch."""

  def __init__(self, max_errors: int = DEFAULT_MAX_ERRORS) -> None:
    """Initialize ConsecutiveErrorBreaker with a positive failure threshold.

    Args:
      max_errors: Consecutive transient failed scars required to trip.

    Raises:
      ValueError: Raised when ``max_errors < 1``.
    """
    if max_errors < 1:
      raise ValueError(f"max_errors must be >= 1, got {max_errors}.")
    self.max_errors = max_errors
    self.consecutive_errors: int = 0
    self.tripped: bool = False
    self.last_scar_id: Optional[str] = None
    self.last_error: str = ""

  def record_success(self) -> None:
    """Reset the consecutive transient error streak after a committed scar."""
    self.consecutive_errors = 0

  def record_transient_failure(self, scar_id: str, error: str) -> bool:
    """Record one scar that exhausted transient retries and evaluate threshold.

    Args:
      scar_id: Canonical ID of the failed scar.
      error: Diagnostic message from the final failed attempt.

    Returns:
      True when ``consecutive_errors >= max_errors`` and the breaker trips.
    """
    self.consecutive_errors += 1
    self.last_scar_id = scar_id
    self.last_error = error
    if self.consecutive_errors >= self.max_errors:
      self.tripped = True
    return self.tripped

  def format_abort_message(
      self,
      committed_count: int,
      total_count: int,
      stop_stats: Dict[str, int],
  ) -> str:
    """Format an actionable batch-abort message when the breaker trips.

    Args:
      committed_count: Number of scars committed before aborting.
      total_count: Total pending scars selected for the batch.
      stop_stats: Drain metrics populated by ``iter_bounded_worker_results()``.

    Returns:
      Single-line diagnostic abort message ending with a newline.
    """
    return (
        f"Aborted batch after {self.consecutive_errors}/{self.max_errors} "
        f"consecutive transient error(s) (last scar={self.last_scar_id}: "
        f"{self.last_error}; committed={committed_count} of {total_count}, "
        f"cancelled_unstarted={stop_stats.get('cancelled_unstarted', 0)}, "
        f"drained_in_flight={stop_stats.get('drained_in_flight', 0)}).\n"
    )


def normalize_agy_exit_code(exc: AgySubagentError) -> int:
  """Return a positive process exit code derived from an AgySubagentError.

  Args:
    exc: Raised AgySubagentError instance.

  Returns:
    Positive integer exit status code (defaults to ``1``).
  """
  if exc.returncode is not None and exc.returncode > 0:
    return exc.returncode
  return 1


def format_batch_start_line(
    agent_name: str,
    pending_count: int,
    workers: int,
    timeout_sec: float,
    max_retries: int,
    max_errors: int,
    model: Optional[str],
) -> str:
  """Format the ``--verbose`` batch start line for ``stderr``.

  Args:
    agent_name: Target ``agy`` agent name.
    pending_count: Number of pending scars selected for processing.
    workers: Configured maximum concurrent worker count.
    timeout_sec: Configured per-call timeout in seconds.
    max_retries: Configured maximum retry count per scar.
    max_errors: Configured consecutive transient error threshold.
    model: Optional model override or ``None``.

  Returns:
    Single formatted verbose batch-start line ending with a newline.
  """
  return (
      f"[verbose] Starting {agent_name}: pending={pending_count} "
      f"workers={workers} timeout={timeout_sec}s max_retries={max_retries} "
      f"max_errors={max_errors} model={model or 'default'}\n"
  )


def format_batch_stop_line(
    reason: str,
    committed_count: int,
    total_count: int,
    failed_count: int,
    stop_stats: Dict[str, int],
) -> str:
  """Format the ``--verbose`` early-stop summary line for ``stderr``.

  Args:
    reason: Short early-stop reason (``cli_error``, ``signal``, or
      ``max_errors``).
    committed_count: Scars committed before stopping.
    total_count: Total scars originally selected for the batch.
    failed_count: Scars recorded as failed before stopping.
    stop_stats: Dictionary populated by ``iter_bounded_worker_results()``.

  Returns:
    Single formatted verbose early-stop line ending with a newline.
  """
  return (
      f"[verbose] Stopped early: reason={reason} "
      f"committed={committed_count}/{total_count} failed={failed_count} "
      f"cancelled_unstarted={stop_stats.get('cancelled_unstarted', 0)} "
      f"drained_in_flight={stop_stats.get('drained_in_flight', 0)}\n"
  )


def _decode_json_list_column(raw_value: Any) -> List[Any]:
  """Decode a SQLite JSON-encoded list column into a Python list."""
  if isinstance(raw_value, list):
    return raw_value
  if raw_value is None:
    return []
  if isinstance(raw_value, str):
    stripped = raw_value.strip()
    if not stripped:
      return []
    try:
      parsed = json.loads(stripped)
      if isinstance(parsed, list):
        return parsed
    except ValueError:
      return [stripped]
  return [raw_value]


def format_saved_scar_row_json(row: Dict[str, Any]) -> str:
  """Format a committed SQLite ``scars`` row dictionary as indented JSON.

  Decodes JSON-encoded list columns (``workflow_phases``, ``ss_targets``,
  ``mf_targets``, ``keywords``, ``languages``, ``frameworks``,
  ``diagnostic_codes``) into native JSON arrays while preserving scalar and
  NULL fields verbatim.

  Args:
    row: Dictionary returned by ``select_scar_row_by_id()``.

  Returns:
    Pretty-printed JSON object string with 2-space indentation.
  """
  formatted: Dict[str, Any] = dict(row)
  for col_name in JSON_LIST_COLUMNS:
    if col_name in formatted:
      formatted[col_name] = _decode_json_list_column(formatted[col_name])
  return json.dumps(formatted, indent=2, ensure_ascii=False)

