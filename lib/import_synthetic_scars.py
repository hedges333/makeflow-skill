"""Single-purpose Simple English scar transformation CLI using agy (GH-209, GH-336).

Selects scars missing required Simple English fields ordered by ``id ASC``,
invokes ``agy --agent synthetic-scar-importer ... --print=<prompt>`` one scar
per call, validates JSON output in Python with ScarCandidateValidator, enforces
NULL ``circuit_breaker`` when ``raw_circuit_breaker`` is empty, commits each
scar immediately, and rebuilds FTS5 and ``meta.db_size_bytes`` on exit or
signal.
"""

import argparse
import os
import signal
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

from lib.base_script import BaseScript, ScriptArgumentParser
from lib.scar_candidate import ScarCandidateValidator
from lib.scar_pipeline_runtime import (
    DEFAULT_MAX_ERRORS,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT_SEC,
    ConsecutiveErrorBreaker,
    ScarAttemptTrace,
    ThreadSafeStreamWriter,
    add_scar_pipeline_cli_args,
    format_batch_start_line,
    format_batch_stop_line,
    format_saved_scar_row_json,
    normalize_agy_exit_code,
)
from lib.scar_subagent import (
    AgySubagentError,
    AgySubagentRunner,
    is_transient_agy_error,
    iter_bounded_worker_results,
    wrap_untrusted_scar,
)
from lib.scars_db import (
    enrich_scars_cursor,
    init_scars_db_schema,
    populate_scars_fts,
    resolve_scars_db_path,
    select_scar_row_by_id,
    select_scars_needing_importer,
    update_db_size_meta,
)
from lib.signal_trap import SignalTrap

SIMPLE_ENGLISH_JSON_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["wound", "trap", "circuit_breaker", "reflex"],
    "properties": {
        "wound": {"type": "string"},
        "trap": {"type": "string"},
        "circuit_breaker": {"type": "string"},
        "reflex": {"type": "string"},
    },
    "additionalProperties": False,
}


def build_importer_prompt(
    scar_row: Dict[str, Any],
    validation_errors: Optional[List[str]] = None,
) -> str:
  """Build a single-scar prompt for ``synthetic-scar-importer``.

  Args:
    scar_row: Pending scar row dictionary from SQLite.
    validation_errors: Optional validator errors from the previous attempt.

  Returns:
    Prompt string wrapping untrusted source fields inside an XML airlock.
  """
  sections = [
      "Transform this synthetic scar into Open Technical English (Simple English).",
      "Return only JSON matching the schema with wound, trap, circuit_breaker, and reflex.",
      "",
      "## Source Scar Record",
      *wrap_untrusted_scar([
          ("id", scar_row["id"]),
          ("ss_id", scar_row.get("ss_id") or ""),
          ("title", scar_row.get("title") or ""),
          ("category", scar_row.get("category") or ""),
          ("applies_when", scar_row.get("applies_when") or ""),
          ("raw_wound", scar_row.get("raw_wound") or ""),
          ("raw_trap", scar_row.get("raw_trap") or ""),
          ("raw_circuit_breaker", scar_row.get("raw_circuit_breaker") or ""),
          ("raw_reflex", scar_row.get("raw_reflex") or ""),
      ]),
  ]
  if validation_errors:
    sections.extend([
        "",
        "## Previous Attempt Validation Errors (Fix Every Item)",
        *[f"- {err}" for err in validation_errors],
    ])
  return "\n".join(sections)


class ImportSyntheticScarsScript(BaseScript):
  """Template Method CLI populating Simple English fields via agy subagent."""

  def __init__(self) -> None:
    """Initialize ImportSyntheticScarsScript with default runner settings."""
    super().__init__()
    self.db_path: str = ""
    self.model: Optional[str] = None
    self.workers: int = 1
    self.agy_bin: str = "agy"
    self.max_retries: int = DEFAULT_MAX_RETRIES
    self.timeout_sec: float = DEFAULT_TIMEOUT_SEC
    self.max_errors: int = DEFAULT_MAX_ERRORS
    self.verbose: bool = False
    self.print_result: bool = False
    self.has_fts5: bool = False
    self.conn: Optional[sqlite3.Connection] = None

  def parse_args(self, args: Optional[List[str]] = None) -> None:
    """Parse CLI arguments for ``import_synthetic_scars.py``.

    Args:
      args: Optional CLI argument list.
    """
    super().parse_args(args)
    parser = ScriptArgumentParser(
        prog="import_synthetic_scars.py",
        description=(
            "Write Simple English scar fields one scar per call with agy."
        ),
        epilog=(
            "Examples:\n"
            "  python3 scripts/import_synthetic_scars.py\n"
            "  python3 scripts/import_synthetic_scars.py --model gemini-2.5-pro --workers 4 --timeout 180 --verbose"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_scar_pipeline_cli_args(parser)
    parsed = parser.parse_args(self.raw_args)
    self.model = parsed.model
    self.workers = parsed.workers
    self.timeout_sec = parsed.timeout
    self.max_retries = parsed.max_retries
    self.max_errors = parsed.max_errors
    self.verbose = parsed.verbose
    self.print_result = parsed.print_result
    self.db_path = resolve_scars_db_path(db_path=parsed.db_path)

  def _finalize_database(self, committed_ids: List[str]) -> None:
    """Rebuild FTS5 index and update meta.db_size_bytes when scars were committed."""
    if not committed_ids or self.conn is None:
      return
    cur = self.conn.cursor()
    if self.has_fts5:
      populate_scars_fts(cur)
    self.conn.commit()
    update_db_size_meta(self.conn, self.db_path)

  def _process_single_scar(
      self,
      row: Dict[str, Any],
      runner: AgySubagentRunner,
      validator: ScarCandidateValidator,
      trap: SignalTrap,
      stop_event: threading.Event,
  ) -> Dict[str, Any]:
    """Run agy and validate Simple English fields for one isolated scar row.

    Performs no SQLite mutations and writes no output streams so it can
    safely run inside worker threads.

    Args:
      row: Pending scar row dictionary from SQLite.
      runner: Configured AgySubagentRunner bound to the active SignalTrap.
      validator: Stateless ScarCandidateValidator instance.
      trap: Active SignalTrap monitored for POSIX shutdown signals.
      stop_event: Shared threading.Event set by the main thread on fatal CLI
        errors or when ConsecutiveErrorBreaker reaches ``--max-errors`` so
        sibling workers abort retries immediately.

    Returns:
      Status dictionary with validated fields, validation errors, CLI error
      details, abort marker, or signal interruption metadata.
    """
    scar_id = row["id"]
    raw_data = {
        "raw_wound": row.get("raw_wound") or "",
        "raw_trap": row.get("raw_trap") or "",
        "raw_circuit_breaker": row.get("raw_circuit_breaker") or "",
        "raw_reflex": row.get("raw_reflex") or "",
    }
    has_raw_cb = bool(str(raw_data["raw_circuit_breaker"]).strip())

    max_attempts = self.max_retries + 1
    trace = ScarAttemptTrace(scar_id=scar_id, max_attempts=max_attempts)
    errors: List[str] = []
    for attempt in range(1, max_attempts + 1):
      if trap.last_signal is not None:
        return {
            "status": "interrupted",
            "signal": trap.last_signal,
            "trace": trace,
        }
      if stop_event.is_set():
        return {"status": "aborted", "trace": trace}

      prompt = build_importer_prompt(row, validation_errors=errors or None)
      attempt_started = time.monotonic()
      try:
        payload = runner.run_json(prompt)
      except AgySubagentError as exc:
        elapsed = time.monotonic() - attempt_started
        if trap.last_signal is not None:
          trace.record_attempt(attempt, "interrupted", elapsed, str(exc))
          return {
              "status": "interrupted",
              "signal": trap.last_signal,
              "trace": trace,
          }
        if stop_event.is_set():
          trace.record_attempt(attempt, "aborted", elapsed, str(exc))
          return {"status": "aborted", "trace": trace}
        exit_code = normalize_agy_exit_code(exc)
        if is_transient_agy_error(exc):
          trace.record_attempt(attempt, "transient_error", elapsed, str(exc))
          if attempt < max_attempts:
            continue
          return {
              "status": "transient_failed",
              "error": str(exc),
              "exit_code": exit_code,
              "attempts": attempt,
              "trace": trace,
          }
        trace.record_attempt(attempt, "cli_error", elapsed, str(exc))
        return {
            "status": "cli_error",
            "error": str(exc),
            "exit_code": exit_code,
            "attempts": attempt,
            "trace": trace,
        }
      except ValueError as exc:
        elapsed = time.monotonic() - attempt_started
        trace.record_attempt(attempt, "parse_error", elapsed, str(exc))
        if stop_event.is_set():
          return {"status": "aborted", "trace": trace}
        errors = [str(exc)]
        continue

      elapsed = time.monotonic() - attempt_started
      if trap.last_signal is not None:
        trace.record_attempt(attempt, "interrupted", elapsed)
        return {
            "status": "interrupted",
            "signal": trap.last_signal,
            "trace": trace,
        }
      if stop_event.is_set():
        trace.record_attempt(attempt, "aborted", elapsed)
        return {"status": "aborted", "trace": trace}

      cb_for_validation = (
          "" if not has_raw_cb else payload.get("circuit_breaker")
      )
      candidate = {
          "id": scar_id,
          "wound": payload.get("wound"),
          "trap": payload.get("trap"),
          "circuit_breaker": cb_for_validation,
          "reflex": payload.get("reflex"),
      }
      errors = validator.validate(candidate, raw_data=raw_data)
      if not errors:
        trace.record_attempt(attempt, "ok", elapsed, "validated")
        return {
            "status": "ok",
            "attempts": attempt,
            "trace": trace,
            "fields": {
                "wound": str(candidate["wound"]).strip(),
                "trap": str(candidate["trap"]).strip(),
                "circuit_breaker": (
                    str(candidate["circuit_breaker"]).strip()
                    if has_raw_cb
                    else None
                ),
                "reflex": str(candidate["reflex"]).strip(),
            },
        }
      trace.record_attempt(
          attempt, "validation_error", elapsed, "; ".join(errors)
      )

    return {
        "status": "validation_failed",
        "errors": errors,
        "attempts": max_attempts,
        "trace": trace,
    }

  def run(self) -> int:
    """Transform pending scars inside SignalTrap and commit immediately on the main thread.

    Returns:
      Process exit code (0 when all selected scars pass, non-zero on error/signal).
    """
    stdout_writer = ThreadSafeStreamWriter(self.stdout)
    stderr_writer = ThreadSafeStreamWriter(self.stderr)

    if not os.path.isfile(self.db_path):
      stderr_writer.write(
          f"Error: Scars database not found at '{self.db_path}'.\n"
      )
      return 1

    self.conn = sqlite3.connect(self.db_path)
    self.has_fts5 = init_scars_db_schema(self.conn)
    cur = self.conn.cursor()
    rows = select_scars_needing_importer(cur)
    if not rows:
      stdout_writer.write("No scars need synthetic-scar-importer.\n")
      return 0

    validator = ScarCandidateValidator()
    breaker = ConsecutiveErrorBreaker(max_errors=self.max_errors)
    committed_ids: List[str] = []
    failed_ids: List[str] = []
    interrupted_signal: Optional[int] = None
    cli_exit_code: Optional[int] = None
    breaker_aborted = False
    processed_count = 0
    stop_event = threading.Event()
    stop_stats: Dict[str, int] = {}

    if self.verbose:
      stderr_writer.write(
          format_batch_start_line(
              agent_name="synthetic-scar-importer",
              pending_count=len(rows),
              workers=self.workers,
              timeout_sec=self.timeout_sec,
              max_retries=self.max_retries,
              max_errors=self.max_errors,
              model=self.model,
          )
      )

    with SignalTrap(exit_on_signal=False, cleanup_on_exit=True) as trap:
      runner = AgySubagentRunner(
          agent_name="synthetic-scar-importer",
          json_schema=SIMPLE_ENGLISH_JSON_SCHEMA,
          model=self.model,
          agy_bin=self.agy_bin,
          timeout_sec=self.timeout_sec,
          signal_trap=trap,
      )
      try:
        for row, outcome in iter_bounded_worker_results(
            rows=rows,
            workers=self.workers,
            worker_fn=lambda r: self._process_single_scar(
                r, runner, validator, trap, stop_event
            ),
            should_stop=lambda: (
                trap.last_signal is not None or stop_event.is_set()
            ),
            stop_stats=stop_stats,
        ):
          status = outcome["status"]
          trace = outcome.get("trace")
          if self.verbose and trace is not None:
            stderr_writer.write_block(trace.render_block(status=status))

          if trap.last_signal is not None:
            interrupted_signal = trap.last_signal
            break

          if status == "interrupted":
            interrupted_signal = outcome["signal"]
            break
          if status == "aborted":
            break
          scar_id = row["id"]
          if status == "cli_error":
            stop_event.set()
            stderr_writer.write(f"Error: {scar_id}: {outcome['error']}\n")
            cli_exit_code = outcome["exit_code"]
            break

          processed_count += 1
          if status == "transient_failed":
            self.conn.rollback()
            failed_ids.append(scar_id)
            attempts = outcome.get("attempts", self.max_retries + 1)
            if breaker.record_transient_failure(scar_id, outcome["error"]):
              stop_event.set()
              breaker_aborted = True
            stderr_writer.write(
                f"Error: Transient agy failure for {scar_id} after "
                f"{attempts} attempt(s): {outcome['error']}\n"
            )
            if breaker_aborted:
              break
            continue

          if status == "validation_failed":
            self.conn.rollback()
            failed_ids.append(scar_id)
            stderr_writer.write(
                f"Error: Validation failed for {scar_id}: "
                f"{'; '.join(outcome['errors'])}\n"
            )
            continue

          validated_fields = outcome["fields"]
          cur.execute(
              """
              UPDATE scars
              SET wound = ?, trap = ?, circuit_breaker = ?, reflex = ?
              WHERE id = ?
              """,
              (
                  validated_fields["wound"],
                  validated_fields["trap"],
                  validated_fields["circuit_breaker"],
                  validated_fields["reflex"],
                  scar_id,
              ),
          )
          enrich_scars_cursor(cur, scar_ids=[scar_id])
          self.conn.commit()
          committed_ids.append(scar_id)
          breaker.record_success()
          stdout_writer.write(
              f"Imported Simple English fields for {scar_id} "
              f"({processed_count}/{len(rows)}).\n"
          )
          if self.print_result:
            saved_row = select_scar_row_by_id(cur, scar_id)
            if saved_row is not None:
              stdout_writer.write_block(format_saved_scar_row_json(saved_row))

        if interrupted_signal is None and trap.last_signal is not None:
          interrupted_signal = trap.last_signal
      except KeyboardInterrupt:
        interrupted_signal = signal.SIGINT
      finally:
        self._finalize_database(committed_ids)

    if self.verbose and (
        cli_exit_code is not None
        or interrupted_signal is not None
        or breaker_aborted
    ):
      stop_reason = (
          "cli_error"
          if cli_exit_code is not None
          else ("signal" if interrupted_signal is not None else "max_errors")
      )
      stderr_writer.write(
          format_batch_stop_line(
              reason=stop_reason,
              committed_count=len(committed_ids),
              total_count=len(rows),
              failed_count=len(failed_ids),
              stop_stats=stop_stats,
          )
      )

    if cli_exit_code is not None:
      return cli_exit_code
    if interrupted_signal is not None:
      stderr_writer.write(
          f"Interrupted by signal {interrupted_signal} after committing "
          f"{len(committed_ids)} of {len(rows)} scar(s).\n"
      )
      return 128 + interrupted_signal
    if breaker_aborted:
      stderr_writer.write(
          breaker.format_abort_message(
              committed_count=len(committed_ids),
              total_count=len(rows),
              stop_stats=stop_stats,
          )
      )
      return 1

    stdout_writer.write(
        f"Import summary: committed={len(committed_ids)} of {len(rows)}, "
        f"failed={len(failed_ids)}.\n"
    )
    return 1 if failed_ids else 0

  def cleanup(self, exit_code: int) -> None:
    """Close SQLite connection if open."""
    if self.conn is not None:
      try:
        self.conn.close()
      except sqlite3.Error as exc:
        self.stderr.write(f"Warning: failed to close SQLite connection: {exc}\n")
      self.conn = None
