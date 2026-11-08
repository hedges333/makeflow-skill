#!/usr/bin/env python3
"""Sync and query synthetic scars using SQLite and FTS5 (GH-118, SCAR-ARCH-05).

Fetches public synthetic scars from syntheticscars.org, caches HTTP ETag to avoid
duplicate downloads, stores scars in SQLite with full-text search (FTS5), and provides
fast on-demand JIT scar queries for MCP tools and CLI runners.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
from typing import Any, Dict, List, Optional
import urllib.error
import urllib.request

# Ensure scripts directory and repo root are on sys.path
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
  sys.path.insert(0, _SCRIPTS_DIR)
_REPO_ROOT = os.path.dirname(_SCRIPTS_DIR)
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from base_script import BaseScript

DEFAULT_REGISTRY_URL = "https://syntheticscars.org/registry.json"


def resolve_scars_db_path(
    db_path: Optional[str] = None,
    agent: Optional[str] = None,
    workspace_dir: Optional[str] = None,
    package_root: Optional[str] = None,
) -> str:
  """Resolves the SQLite scars database path according to priority cascade."""
  if db_path:
    return os.path.abspath(os.path.expanduser(db_path))

  env_path = os.getenv("MAKEFLOW_SCARS_DB")
  if env_path:
    return os.path.abspath(os.path.expanduser(env_path))

  pkg_root = package_root or _REPO_ROOT
  bundled_path = os.path.join(
      pkg_root, "skills", "makeflow", "data", "scars_registry.db"
  )
  if os.path.exists(bundled_path):
    return os.path.abspath(bundled_path)

  if workspace_dir:
    ws_db = os.path.join(workspace_dir, ".workflow", "cache", "scars_registry.db")
    if os.path.exists(ws_db):
      return os.path.abspath(ws_db)

  home = os.path.expanduser("~")
  agent_key = (agent or "").strip().lower()

  agent_paths = {
      "gemini": os.path.join(
          home, ".gemini", "antigravity-cli", "plugin_data", "makeflow", "scars_registry.db"
      ),
      "claude": os.path.join(
          home, ".claude", "plugin_data", "makeflow", "scars_registry.db"
      ),
      "cursor": os.path.join(
          home, ".cursor", "plugin_data", "makeflow", "scars_registry.db"
      ),
      "copilot": os.path.join(
          home, ".copilot", "plugin_data", "makeflow", "scars_registry.db"
      ),
      "chatgpt": os.path.join(
          home, ".chatgpt", "plugin_data", "makeflow", "scars_registry.db"
      ),
  }

  if agent_key in agent_paths:
    return agent_paths[agent_key]

  xdg_cache = os.getenv("XDG_CACHE_HOME")
  if xdg_cache:
    xdg_db = os.path.join(xdg_cache, "makeflow", "scars_registry.db")
    if os.path.exists(xdg_db):
      return os.path.abspath(xdg_db)

  user_default = os.path.join(home, ".cache", "makeflow", "scars_registry.db")
  if os.path.exists(user_default):
    return user_default

  return os.path.abspath(bundled_path)


def _sanitize_fts_query(query: str) -> str:
  """Sanitizes user search query for safe FTS5 MATCH evaluation."""
  tokens = re.findall(r"[a-zA-Z0-9_\-]+", query)
  if not tokens:
    return ""
  quoted = []
  for t in tokens:
    cleaned = t.replace('"', "")
    if cleaned:
      quoted.append(f'"{cleaned}"*')
  return " ".join(quoted)


def query_scars(
    db_path: str,
    query: str,
    category: Optional[str] = None,
    language: Optional[str] = None,
    limit: int = 3,
    source: Optional[str] = None,
) -> List[Dict[str, Any]]:
  """Queries SQLite scars database using FTS5 or fallback keyword search."""
  if not db_path or not os.path.isfile(db_path):
    return []

  conn = None
  try:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='scars'"
    )
    if not cur.fetchone():
      return []

    cur.execute("PRAGMA table_info(scars)")
    cols = {r[1] for r in cur.fetchall()}
    has_source = "source" in cols
    has_orig_id = "original_id" in cols
    has_raw_hash = "raw_hash" in cols

    source_col = "s.source" if has_source else "'upstream' AS source"
    orig_id_col = "s.original_id" if has_orig_id else "NULL AS original_id"
    raw_hash_col = "s.raw_hash" if has_raw_hash else "NULL AS raw_hash"

    select_fields = (
        "s.id, s.title, s.category, s.category_slug, s.visibility,"
        " s.workflow_phases, s.applies_when, s.graduation_status, s.wound,"
        f" s.trap, s.circuit_breaker, s.reflex, {source_col}, {orig_id_col}, {raw_hash_col} "
    )

    has_fts = False
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='scars_fts'"
    )
    if cur.fetchone():
      has_fts = True

    results = []

    if has_fts:
      sanitized = _sanitize_fts_query(query)
      if sanitized:
        sql = (
            f"SELECT {select_fields}"
            "FROM scars s "
            "JOIN scars_fts fts ON s.id = fts.id "
            "WHERE scars_fts MATCH ? "
        )
        params: List[Any] = [sanitized]

        if source:
          if has_source:
            sql += "AND LOWER(s.source) = LOWER(?) "
            params.append(source.strip())
          elif source.strip().lower() != "upstream":
            return []

        if category:
          sql += "AND (LOWER(s.category) = LOWER(?) OR LOWER(s.category_slug) = LOWER(?)) "
          params.extend([category, category])

        if language:
          lang_pattern = f"%{language.strip().lower()}%"
          sql += (
              "AND (LOWER(s.applies_when) LIKE ? OR LOWER(s.title) LIKE ?"
              " OR LOWER(s.reflex) LIKE ?) "
          )
          params.extend([lang_pattern, lang_pattern, lang_pattern])

        sql += "ORDER BY rank LIMIT ?"
        params.append(max(1, limit))

        try:
          cur.execute(sql, params)
          rows = cur.fetchall()
          for r in rows:
            results.append(_format_scar_row(r))
          if results:
            return results
        except sqlite3.OperationalError:
          # FTS error or syntax fallback
          pass

    # Fallback search using standard LIKE queries
    query_tokens = [tok.strip().lower() for tok in query.split() if tok.strip()]
    if not query_tokens:
      return []

    sql = (
        f"SELECT {select_fields}"
        "FROM scars s WHERE ("
    )
    where_clauses = []
    params = []
    for tok in query_tokens:
      pattern = f"%{tok}%"
      where_clauses.append(
          "(LOWER(s.title) LIKE ? OR LOWER(s.wound) LIKE ? OR LOWER(s.trap) LIKE ?"
          " OR LOWER(s.reflex) LIKE ? OR LOWER(s.applies_when) LIKE ? OR LOWER(s.id) LIKE ?)"
      )
      params.extend([pattern, pattern, pattern, pattern, pattern, pattern])

    sql += " AND ".join(where_clauses) + ") "

    if source:
      if has_source:
        sql += "AND LOWER(s.source) = LOWER(?) "
        params.append(source.strip())
      elif source.strip().lower() != "upstream":
        return []

    if category:
      sql += "AND (LOWER(s.category) = LOWER(?) OR LOWER(s.category_slug) = LOWER(?)) "
      params.extend([category, category])

    if language:
      lang_pattern = f"%{language.strip().lower()}%"
      sql += (
          "AND (LOWER(s.applies_when) LIKE ? OR LOWER(s.title) LIKE ?"
          " OR LOWER(s.reflex) LIKE ?) "
      )
      params.extend([lang_pattern, lang_pattern, lang_pattern])

    sql += "LIMIT ?"
    params.append(max(1, limit))

    cur.execute(sql, params)
    rows = cur.fetchall()
    for r in rows:
      results.append(_format_scar_row(r))

    return results
  except (sqlite3.Error, OSError, ValueError):
    return []
  finally:
    if conn:
      conn.close()


def _format_scar_row(row: Any) -> Dict[str, Any]:
  """Formats a SQLite row tuple into a structured scar dictionary."""
  raw_phases = row[5] or "[]"
  try:
    phases = json.loads(raw_phases)
  except (json.JSONDecodeError, TypeError, ValueError):
    phases = [p.strip() for p in raw_phases.split(",") if p.strip()]

  source = row[12] if len(row) > 12 and row[12] is not None else "upstream"
  orig_id = row[13] if len(row) > 13 and row[13] is not None else None
  raw_hash = row[14] if len(row) > 14 and row[14] is not None else None

  return {
      "id": row[0],
      "title": row[1],
      "category": row[2],
      "categorySlug": row[3],
      "visibility": row[4],
      "workflowPhases": phases,
      "appliesWhen": row[6],
      "graduationStatus": row[7],
      "wound": row[8],
      "trap": row[9],
      "circuitBreaker": row[10],
      "reflex": row[11],
      "source": source,
      "originalId": orig_id,
      "original_id": orig_id,
      "rawHash": raw_hash,
      "raw_hash": raw_hash,
  }


class ScriptArgumentError(Exception):
  """Raised when CLI argument parsing encounters an error."""

  def __init__(self, message: str, exit_code: int = 2) -> None:
    super().__init__(message)
    self.exit_code = exit_code


class ScriptArgumentParser(argparse.ArgumentParser):
  """Custom ArgumentParser that redirects output to script streams."""

  def __init__(self, script: Any, *args: Any, **kwargs: Any) -> None:
    super().__init__(*args, **kwargs)
    self.script = script

  def _print_message(self, message: str, file: Optional[Any] = None) -> None:
    if message:
      if file is sys.stderr:
        self.script.stderr.write(message)
      else:
        self.script.stdout.write(message)

  def error(self, message: str) -> None:
    raise ScriptArgumentError(message, exit_code=2)


class SyncSyntheticScarsScript(BaseScript):
  """Template Method script synchronizing and querying synthetic scars."""

  def __init__(self) -> None:
    super().__init__()
    self.db_path: str = ""
    self.url: str = DEFAULT_REGISTRY_URL
    self.force: bool = False
    self.status_only: bool = False
    self.is_help: bool = False
    self.agent: Optional[str] = None
    self.query: Optional[str] = None
    self.category: Optional[str] = None
    self.language: Optional[str] = None
    self.source: Optional[str] = None
    self.limit: int = 3
    self.has_fts5: bool = False
    self.conn: Optional[sqlite3.Connection] = None

  def parse_args(self, args: Optional[List[str]] = None) -> None:
    super().parse_args(args)
    parser = ScriptArgumentParser(
        self,
        prog="sync_synthetic_scars.py",
        description="Synchronize and query synthetic scars registry.",
        add_help=False,
    )
    parser.add_argument(
        "-h",
        "--help",
        action="store_true",
        help="Show this help message and exit.",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Path to SQLite scars database.",
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_REGISTRY_URL,
        help="URL of the synthetic scars registry JSON.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force download and rebuild database even if ETag matches.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        dest="status_only",
        help="Print current database status and exit.",
    )
    parser.add_argument(
        "--agent",
        default=None,
        help="Agent target flavor (gemini, claude, cursor, copilot, chatgpt).",
    )
    parser.add_argument(
        "--query",
        default=None,
        help="Query string for search testing.",
    )
    parser.add_argument(
        "--category",
        default=None,
        help="Category filter for query.",
    )
    parser.add_argument(
        "--language",
        default=None,
        help="Language filter for query.",
    )
    parser.add_argument(
        "--source",
        default=None,
        help="Source filter for query (makeflow, upstream).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="Maximum results returned by query (default 3).",
    )

    parsed = parser.parse_args(self.raw_args)
    if parsed.help:
      parser.print_help(self.stdout)
      self.is_help = True
      return

    self.url = parsed.url
    self.force = parsed.force
    self.status_only = parsed.status_only
    self.agent = parsed.agent
    self.query = parsed.query
    self.category = parsed.category
    self.language = parsed.language
    self.source = parsed.source
    self.limit = parsed.limit
    self.db_path = resolve_scars_db_path(
        db_path=parsed.db_path,
        agent=self.agent,
    )

  def setup(self) -> None:
    """Ensures destination directory exists and prepares schema."""
    if self.is_help:
      return
    if self.db_path:
      os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)

  def _init_db_schema(self) -> None:
    """Initializes tables and probes FTS5 virtual table support."""
    if not self.conn:
      self.conn = sqlite3.connect(self.db_path)

    cur = self.conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS scars (
            id TEXT PRIMARY KEY,
            title TEXT,
            category TEXT,
            category_slug TEXT,
            visibility TEXT,
            workflow_phases TEXT,
            applies_when TEXT,
            graduation_status TEXT,
            wound TEXT,
            trap TEXT,
            circuit_breaker TEXT,
            reflex TEXT,
            source TEXT DEFAULT 'upstream',
            original_id TEXT,
            raw_hash TEXT
        )
    """)

    # Migrate columns if scars table already existed without new columns
    cur.execute("PRAGMA table_info(scars)")
    existing_cols = {r[1] for r in cur.fetchall()}
    if "source" not in existing_cols:
      cur.execute("ALTER TABLE scars ADD COLUMN source TEXT DEFAULT 'upstream'")
    if "original_id" not in existing_cols:
      cur.execute("ALTER TABLE scars ADD COLUMN original_id TEXT")
    if "raw_hash" not in existing_cols:
      cur.execute("ALTER TABLE scars ADD COLUMN raw_hash TEXT")

    self.has_fts5 = False
    try:
      cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='scars_fts'")
      if cur.fetchone():
        cur.execute("PRAGMA table_info(scars_fts)")
        fts_cols = {r[1] for r in cur.fetchall()}
        if "source" not in fts_cols or "original_id" not in fts_cols:
          cur.execute("DROP TABLE scars_fts")

      cur.execute("""
          CREATE VIRTUAL TABLE IF NOT EXISTS scars_fts USING fts5(
              id UNINDEXED,
              title,
              category,
              applies_when,
              wound,
              trap,
              reflex,
              source UNINDEXED,
              original_id UNINDEXED
          )
      """)
      cur.execute("SELECT COUNT(*) FROM scars_fts")
      fts_count = cur.fetchone()[0]
      cur.execute("SELECT COUNT(*) FROM scars")
      total_count = cur.fetchone()[0]
      if fts_count == 0 and total_count > 0:
        cur.execute("""
            INSERT INTO scars_fts (
                id, title, category, applies_when, wound, trap, reflex, source, original_id
            )
            SELECT id, title, category, applies_when, wound, trap, reflex, source, original_id
            FROM scars
        """)
      self.has_fts5 = True
    except sqlite3.OperationalError:
      self.has_fts5 = False

    self.conn.commit()

  def _report_status(self) -> int:
    """Reports status of the SQLite database."""
    if not os.path.isfile(self.db_path):
      self.stdout.write(f"Scars database not found at '{self.db_path}'.\n")
      return 0

    conn = None
    try:
      file_size = os.path.getsize(self.db_path)
      conn = sqlite3.connect(self.db_path)
      cur = conn.cursor()
      cur.execute(
          "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
      )
      if not cur.fetchone():
        self.stdout.write(f"Scars database at '{self.db_path}' is uninitialized.\n")
        return 0

      cur.execute("SELECT key, value FROM meta")
      meta = dict(cur.fetchall())
      total = meta.get("total_scars", "0")
      etag = meta.get("etag", "none")
      last_synced = meta.get("last_synced_at", "never")

      self.stdout.write(
          f"Database: {self.db_path}\n"
          f"Size: {file_size} bytes\n"
          f"Total scars: {total} scars indexed\n"
          f"ETag: {etag}\n"
          f"Last synced: {last_synced}\n"
      )
      return 0
    finally:
      if conn:
        conn.close()

  def run(self) -> int:
    """Executes synchronization or query workflow."""
    if self.is_help:
      return 0

    if self.status_only:
      return self._report_status()

    if self.query is not None:
      matches = query_scars(
          db_path=self.db_path,
          query=self.query,
          category=self.category,
          language=self.language,
          limit=self.limit,
          source=self.source,
      )
      self.stdout.write(json.dumps(matches, indent=2) + "\n")
      return 0

    return self._sync_scars()

  def _sync_scars(self) -> int:
    """Fetches scars JSON, checks ETag, and updates SQLite tables."""
    self._init_db_schema()

    current_etag = None
    if not self.force and os.path.isfile(self.db_path):
      cur = self.conn.cursor()
      cur.execute("SELECT value FROM meta WHERE key = 'etag'")
      row = cur.fetchone()
      if row:
        current_etag = row[0]

    req = urllib.request.Request(
        self.url,
        headers={"User-Agent": "makeflow-sync/1.0", "Accept": "application/json"},
    )
    if current_etag and not self.force:
      req.add_header("If-None-Match", current_etag)

    try:
      with urllib.request.urlopen(req, timeout=30) as resp:
        status_code = getattr(resp, "status", None)
        if status_code is None and hasattr(resp, "getcode"):
          status_code = resp.getcode()

        if status_code == 304:
          self.stdout.write(
              f"Scars database is up to date (HTTP 304 Not Modified, ETag: {current_etag}).\n"
          )
          return 0

        new_etag = resp.headers.get("ETag") if hasattr(resp, "headers") else None
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
        return 0
      self.stderr.write(f"Error: HTTP error during sync: {exc}\n")
      return 1
    except Exception as exc:
      self.stderr.write(f"Error: Failed to fetch or parse registry JSON: {exc}\n")
      return 1

    scars_list = data.get("scars", []) if isinstance(data, dict) else data
    if not isinstance(scars_list, list):
      self.stderr.write("Error: Registry response does not contain a valid scar list.\n")
      return 1

    # Atomic transaction
    cur = self.conn.cursor()
    cur.execute("BEGIN TRANSACTION")
    try:
      # Query existing scars
      cur.execute(
          "SELECT id, title, wound, trap, reflex, raw_hash, source, original_id FROM scars"
      )
      existing_rows = {
          r[0]: {
              "id": r[0],
              "title": r[1],
              "wound": r[2],
              "trap": r[3],
              "reflex": r[4],
              "raw_hash": r[5],
              "source": r[6] or "upstream",
              "original_id": r[7],
          }
          for r in cur.fetchall()
      }

      incoming_upstream_ids = set()
      modified = False

      # First pass: collision reconciliation with Makeflow scars
      for item in scars_list:
        scar_id = item.get("id")
        if not scar_id:
          continue
        incoming_upstream_ids.add(scar_id)

        raw_hash = hashlib.md5(
            json.dumps(item, sort_keys=True).encode("utf-8")
        ).hexdigest()

        if scar_id in existing_rows and existing_rows[scar_id]["source"] == "makeflow":
          mf_row = existing_rows[scar_id]
          content_matches = False
          if mf_row.get("raw_hash") and mf_row["raw_hash"] == raw_hash:
            content_matches = True
          elif (
              mf_row.get("title") == item.get("title")
              and mf_row.get("wound") == item.get("wound")
              and mf_row.get("trap") == item.get("trap")
              and mf_row.get("reflex") == item.get("reflex")
          ):
            content_matches = True

          if content_matches:
            # Keep upstream scar, remove Makeflow duplicate
            cur.execute(
                "DELETE FROM scars WHERE id = ? AND source = 'makeflow'",
                (scar_id,),
            )
            del existing_rows[scar_id]
            modified = True
          else:
            # Rename Makeflow scar safely without primary key collision
            suffix = scar_id[5:] if scar_id.startswith("SCAR-") else scar_id
            base_mf_id = f"SCAR-MF-{suffix}"
            new_mf_id = base_mf_id
            if new_mf_id in existing_rows:
              existing_mf = existing_rows[new_mf_id]
              if (
                  existing_mf.get("title") == mf_row.get("title")
                  and existing_mf.get("wound") == mf_row.get("wound")
              ):
                cur.execute(
                    "DELETE FROM scars WHERE id = ? AND source = 'makeflow'",
                    (scar_id,),
                )
                del existing_rows[scar_id]
                modified = True
                continue
              counter = 2
              while f"{base_mf_id}-{counter}" in existing_rows:
                counter += 1
              new_mf_id = f"{base_mf_id}-{counter}"

            cur.execute(
                "UPDATE scars SET id = ?, original_id = ? WHERE id = ? AND source = 'makeflow'",
                (new_mf_id, scar_id, scar_id),
            )
            mf_row["id"] = new_mf_id
            mf_row["original_id"] = scar_id
            existing_rows[new_mf_id] = mf_row
            del existing_rows[scar_id]
            modified = True

      # Second pass: delete upstream rows that are no longer in upstream list
      for eid, erow in list(existing_rows.items()):
        if erow.get("source") == "upstream" and eid not in incoming_upstream_ids:
          cur.execute("DELETE FROM scars WHERE id = ? AND source = 'upstream'", (eid,))
          del existing_rows[eid]
          modified = True

      # Third pass: delta sync incoming upstream scars
      inserted_or_updated = 0
      for item in scars_list:
        scar_id = item.get("id")
        if not scar_id:
          continue

        raw_hash = hashlib.md5(
            json.dumps(item, sort_keys=True).encode("utf-8")
        ).hexdigest()

        existing_row = existing_rows.get(scar_id)
        if (
            existing_row
            and existing_row.get("source") == "upstream"
            and existing_row.get("raw_hash") == raw_hash
        ):
          # Skip table rewrite for unchanged row
          continue

        title = item.get("title", "")
        category = item.get("category", "")
        cat_slug = item.get("categorySlug") or item.get("category_slug", "")
        visibility = item.get("visibility", "public")

        phases_raw = item.get("workflowPhases") or item.get("workflow_phases", [])
        if isinstance(phases_raw, list):
          phases_json = json.dumps(phases_raw)
        else:
          phases_json = json.dumps([str(phases_raw)])

        applies_when = item.get("appliesWhen") or item.get("applies_when", "")
        grad_status = item.get("graduationStatus") or item.get("graduation_status", "")
        wound = item.get("wound", "")
        trap = item.get("trap", "")
        breaker = item.get("circuitBreaker") or item.get("circuit_breaker", "")
        reflex = item.get("reflex", "")

        cur.execute(
            """
            INSERT OR REPLACE INTO scars (
                id, title, category, category_slug, visibility,
                workflow_phases, applies_when, graduation_status,
                wound, trap, circuit_breaker, reflex, source, original_id, raw_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'upstream', NULL, ?)
        """,
            (
                scar_id,
                title,
                category,
                cat_slug,
                visibility,
                phases_json,
                applies_when,
                grad_status,
                wound,
                trap,
                breaker,
                reflex,
                raw_hash,
            ),
        )
        existing_rows[scar_id] = {
            "id": scar_id,
            "title": title,
            "wound": wound,
            "trap": trap,
            "reflex": reflex,
            "raw_hash": raw_hash,
            "source": "upstream",
            "original_id": None,
        }
        modified = True
        inserted_or_updated += 1

      # Check if scars_fts needs refresh (if modified or scars_fts empty while scars not empty)
      if self.has_fts5:
        cur.execute("SELECT COUNT(*) FROM scars_fts")
        fts_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM scars")
        total_count = cur.fetchone()[0]
        if modified or (fts_count == 0 and total_count > 0):
          cur.execute("DELETE FROM scars_fts")
          cur.execute("""
              INSERT INTO scars_fts (
                  id, title, category, applies_when, wound, trap, reflex, source, original_id
              )
              SELECT id, title, category, applies_when, wound, trap, reflex, source, original_id
              FROM scars
          """)
          cur.execute("INSERT INTO scars_fts(scars_fts) VALUES('optimize')")
      else:
        cur.execute("SELECT COUNT(*) FROM scars")
        total_count = cur.fetchone()[0]

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
      file_size = os.path.getsize(self.db_path) if os.path.isfile(self.db_path) else 0
      cur.execute(
          "INSERT OR REPLACE INTO meta (key, value) VALUES ('db_size_bytes', ?)",
          (str(file_size),),
      )
      self.conn.commit()
    except Exception as exc:
      self.conn.rollback()
      self.stderr.write(f"Error: Failed to insert scars into database: {exc}\n")
      return 1

    self.stdout.write(
        f"Successfully synchronized {len(scars_list)} synthetic scars ({inserted_or_updated} updated) to '{self.db_path}'.\n"
    )
    return 0

  def cleanup(self, exit_code: int) -> None:
    """Closes SQLite connection."""
    if self.conn:
      try:
        self.conn.close()
      except sqlite3.Error:
        pass
      self.conn = None


if __name__ == "__main__":
  sys.exit(SyncSyntheticScarsScript().execute())
