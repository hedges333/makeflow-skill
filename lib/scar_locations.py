"""Canonical single source of truth for synthetic scar filesystem locations (GH-304).

Resolves built-in Tier 1 Markdown process scar spokes alongside ordered,
token-expanded, deduplicated SQLite scar database tiers across project,
user-wide agent, custom configured, environment, and bundled locations.
"""

from dataclasses import dataclass
import os
from typing import Any, Dict, List, Optional, Tuple

from lib.agent_paths import AgentPathRegistry
from lib.config import (
    DEFAULT_SCAR_DATABASES,
    SCARS_ENV_VAR,
    expand_scar_database_path,
    expand_user_path,
    insert_scar_databases_before_bundled,
    resolve_workflow_config,
)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@dataclass(frozen=True)
class ScarLocations:
  """Immutable resolved filesystem locations for Makeflow synthetic scars.

  Attributes:
    process_scars_dir: Built-in Tier 1 Markdown process scars directory derived
      from ``plugin_root`` (not user-configurable).
    process_scar_files: Sorted tuple of existing ``*.md`` spoke files inside
      ``process_scars_dir``.
    databases: Ordered, token-expanded, ``realpath``-deduplicated tuple of
      configured SQLite scar database paths (highest precedence first).
    existing_databases: Ordered subset of ``databases`` that currently exist as
      files on disk.
    project_db: Project-local SQLite scar database path under
      ``<workspace>/.workflow/scars/scars_registry.db``, or ``None`` when no
      workspace directory was supplied.
    user_db: Active agent user-wide SQLite scar database path.
    bundled_db: Built-in bundled SQLite scar database path under ``plugin_root``.
  """

  process_scars_dir: str
  process_scar_files: Tuple[str, ...]
  databases: Tuple[str, ...]
  existing_databases: Tuple[str, ...]
  project_db: Optional[str]
  user_db: str
  bundled_db: str

  def to_dict(self) -> Dict[str, Any]:
    """Serialize resolved scar locations into a JSON-friendly dictionary.

    Returns:
      Dictionary exposing built-in process scar paths, canonical tier paths,
      and ordered configured/existing SQLite database lists.
    """
    return {
        "process_scars_dir": self.process_scars_dir,
        "process_scar_files": list(self.process_scar_files),
        "project_db": self.project_db,
        "user_db": self.user_db,
        "bundled_db": self.bundled_db,
        "configured_databases": list(self.databases),
        "existing_databases": list(self.existing_databases),
    }


def _discover_process_scar_files(process_scars_dir: str) -> Tuple[str, ...]:
  """Return sorted absolute paths to existing ``*.md`` files in ``process_scars_dir``."""
  if not os.path.isdir(process_scars_dir):
    return ()
  try:
    entries = sorted(os.listdir(process_scars_dir))
  except OSError:
    return ()
  files: List[str] = []
  for name in entries:
    if not name.endswith(".md"):
      continue
    full_path = os.path.join(process_scars_dir, name)
    if os.path.isfile(full_path):
      files.append(full_path)
  return tuple(files)


def _parse_scar_dbs_env() -> List[str]:
  """Parse comma-separated database paths from ``MAKEFILE_WORKFLOW_SCAR_DBS``."""
  raw = os.getenv(SCARS_ENV_VAR, "")
  if not raw:
    return []
  return [item.strip() for item in raw.split(",") if item.strip()]


def resolve_scar_locations(
    workspace_dir: Optional[str] = None,
    agent: Optional[str] = None,
    home_dir: Optional[str] = None,
    plugin_root: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> ScarLocations:
  """Resolve all built-in process scar files and ordered SQLite scar databases.

  Standard configuration cascade supplies ``scars.databases`` from the
  highest-precedence config layer defining it. ``MAKEFILE_WORKFLOW_SCAR_DBS``
  inserts immediately before the bundled registry entry (or appends when the
  bundled entry was omitted). ``MAKEFLOW_SCARS_DB`` prepends as the highest
  precedence database when set.

  Args:
    workspace_dir: Optional project repository root directory.
    agent: Optional active agent identifier override.
    home_dir: Optional home directory override for hermetic testing.
    plugin_root: Optional Makeflow plugin root directory override.
    config: Optional pre-resolved workflow configuration dictionary.

  Returns:
    Frozen ``ScarLocations`` instance with canonical process and SQLite paths.
  """
  eff_plugin_root = (
      os.path.abspath(expand_user_path(plugin_root, home_dir=home_dir))
      if plugin_root
      else _REPO_ROOT
  )
  ws_abs = (
      os.path.abspath(expand_user_path(workspace_dir, home_dir=home_dir))
      if workspace_dir
      else None
  )

  process_scars_dir = os.path.join(
      eff_plugin_root, "skills", "makeflow", "references", "scars"
  )
  process_scar_files = _discover_process_scar_files(process_scars_dir)

  profile = AgentPathRegistry(home_dir=home_dir).get_profile(agent)
  project_db = (
      os.path.join(ws_abs, ".workflow", "scars", "scars_registry.db")
      if ws_abs
      else None
  )
  user_db = profile.scars_db_path
  bundled_db = os.path.join(
      eff_plugin_root, "skills", "makeflow", "data", "scars_registry.db"
  )

  cfg = (
      config
      if isinstance(config, dict)
      else resolve_workflow_config(
          workspace_dir=ws_abs if ws_abs is not None else "",
          agent=agent,
          home_dir=home_dir,
          plugin_root=eff_plugin_root,
      )
  )
  raw_scars = cfg.get("scars")
  raw_dbs = (
      raw_scars.get("databases")
      if isinstance(raw_scars, dict) and isinstance(raw_scars.get("databases"), list)
      else list(DEFAULT_SCAR_DATABASES)
  )

  env_extra_dbs = _parse_scar_dbs_env()
  if env_extra_dbs:
    raw_dbs = insert_scar_databases_before_bundled(
        list(raw_dbs),
        env_extra_dbs,
        workspace_dir=ws_abs,
        agent=agent,
        home_dir=home_dir,
        plugin_root=eff_plugin_root,
    )

  candidates: List[str] = []
  env_single_db = os.getenv("MAKEFLOW_SCARS_DB", "").strip()
  if env_single_db:
    candidates.append(
        os.path.abspath(expand_user_path(env_single_db, home_dir=home_dir))
    )

  for raw in raw_dbs:
    resolved = expand_scar_database_path(
        raw,
        workspace_dir=ws_abs,
        agent=agent,
        home_dir=home_dir,
        plugin_root=eff_plugin_root,
    )
    if resolved:
      candidates.append(resolved)

  deduped: List[str] = []
  seen_real = set()
  for path in candidates:
    real_key = os.path.realpath(path)
    if real_key not in seen_real:
      seen_real.add(real_key)
      deduped.append(path)

  databases = tuple(deduped)
  existing_databases = tuple(p for p in databases if os.path.isfile(p))

  return ScarLocations(
      process_scars_dir=process_scars_dir,
      process_scar_files=process_scar_files,
      databases=databases,
      existing_databases=existing_databases,
      project_db=project_db,
      user_db=user_db,
      bundled_db=bundled_db,
  )


def get_configured_scar_databases(
    workspace_dir: Optional[str] = None,
    agent: Optional[str] = None,
    home_dir: Optional[str] = None,
    plugin_root: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> List[str]:
  """Return ordered, token-expanded, deduplicated SQLite scar database paths.

  Args:
    workspace_dir: Optional project root directory path.
    agent: Optional active agent override.
    home_dir: Optional home directory override for isolated testing.
    plugin_root: Optional Makeflow plugin root directory override.
    config: Optional pre-resolved workflow configuration dictionary.

  Returns:
    List of normalized absolute SQLite database paths in precedence order.
  """
  return list(
      resolve_scar_locations(
          workspace_dir=workspace_dir,
          agent=agent,
          home_dir=home_dir,
          plugin_root=plugin_root,
          config=config,
      ).databases
  )
