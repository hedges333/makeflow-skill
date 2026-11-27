"""SQLite schema, FTS5 query, enrichment, and session ledger for synthetic scars (GH-118, GH-209).

Provides the core data-access spine for bundled and stacked SQLite synthetic
scars registries: schema initialization, FTS5 indexing, lossless 4-field
raw-hash backfilling, deterministic metadata enrichment, multi-dimensional
queries, protected-span extraction, and per-ticket session scar tracking.
"""

from dataclasses import replace
import hashlib
import json
import os
import re
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

from lib.agent_paths import PathContext, resolve_path_context
from lib.config import expand_user_path
from lib.scar_locations import resolve_scar_locations
from lib.scar_metadata import (
    _normalize_workflow_phases,
    extract_scar_metadata,
    parse_json_list,
)
from lib.scar_ontology import (
    TARGET_INT_TO_PHASE_LABEL,
    normalize_domain_category,
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
    home_dir: Optional[str] = None,
    *,
    context: Optional[PathContext] = None,
) -> str:
  """Resolve the primary SQLite scars database path by priority cascade.

  Args:
    db_path: Optional explicit filesystem path override.
    agent: Optional agent flavor name (gemini, claude, cursor, copilot, codex, chatgpt).
    workspace_dir: Optional workspace directory to check for a project-local DB.
    package_root: Optional plugin root directory override.
    home_dir: Optional home directory override for isolated testing.
    context: Optional bundled ``PathContext`` overriding legacy path kwargs.

  Returns:
    Normalized absolute path to the selected SQLite database file.

  Raises:
    TypeError: If ``context`` is not ``None`` and not a ``PathContext``.
    ValueError: If ``context`` is mixed with any legacy path keyword argument
      (including ``package_root``).
  """
  ctx = resolve_path_context(
      context,
      workspace_dir=workspace_dir,
      agent=agent,
      home_dir=home_dir,
      package_root=package_root,
  )
  if db_path:
    return os.path.abspath(expand_user_path(db_path, home_dir=ctx.home_dir))

  env_path = os.getenv("MAKEFLOW_SCARS_DB")
  if env_path:
    return os.path.abspath(expand_user_path(env_path, home_dir=ctx.home_dir))

  effective_ctx = replace(ctx, plugin_root=ctx.plugin_root or _REPO_ROOT)
  try:
    locations = resolve_scar_locations(context=effective_ctx)
    resolved_agent_db = locations.user_db if (ctx.agent or "").strip() else None
  except ValueError:
    locations = resolve_scar_locations(context=replace(effective_ctx, agent=None))
    resolved_agent_db = None

  if os.path.exists(locations.bundled_db):
    return locations.bundled_db

  if locations.project_db and os.path.exists(locations.project_db):
    return locations.project_db

  if resolved_agent_db:
    return resolved_agent_db

  home = (
      os.path.abspath(os.path.expanduser(ctx.home_dir))
      if ctx.home_dir
      else os.path.expanduser("~")
  )
  xdg_cache = os.getenv("XDG_CACHE_HOME")
  if xdg_cache:
    xdg_db = os.path.join(xdg_cache, "makeflow", "scars_registry.db")
    if os.path.exists(xdg_db):
      return os.path.abspath(xdg_db)

  user_default = os.path.join(home, ".cache", "makeflow", "scars_registry.db")
  if os.path.exists(user_default):
    return user_default

  return locations.bundled_db


def resolve_scars_db_paths(
    db_path: Optional[str] = None,
    agent: Optional[str] = None,
    workspace_dir: Optional[str] = None,
    package_root: Optional[str] = None,
    home_dir: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
    *,
    context: Optional[PathContext] = None,
) -> List[str]:
  """Resolve existing SQLite scars databases in configured precedence order.

  When ``db_path`` is explicitly supplied, isolates resolution to that single
  file without cascading into workspace, user, custom, or bundled tiers.
  Otherwise delegates to ``resolve_scar_locations()`` to return existing
  ordered SQLite scar databases without creating empty files on disk.

  Args:
    db_path: Optional explicit single database file path.
    agent: Optional active agent name override.
    workspace_dir: Optional project root directory path.
    package_root: Optional Makeflow plugin root directory override.
    home_dir: Optional home directory override for isolated testing.
    config: Optional pre-resolved workflow configuration dictionary.
    context: Optional bundled ``PathContext`` overriding legacy path kwargs.

  Returns:
    Deduplicated list of existing absolute SQLite database paths in exact
    precedence order (highest first).

  Raises:
    TypeError: If ``context`` is not ``None`` and not a ``PathContext``.
    ValueError: If ``context`` is mixed with any legacy path keyword argument
      (including ``package_root``).
  """
  ctx = resolve_path_context(
      context,
      workspace_dir=workspace_dir,
      agent=agent,
      home_dir=home_dir,
      package_root=package_root,
  )
  if db_path:
    norm_explicit = os.path.abspath(
        expand_user_path(db_path, home_dir=ctx.home_dir)
    )
    return [norm_explicit] if os.path.isfile(norm_explicit) else []

  effective_ctx = replace(ctx, plugin_root=ctx.plugin_root or _REPO_ROOT)
  locations = resolve_scar_locations(config=config, context=effective_ctx)
  return list(locations.existing_databases)


_TIER_SCOPE_PREFIXES: Dict[str, str] = {
    "project": "LOCAL",
    "local": "LOCAL",
    "user": "USER",
    "custom": "CUSTOM",
}


def next_tier_scar_id(
    db_path: str,
    category: str,
    tier: str = "project",
) -> str:
  """Allocate the next unused tier-scoped 4-digit scar ID in ``db_path``.

  Reads existing ``scars.id`` values when ``db_path`` exists without creating
  an empty database file on disk.

  Args:
    db_path: Filesystem path to the target SQLite scars database.
    category: Canonical domain category code or prose alias.
    tier: Storage tier name (``project``, ``local``, ``user``, or ``custom``).

  Returns:
    Unique scar identifier such as ``SCAR-LOCAL-DATA-0001``.

  Raises:
    ValueError: If ``tier`` is unrecognized.
  """
  cleaned_tier = str(tier or "project").strip().lower()
  if cleaned_tier not in _TIER_SCOPE_PREFIXES:
    valid = ", ".join(sorted(_TIER_SCOPE_PREFIXES.keys()))
    raise ValueError(
        f"Unsupported scar tier '{tier}'. Expected one of: {valid}."
    )
  scope_prefix = _TIER_SCOPE_PREFIXES[cleaned_tier]
  domain = normalize_domain_category(category)
  prefix = f"SCAR-{scope_prefix}-{domain}"

  existing: Set[str] = set()
  norm_db_path = (
      os.path.abspath(expand_user_path(db_path)) if db_path and db_path.strip() else ""
  )
  if norm_db_path and os.path.isfile(norm_db_path):
    conn = None
    try:
      conn = sqlite3.connect(norm_db_path)
      cur = conn.cursor()
      cur.execute(
          "SELECT name FROM sqlite_master WHERE type='table' AND name='scars'"
      )
      if cur.fetchone():
        cur.execute("SELECT id FROM scars WHERE id IS NOT NULL")
        existing = {
            str(r[0]).strip().upper()
            for r in cur.fetchall()
            if r and str(r[0]).strip()
        }
    except sqlite3.Error:
      existing = set()
    finally:
      if conn:
        conn.close()

  seq = 1
  while True:
    candidate = f"{prefix}-{seq:04d}"
    if candidate.upper() not in existing:
      return candidate
    seq += 1


def _serialize_json_list_field(raw_value: Any, default_none: bool = False) -> Optional[str]:
  """Serialize a list or JSON string field for SQLite storage."""
  if raw_value is None:
    return None if default_none else "[]"
  if isinstance(raw_value, str):
    stripped = raw_value.strip()
    if not stripped:
      return None if default_none else "[]"
    try:
      parsed = json.loads(stripped)
      if isinstance(parsed, list):
        return json.dumps(parsed)
    except (json.JSONDecodeError, ValueError, TypeError):
      pass
    return json.dumps([stripped])
  if isinstance(raw_value, (list, tuple)):
    return json.dumps(list(raw_value))
  return None if default_none else "[]"


def upsert_scar(db_path: str, record: Dict[str, Any]) -> Dict[str, Any]:
  """Initialize schema, upsert one scar row, enrich metadata, and rebuild FTS5.

  Acts as the single schema writer for all SQLite scar tiers so stored rows,
  ``raw_hash`` digests, metadata columns, and ``scars_fts`` stay synchronized.

  Args:
    db_path: Filesystem path to the target SQLite scars database.
    record: Dictionary containing ``id``, ``title``, ``category``, ``wound``,
      ``trap``, ``reflex``, ``socratic_probe``, and optional metadata fields.

  Returns:
    Dictionary with ``id``, ``db_path``, ``category``, ``source``,
    ``raw_hash``, and ``updated`` (True when an existing row was overwritten).

  Raises:
    ValueError: If ``db_path``, ``id``, or ``title`` is invalid or ``db_path``
      points to an existing directory.
  """
  if not isinstance(db_path, str) or not db_path.strip() or "\x00" in db_path:
    raise ValueError("db_path must be a non-empty filesystem path.")
  abs_db_path = os.path.abspath(os.path.expanduser(db_path.strip()))
  if os.path.isdir(abs_db_path):
    raise ValueError(f"Target scar database path '{abs_db_path}' is a directory.")

  scar_id = str(record.get("id") or "").strip()
  if not scar_id:
    raise ValueError("Scar record must include a non-empty 'id'.")
  title = str(record.get("title") or "").strip()
  if not title:
    raise ValueError("Scar record must include a non-empty 'title'.")

  wound = str(record.get("wound") or "").strip()
  trap = str(record.get("trap") or "").strip()
  circuit_breaker = str(record.get("circuit_breaker") or "").strip()
  reflex = str(record.get("reflex") or "").strip()
  socratic_probe = str(record.get("socratic_probe") or "").strip()
  applies_when = str(record.get("applies_when") or "").strip()

  domain = normalize_domain_category(
      str(record.get("category") or ""),
      scar_id=scar_id,
      title=title,
      wound=wound,
      trap=trap,
      reflex=reflex,
  )
  category_slug = str(record.get("category_slug") or domain.lower()).strip().lower()
  source = str(record.get("source") or "local").strip().lower()
  visibility = str(record.get("visibility") or source).strip()
  graduation_status = str(record.get("graduation_status") or source).strip()

  raw_ss_id = record.get("ss_id") if "ss_id" in record else record.get("ssId")
  ss_id = str(raw_ss_id).strip() if raw_ss_id is not None and str(raw_ss_id).strip() else None
  raw_orig = (
      record.get("original_id")
      if "original_id" in record
      else record.get("originalId")
  )
  original_id = str(raw_orig).strip() if raw_orig is not None and str(raw_orig).strip() else None

  workflow_phases_json = _serialize_json_list_field(
      record.get("workflow_phases"), default_none=False
  )
  ss_targets_json = _serialize_json_list_field(
      record.get("ss_targets"), default_none=True
  )

  raw_wound = str(record.get("raw_wound") if record.get("raw_wound") is not None else wound)
  raw_trap = str(record.get("raw_trap") if record.get("raw_trap") is not None else trap)
  raw_cb = str(
      record.get("raw_circuit_breaker")
      if record.get("raw_circuit_breaker") is not None
      else circuit_breaker
  )
  raw_reflex = str(record.get("raw_reflex") if record.get("raw_reflex") is not None else reflex)
  raw_hash = compute_raw_fields_hash(raw_wound, raw_trap, raw_cb, raw_reflex)

  parent_dir = os.path.dirname(abs_db_path)
  if parent_dir:
    os.makedirs(parent_dir, exist_ok=True)

  conn = sqlite3.connect(abs_db_path)
  try:
    has_fts5 = init_scars_db_schema(conn)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM scars WHERE id = ?", (scar_id,))
    existed = cur.fetchone() is not None

    cur.execute(
        """
        INSERT INTO scars (
            id, ss_id, title, category, category_slug, visibility,
            workflow_phases, ss_targets, applies_when, graduation_status,
            raw_wound, raw_trap, raw_circuit_breaker, raw_reflex, raw_hash,
            wound, trap, circuit_breaker, reflex, socratic_probe,
            source, original_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            ss_id = excluded.ss_id,
            title = excluded.title,
            category = excluded.category,
            category_slug = excluded.category_slug,
            visibility = excluded.visibility,
            workflow_phases = excluded.workflow_phases,
            ss_targets = excluded.ss_targets,
            applies_when = excluded.applies_when,
            graduation_status = excluded.graduation_status,
            raw_wound = excluded.raw_wound,
            raw_trap = excluded.raw_trap,
            raw_circuit_breaker = excluded.raw_circuit_breaker,
            raw_reflex = excluded.raw_reflex,
            raw_hash = excluded.raw_hash,
            wound = excluded.wound,
            trap = excluded.trap,
            circuit_breaker = excluded.circuit_breaker,
            reflex = excluded.reflex,
            socratic_probe = excluded.socratic_probe,
            source = excluded.source,
            original_id = excluded.original_id
        """,
        (
            scar_id,
            ss_id,
            title,
            domain,
            category_slug,
            visibility,
            workflow_phases_json,
            ss_targets_json,
            applies_when,
            graduation_status,
            raw_wound,
            raw_trap,
            raw_cb,
            raw_reflex,
            raw_hash,
            wound,
            trap,
            circuit_breaker,
            reflex,
            socratic_probe,
            source,
            original_id,
        ),
    )
    enrich_scars_cursor(cur, scar_ids=[scar_id])
    if has_fts5:
      populate_scars_fts(cur)
    cur.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('raw_hash_version', ?)",
        (RAW_HASH_VERSION,),
    )
    conn.commit()
    update_db_size_meta(conn, abs_db_path)
    return {
        "id": scar_id,
        "db_path": abs_db_path,
        "category": domain,
        "source": source,
        "raw_hash": raw_hash,
        "updated": existed,
    }
  finally:
    conn.close()


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


def _is_row_shadowed(
    row: Dict[str, Any],
    shadowed_primary_ids: Set[str],
    shadowed_ss_ids: Set[str],
    shadowed_original_ids: Set[str],
) -> bool:
  """Return True when a lower-precedence row is shadowed by higher-precedence tier IDs.

  Unifies primary ``id`` shadowing across all sources while keeping upstream
  ``ss_id`` and Makeflow legacy ``original_id`` namespaces strictly distinct
  (higher ``original_id`` never suppresses lower ``ss_id`` and vice versa).
  """
  row_id = str(row.get("id") or "").strip().upper()
  raw_ss = row.get("ssId") if "ssId" in row else row.get("ss_id")
  row_ss_id = str(raw_ss or "").strip().upper()
  raw_orig = row.get("originalId") if "originalId" in row else row.get("original_id")
  row_orig_id = str(raw_orig or "").strip().upper()

  if row_id and (
      row_id in shadowed_primary_ids
      or row_id in shadowed_ss_ids
      or row_id in shadowed_original_ids
  ):
    return True
  if row_ss_id and (
      row_ss_id in shadowed_primary_ids or row_ss_id in shadowed_ss_ids
  ):
    return True
  if row_orig_id and (
      row_orig_id in shadowed_primary_ids or row_orig_id in shadowed_original_ids
  ):
    return True
  return False


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
    shadowed_sets: Optional[Tuple[Set[str], Set[str], Set[str]]] = None,
) -> List[Dict[str, Any]]:
  """Execute multi-dimensional FTS5/LIKE scar query on an open SQLite connection."""
  excluded_tokens = resolve_excluded_category_tokens(exclude_categories)
  eff_limit = max(1, limit)
  if shadowed_sets is not None:
    shadowed_primary, shadowed_ss, shadowed_orig = shadowed_sets
  else:
    shadowed_primary, shadowed_ss, shadowed_orig = set(), set(), set()
  has_shadow = bool(shadowed_primary or shadowed_ss or shadowed_orig)
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
        if has_shadow:
          filtered_sql += "ORDER BY rank"
        else:
          filtered_sql += "ORDER BY rank LIMIT ?"
          params.append(eff_limit)

        try:
          cur.execute(filtered_sql, params)
          for r in cur:
            formatted = _format_scar_row(r)
            if has_shadow and _is_row_shadowed(
                formatted, shadowed_primary, shadowed_ss, shadowed_orig
            ):
              continue
            results.append(formatted)
            if len(results) >= eff_limit:
              break
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

    if has_shadow:
      filtered_sql += "ORDER BY s.id ASC"
    else:
      filtered_sql += "ORDER BY s.id ASC LIMIT ?"
      params.append(eff_limit)

    cur.execute(filtered_sql, params)
    for r in cur:
      formatted = _format_scar_row(r)
      if has_shadow and _is_row_shadowed(
          formatted, shadowed_primary, shadowed_ss, shadowed_orig
      ):
        continue
      results.append(formatted)
      if len(results) >= eff_limit:
        break

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
    shadowed_sets: Optional[Tuple[Set[str], Set[str], Set[str]]] = None,
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
        shadowed_sets=shadowed_sets,
    )
  except (sqlite3.Error, OSError, ValueError):
    return []
  finally:
    if conn:
      conn.close()


def _collect_db_scar_keys(
    db_path: str,
) -> Tuple[Set[str], Set[str], Set[str]]:
  """Collect normalized uppercase ``(primary_ids, ss_ids, original_ids)`` from one DB."""
  if not db_path or not os.path.isfile(db_path):
    return set(), set(), set()
  conn = None
  try:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='scars'"
    )
    if not cur.fetchone():
      return set(), set(), set()
    cur.execute("PRAGMA table_info(scars)")
    cols = {r[1] for r in cur.fetchall()}
    if "id" not in cols:
      return set(), set(), set()
    has_ss = "ss_id" in cols
    has_orig = "original_id" in cols
    select_cols = ["id"]
    if has_ss:
      select_cols.append("ss_id")
    if has_orig:
      select_cols.append("original_id")
    cur.execute(f"SELECT {', '.join(select_cols)} FROM scars")
    primary_ids: Set[str] = set()
    ss_ids: Set[str] = set()
    original_ids: Set[str] = set()
    for row in cur.fetchall():
      if row[0] is not None and str(row[0]).strip():
        primary_ids.add(str(row[0]).strip().upper())
      col_idx = 1
      if has_ss:
        if row[col_idx] is not None and str(row[col_idx]).strip():
          ss_ids.add(str(row[col_idx]).strip().upper())
        col_idx += 1
      if has_orig:
        if row[col_idx] is not None and str(row[col_idx]).strip():
          original_ids.add(str(row[col_idx]).strip().upper())
    return primary_ids, ss_ids, original_ids
  except (sqlite3.Error, OSError, ValueError):
    return set(), set(), set()
  finally:
    if conn:
      conn.close()


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
) -> List[Dict[str, Any]]:
  """Query ordered SQLite scars databases in precedence order with namespace-safe ID shadowing.

  Returns only complete processed scars (non-empty ``wound``, ``trap``,
  ``reflex``, ``socratic_probe``, plus non-empty ``circuit_breaker`` whenever
  ``raw_circuit_breaker`` is non-empty). Sibling rows inside the same SQLite
  database never shadow one another. Between tiers, higher-precedence primary
  IDs shadow matching lower-precedence rows across all sources while upstream
  ``ss_id`` and Makeflow legacy ``original_id`` namespaces stay distinct.

  Args:
    db_path: Single SQLite database file path or ordered sequence of paths.
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

  Returns:
    List of structured scar dictionaries up to ``limit``.
  """
  if not db_path:
    return []
  if isinstance(db_path, str):
    raw_paths = [db_path]
  else:
    raw_paths = [str(p) for p in db_path if p]

  existing_paths = [p for p in raw_paths if os.path.isfile(p)]
  if not existing_paths:
    return []

  resolved_excluded = resolve_excluded_category_tokens(exclude_categories)
  eff_limit = max(1, limit)
  combined: List[Dict[str, Any]] = []
  shadowed_primary_ids: Set[str] = set()
  shadowed_ss_ids: Set[str] = set()
  shadowed_original_ids: Set[str] = set()

  for idx, single_path in enumerate(existing_paths):
    remaining = eff_limit - len(combined)
    if remaining <= 0:
      break
    db_matches = _query_single_scars_db(
        db_path=single_path,
        query=query,
        category=category,
        language=language,
        limit=remaining,
        source=source,
        framework=framework,
        diagnostic_code=diagnostic_code,
        workflow_phase=workflow_phase,
        mf_target=mf_target,
        exclude_categories=resolved_excluded,
        shadowed_sets=(
            shadowed_primary_ids,
            shadowed_ss_ids,
            shadowed_original_ids,
        ),
    )
    for row in db_matches:
      combined.append(row)
      if len(combined) >= eff_limit:
        return combined

    if idx < len(existing_paths) - 1:
      tier_primary, tier_ss, tier_orig = _collect_db_scar_keys(single_path)
      shadowed_primary_ids.update(tier_primary)
      shadowed_ss_ids.update(tier_ss)
      shadowed_original_ids.update(tier_orig)

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
