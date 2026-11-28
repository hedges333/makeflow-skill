"""Single-purpose upstream synthetic scars registry synchronization CLI (GH-209).

Fetches https://syntheticscars.org/registry.json, runs a lossless one-time
4-field raw_hash backfill, updates matched ss_id rows in place without altering
Makeflow IDs, imports unmatched ss_id rows in deterministic ascending order,
leaves Makeflow-owned rows untouched, rebuilds FTS5, updates meta.db_size_bytes,
and prints explicit pipeline status counts.
"""

import argparse
from datetime import datetime, timezone
import json
import os
import sqlite3
from typing import Any, Dict, List, Optional, Set
import urllib.error
import urllib.request

from lib.base_script import BaseScript, ScriptArgumentParser
from lib.scar_metadata import extract_scar_metadata
from lib.scar_ontology import compute_canonical_scar_id
from lib.scars_db import (
    DEFAULT_REGISTRY_URL,
    backfill_raw_hashes_if_needed,
    compute_raw_fields_hash,
    count_scar_pipeline_status,
    enrich_scars_cursor,
    init_scars_db_schema,
    populate_scars_fts,
    resolve_scars_db_path,
    update_db_size_meta,
)


def _extract_ss_targets_json(item: Dict[str, Any]) -> str:
  """Serialize upstream workflow phases into a JSON array string."""
  phases_raw = item.get("workflowPhases")
  if phases_raw is None:
    phases_raw = item.get("workflow_phases", [])
  if isinstance(phases_raw, list):
    return json.dumps(phases_raw)
  if phases_raw is None:
    return "[]"
  return json.dumps([str(phases_raw)])


class SyncSyntheticScarsScript(BaseScript):
  """Template Method CLI syncing upstream syntheticscars.org rows into SQLite."""

  def __init__(self) -> None:
    """Initialize SyncSyntheticScarsScript with default registry URL and DB state."""
    super().__init__()
    self.db_path: str = ""
    self.url: str = DEFAULT_REGISTRY_URL
    self.has_fts5: bool = False
    self.conn: Optional[sqlite3.Connection] = None

  def parse_args(self, args: Optional[List[str]] = None) -> None:
    """Parse CLI arguments accepting only optional ``--db-path``.

    Args:
      args: Optional CLI argument list.
    """
    super().parse_args(args)
    parser = ScriptArgumentParser(
        prog="sync_synthetic_scars.py",
        description=(
            "Synchronize upstream syntheticscars.org records into SQLite."
        ),
        epilog=(
            "Examples:\n"
            "  python3 scripts/sync_synthetic_scars.py\n"
            "  python3 scripts/sync_synthetic_scars.py --db-path skills/makeflow/data/scars_registry.db"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Optional path to SQLite scars registry database.",
    )
    parsed = parser.parse_args(self.raw_args)
    self.db_path = resolve_scars_db_path(db_path=parsed.db_path)

  def setup(self) -> None:
    """Ensure the parent directory for the SQLite database exists."""
    if self.db_path:
      os.makedirs(
          os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True
      )

  def _emit_status_counts(self, cur: sqlite3.Cursor) -> None:
    """Print explicit pipeline and curation counts.

    Args:
      cur: Open SQLite cursor on an initialized scars database.
    """
    counts = count_scar_pipeline_status(cur)
    self.stdout.write(
        f"Total scars: {counts['total']}\n"
        f"Complete scars: {counts['complete']}\n"
        f"Scars needing synthetic-scar-importer: {counts['needs_importer']}\n"
        f"Scars needing socratic-probe-writer: {counts['needs_probe_writer']}\n"
        f"Active scars: {counts['active']}\n"
        f"Inactive scars: {counts['inactive']}\n"
        f"Vetted scars: {counts['vetted']}\n"
        f"Unvetted scars: {counts['unvetted']}\n"
        f"Overridden scars: {counts['overridden']}\n"
    )

  def run(self) -> int:
    """Fetch upstream registry JSON, reconcile SQLite rows, and print status counts.

    Returns:
      Process exit code (0 on success, 1 on network/parse/database error).
    """
    self.conn = sqlite3.connect(self.db_path)
    self.has_fts5 = init_scars_db_schema(self.conn)
    backfilled = backfill_raw_hashes_if_needed(self.conn)

    current_etag: Optional[str] = None
    if backfilled == 0:
      cur = self.conn.cursor()
      cur.execute("SELECT value FROM meta WHERE key = 'etag'")
      etag_row = cur.fetchone()
      if etag_row and etag_row[0]:
        current_etag = str(etag_row[0])

    req = urllib.request.Request(
        self.url,
        headers={
            "User-Agent": "makeflow-sync/1.0",
            "Accept": "application/json",
        },
    )
    if current_etag:
      req.add_header("If-None-Match", current_etag)

    new_etag: Optional[str] = None
    try:
      with urllib.request.urlopen(req, timeout=30) as resp:
        status_code = getattr(resp, "status", None)
        if status_code is None and hasattr(resp, "getcode"):
          status_code = resp.getcode()

        if status_code == 304:
          self.stdout.write(
              f"Scars database is up to date (HTTP 304 Not Modified, ETag: {current_etag}).\n"
          )
          self._emit_status_counts(self.conn.cursor())
          return 0

        new_etag = (
            resp.headers.get("ETag") if hasattr(resp, "headers") else None
        )
        if not new_etag and hasattr(resp, "info"):
          info_hdrs = resp.info()
          if info_hdrs:
            new_etag = info_hdrs.get("ETag")

        raw_body = resp.read().decode("utf-8")
        data = json.loads(raw_body)
    except urllib.error.HTTPError as exc:
      if exc.code == 304:
        self.stdout.write(
            f"Scars database is up to date (HTTP 304 Not Modified, ETag: {current_etag}).\n"
        )
        self._emit_status_counts(self.conn.cursor())
        return 0
      self.stderr.write(f"Error: HTTP error during sync: {exc}\n")
      return 1
    except Exception as exc:  # pylint: disable=broad-except
      self.stderr.write(
          f"Error: Failed to fetch or parse registry JSON: {exc}\n"
      )
      return 1

    scars_list = data.get("scars", []) if isinstance(data, dict) else data
    if not isinstance(scars_list, list):
      self.stderr.write(
          "Error: Registry response does not contain a valid scar list.\n"
      )
      return 1

    cur = self.conn.cursor()
    cur.execute("BEGIN TRANSACTION")
    updated_count = 0
    inserted_count = 0
    modified = False
    reset_notices: List[str] = []
    try:
      cur.execute(
          "SELECT id, ss_id, raw_hash, source, active, vetted, override "
          "FROM scars"
      )
      reserved_ids: Set[str] = set()
      existing_by_ss_id: Dict[str, Dict[str, Any]] = {}
      for (
          row_id,
          row_ss_id,
          row_hash,
          row_source,
          row_active,
          row_vetted,
          row_override,
      ) in cur.fetchall():
        reserved_ids.add(row_id)
        src = (row_source or "upstream").strip().lower()
        row_info = {
            "id": row_id,
            "ss_id": row_ss_id,
            "raw_hash": row_hash,
            "source": src,
            "active": int(1 if row_active is None else row_active),
            "vetted": int(0 if row_vetted is None else row_vetted),
            "override": int(0 if row_override is None else row_override),
        }
        if src == "makeflow":
          if row_ss_id:
            existing_by_ss_id[row_ss_id] = row_info
          continue
        ss_key = row_ss_id or row_id
        existing_by_ss_id[ss_key] = row_info

      matched_items: List[Dict[str, Any]] = []
      unmatched_items: List[Dict[str, Any]] = []
      seen_upstream_ids: Set[str] = set()
      for item in scars_list:
        if not isinstance(item, dict):
          continue
        raw_upstream_id = str(item.get("id") or "").strip()
        if not raw_upstream_id or raw_upstream_id in seen_upstream_ids:
          continue
        seen_upstream_ids.add(raw_upstream_id)
        if raw_upstream_id in existing_by_ss_id:
          matched_items.append(item)
        else:
          unmatched_items.append(item)

      # 1.a Matched ss_id: check 4-field raw_hash; update in place if changed
      for item in matched_items:
        raw_upstream_id = str(item.get("id") or "").strip()
        existing = existing_by_ss_id[raw_upstream_id]
        if existing["source"] == "makeflow":
          continue
        raw_wound = str(item.get("wound", "") or "")
        raw_trap = str(item.get("trap", "") or "")
        raw_breaker = str(
            item.get("circuitBreaker") or item.get("circuit_breaker", "") or ""
        )
        raw_reflex = str(item.get("reflex", "") or "")
        incoming_hash = compute_raw_fields_hash(
            raw_wound, raw_trap, raw_breaker, raw_reflex
        )

        if existing["raw_hash"] == incoming_hash:
          if existing["ss_id"] is None:
            cur.execute(
                "UPDATE scars SET ss_id = ? WHERE id = ?",
                (raw_upstream_id, existing["id"]),
            )
            modified = True
          continue

        if existing["override"] == 1 or existing["active"] == 0:
          reset_notices.append(
              f"Notice: upstream change reset curation flags for "
              f"{existing['id']} (ss_id={raw_upstream_id}, "
              f"previous active={existing['active']}, "
              f"override={existing['override']}).\n"
          )

        title = str(item.get("title", "") or "")
        raw_category = str(item.get("category", "") or "")
        raw_cat_slug = str(
            item.get("categorySlug") or item.get("category_slug", "") or ""
        )
        visibility = str(item.get("visibility", "public") or "public")
        ss_targets_json = _extract_ss_targets_json(item)
        applies_when = str(
            item.get("appliesWhen") or item.get("applies_when", "") or ""
        )
        grad_status = str(
            item.get("graduationStatus")
            or item.get("graduation_status", "")
            or ""
        )
        meta = extract_scar_metadata({
            "id": existing["id"],
            "ss_id": raw_upstream_id,
            "title": title,
            "category": raw_category,
            "category_slug": raw_cat_slug,
            "workflow_phases": ss_targets_json,
            "ss_targets": ss_targets_json,
            "applies_when": applies_when,
            "raw_wound": raw_wound,
            "raw_trap": raw_trap,
            "raw_circuit_breaker": raw_breaker,
            "raw_reflex": raw_reflex,
        })
        domain = meta["domain_category"]
        cur.execute(
            """
            UPDATE scars
            SET ss_id = ?,
                title = ?,
                category = ?,
                category_slug = ?,
                visibility = ?,
                workflow_phases = ?,
                ss_targets = ?,
                mf_targets = ?,
                applies_when = ?,
                graduation_status = ?,
                raw_wound = ?,
                raw_trap = ?,
                raw_circuit_breaker = ?,
                raw_reflex = ?,
                raw_hash = ?,
                wound = NULL,
                trap = NULL,
                circuit_breaker = NULL,
                reflex = NULL,
                socratic_probe = NULL,
                active = 1,
                vetted = 0,
                override = 0,
                keywords = ?,
                languages = ?,
                frameworks = ?,
                diagnostic_codes = ?
            WHERE id = ?
            """,
            (
                raw_upstream_id,
                title,
                domain,
                domain.lower(),
                visibility,
                json.dumps(meta["workflow_phases"]),
                ss_targets_json,
                json.dumps(meta["mf_targets"]),
                applies_when,
                grad_status,
                raw_wound,
                raw_trap,
                raw_breaker,
                raw_reflex,
                incoming_hash,
                json.dumps(meta["keywords"]),
                json.dumps(meta["languages"]),
                json.dumps(meta["frameworks"]),
                json.dumps(meta["diagnostic_codes"]),
                existing["id"],
            ),
        )
        updated_count += 1
        modified = True

      # 1.b Unmatched ss_id: sort ascending and derive deterministic Makeflow ID
      unmatched_items.sort(key=lambda rec: str(rec.get("id") or "").strip())
      for item in unmatched_items:
        raw_upstream_id = str(item.get("id") or "").strip()
        title = str(item.get("title", "") or "")
        raw_category = str(item.get("category", "") or "")
        raw_cat_slug = str(
            item.get("categorySlug") or item.get("category_slug", "") or ""
        )
        visibility = str(item.get("visibility", "public") or "public")
        ss_targets_json = _extract_ss_targets_json(item)
        applies_when = str(
            item.get("appliesWhen") or item.get("applies_when", "") or ""
        )
        grad_status = str(
            item.get("graduationStatus")
            or item.get("graduation_status", "")
            or ""
        )
        raw_wound = str(item.get("wound", "") or "")
        raw_trap = str(item.get("trap", "") or "")
        raw_breaker = str(
            item.get("circuitBreaker") or item.get("circuit_breaker", "") or ""
        )
        raw_reflex = str(item.get("reflex", "") or "")
        incoming_hash = compute_raw_fields_hash(
            raw_wound, raw_trap, raw_breaker, raw_reflex
        )

        seed_meta = extract_scar_metadata({
            "id": raw_upstream_id,
            "ss_id": raw_upstream_id,
            "title": title,
            "category": raw_category,
            "category_slug": raw_cat_slug,
            "workflow_phases": ss_targets_json,
            "ss_targets": ss_targets_json,
            "applies_when": applies_when,
            "raw_wound": raw_wound,
            "raw_trap": raw_trap,
            "raw_circuit_breaker": raw_breaker,
            "raw_reflex": raw_reflex,
        })
        domain = seed_meta["domain_category"]
        canonical_id = compute_canonical_scar_id(
            raw_id=raw_upstream_id,
            domain=domain,
            languages=seed_meta["languages"],
            reserved_ids=reserved_ids,
        )
        final_meta = extract_scar_metadata({
            "id": canonical_id,
            "ss_id": raw_upstream_id,
            "title": title,
            "category": domain,
            "category_slug": domain.lower(),
            "workflow_phases": ss_targets_json,
            "ss_targets": ss_targets_json,
            "applies_when": applies_when,
            "raw_wound": raw_wound,
            "raw_trap": raw_trap,
            "raw_circuit_breaker": raw_breaker,
            "raw_reflex": raw_reflex,
        })
        cur.execute(
            """
            INSERT INTO scars (
                id, ss_id, title, category, category_slug, visibility,
                workflow_phases, ss_targets, mf_targets, applies_when,
                graduation_status, raw_wound, raw_trap, raw_circuit_breaker,
                raw_reflex, raw_hash, wound, trap, circuit_breaker, reflex,
                socratic_probe, source, original_id,
                keywords, languages, frameworks, diagnostic_codes,
                active, vetted, override
            ) VALUES (
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, NULL, NULL, NULL, NULL,
                NULL, 'upstream', NULL,
                ?, ?, ?, ?,
                1, 0, 0
            )
            """,
            (
                canonical_id,
                raw_upstream_id,
                title,
                domain,
                domain.lower(),
                visibility,
                json.dumps(final_meta["workflow_phases"]),
                ss_targets_json,
                json.dumps(final_meta["mf_targets"]),
                applies_when,
                grad_status,
                raw_wound,
                raw_trap,
                raw_breaker,
                raw_reflex,
                incoming_hash,
                json.dumps(final_meta["keywords"]),
                json.dumps(final_meta["languages"]),
                json.dumps(final_meta["frameworks"]),
                json.dumps(final_meta["diagnostic_codes"]),
            ),
        )
        inserted_count += 1
        modified = True

      cur.execute(
          """
          SELECT id FROM scars
          WHERE COALESCE(source, 'upstream') != 'makeflow'
            AND (keywords IS NULL OR keywords = '')
          """
      )
      unenriched_ids = [r[0] for r in cur.fetchall()]
      if unenriched_ids:
        enrich_scars_cursor(cur, scar_ids=unenriched_ids)
        modified = True

      cur.execute("SELECT COUNT(*) FROM scars")
      total_count = int(cur.fetchone()[0])

      if self.has_fts5:
        cur.execute("SELECT COUNT(*) FROM scars_fts")
        fts_count = int(cur.fetchone()[0])
        if modified or (fts_count == 0 and total_count > 0):
          populate_scars_fts(cur)

      now_iso = datetime.now(timezone.utc).isoformat()
      cur.execute(
          "INSERT OR REPLACE INTO meta (key, value) VALUES ('etag', ?)",
          (new_etag or "",),
      )
      cur.execute(
          "INSERT OR REPLACE INTO meta (key, value) VALUES ('last_synced_at', ?)",
          (now_iso,),
      )
      cur.execute(
          "INSERT OR REPLACE INTO meta (key, value) VALUES ('total_scars', ?)",
          (str(total_count),),
      )
      self.conn.commit()
      if modified:
        self.conn.execute("VACUUM")
      update_db_size_meta(self.conn, self.db_path)
    except Exception as exc:  # pylint: disable=broad-except
      self.conn.rollback()
      self.stderr.write(
          f"Error: Failed to synchronize scars into database: {exc}\n"
      )
      return 1

    for notice in reset_notices:
      self.stdout.write(notice)
    self.stdout.write(
        f"Synchronized upstream registry into '{self.db_path}' "
        f"(inserted={inserted_count}, updated={updated_count}).\n"
    )
    self._emit_status_counts(self.conn.cursor())
    return 0

  def cleanup(self, exit_code: int) -> None:
    """Close SQLite connection if open."""
    if self.conn is not None:
      try:
        self.conn.close()
      except sqlite3.Error as exc:
        self.stderr.write(f"Warning: failed to close SQLite connection: {exc}\n")
      self.conn = None
