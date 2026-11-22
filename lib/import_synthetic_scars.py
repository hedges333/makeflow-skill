"""Single-purpose Simple English scar transformation CLI using agy (GH-209).

Selects scars missing required Simple English fields ordered by ``id ASC``,
invokes ``agy --print --agent synthetic-scar-importer`` one scar at a time,
validates JSON output in Python with ScarCandidateValidator, enforces NULL
``circuit_breaker`` when ``raw_circuit_breaker`` is empty, commits each scar
immediately, and rebuilds FTS5 and ``meta.db_size_bytes`` on exit or signal.
"""

import argparse
import os
import signal
import sqlite3
from typing import Any, Dict, List, Optional

from lib.base_script import BaseScript, ScriptArgumentParser
from lib.scar_candidate import ScarCandidateValidator
from lib.scar_subagent import (
    AgySubagentError,
    AgySubagentRunner,
    wrap_untrusted_scar,
)
from lib.scars_db import (
    enrich_scars_cursor,
    init_scars_db_schema,
    populate_scars_fts,
    resolve_scars_db_path,
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
    self.agy_bin: str = "agy"
    self.max_retries: int = 2
    self.timeout_sec: float = 120.0
    self.has_fts5: bool = False
    self.conn: Optional[sqlite3.Connection] = None

  def parse_args(self, args: Optional[List[str]] = None) -> None:
    """Parse CLI arguments accepting optional ``--model`` and ``--db-path``.

    Args:
      args: Optional CLI argument list.
    """
    super().parse_args(args)
    parser = ScriptArgumentParser(
        prog="import_synthetic_scars.py",
        description=(
            "Write Simple English scar fields one scar at a time with agy."
        ),
        epilog=(
            "Examples:\n"
            "  python3 scripts/import_synthetic_scars.py\n"
            "  python3 scripts/import_synthetic_scars.py --model gemini-2.5-pro"
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
    parsed = parser.parse_args(self.raw_args)
    self.model = parsed.model
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

  def run(self) -> int:
    """Transform pending scars one by one inside SignalTrap and commit immediately.

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
    rows = select_scars_needing_importer(cur)
    if not rows:
      self.stdout.write("No scars need synthetic-scar-importer.\n")
      return 0

    validator = ScarCandidateValidator()
    committed_ids: List[str] = []
    failed_ids: List[str] = []
    interrupted_signal: Optional[int] = None
    cli_exit_code: Optional[int] = None

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
        for idx, row in enumerate(rows, start=1):
          if trap.last_signal is not None:
            interrupted_signal = trap.last_signal
            break

          scar_id = row["id"]
          raw_data = {
              "raw_wound": row.get("raw_wound") or "",
              "raw_trap": row.get("raw_trap") or "",
              "raw_circuit_breaker": row.get("raw_circuit_breaker") or "",
              "raw_reflex": row.get("raw_reflex") or "",
          }
          has_raw_cb = bool(str(raw_data["raw_circuit_breaker"]).strip())

          errors: List[str] = []
          validated_fields: Optional[Dict[str, Optional[str]]] = None

          for _attempt in range(self.max_retries + 1):
            if trap.last_signal is not None:
              interrupted_signal = trap.last_signal
              break

            prompt = build_importer_prompt(
                row, validation_errors=errors or None
            )
            try:
              payload = runner.run_json(prompt)
            except AgySubagentError as exc:
              if trap.last_signal is not None:
                interrupted_signal = trap.last_signal
                break
              self.stderr.write(f"Error: {exc}\n")
              cli_exit_code = (
                  exc.returncode
                  if (exc.returncode is not None and exc.returncode > 0)
                  else 1
              )
              break
            except ValueError as exc:
              errors = [str(exc)]
              continue

            if trap.last_signal is not None:
              interrupted_signal = trap.last_signal
              break

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
              validated_fields = {
                  "wound": str(candidate["wound"]).strip(),
                  "trap": str(candidate["trap"]).strip(),
                  "circuit_breaker": (
                      str(candidate["circuit_breaker"]).strip()
                      if has_raw_cb
                      else None
                  ),
                  "reflex": str(candidate["reflex"]).strip(),
              }
              break

          if cli_exit_code is not None or interrupted_signal is not None:
            break

          if validated_fields is None:
            self.conn.rollback()
            failed_ids.append(scar_id)
            self.stderr.write(
                f"Error: Validation failed for {scar_id}: "
                f"{'; '.join(errors)}\n"
            )
            continue

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
          self.stdout.write(
              f"Imported Simple English fields for {scar_id} "
              f"({idx}/{len(rows)}).\n"
          )
      except KeyboardInterrupt:
        interrupted_signal = signal.SIGINT
      finally:
        self._finalize_database(committed_ids)

    if cli_exit_code is not None:
      return cli_exit_code
    if interrupted_signal is not None:
      self.stderr.write(
          f"Interrupted by signal {interrupted_signal} after committing "
          f"{len(committed_ids)} of {len(rows)} scar(s).\n"
      )
      return 128 + interrupted_signal

    self.stdout.write(
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
