"""SQLite schema, FTS5 query, enrichment, and session ledger for synthetic scars (GH-118, GH-209).

Provides the core data-access spine for bundled and stacked SQLite synthetic
scars registries: schema initialization, FTS5 indexing, lossless 4-field
raw-hash backfilling, deterministic metadata enrichment, multi-dimensional
queries, protected-span extraction, and per-ticket session scar tracking.
"""

import hashlib
import json
import os
from pathlib import PurePath
import re
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

from lib.agent_paths import AgentPathRegistry
from lib.scar_metadata import (
    _normalize_workflow_phases,
    extract_scar_metadata,
    parse_json_list,
)
from lib.scar_ontology import (
    TARGET_INT_TO_PHASE_LABEL,
    resolve_category_filter_tokens,
    resolve_excluded_category_tokens,
)
from lib.ticket_state import TicketStateStore

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_REGISTRY_URL = "https://syntheticscars.org/registry.json"
RAW_HASH_VERSION = "4fields-v1"

_SCARS_COLUMN_DEFS: Tuple[Tuple[str, str], ...] = (
    ("id", "TEXT NOT NULL PRIMARY KEY"),
    ("ss_id", "TEXT"),
    ("title", "TEXT"),
    ("category", "TEXT"),
    ("category_slug", "TEXT"),
    ("visibility", "TEXT"),
    ("workflow_phases", "TEXT"),
    ("ss_targets", "TEXT"),
    ("mf_targets", "TEXT"),
    ("applies_when", "TEXT"),
    ("graduation_status", "TEXT"),
    ("raw_wound", "TEXT"),
    ("raw_trap", "TEXT"),
    ("raw_circuit_breaker", "TEXT"),
    ("raw_reflex", "TEXT"),
    ("raw_hash", "TEXT"),
    ("wound", "TEXT"),
    ("trap", "TEXT"),
    ("circuit_breaker", "TEXT"),
    ("reflex", "TEXT"),
    ("socratic_probe", "TEXT"),
    ("source", "TEXT DEFAULT 'upstream'"),
    ("original_id", "TEXT"),
    ("keywords", "TEXT"),
    ("languages", "TEXT"),
    ("frameworks", "TEXT"),
    ("diagnostic_codes", "TEXT"),
)

_SIMPLE_ENGLISH_COMPLETE_SQL = (
    "wound IS NOT NULL AND TRIM(wound) != '' "
    "AND trap IS NOT NULL AND TRIM(trap) != '' "
    "AND reflex IS NOT NULL AND TRIM(reflex) != '' "
    "AND (raw_circuit_breaker IS NULL "
    "OR TRIM(raw_circuit_breaker) = '' "
    "OR (circuit_breaker IS NOT NULL AND TRIM(circuit_breaker) != ''))"
)

_NEEDS_IMPORTER_SQL = (
    "wound IS NULL OR TRIM(wound) = '' "
    "OR trap IS NULL OR TRIM(trap) = '' "
    "OR reflex IS NULL OR TRIM(reflex) = '' "
    "OR (raw_circuit_breaker IS NOT NULL "
    "AND TRIM(raw_circuit_breaker) != '' "
    "AND (circuit_breaker IS NULL OR TRIM(circuit_breaker) = ''))"
)

_NEEDS_PROBE_WRITER_SQL = (
    f"({_SIMPLE_ENGLISH_COMPLETE_SQL}) "
    "AND (socratic_probe IS NULL OR TRIM(socratic_probe) = '')"
)

_COMPLETE_SCAR_SQL = (
    f"({_SIMPLE_ENGLISH_COMPLETE_SQL}) "
    "AND socratic_probe IS NOT NULL AND TRIM(socratic_probe) != ''"
)


def compute_raw_fields_hash(
    raw_wound: Any,
    raw_trap: Any,
    raw_circuit_breaker: Any,
    raw_reflex: Any,
) -> str:
  """Compute a deterministic SHA-256 digest over the four raw prose fields.

  Args:
    raw_wound: Raw upstream wound text.
    raw_trap: Raw upstream trap text.
    raw_circuit_breaker: Raw upstream circuit breaker text.
    raw_reflex: Raw upstream reflex text.

  Returns:
    Hex-encoded SHA-256 digest of the canonical 4-field JSON list.
  """
  canonical = json.dumps(
      [
          str(raw_wound or ""),
          str(raw_trap or ""),
          str(raw_circuit_breaker or ""),
          str(raw_reflex or ""),
      ],
      ensure_ascii=False,
      separators=(",", ":"),
  )
  return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def resolve_scars_db_path(
    db_path: Optional[str] = None,
    agent: Optional[str] = None,
    workspace_dir: Optional[str] = None,
    package_root: Optional[str] = None,
) -> str:
  """Resolve the primary SQLite scars database path by priority cascade.

  Args:
    db_path: Optional explicit filesystem path override.
    agent: Optional agent flavor name (gemini, claude, cursor, copilot, codex, chatgpt).
    workspace_dir: Optional workspace directory to check for a local cache DB.
    package_root: Optional plugin root directory override.

  Returns:
    Normalized absolute path to the selected SQLite database file.
  """
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
    ws_db = os.path.join(
        workspace_dir, ".workflow", "cache", "scars_registry.db"
    )
    if os.path.exists(ws_db):
      return os.path.abspath(ws_db)

  home = os.path.expanduser("~")
  agent_key = (agent or "").strip().lower()
  if agent_key:
    registry = AgentPathRegistry(home_dir=home)
    try:
      return registry.get_profile(agent_key).scars_db_path
    except ValueError:
      pass

  xdg_cache = os.getenv("XDG_CACHE_HOME")
  if xdg_cache:
    xdg_db = os.path.join(xdg_cache, "makeflow", "scars_registry.db")
    if os.path.exists(xdg_db):
      return os.path.abspath(xdg_db)

  user_default = os.path.join(home, ".cache", "makeflow", "scars_registry.db")
  if os.path.exists(user_default):
    return user_default

  return os.path.abspath(bundled_path)


def _resolve_custom_path(raw_path: str, workspace_dir: Optional[str]) -> str:
  """Expand user tilde and resolve relative custom scar paths against workspace_dir."""
  expanded = os.path.expanduser(raw_path.strip())
  if not os.path.isabs(expanded) and workspace_dir:
    expanded = os.path.join(workspace_dir, expanded)
  return os.path.abspath(expanded)


def resolve_scars_db_paths(
    db_path: Optional[str] = None,
    agent: Optional[str] = None,
    workspace_dir: Optional[str] = None,
    package_root: Optional[str] = None,
    custom_dirs: Optional[Sequence[str]] = None,
) -> List[str]:
  """Resolve all existing SQLite scars databases in multi-tier precedence order.

  Precedence order:
    1. Explicit ``db_path``.
    2. Environment override ``MAKEFLOW_SCARS_DB``.
    3. Workspace database under ``<workspace_dir>/.workflow/``.
    4. User database under agent plugin_data or cache directories.
    5. Custom configured scar directories (``<dir>/scars_registry.db``).
    6. Bundled database under ``<package_root>/skills/makeflow/data/``.

  Args:
    db_path: Optional explicit database file path.
    agent: Optional agent flavor name.
    workspace_dir: Optional workspace directory path.
    package_root: Optional plugin root directory override.
    custom_dirs: Optional sequence of custom configured scar directories.

  Returns:
    Deduplicated list of existing absolute database file paths.
  """
  candidates: List[str] = []

  if db_path:
    candidates.append(db_path)

  env_path = os.getenv("MAKEFLOW_SCARS_DB")
  if env_path:
    candidates.append(env_path)

  if workspace_dir:
    candidates.append(
        os.path.join(workspace_dir, ".workflow", "cache", "scars_registry.db")
    )
    candidates.append(
        os.path.join(workspace_dir, ".workflow", "scars_registry.db")
    )

  home = os.path.expanduser("~")
  registry = AgentPathRegistry(home_dir=home)
  try:
    candidates.extend(registry.scars_db_paths(active_agent=agent))
  except ValueError:
    candidates.extend(registry.scars_db_paths(active_agent=None))

  xdg_cache = os.getenv("XDG_CACHE_HOME")
  if xdg_cache:
    candidates.append(os.path.join(xdg_cache, "makeflow", "scars_registry.db"))
  candidates.append(
      os.path.join(home, ".cache", "makeflow", "scars_registry.db")
  )

  for raw_dir in custom_dirs or ():
    if not isinstance(raw_dir, str) or not raw_dir.strip():
      continue
    resolved_dir = _resolve_custom_path(raw_dir, workspace_dir)
    if resolved_dir.endswith(".db"):
      candidates.append(resolved_dir)
    else:
      candidates.append(os.path.join(resolved_dir, "scars_registry.db"))

  pkg_root = package_root or _REPO_ROOT
  candidates.append(
      os.path.join(pkg_root, "skills", "makeflow", "data", "scars_registry.db")
  )

  resolved: List[str] = []
  seen: Set[str] = set()
  for raw_p in candidates:
    if not raw_p:
      continue
    norm_p = os.path.abspath(os.path.expanduser(raw_p))
    if norm_p not in seen and os.path.isfile(norm_p):
      seen.add(norm_p)
      resolved.append(norm_p)
  return resolved


def resolve_scar_markdown_dirs(
    workspace_dir: Optional[str] = None,
    agent: Optional[str] = None,
    custom_dirs: Optional[Sequence[str]] = None,
    home_dir: Optional[str] = None,
) -> List[str]:
  """Resolve existing Markdown scar directories in 3-tier precedence order.

  Precedence order:
    1. Project-local ``<workspace_dir>/.workflow/scars``.
    2. User-wide agent ``plugin_data/makeflow/scars`` (active agent first).
    3. Custom configured scar directories from cascaded ``scars.directories``.

  Args:
    workspace_dir: Optional project root directory.
    agent: Optional active agent override.
    custom_dirs: Optional sequence of configured custom scar directories.
    home_dir: Optional home directory override for isolated testing.

  Returns:
    Deduplicated list of existing absolute Markdown scar directory paths.
  """
  candidates: List[str] = []
  if workspace_dir:
    candidates.append(
        os.path.join(
            os.path.abspath(os.path.expanduser(workspace_dir)),
            ".workflow",
            "scars",
        )
    )

  registry = AgentPathRegistry(home_dir=home_dir)
  try:
    candidates.extend(registry.scars_dirs(active_agent=agent))
  except ValueError:
    candidates.extend(registry.scars_dirs(active_agent=None))

  for raw_dir in custom_dirs or ():
    if not isinstance(raw_dir, str) or not raw_dir.strip():
      continue
    candidates.append(_resolve_custom_path(raw_dir, workspace_dir))

  resolved: List[str] = []
  seen: Set[str] = set()
  for raw_dir in candidates:
    norm_dir = os.path.abspath(os.path.expanduser(raw_dir))
    if norm_dir not in seen and os.path.isdir(norm_dir):
      seen.add(norm_dir)
      resolved.append(norm_dir)
  return resolved


_IN_MEMORY_LOADED_SCARS: Dict[str, Set[str]] = {}


def detect_active_ticket_id(state_dir: Optional[str]) -> str:
  """Extract the active ticket ID from ``<state_dir>/10_ticket.json`` if present.

  Args:
    state_dir: Path to ``.workflow/state`` directory.

  Returns:
    Active ticket identifier string, or empty string when absent.
  """
  if not state_dir or not os.path.isdir(state_dir):
    return ""
  meta = TicketStateStore(state_dir).load()
  return meta.ticket_id if meta is not None else ""


def load_session_scars(
    state_dir: Optional[str] = None,
    session_key: Optional[str] = None,
) -> Set[str]:
  """Load scar IDs already returned during the current ticket session.

  Args:
    state_dir: Optional ``.workflow/state`` directory for persistent ledger.
    session_key: Optional fallback key for in-memory session storage.

  Returns:
    Set of previously loaded scar IDs.
  """
  if state_dir and os.path.isdir(state_dir):
    ledger_path = os.path.join(state_dir, "loaded_scars.json")
    active_ticket = detect_active_ticket_id(state_dir)
    if os.path.isfile(ledger_path):
      try:
        with open(ledger_path, "r", encoding="utf-8") as f:
          data = json.load(f)
        if isinstance(data, dict):
          recorded_ticket = str(data.get("ticketId") or "")
          if recorded_ticket == active_ticket:
            ids = data.get("loadedIds")
            if isinstance(ids, list):
              return {str(x) for x in ids if str(x).strip()}
          return set()
      except (OSError, ValueError, TypeError):
        return set()
    return set()

  key = os.path.abspath(state_dir or session_key or "default")
  return set(_IN_MEMORY_LOADED_SCARS.get(key, set()))


def save_session_scars(
    loaded_ids: Iterable[str],
    state_dir: Optional[str] = None,
    session_key: Optional[str] = None,
) -> None:
  """Persist loaded scar IDs to ``<state_dir>/loaded_scars.json`` or memory.

  Args:
    loaded_ids: Iterable of scar IDs loaded during the current ticket session.
    state_dir: Optional ``.workflow/state`` directory for disk persistence.
    session_key: Optional fallback key for in-memory session storage.
  """
  clean_ids = sorted({str(x).strip() for x in loaded_ids if str(x).strip()})
  key = os.path.abspath(state_dir or session_key or "default")
  _IN_MEMORY_LOADED_SCARS[key] = set(clean_ids)

  if state_dir and os.path.isdir(state_dir):
    ledger_path = os.path.join(state_dir, "loaded_scars.json")
    payload = {
        "ticketId": detect_active_ticket_id(state_dir),
        "loadedIds": clean_ids,
    }
    try:
      with open(ledger_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    except OSError:
      pass


def format_loaded_scar_reminder(scar: Dict[str, Any]) -> Dict[str, Any]:
  """Format a compact reminder dictionary for an already-loaded scar.

  Args:
    scar: Full structured scar dictionary.

  Returns:
    Compact reminder dictionary retaining IDs, targets, and ``socratic_probe``.
  """
  scar_id = str(scar.get("id", ""))
  title = str(scar.get("title", ""))
  probe = str(scar.get("socraticProbe") or scar.get("socratic_probe") or "")
  return {
      "id": scar_id,
      "ssId": scar.get("ssId") if "ssId" in scar else scar.get("ss_id"),
      "ss_id": scar.get("ss_id") if "ss_id" in scar else scar.get("ssId"),
      "title": title,
      "category": scar.get("category", ""),
      "categorySlug": scar.get("categorySlug", ""),
      "source": scar.get("source", "upstream"),
      "originalId": scar.get("originalId"),
      "original_id": scar.get("original_id"),
      "workflowPhases": scar.get("workflowPhases", []),
      "workflow_phases": scar.get("workflow_phases", []),
      "ssTargets": scar.get("ssTargets", []),
      "ss_targets": scar.get("ss_targets", []),
      "mfTargets": scar.get("mfTargets", []),
      "mf_targets": scar.get("mf_targets", []),
      "keywords": scar.get("keywords", []),
      "languages": scar.get("languages", []),
      "frameworks": scar.get("frameworks", []),
      "diagnosticCodes": scar.get("diagnosticCodes", []),
      "diagnostic_codes": scar.get("diagnostic_codes", []),
      "alreadyLoaded": True,
      "reminder": (
          f"Scar {scar_id} ({title}) was already loaded in this session. "
          "Refer to its reflex and socratic_probe in your context or .workflow/state/ cards."
      ),
      "socraticProbe": probe,
      "socratic_probe": probe,
  }


def partition_session_scars(
    matches: Sequence[Dict[str, Any]],
    loaded_ids: Set[str],
    limit: int = 3,
    refresh_loaded: bool = False,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Set[str]]:
  """Partition query matches into newly loaded scars and already-loaded reminders.

  Args:
    matches: Ordered list of matching scar dictionaries.
    loaded_ids: Set of scar IDs already returned in this session.
    limit: Maximum number of results to emit.
    refresh_loaded: When True, emit full scar payloads even if previously loaded.

  Returns:
    Tuple of ``(selected_scars, already_loaded_reminders, updated_loaded_ids)``.
  """
  eff_limit = max(1, limit)
  if refresh_loaded:
    selected = list(matches[:eff_limit])
    updated = set(loaded_ids) | {
        str(m["id"]) for m in selected if m.get("id")
    }
    return selected, [], updated

  new_scars: List[Dict[str, Any]] = []
  already_loaded: List[Dict[str, Any]] = []
  for m in matches:
    m_id = str(m.get("id", ""))
    if m_id in loaded_ids:
      already_loaded.append(format_loaded_scar_reminder(m))
    else:
      new_scars.append(m)

  selected_new = new_scars[:eff_limit]
  updated_loaded = set(loaded_ids) | {
      str(m["id"]) for m in selected_new if m.get("id")
  }
  if selected_new:
    return selected_new, already_loaded, updated_loaded
  if already_loaded:
    rem_slice = already_loaded[:eff_limit]
    return rem_slice, rem_slice, updated_loaded
  return [], [], updated_loaded


def populate_scars_fts(cur: sqlite3.Cursor) -> None:
  """Rebuild ``scars_fts`` from ``scars`` rows and optimize the FTS5 index.

  Args:
    cur: Open SQLite cursor on a connection with an initialized ``scars_fts`` table.
  """
  cur.execute("DELETE FROM scars_fts")
  cur.execute(
      """
      INSERT INTO scars_fts (
          id, ss_id, title, category, applies_when,
          wound, trap, reflex, socratic_probe,
          keywords, languages, frameworks, diagnostic_codes, workflow_phases,
          source, original_id
      )
      SELECT
          id,
          COALESCE(ss_id, ''),
          title,
          category,
          applies_when,
          COALESCE(wound, raw_wound, ''),
          COALESCE(trap, raw_trap, ''),
          COALESCE(reflex, raw_reflex, ''),
          COALESCE(socratic_probe, ''),
          COALESCE(keywords, '[]'),
          COALESCE(languages, '[]'),
          COALESCE(frameworks, '[]'),
          COALESCE(diagnostic_codes, '[]'),
          COALESCE(workflow_phases, '[]'),
          source,
          COALESCE(original_id, '')
      FROM scars
      """
  )
  cur.execute("INSERT INTO scars_fts(scars_fts) VALUES('optimize')")


def _rebuild_scars_not_null_table(
    cur: sqlite3.Cursor, existing_cols: Set[str]
) -> None:
  """Atomically rebuild legacy ``scars`` table so ``id`` enforces ``NOT NULL``."""
  col_defs_sql = ",\n            ".join(
      f"{col_name} {col_def}" for col_name, col_def in _SCARS_COLUMN_DEFS
  )
  cur.execute("DROP TABLE IF EXISTS scars__rebuild_tmp")
  cur.execute(
      f"CREATE TABLE scars__rebuild_tmp (\n            {col_defs_sql}\n        )"
  )
  copy_cols = [
      col_name for col_name, _ in _SCARS_COLUMN_DEFS if col_name in existing_cols
  ]
  copy_cols_sql = ", ".join(copy_cols)
  cur.execute(
      f"INSERT INTO scars__rebuild_tmp ({copy_cols_sql}) "
      f"SELECT {copy_cols_sql} FROM scars WHERE id IS NOT NULL AND TRIM(id) != ''"
  )
  cur.execute("DROP TABLE scars")
  cur.execute("ALTER TABLE scars__rebuild_tmp RENAME TO scars")


def init_scars_db_schema(conn: sqlite3.Connection) -> bool:
  """Initialize ``meta``, ``scars``, indexes, and ``scars_fts`` virtual table.

  Args:
    conn: Open SQLite connection to initialize or migrate in place.

  Returns:
    True when FTS5 virtual table support is available and initialized.
  """
  cur = conn.cursor()
  cur.execute(
      """
      CREATE TABLE IF NOT EXISTS meta (
          key TEXT PRIMARY KEY,
          value TEXT
      )
      """
  )
  col_defs_sql = ",\n            ".join(
      f"{col_name} {col_def}" for col_name, col_def in _SCARS_COLUMN_DEFS
  )
  cur.execute(
      f"""
      CREATE TABLE IF NOT EXISTS scars (
          {col_defs_sql}
      )
      """
  )

  cur.execute("PRAGMA table_info(scars)")
  table_info = cur.fetchall()
  existing_cols = {r[1] for r in table_info}
  id_info = next((r for r in table_info if r[1] == "id"), None)
  if id_info is not None and int(id_info[3] or 0) == 0:
    _rebuild_scars_not_null_table(cur, existing_cols)
    cur.execute("PRAGMA table_info(scars)")
    existing_cols = {r[1] for r in cur.fetchall()}

  for col_name, col_def in _SCARS_COLUMN_DEFS[1:]:
    if col_name not in existing_cols:
      cur.execute(f"ALTER TABLE scars ADD COLUMN {col_name} {col_def}")

  cur.execute(
      "CREATE UNIQUE INDEX IF NOT EXISTS idx_scars_ss_id "
      "ON scars(ss_id) WHERE ss_id IS NOT NULL"
  )

  cur.execute(
      """
      UPDATE scars
      SET raw_wound = COALESCE(raw_wound, wound),
          raw_trap = COALESCE(raw_trap, trap),
          raw_circuit_breaker = COALESCE(raw_circuit_breaker, circuit_breaker),
          raw_reflex = COALESCE(raw_reflex, reflex)
      WHERE raw_wound IS NULL
      """
  )

  has_fts5 = False
  try:
    cur.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='scars_fts'"
    )
    fts_row = cur.fetchone()
    if fts_row:
      fts_sql = str(fts_row[0] or "").lower()
      cur.execute("PRAGMA table_info(scars_fts)")
      fts_cols = {r[1] for r in cur.fetchall()}
      required_fts_cols = {
          "id",
          "ss_id",
          "title",
          "category",
          "applies_when",
          "wound",
          "trap",
          "reflex",
          "socratic_probe",
          "keywords",
          "languages",
          "frameworks",
          "diagnostic_codes",
          "workflow_phases",
          "source",
          "original_id",
      }
      if (
          not required_fts_cols.issubset(fts_cols)
          or "id unindexed" in fts_sql
          or "original_id unindexed" in fts_sql
      ):
        cur.execute("DROP TABLE scars_fts")

    cur.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS scars_fts USING fts5(
            id,
            ss_id,
            title,
            category,
            applies_when,
            wound,
            trap,
            reflex,
            socratic_probe,
            keywords,
            languages,
            frameworks,
            diagnostic_codes,
            workflow_phases,
            source UNINDEXED,
            original_id
        )
        """
    )
    cur.execute("SELECT COUNT(*) FROM scars_fts")
    fts_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM scars")
    total_count = cur.fetchone()[0]
    if fts_count == 0 and total_count > 0:
      populate_scars_fts(cur)
    has_fts5 = True
  except sqlite3.OperationalError:
    has_fts5 = False

  conn.commit()
  return has_fts5


def update_db_size_meta(conn: sqlite3.Connection, db_path: str) -> int:
  """Record current SQLite database file byte size into ``meta.db_size_bytes``.

  Args:
    conn: Open SQLite connection.
    db_path: Filesystem path to the SQLite file.

  Returns:
    Measured file size in bytes (0 when file does not exist on disk).
  """
  file_size = os.path.getsize(db_path) if os.path.isfile(db_path) else 0
  cur = conn.cursor()
  cur.execute(
      "INSERT OR REPLACE INTO meta (key, value) VALUES ('db_size_bytes', ?)",
      (str(file_size),),
  )
  conn.commit()
  return file_size


def backfill_raw_hashes_if_needed(conn: sqlite3.Connection) -> int:
  """Perform lossless one-time 4-field ``raw_hash`` backfill tracked in ``meta``.

  Recomputes ``raw_hash`` over ``(raw_wound, raw_trap, raw_circuit_breaker,
  raw_reflex)`` for all existing rows when ``meta.raw_hash_version`` differs
  from ``RAW_HASH_VERSION``. Leaves all transformed prose columns untouched.

  Args:
    conn: Open SQLite connection with initialized schema.

  Returns:
    Number of rows whose ``raw_hash`` was backfilled (0 if already current).
  """
  cur = conn.cursor()
  cur.execute("SELECT value FROM meta WHERE key = 'raw_hash_version'")
  row = cur.fetchone()
  if row and row[0] == RAW_HASH_VERSION:
    return 0

  cur.execute(
      "SELECT id, raw_wound, raw_trap, raw_circuit_breaker, raw_reflex FROM scars"
  )
  rows = cur.fetchall()
  updates = [
      (compute_raw_fields_hash(r[1], r[2], r[3], r[4]), r[0]) for r in rows
  ]
  cur.executemany("UPDATE scars SET raw_hash = ? WHERE id = ?", updates)
  cur.execute(
      "INSERT OR REPLACE INTO meta (key, value) VALUES ('raw_hash_version', ?)",
      (RAW_HASH_VERSION,),
  )
  conn.commit()
  return len(updates)


def enrich_scars_cursor(
    cur: sqlite3.Cursor,
    scar_ids: Optional[Sequence[str]] = None,
) -> int:
  """Populate metadata columns in ``scars`` for all or specified scar IDs.

  Args:
    cur: Open SQLite cursor.
    scar_ids: Optional sequence of canonical Makeflow scar IDs to enrich.

  Returns:
    Number of enriched rows.
  """
  base_cols = (
      "id, ss_id, title, category, category_slug, workflow_phases,"
      " ss_targets, applies_when, raw_wound, raw_trap, raw_circuit_breaker,"
      " raw_reflex, wound, trap, circuit_breaker, reflex, socratic_probe,"
      " original_id"
  )
  if scar_ids is not None:
    if not scar_ids:
      return 0
    placeholders = ", ".join(["?"] * len(scar_ids))
    cur.execute(
        f"SELECT {base_cols} FROM scars WHERE id IN ({placeholders})",
        tuple(scar_ids),
    )
  else:
    cur.execute(f"SELECT {base_cols} FROM scars")
  rows = cur.fetchall()
  enriched_count = 0
  for r in rows:
    raw_ss_targets = r[6]
    if raw_ss_targets is None:
      raw_ss_targets = r[5]
    scar_row = {
        "id": r[0],
        "ss_id": r[1],
        "title": r[2],
        "category": r[3],
        "category_slug": r[4],
        "workflow_phases": r[5],
        "ss_targets": raw_ss_targets,
        "applies_when": r[7],
        "raw_wound": r[8],
        "raw_trap": r[9],
        "raw_circuit_breaker": r[10],
        "raw_reflex": r[11],
        "wound": r[12],
        "trap": r[13],
        "circuit_breaker": r[14],
        "reflex": r[15],
        "socratic_probe": r[16],
        "original_id": r[17],
    }
    meta = extract_scar_metadata(scar_row)
    cur.execute(
        """
        UPDATE scars
        SET keywords = ?,
            languages = ?,
            frameworks = ?,
            diagnostic_codes = ?,
            workflow_phases = ?,
            ss_targets = ?,
            mf_targets = ?
        WHERE id = ?
        """,
        (
            json.dumps(meta["keywords"]),
            json.dumps(meta["languages"]),
            json.dumps(meta["frameworks"]),
            json.dumps(meta["diagnostic_codes"]),
            json.dumps(meta["workflow_phases"]),
            raw_ss_targets,
            json.dumps(meta["mf_targets"]),
            r[0],
        ),
    )
    enriched_count += 1
  return enriched_count


def enrich_scars_database(db_path: str) -> Dict[str, Any]:
  """Enrich metadata columns and rebuild FTS5 index for all scars in ``db_path``.

  Args:
    db_path: Path to SQLite scars registry database.

  Returns:
    Summary dictionary with ``db_path``, ``enriched``, ``enriched_scars``, and
    ``total_scars``.
  """
  resolved_path = resolve_scars_db_path(db_path=db_path)
  if not os.path.isfile(resolved_path):
    return {
        "db_path": resolved_path,
        "enriched": 0,
        "enriched_scars": 0,
        "total_scars": 0,
    }

  conn = sqlite3.connect(resolved_path)
  try:
    has_fts5 = init_scars_db_schema(conn)
    cur = conn.cursor()
    cur.execute("BEGIN TRANSACTION")
    enriched_count = enrich_scars_cursor(cur)
    if has_fts5:
      populate_scars_fts(cur)
    conn.commit()
    update_db_size_meta(conn, resolved_path)
    return {
        "db_path": resolved_path,
        "enriched": enriched_count,
        "enriched_scars": enriched_count,
        "total_scars": enriched_count,
    }
  finally:
    conn.close()


def count_scar_pipeline_status(cur: sqlite3.Cursor) -> Dict[str, int]:
  """Count total, complete, importer-pending, and probe-pending scars.

  Args:
    cur: Open SQLite cursor on an initialized scars database.

  Returns:
    Dictionary with keys ``total``, ``complete``, ``needs_importer``, and
    ``needs_probe_writer``.
  """
  cur.execute("SELECT COUNT(*) FROM scars")
  total = int(cur.fetchone()[0])
  cur.execute(f"SELECT COUNT(*) FROM scars WHERE {_COMPLETE_SCAR_SQL}")
  complete = int(cur.fetchone()[0])
  cur.execute(f"SELECT COUNT(*) FROM scars WHERE {_NEEDS_IMPORTER_SQL}")
  needs_importer = int(cur.fetchone()[0])
  cur.execute(f"SELECT COUNT(*) FROM scars WHERE {_NEEDS_PROBE_WRITER_SQL}")
  needs_probe_writer = int(cur.fetchone()[0])
  return {
      "total": total,
      "complete": complete,
      "needs_importer": needs_importer,
      "needs_probe_writer": needs_probe_writer,
  }


def select_scars_needing_importer(cur: sqlite3.Cursor) -> List[Dict[str, Any]]:
  """Select scars needing Simple English transformation ordered by ``id ASC``.

  Args:
    cur: Open SQLite cursor on an initialized scars database.

  Returns:
    List of row dictionaries containing raw and transformed scar fields.
  """
  cur.execute(
      f"""
      SELECT id, ss_id, title, category, applies_when,
             raw_wound, raw_trap, raw_circuit_breaker, raw_reflex,
             wound, trap, circuit_breaker, reflex, socratic_probe
      FROM scars
      WHERE {_NEEDS_IMPORTER_SQL}
      ORDER BY id ASC
      """
  )
  cols = [desc[0] for desc in cur.description]
  return [dict(zip(cols, row)) for row in cur.fetchall()]


def select_scars_needing_probe_writer(
    cur: sqlite3.Cursor,
) -> List[Dict[str, Any]]:
  """Select scars with complete Simple English fields needing ``socratic_probe``.

  Args:
    cur: Open SQLite cursor on an initialized scars database.

  Returns:
    List of row dictionaries ordered by ``id ASC``.
  """
  cur.execute(
      f"""
      SELECT id, ss_id, title, category, applies_when,
             raw_wound, raw_trap, raw_circuit_breaker, raw_reflex,
             wound, trap, circuit_breaker, reflex, socratic_probe
      FROM scars
      WHERE {_NEEDS_PROBE_WRITER_SQL}
      ORDER BY id ASC
      """
  )
  cols = [desc[0] for desc in cur.description]
  return [dict(zip(cols, row)) for row in cur.fetchall()]


def _sanitize_fts_query(query: str) -> str:
  """Sanitize user search query for safe FTS5 MATCH evaluation."""
  tokens = re.findall(r"[a-zA-Z0-9_\-]+", query)
  if not tokens:
    return ""
  quoted = []
  for t in tokens:
    cleaned = t.replace('"', "")
    if cleaned:
      quoted.append(f'"{cleaned}"*')
  return " ".join(quoted)


def _format_scar_row(row: Any) -> Dict[str, Any]:
  """Format a SQLite row tuple into a structured scar dictionary."""
  phases = parse_json_list(row[5] or "[]")

  socratic_probe = row[12] if len(row) > 12 and row[12] is not None else ""
  source = row[13] if len(row) > 13 and row[13] is not None else "upstream"
  orig_id = row[14] if len(row) > 14 and row[14] is not None else None
  raw_hash = row[15] if len(row) > 15 and row[15] is not None else None
  keywords = parse_json_list(row[16]) if len(row) > 16 else []
  languages = parse_json_list(row[17]) if len(row) > 17 else []
  frameworks = parse_json_list(row[18]) if len(row) > 18 else []
  diag_codes = parse_json_list(row[19]) if len(row) > 19 else []
  ss_id = row[20] if len(row) > 20 and row[20] is not None else None
  ss_targets = parse_json_list(row[21]) if len(row) > 21 else []
  mf_targets_raw = parse_json_list(row[22]) if len(row) > 22 else []
  mf_targets: List[int] = []
  for item in mf_targets_raw:
    try:
      mf_targets.append(int(item))
    except (TypeError, ValueError):
      continue

  return {
      "id": row[0],
      "ssId": ss_id,
      "ss_id": ss_id,
      "title": row[1],
      "category": row[2],
      "categorySlug": row[3],
      "visibility": row[4],
      "workflowPhases": phases,
      "workflow_phases": phases,
      "ssTargets": ss_targets,
      "ss_targets": ss_targets,
      "mfTargets": mf_targets,
      "mf_targets": mf_targets,
      "appliesWhen": row[6],
      "graduationStatus": row[7],
      "wound": row[8],
      "trap": row[9],
      "circuitBreaker": row[10],
      "reflex": row[11],
      "socraticProbe": socratic_probe,
      "socratic_probe": socratic_probe,
      "source": source,
      "originalId": orig_id,
      "original_id": orig_id,
      "rawHash": raw_hash,
      "raw_hash": raw_hash,
      "keywords": keywords,
      "languages": languages,
      "frameworks": frameworks,
      "diagnosticCodes": diag_codes,
      "diagnostic_codes": diag_codes,
  }


def _query_scars_connection(
    conn: sqlite3.Connection,
    query: str = "",
    category: Optional[str] = None,
    language: Optional[str] = None,
    limit: int = 3,
    source: Optional[str] = None,
    framework: Optional[str] = None,
    diagnostic_code: Optional[str] = None,
    workflow_phase: Optional[str] = None,
    mf_target: Optional[int] = None,
    exclude_categories: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
  """Execute multi-dimensional FTS5/LIKE scar query on an open SQLite connection."""
  excluded_tokens = resolve_excluded_category_tokens(exclude_categories)
  try:
    cur = conn.cursor()

    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='scars'"
    )
    if not cur.fetchone():
      return []

    cur.execute("PRAGMA table_info(scars)")
    cols = {r[1] for r in cur.fetchall()}
    for req_col in ("wound", "trap", "reflex", "socratic_probe"):
      if req_col not in cols:
        return []

    has_source = "source" in cols
    has_orig_id = "original_id" in cols
    has_raw_hash = "raw_hash" in cols
    has_keywords = "keywords" in cols
    has_languages = "languages" in cols
    has_frameworks = "frameworks" in cols
    has_diag_codes = "diagnostic_codes" in cols
    has_ss_id = "ss_id" in cols
    has_ss_targets = "ss_targets" in cols
    has_mf_targets = "mf_targets" in cols
    has_raw_cb = "raw_circuit_breaker" in cols
    has_cb = "circuit_breaker" in cols

    wound_expr = "s.wound"
    trap_expr = "s.trap"
    cb_expr = "COALESCE(s.circuit_breaker, '')" if has_cb else "''"
    reflex_expr = "s.reflex"
    probe_expr = "s.socratic_probe"
    source_col = "s.source" if has_source else "'upstream' AS source"
    orig_id_col = "s.original_id" if has_orig_id else "NULL AS original_id"
    raw_hash_col = "s.raw_hash" if has_raw_hash else "NULL AS raw_hash"
    keywords_col = "s.keywords" if has_keywords else "'[]' AS keywords"
    languages_col = "s.languages" if has_languages else "'[]' AS languages"
    frameworks_col = "s.frameworks" if has_frameworks else "'[]' AS frameworks"
    diag_codes_col = (
        "s.diagnostic_codes" if has_diag_codes else "'[]' AS diagnostic_codes"
    )
    ss_id_col = "s.ss_id" if has_ss_id else "NULL AS ss_id"
    ss_targets_col = "s.ss_targets" if has_ss_targets else "'[]' AS ss_targets"
    mf_targets_col = "s.mf_targets" if has_mf_targets else "'[]' AS mf_targets"

    select_fields = (
        "s.id, s.title, s.category, s.category_slug, s.visibility,"
        f" s.workflow_phases, s.applies_when, s.graduation_status, {wound_expr},"
        f" {trap_expr}, {cb_expr}, {reflex_expr}, {probe_expr},"
        f" {source_col}, {orig_id_col}, {raw_hash_col},"
        f" {keywords_col}, {languages_col}, {frameworks_col}, {diag_codes_col},"
        f" {ss_id_col}, {ss_targets_col}, {mf_targets_col} "
    )

    def _append_filters(base_sql: str, params_list: List[Any]) -> Optional[str]:
      sql_out = (
          base_sql
          + "AND s.wound IS NOT NULL AND TRIM(s.wound) != '' "
          + "AND s.trap IS NOT NULL AND TRIM(s.trap) != '' "
          + "AND s.reflex IS NOT NULL AND TRIM(s.reflex) != '' "
          + "AND s.socratic_probe IS NOT NULL AND TRIM(s.socratic_probe) != '' "
      )
      if has_raw_cb and has_cb:
        sql_out += (
            "AND (s.raw_circuit_breaker IS NULL "
            "OR TRIM(s.raw_circuit_breaker) = '' "
            "OR (s.circuit_breaker IS NOT NULL AND TRIM(s.circuit_breaker) != '')) "
        )
      if source and source.strip():
        if has_source:
          sql_out += "AND LOWER(s.source) = LOWER(?) "
          params_list.append(source.strip())
        elif source.strip().lower() != "upstream":
          return None

      if category and category.strip():
        cat_tokens = resolve_category_filter_tokens(category.strip())
        if cat_tokens:
          placeholders = ", ".join(["?"] * len(cat_tokens))
          lowered_tokens = [tok.lower() for tok in cat_tokens]
          sql_out += (
              f"AND (LOWER(s.category) IN ({placeholders}) "
              f"OR LOWER(s.category_slug) IN ({placeholders})) "
          )
          params_list.extend(lowered_tokens)
          params_list.extend(lowered_tokens)
        else:
          sql_out += (
              "AND (LOWER(s.category) = LOWER(?) "
              "OR LOWER(s.category_slug) = LOWER(?)) "
          )
          params_list.extend([category.strip(), category.strip()])

      if excluded_tokens:
        ex_placeholders = ", ".join(["?"] * len(excluded_tokens))
        lowered_excluded = [tok.lower() for tok in excluded_tokens]
        sql_out += (
            f"AND LOWER(COALESCE(s.category, '')) NOT IN ({ex_placeholders}) "
            f"AND LOWER(COALESCE(s.category_slug, '')) NOT IN ({ex_placeholders}) "
        )
        params_list.extend(lowered_excluded)
        params_list.extend(lowered_excluded)

      if language and language.strip():
        lang_clean = language.strip().lower()
        lang_substr = f"%{lang_clean}%"
        if has_languages:
          lang_json_tok = f'%"{lang_clean}"%'
          sql_out += (
              "AND (LOWER(COALESCE(s.languages, '')) LIKE ? "
              "OR ((s.languages IS NULL OR s.languages = '' OR s.languages = '[]') "
              "AND (LOWER(s.applies_when) LIKE ? OR LOWER(s.title) LIKE ? "
              f"OR LOWER({reflex_expr}) LIKE ?))) "
          )
          params_list.extend(
              [lang_json_tok, lang_substr, lang_substr, lang_substr]
          )
        else:
          sql_out += (
              "AND (LOWER(s.applies_when) LIKE ? OR LOWER(s.title) LIKE ? "
              f"OR LOWER({reflex_expr}) LIKE ?) "
          )
          params_list.extend([lang_substr, lang_substr, lang_substr])

      if framework and framework.strip():
        fw_clean = framework.strip().lower()
        fw_substr = f"%{fw_clean}%"
        if has_frameworks:
          fw_json_tok = f'%"{fw_clean}"%'
          sql_out += (
              "AND (LOWER(COALESCE(s.frameworks, '')) LIKE ? "
              "OR ((s.frameworks IS NULL OR s.frameworks = '' OR s.frameworks = '[]') "
              "AND (LOWER(s.applies_when) LIKE ? OR LOWER(s.title) LIKE ? "
              f"OR LOWER({wound_expr}) LIKE ? OR LOWER({reflex_expr}) LIKE ?))) "
          )
          params_list.extend(
              [fw_json_tok, fw_substr, fw_substr, fw_substr, fw_substr]
          )
        else:
          sql_out += (
              "AND (LOWER(s.applies_when) LIKE ? OR LOWER(s.title) LIKE ? "
              f"OR LOWER({wound_expr}) LIKE ? OR LOWER({reflex_expr}) LIKE ?) "
          )
          params_list.extend([fw_substr, fw_substr, fw_substr, fw_substr])

      if diagnostic_code and diagnostic_code.strip():
        diag_clean = diagnostic_code.strip().lower()
        diag_substr = f"%{diag_clean}%"
        if has_diag_codes:
          diag_json_tok = f'%"{diag_clean}"%'
          sql_out += (
              "AND (LOWER(COALESCE(s.diagnostic_codes, '')) LIKE ? "
              "OR ((s.diagnostic_codes IS NULL OR s.diagnostic_codes = '' OR s.diagnostic_codes = '[]') "
              f"AND (LOWER(s.title) LIKE ? OR LOWER({wound_expr}) LIKE ? "
              f"OR LOWER({trap_expr}) LIKE ? OR LOWER({reflex_expr}) LIKE ?))) "
          )
          params_list.extend(
              [diag_json_tok, diag_substr, diag_substr, diag_substr, diag_substr]
          )
        else:
          sql_out += (
              f"AND (LOWER(s.title) LIKE ? OR LOWER({wound_expr}) LIKE ? "
              f"OR LOWER({trap_expr}) LIKE ? OR LOWER({reflex_expr}) LIKE ?) "
          )
          params_list.extend(
              [diag_substr, diag_substr, diag_substr, diag_substr]
          )

      if workflow_phase and workflow_phase.strip():
        wp_clean = workflow_phase.strip().lower()
        wp_substr = f"%{wp_clean}%"
        norm_wp = _normalize_workflow_phases(
            [workflow_phase.strip()],
            default_fallback=False,
        )
        if norm_wp:
          norm_substr = f"%{norm_wp[0].lower()}%"
          sql_out += (
              "AND (LOWER(COALESCE(s.workflow_phases, '')) LIKE ? "
              "OR LOWER(COALESCE(s.workflow_phases, '')) LIKE ?) "
          )
          params_list.extend([wp_substr, norm_substr])
        else:
          sql_out += "AND LOWER(COALESCE(s.workflow_phases, '')) LIKE ? "
          params_list.append(wp_substr)

      if mf_target is not None:
        target_int = int(mf_target)
        if has_mf_targets:
          sql_out += (
              "AND (',' || REPLACE(REPLACE(REPLACE(COALESCE(s.mf_targets, ''),"
              " ' ', ''), '[', ''), ']', '') || ',') LIKE ? "
          )
          params_list.append(f"%,{target_int},%")
        else:
          phase_label = TARGET_INT_TO_PHASE_LABEL.get(target_int)
          if phase_label:
            sql_out += (
                "AND (LOWER(COALESCE(s.workflow_phases, '')) LIKE ? "
                "OR LOWER(COALESCE(s.workflow_phases, '')) LIKE ?) "
            )
            params_list.extend(
                [f"%{phase_label.lower()}%", f"%t{target_int}%"]
            )
          else:
            sql_out += "AND LOWER(COALESCE(s.workflow_phases, '')) LIKE ? "
            params_list.append(f"%{target_int}%")

      return sql_out

    has_fts = False
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='scars_fts'"
    )
    if cur.fetchone():
      has_fts = True

    results: List[Dict[str, Any]] = []
    query_str = query or ""

    if has_fts and query_str.strip():
      sanitized = _sanitize_fts_query(query_str)
      if sanitized:
        sql = (
            f"SELECT {select_fields}"
            "FROM scars s "
            "JOIN scars_fts fts ON s.id = fts.id "
            "WHERE scars_fts MATCH ? "
        )
        params: List[Any] = [sanitized]
        filtered_sql = _append_filters(sql, params)
        if filtered_sql is None:
          return []
        filtered_sql += "ORDER BY rank LIMIT ?"
        params.append(max(1, limit))

        try:
          cur.execute(filtered_sql, params)
          rows = cur.fetchall()
          for r in rows:
            results.append(_format_scar_row(r))
          if results:
            return results
        except sqlite3.OperationalError:
          pass

    query_tokens = [
        tok.strip().lower() for tok in query_str.split() if tok.strip()
    ]
    has_any_filter = (
        mf_target is not None
        or bool(excluded_tokens)
        or any(
            bool(f and str(f).strip())
            for f in (
                category,
                language,
                source,
                framework,
                diagnostic_code,
                workflow_phase,
            )
        )
    )
    if not query_tokens and not has_any_filter:
      return []

    params = []
    if query_tokens:
      sql = f"SELECT {select_fields}FROM scars s WHERE ("
      where_clauses = []
      for tok in query_tokens:
        pattern = f"%{tok}%"
        clause = (
            f"LOWER(s.title) LIKE ? OR LOWER({wound_expr}) LIKE ? OR LOWER({trap_expr}) LIKE ?"
            f" OR LOWER({reflex_expr}) LIKE ? OR LOWER({probe_expr}) LIKE ?"
            " OR LOWER(s.applies_when) LIKE ? OR LOWER(s.id) LIKE ?"
        )
        tok_params = [
            pattern,
            pattern,
            pattern,
            pattern,
            pattern,
            pattern,
            pattern,
        ]
        if has_ss_id:
          clause += " OR LOWER(COALESCE(s.ss_id, '')) LIKE ?"
          tok_params.append(pattern)
        if has_orig_id:
          clause += " OR LOWER(COALESCE(s.original_id, '')) LIKE ?"
          tok_params.append(pattern)
        if has_keywords:
          clause += " OR LOWER(COALESCE(s.keywords, '')) LIKE ?"
          tok_params.append(pattern)
        if has_languages:
          clause += " OR LOWER(COALESCE(s.languages, '')) LIKE ?"
          tok_params.append(pattern)
        if has_frameworks:
          clause += " OR LOWER(COALESCE(s.frameworks, '')) LIKE ?"
          tok_params.append(pattern)
        if has_diag_codes:
          clause += " OR LOWER(COALESCE(s.diagnostic_codes, '')) LIKE ?"
          tok_params.append(pattern)
        where_clauses.append(f"({clause})")
        params.extend(tok_params)
      sql += " AND ".join(where_clauses) + ") "
    else:
      sql = f"SELECT {select_fields}FROM scars s WHERE 1=1 "

    filtered_sql = _append_filters(sql, params)
    if filtered_sql is None:
      return []

    filtered_sql += "ORDER BY s.id ASC LIMIT ?"
    params.append(max(1, limit))

    cur.execute(filtered_sql, params)
    rows = cur.fetchall()
    for r in rows:
      results.append(_format_scar_row(r))

    return results
  except (sqlite3.Error, OSError, ValueError):
    return []


def _query_single_scars_db(
    db_path: str,
    query: str = "",
    category: Optional[str] = None,
    language: Optional[str] = None,
    limit: int = 3,
    source: Optional[str] = None,
    framework: Optional[str] = None,
    diagnostic_code: Optional[str] = None,
    workflow_phase: Optional[str] = None,
    mf_target: Optional[int] = None,
    exclude_categories: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
  """Query a single file-backed SQLite scars database for complete processed scars only."""
  if not db_path or not os.path.isfile(db_path):
    return []

  conn = None
  try:
    conn = sqlite3.connect(db_path)
    return _query_scars_connection(
        conn=conn,
        query=query,
        category=category,
        language=language,
        limit=limit,
        source=source,
        framework=framework,
        diagnostic_code=diagnostic_code,
        workflow_phase=workflow_phase,
        mf_target=mf_target,
        exclude_categories=exclude_categories,
    )
  except (sqlite3.Error, OSError, ValueError):
    return []
  finally:
    if conn:
      conn.close()


def _infer_markdown_dir_tier(md_dir: str) -> str:
  """Infer source tier label (``local``, ``user``, or ``custom``) for a Markdown scar directory."""
  norm = os.path.normpath(os.path.abspath(os.path.expanduser(md_dir)))
  parts = PurePath(norm).parts
  if len(parts) >= 2 and parts[-2:] == (".workflow", "scars"):
    return "local"
  if len(parts) >= 3 and parts[-3:] == ("plugin_data", "makeflow", "scars"):
    return "user"
  return "custom"


def _query_markdown_scars_dir(
    md_dir: str,
    query: str = "",
    category: Optional[str] = None,
    language: Optional[str] = None,
    limit: int = 3,
    source: Optional[str] = None,
    framework: Optional[str] = None,
    diagnostic_code: Optional[str] = None,
    workflow_phase: Optional[str] = None,
    mf_target: Optional[int] = None,
    exclude_categories: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
  """Parse a Markdown scar directory into an ephemeral in-memory FTS5 SQLite DB and query it."""
  if not md_dir or not os.path.isdir(md_dir):
    return []
  # Scoped import avoids circular dependency (scar_miner -> scar_candidate -> scars_db).
  from lib.scar_miner import MarkdownScarStore

  source_tier = _infer_markdown_dir_tier(md_dir)
  parsed_rows = MarkdownScarStore().parse_directory(
      md_dir, source_tier=source_tier
  )
  if not parsed_rows:
    return []

  conn = None
  try:
    conn = sqlite3.connect(":memory:")
    has_fts5 = init_scars_db_schema(conn)
    cur = conn.cursor()
    for row in parsed_rows:
      cur.execute(
          """
          INSERT OR REPLACE INTO scars (
              id, title, category, category_slug, visibility, applies_when,
              graduation_status, raw_wound, raw_trap, raw_circuit_breaker,
              raw_reflex, wound, trap, circuit_breaker, reflex,
              socratic_probe, source
          ) VALUES (?, ?, ?, ?, 'local', ?, 'local', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
          """,
          (
              row["id"],
              row["title"],
              row["category"],
              row["category_slug"],
              row.get("applies_when", ""),
              row["wound"],
              row["trap"],
              row.get("circuit_breaker", ""),
              row["reflex"],
              row["wound"],
              row["trap"],
              row.get("circuit_breaker", ""),
              row["reflex"],
              row["socratic_probe"],
              row["source"],
          ),
      )
    enrich_scars_cursor(cur)
    if has_fts5:
      populate_scars_fts(cur)
    conn.commit()
    return _query_scars_connection(
        conn=conn,
        query=query,
        category=category,
        language=language,
        limit=limit,
        source=source,
        framework=framework,
        diagnostic_code=diagnostic_code,
        workflow_phase=workflow_phase,
        mf_target=mf_target,
        exclude_categories=exclude_categories,
    )
  except (sqlite3.Error, OSError, ValueError):
    return []
  finally:
    if conn:
      conn.close()


def count_markdown_dir_scars(markdown_dirs: Sequence[str]) -> int:
  """Count total valid parsed scars across a sequence of Markdown scar directories."""
  if not markdown_dirs:
    return 0
  from lib.scar_miner import MarkdownScarStore

  store = MarkdownScarStore()
  total = 0
  for md_dir in markdown_dirs:
    if md_dir and os.path.isdir(md_dir):
      total += len(store.parse_directory(md_dir))
  return total


def query_scars(
    db_path: Union[str, Sequence[str]],
    query: str = "",
    category: Optional[str] = None,
    language: Optional[str] = None,
    limit: int = 3,
    source: Optional[str] = None,
    framework: Optional[str] = None,
    diagnostic_code: Optional[str] = None,
    workflow_phase: Optional[str] = None,
    mf_target: Optional[int] = None,
    exclude_categories: Optional[Iterable[str]] = None,
    markdown_dirs: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
  """Query stacked Markdown scar directories and SQLite scars databases in precedence order.

  Returns only complete processed scars (non-empty ``wound``, ``trap``,
  ``reflex``, ``socratic_probe``, plus non-empty ``circuit_breaker`` whenever
  ``raw_circuit_breaker`` is non-empty) and deduplicates across tiers by
  ``id``, ``ss_id``, and ``original_id``.

  Args:
    db_path: Single database file path or sequence of stacked paths.
    query: Optional keyword or scar ID search string.
    category: Optional category code or prose alias filter.
    language: Optional normalized language tag filter.
    limit: Maximum number of matching scars to return.
    source: Optional source filter (``makeflow``, ``upstream``, ``local``,
      ``user``, or ``custom``).
    framework: Optional framework or library filter.
    diagnostic_code: Optional compiler or runtime diagnostic code filter.
    workflow_phase: Optional workflow phase label filter.
    mf_target: Optional Makeflow integer target filter.
    exclude_categories: Optional iterable of category codes, prose aliases, or
      ``WORKFLOW`` to exclude from results.
    markdown_dirs: Optional ordered sequence of Markdown scar directories to
      index ephemerally via in-memory FTS5 ahead of SQLite databases.

  Returns:
    List of structured scar dictionaries up to ``limit``.
  """
  if not db_path and not markdown_dirs:
    return []
  if not db_path:
    paths: List[str] = []
  elif isinstance(db_path, str):
    paths = [db_path]
  else:
    paths = [str(p) for p in db_path if p]

  md_dir_list = [str(d) for d in (markdown_dirs or ()) if d]

  resolved_excluded = resolve_excluded_category_tokens(exclude_categories)
  eff_limit = max(1, limit)
  combined: List[Dict[str, Any]] = []
  seen_makeflow_keys: Set[str] = set()
  seen_upstream_keys: Set[str] = set()
  seen_legacy_upstream_ids: Set[str] = set()

  def _merge_batch(batch_rows: List[Dict[str, Any]]) -> bool:
    current_makeflow_ids: Set[str] = set()
    current_upstream_ids: Set[str] = set()
    current_legacy_upstream_ids: Set[str] = set()
    for row in batch_rows:
      scar_id = str(row.get("id") or "")
      ss_id = str(row.get("ssId") or row.get("ss_id") or "")
      orig_id = str(row.get("originalId") or row.get("original_id") or "")
      row_source = str(row.get("source") or "upstream").strip().lower()
      if row_source == "makeflow":
        candidate_keys = [k for k in (scar_id, orig_id) if k]
        blocked_keys = seen_makeflow_keys | seen_legacy_upstream_ids
        if any(k in blocked_keys for k in candidate_keys):
          continue
        current_makeflow_ids.update(candidate_keys)
      else:
        candidate_keys = [k for k in (scar_id, ss_id, orig_id) if k]
        if any(k in seen_upstream_keys for k in candidate_keys):
          continue
        current_upstream_ids.update(candidate_keys)
        if not ss_id and scar_id:
          current_legacy_upstream_ids.add(scar_id)
      combined.append(row)
      if len(combined) >= eff_limit:
        return True
    seen_makeflow_keys.update(current_makeflow_ids)
    seen_upstream_keys.update(current_upstream_ids)
    seen_legacy_upstream_ids.update(current_legacy_upstream_ids)
    return False

  for md_dir in md_dir_list:
    if not os.path.isdir(md_dir):
      continue
    prior_seen_count = len(seen_makeflow_keys) + len(seen_upstream_keys)
    md_matches = _query_markdown_scars_dir(
        md_dir=md_dir,
        query=query,
        category=category,
        language=language,
        limit=eff_limit + prior_seen_count + 5,
        source=source,
        framework=framework,
        diagnostic_code=diagnostic_code,
        workflow_phase=workflow_phase,
        mf_target=mf_target,
        exclude_categories=resolved_excluded,
    )
    if _merge_batch(md_matches):
      return combined

  for single_path in paths:
    if not os.path.isfile(single_path):
      continue
    prior_seen_count = len(seen_makeflow_keys) + len(seen_upstream_keys)
    db_matches = _query_single_scars_db(
        db_path=single_path,
        query=query,
        category=category,
        language=language,
        limit=eff_limit + prior_seen_count + 5,
        source=source,
        framework=framework,
        diagnostic_code=diagnostic_code,
        workflow_phase=workflow_phase,
        mf_target=mf_target,
        exclude_categories=resolved_excluded,
    )
    if _merge_batch(db_matches):
      return combined

  return combined


def extract_protected_spans(text: str) -> Dict[str, List[str]]:
  """Extract fenced code blocks, display LaTeX, and inline LaTeX from text.

  Ignores dollar signs inside fenced code blocks and inline backtick code spans.

  Args:
    text: Input markdown or prose string.

  Returns:
    Dictionary mapping ``fenced_code``, ``display_math``, and ``inline_math``
    to ordered lists of extracted raw spans.
  """
  content = text or ""
  fenced_code: List[str] = []
  display_math: List[str] = []
  inline_math: List[str] = []

  def _mask_fenced(match: re.Match[str]) -> str:
    fenced_code.append(match.group(0))
    return f"\x00FENCED_{len(fenced_code) - 1}\x00"

  masked = re.sub(r"```[\s\S]*?```", _mask_fenced, content)
  masked = re.sub(r"`[^`\n]+`", "\x00INLINE_CODE\x00", masked)

  def _mask_display(match: re.Match[str]) -> str:
    display_math.append(match.group(0))
    return f"\x00DISPLAY_{len(display_math) - 1}\x00"

  masked = re.sub(r"\$\$[\s\S]+?\$\$", _mask_display, masked)

  def _mask_inline(match: re.Match[str]) -> str:
    inline_math.append(match.group(0))
    return f"\x00INLINE_{len(inline_math) - 1}\x00"

  re.sub(r"(?<!\$)\$(?!\$)([^\$\n]+?)(?<!\$)\$(?!\$)", _mask_inline, masked)

  return {
      "fenced_code": fenced_code,
      "display_math": display_math,
      "inline_math": inline_math,
  }


def _strip_protected_and_inline_code(text: str) -> str:
  """Mask fenced code, inline code, display math, and inline math for prose checks."""
  masked = re.sub(r"```[\s\S]*?```", " ", text or "")
  masked = re.sub(r"`[^`\n]+`", " ", masked)
  masked = re.sub(r"\$\$[\s\S]+?\$\$", " ", masked)
  masked = re.sub(r"(?<!\$)\$(?!\$)([^\$\n]+?)(?<!\$)\$(?!\$)", " ", masked)
  return masked


def verify_scar_transformation(
    raw_row: Dict[str, Any], transformed_row: Dict[str, Any]
) -> List[str]:
  """Verify that a Simple English scar transformation preserves spans and prose rules.

  Args:
    raw_row: Dictionary containing ``raw_wound``, ``raw_trap``,
      ``raw_circuit_breaker``, and ``raw_reflex``.
    transformed_row: Dictionary containing transformed scar prose fields.

  Returns:
    List of error strings (empty when valid).
  """
  errors: List[str] = []

  for req_field in ("wound", "trap", "reflex"):
    val = transformed_row.get(req_field)
    if not isinstance(val, str) or not val.strip():
      errors.append(f"Field '{req_field}' must be a non-empty string.")

  raw_cb = str(raw_row.get("raw_circuit_breaker") or "")
  trans_cb = transformed_row.get("circuit_breaker")
  if raw_cb.strip():
    if not isinstance(trans_cb, str) or not trans_cb.strip():
      errors.append(
          "Field 'circuit_breaker' must be non-empty when 'raw_circuit_breaker' is non-empty."
      )
  elif trans_cb is None:
    errors.append(
        "Field 'circuit_breaker' must be '' (not None) when 'raw_circuit_breaker' is empty."
    )

  field_pairs = (
      ("raw_wound", "wound"),
      ("raw_trap", "trap"),
      ("raw_circuit_breaker", "circuit_breaker"),
      ("raw_reflex", "reflex"),
  )
  for raw_key, trans_key in field_pairs:
    raw_text = str(raw_row.get(raw_key) or "")
    trans_text = str(transformed_row.get(trans_key) or "")
    raw_spans = extract_protected_spans(raw_text)
    trans_spans = extract_protected_spans(trans_text)
    for span_kind in ("fenced_code", "display_math", "inline_math"):
      if raw_spans[span_kind] != trans_spans[span_kind]:
        errors.append(
            f"Field '{trans_key}' {span_kind} mismatch: expected {raw_spans[span_kind]}, got {trans_spans[span_kind]}."
        )

  for prose_field in (
      "wound",
      "trap",
      "circuit_breaker",
      "reflex",
      "socratic_probe",
  ):
    val_str = str(transformed_row.get(prose_field) or "")
    prose_only = _strip_protected_and_inline_code(val_str)
    if ";" in prose_only:
      errors.append(
          f"Field '{prose_field}' contains forbidden semicolon ';' in prose."
      )
    if "\u2014" in prose_only:
      errors.append(
          f"Field '{prose_field}' contains forbidden em-dash '\u2014' in prose."
      )

  probe_str = str(transformed_row.get("socratic_probe") or "").strip()
  if probe_str and not probe_str.endswith("?"):
    errors.append("Field 'socratic_probe' must end with '?'.")

  return errors
