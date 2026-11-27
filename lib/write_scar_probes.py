"""Single-purpose Socratic probe generation CLI using agy (GH-209, GH-336).

Selects scars with complete Simple English fields and missing ``socratic_probe``
ordered by ``id ASC``, invokes ``agy --agent socratic-probe-writer ... --print=<prompt>``
one scar per call, validates JSON output with ``validate_socratic_probe()``,
enriches metadata and commits immediately per scar, and rebuilds FTS5 and
``meta.db_size_bytes`` on exit or signal.
"""

import argparse
import os
import signal
import sqlite3
import threading
from typing import Any, Dict, List, Optional

from lib.base_script import BaseScript, ScriptArgumentParser
from lib.scar_candidate import validate_socratic_probe
from lib.scar_subagent import (
    AgySubagentError,
    AgySubagentRunner,
    iter_bounded_worker_results,
    parse_positive_workers,
    wrap_untrusted_scar,
)
from lib.scars_db import (
    enrich_scars_cursor,
    init_scars_db_schema,
    populate_scars_fts,
    resolve_scars_db_path,
    select_scars_needing_probe_writer,
    update_db_size_meta,
)
from lib.signal_trap import SignalTrap

SOCRATIC_PROBE_JSON_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["socratic_probe"],
    "properties": {
        "socratic_probe": {"type": "string"},
    },
    "additionalProperties": False,
}


def build_probe_prompt(
    scar_row: Dict[str, Any],
    validation_errors: Optional[List[str]] = None,
) -> str:
  """Build a single-scar prompt for ``socratic-probe-writer``.

  Args:
    scar_row: Ready scar row dictionary from SQLite.
    validation_errors: Optional validator errors from the previous attempt.

  Returns:
    Prompt string wrapping untrusted scar fields inside an XML airlock.
  """
  sections = [
      "Write a kinetic Socratic probe (1 to 3 short questions ending in '?').",
      "Return only JSON matching the schema with socratic_probe.",
      "",
      "## Transformed Scar Context",
      *wrap_untrusted_scar([
          ("id", scar_row["id"]),
          ("ss_id", scar_row.get("ss_id") or ""),
          ("title", scar_row.get("title") or ""),
          ("wound", scar_row.get("wound") or ""),
          ("trap", scar_row.get("trap") or ""),
          ("circuit_breaker", scar_row.get("circuit_breaker") or ""),
          ("reflex", scar_row.get("reflex") or ""),
      ]),
  ]
  if validation_errors:
    sections.extend([
        "",
        "## Previous Attempt Validation Errors (Fix Every Item)",
        *[f"- {err}" for err in validation_errors],
    ])
  return "\n".join(sections)


class WriteScarProbesScript(BaseScript):
  """Template Method CLI populating ``socratic_probe`` fields via agy subagent."""

  def __init__(self) -> None:
    """Initialize WriteScarProbesScript with default runner settings."""
    super().__init__()
    self.db_path: str = ""
    self.model: Optional[str] = None
    self.workers: int = 1
    self.agy_bin: str = "agy"
    self.max_retries: int = 2
    self.timeout_sec: float = 120.0
    self.has_fts5: bool = False
    self.conn: Optional[sqlite3.Connection] = None

  def parse_args(self, args: Optional[List[str]] = None) -> None:
    """Parse CLI arguments accepting optional ``--model``, ``--db-path``, and ``--workers``.

    Args:
      args: Optional CLI argument list.
    """
    super().parse_args(args)
    parser = ScriptArgumentParser(
        prog="write_scar_probes.py",
        description=(
            "Write Socratic probes one scar per call with agy."
        ),
        epilog=(
            "Examples:\n"
            "  python3 scripts/write_scar_probes.py\n"
            "  python3 scripts/write_scar_probes.py --model gemini-2.5-pro --workers 4"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
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
    parsed = parser.parse_args(self.raw_args)
    self.model = parsed.model
    self.workers = parsed.workers
    self.db_path = resolve_scars_db_path(db_path=parsed.db_path)

  def _finalize_database(self, committed_ids: List[str]) -> None:
    """Rebuild FTS5 index and update meta.db_size_bytes when probes were committed."""
    if not committed_ids or self.conn is None:
      return
    cur = self.conn.cursor()
    if self.has_fts5:
      populate_scars_fts(cur)
    self.conn.commit()
    update_db_size_meta(self.conn, self.db_path)

  def _process_single_probe(
      self,
      row: Dict[str, Any],
      runner: AgySubagentRunner,
      trap: SignalTrap,
      stop_event: threading.Event,
  ) -> Dict[str, Any]:
    """Run agy and validate ``socratic_probe`` for one isolated scar row.

    Performs no SQLite mutations and writes no output streams so it can
    safely run inside worker threads.

    Args:
      row: Ready scar row dictionary from SQLite.
      runner: Configured AgySubagentRunner bound to the active SignalTrap.
      trap: Active SignalTrap monitored for POSIX shutdown signals.
      stop_event: Shared threading.Event set by the main thread on fatal CLI
        errors so sibling workers abort retries immediately.

    Returns:
      Status dictionary with validated probe text, validation errors, CLI
      error details, abort marker, or signal interruption metadata.
    """
    scar_id = row["id"]
    errors: List[str] = []
    for _attempt in range(self.max_retries + 1):
      if trap.last_signal is not None:
        return {"status": "interrupted", "signal": trap.last_signal}
      if stop_event.is_set():
        return {"status": "aborted"}

      prompt = build_probe_prompt(row, validation_errors=errors or None)
      try:
        payload = runner.run_json(prompt)
      except AgySubagentError as exc:
        if trap.last_signal is not None:
          return {"status": "interrupted", "signal": trap.last_signal}
        if stop_event.is_set():
          return {"status": "aborted"}
        exit_code = (
            exc.returncode
            if (exc.returncode is not None and exc.returncode > 0)
            else 1
        )
        return {
            "status": "cli_error",
            "error": str(exc),
            "exit_code": exit_code,
        }
      except ValueError as exc:
        if stop_event.is_set():
          return {"status": "aborted"}
        errors = [str(exc)]
        continue

      if trap.last_signal is not None:
        return {"status": "interrupted", "signal": trap.last_signal}
      if stop_event.is_set():
        return {"status": "aborted"}

      probe_val = payload.get("socratic_probe")
      errors = validate_socratic_probe(probe_val, scar_id=scar_id)
      if not errors:
        return {"status": "ok", "probe": str(probe_val).strip()}

    return {"status": "validation_failed", "errors": errors}

  def run(self) -> int:
    """Generate and validate probes inside SignalTrap and commit immediately on the main thread.

    Returns:
      Process exit code (0 when all selected scars pass, non-zero on error/signal).
    """
    if not os.path.isfile(self.db_path):
      self.stderr.write(
          f"Error: Scars database not found at '{self.db_path}'.\n"
      )
      return 1

    self.conn = sqlite3.connect(self.db_path)
    self.has_fts5 = init_scars_db_schema(self.conn)
    cur = self.conn.cursor()
    rows = select_scars_needing_probe_writer(cur)
    if not rows:
      self.stdout.write("No scars need socratic-probe-writer.\n")
      return 0

    committed_ids: List[str] = []
    failed_ids: List[str] = []
    interrupted_signal: Optional[int] = None
    cli_exit_code: Optional[int] = None
    processed_count = 0
    stop_event = threading.Event()

    with SignalTrap(exit_on_signal=False, cleanup_on_exit=True) as trap:
      runner = AgySubagentRunner(
          agent_name="socratic-probe-writer",
          json_schema=SOCRATIC_PROBE_JSON_SCHEMA,
          model=self.model,
          agy_bin=self.agy_bin,
          timeout_sec=self.timeout_sec,
          signal_trap=trap,
      )
      try:
        for row, outcome in iter_bounded_worker_results(
            rows=rows,
            workers=self.workers,
            worker_fn=lambda r: self._process_single_probe(
                r, runner, trap, stop_event
            ),
            should_stop=lambda: (
                trap.last_signal is not None or stop_event.is_set()
            ),
        ):
          if trap.last_signal is not None:
            interrupted_signal = trap.last_signal
            break

          status = outcome["status"]
          if status == "interrupted":
            interrupted_signal = outcome["signal"]
            break
          if status == "aborted":
            break
          if status == "cli_error":
            stop_event.set()
            self.stderr.write(f"Error: {outcome['error']}\n")
            cli_exit_code = outcome["exit_code"]
            break

          scar_id = row["id"]
          processed_count += 1
          if status == "validation_failed":
            self.conn.rollback()
            failed_ids.append(scar_id)
            self.stderr.write(
                f"Error: Probe validation failed for {scar_id}: "
                f"{'; '.join(outcome['errors'])}\n"
            )
            continue

          valid_probe = outcome["probe"]
          cur.execute(
              "UPDATE scars SET socratic_probe = ? WHERE id = ?",
              (valid_probe, scar_id),
          )
          enrich_scars_cursor(cur, scar_ids=[scar_id])
          self.conn.commit()
          committed_ids.append(scar_id)
          self.stdout.write(
              f"Wrote Socratic probe for {scar_id} "
              f"({processed_count}/{len(rows)}).\n"
          )

        if interrupted_signal is None and trap.last_signal is not None:
          interrupted_signal = trap.last_signal
      except KeyboardInterrupt:
        interrupted_signal = signal.SIGINT
      finally:
        self._finalize_database(committed_ids)

    if cli_exit_code is not None:
      return cli_exit_code
    if interrupted_signal is not None:
      self.stderr.write(
          f"Interrupted by signal {interrupted_signal} after committing "
          f"{len(committed_ids)} of {len(rows)} probe(s).\n"
      )
      return 128 + interrupted_signal

    self.stdout.write(
        f"Probe summary: committed={len(committed_ids)} of {len(rows)}, "
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
