"""Shared configuration, plugin root detection, and Memento state resolution helpers (GH-169, SCAR-ARCH-05).

Provides deterministic configuration loading, deep merging, environment variable
overrides, plugin directory detection, and state directory lookup for CLI hooks
and MCP server endpoints.
"""

import copy
import json
import math
import os
import re
import tempfile
from typing import Any, Dict, List, Mapping, Optional

from lib.active_state import ActiveStateStore, list_state_card_files
from lib.agent_paths import AgentPathRegistry
from lib.ticket_state import TICKET_JSON_FILENAME

SCARS_ENV_VAR = "MAKEFILE_WORKFLOW_SCAR_DBS"
DEFAULT_BUNDLED_SCAR_DB_TOKEN = (
    "{plugin_root}/skills/makeflow/data/scars_registry.db"
)

DEFAULT_SCAR_DATABASES = [
    ".workflow/scars/scars_registry.db",
    "{agent_data}/scars_registry.db",
    DEFAULT_BUNDLED_SCAR_DB_TOKEN,
]
PLANNING_BDD_MODE_ENV_VAR = "MAKEFILE_WORKFLOW_PLANNING_BDD_MODE"
VALID_PLANNING_BDD_MODES = ("adaptive", "full_gherkin", "planning_only", "ask")

DEFAULT_WORKFLOW_CONFIG = {
    "models": {
        "adversarial-critic": "inherit",
        "archaeology-worker": "inherit",
        "tdd-worker": "inherit",
        "triage-worker": "inherit",
        "synthetic-scar-importer": "inherit",
    },
    "review": {
        "maxReviewRounds": 3,
        "strictSixPillars": True,
        "allowCriticHermeticTests": False,
    },
    "planning": {
        "bddMode": "",
    },
    "testing": {
        "regressionCommand": "",
        "regressionTimeoutSeconds": 300,
    },
    "ci": {
        "pollIntervalSeconds": 30,
        "quiescenceSeconds": 180,
        "enablePeerReviews": False,
        "minApprovals": 0,
        "requireCodeOwnerReviews": False,
        "enableSessionParking": False,
        "parkingCronExpression": "",
    },
    "database": {
        "prodDatabases": [],
        "testDatabases": [],
        "prodHosts": [],
    },
    "state": {
        "completedCardAction": "unlink",
    },
    "scars": {
        "databases": list(DEFAULT_SCAR_DATABASES),
    },
}

ENV_MAPPINGS = {
    "MAKEFILE_WORKFLOW_CRITIC_MODEL": "adversarial-critic",
    "MAKEFILE_WORKFLOW_WORKER_MODEL": "archaeology-worker",
    "MAKEFILE_WORKFLOW_ARCHAEOLOGY_MODEL": "archaeology-worker",
    "MAKEFILE_WORKFLOW_TDD_MODEL": "tdd-worker",
    "MAKEFILE_WORKFLOW_TRIAGE_MODEL": "triage-worker",
    "MAKEFILE_WORKFLOW_SCAR_IMPORTER_MODEL": "synthetic-scar-importer",
}

DB_ENV_MAPPINGS = {
    "MAKEFILE_WORKFLOW_PROD_DBS": "prodDatabases",
    "MAKEFILE_WORKFLOW_TEST_DBS": "testDatabases",
    "MAKEFILE_WORKFLOW_PROD_HOSTS": "prodHosts",
}

CI_ENV_MAPPINGS = {
    "MAKEFILE_WORKFLOW_CI_QUIESCENCE_SECONDS": "quiescenceSeconds",
    "MAKEFILE_WORKFLOW_CI_POLL_INTERVAL": "pollIntervalSeconds",
    "MAKEFILE_WORKFLOW_CI_MIN_APPROVALS": "minApprovals",
}

CI_BOOL_ENV_MAPPINGS = {
    "MAKEFILE_WORKFLOW_CI_ENABLE_PEER_REVIEWS": "enablePeerReviews",
    "MAKEFILE_WORKFLOW_CI_REQUIRE_CODEOWNER_REVIEWS": "requireCodeOwnerReviews",
    "MAKEFILE_WORKFLOW_CI_ENABLE_SESSION_PARKING": "enableSessionParking",
}

CI_STR_ENV_MAPPINGS = {
    "MAKEFILE_WORKFLOW_CI_PARKING_CRON": "parkingCronExpression",
}

_CI_FIELD_SPECS = {
    "pollIntervalSeconds": (30, 1),
    "quiescenceSeconds": (180, 0),
    "minApprovals": (0, 0),
}

_CI_BOOL_DEFAULTS = {
    "enablePeerReviews": False,
    "requireCodeOwnerReviews": False,
    "enableSessionParking": False,
}

_CI_STR_DEFAULTS = {
    "parkingCronExpression": "",
}

MAKE_RECURSION_ENV_VARS = (
    "MAKEFLAGS",
    "MFLAGS",
    "MAKEOVERRIDES",
    "MAKELEVEL",
    "GNUMAKEFLAGS",
)

MAKEFLOW_EXPORTED_ENV_VARS = (
    "TICKET",
    "ALLOW_BLOCKED",
    "FROM",
    "TO",
    "APPROVED",
    "BLOCKERS",
    "ALLOW_MAIN_CONFLICT",
    "NO_FETCH",
    "TRANSCRIPT",
    "FILE",
    "TEST_DIR",
    "TEST_CMD",
    "ALLOW_HEAD_MOVE",
    "CITE",
    "REGRESSION_CMD",
    "SUBPROJECT",
    "PATH_PREFIX",
    "TREE_LIMIT",
    "NO_LIMIT",
    "COUNT_ONLY",
)

_SANITIZED_MAKE_ENV_VARS = frozenset(MAKE_RECURSION_ENV_VARS) | frozenset(
    MAKEFLOW_EXPORTED_ENV_VARS
)


def sanitize_make_subprocess_env(
    base_env: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
  """Returns a copied environment stripped of GNU Make and Makeflow exports.

  Removes GNU Make recursion variables (``MAKEFLAGS``, ``MFLAGS``,
  ``MAKEOVERRIDES``, ``MAKELEVEL``, ``GNUMAKEFLAGS``) and all Makeflow
  ``.workflow/state/Makefile`` exported variables so child processes and
  nested Make invocations do not inherit parent overrides.

  Args:
    base_env: Optional source environment mapping. Defaults to ``os.environ``.

  Returns:
    Fresh dictionary containing all other environment entries unchanged.
  """
  source = os.environ if base_env is None else base_env
  env = dict(source)
  for key in _SANITIZED_MAKE_ENV_VARS:
    env.pop(key, None)
  return env


def _parse_int_min(val, default, min_val):
  """Parses a non-boolean integer or ASCII digit string >= min_val.

  Args:
    val: Raw integer, ASCII digit string, or invalid configuration value.
    default: Fallback value returned when ``val`` fails validation.
    min_val: Minimum inclusive integer value allowed.

  Returns:
    Validated integer >= ``min_val``, or ``default`` when invalid.
  """
  if isinstance(val, int) and not isinstance(val, bool) and val >= min_val:
    return val
  if isinstance(val, str):
    stripped = val.strip()
    if stripped.isascii() and stripped.isdigit():
      num = int(stripped)
      if num >= min_val:
        return num
  return default


def _parse_non_negative_int(val, default):
  """Parses an integer >= 0 while rejecting booleans and non-ASCII digits.

  Args:
    val: Raw configuration value to validate.
    default: Fallback value returned when ``val`` is invalid.

  Returns:
    Non-negative integer or ``default``.
  """
  return _parse_int_min(val, default, 0)


def _parse_positive_int(val, default):
  """Parses an integer >= 1 while rejecting booleans and non-ASCII digits.

  Args:
    val: Raw configuration value to validate.
    default: Fallback value returned when ``val`` is invalid.

  Returns:
    Positive integer or ``default``.
  """
  return _parse_int_min(val, default, 1)


def _parse_bool_env(raw_val: Optional[str]) -> Optional[bool]:
  """Parses a boolean environment variable string.

  Args:
    raw_val: Raw environment variable value, or None.

  Returns:
    True/False for recognized boolean strings, or None when unset or invalid.
  """
  if raw_val is None:
    return None
  lowered = raw_val.strip().lower()
  if lowered in ("1", "true", "yes"):
    return True
  if lowered in ("0", "false", "no"):
    return False
  return None


def _normalize_ci_config(raw_ci):
  """Normalizes CI configuration values to validated bounds and types.

  Args:
    raw_ci: Raw ``ci`` configuration mapping or invalid non-dict value.

  Returns:
    Dictionary with validated ``pollIntervalSeconds``, ``quiescenceSeconds``,
    ``minApprovals``, ``enablePeerReviews``, ``requireCodeOwnerReviews``,
    ``enableSessionParking``, and ``parkingCronExpression`` values.
  """
  ci_dict = raw_ci if isinstance(raw_ci, dict) else {}
  normalized = {
      key: _parse_int_min(ci_dict.get(key), default_val, min_val)
      for key, (default_val, min_val) in _CI_FIELD_SPECS.items()
  }
  for key, default_bool in _CI_BOOL_DEFAULTS.items():
    raw_bool = ci_dict.get(key)
    normalized[key] = raw_bool if isinstance(raw_bool, bool) else default_bool
  for key, default_str in _CI_STR_DEFAULTS.items():
    raw_str = ci_dict.get(key)
    normalized[key] = raw_str.strip() if isinstance(raw_str, str) else default_str
  return normalized


def _apply_ci_env_overrides(ci_cfg):
  """Applies validated CI environment overrides onto ``ci_cfg`` in place.

  Args:
    ci_cfg: Mutable dictionary containing normalized CI settings.

  Returns:
    List of ``(env_var, value)`` pairs for accepted environment overrides.
  """
  applied = []
  for env_var, ci_key in CI_ENV_MAPPINGS.items():
    _, min_val = _CI_FIELD_SPECS[ci_key]
    num = _parse_int_min(os.getenv(env_var), None, min_val)
    if num is not None:
      ci_cfg[ci_key] = num
      applied.append((env_var, num))
  for env_var, ci_key in CI_BOOL_ENV_MAPPINGS.items():
    parsed_bool = _parse_bool_env(os.getenv(env_var))
    if parsed_bool is not None:
      ci_cfg[ci_key] = parsed_bool
      applied.append((env_var, parsed_bool))
  for env_var, ci_key in CI_STR_ENV_MAPPINGS.items():
    raw_str = os.getenv(env_var)
    if raw_str is not None and raw_str.strip():
      ci_cfg[ci_key] = raw_str.strip()
      applied.append((env_var, ci_cfg[ci_key]))
  return applied


def _normalize_review_config(raw_review):
  """Normalizes review configuration values with safe defaults.

  Args:
    raw_review: Raw ``review`` configuration mapping or invalid non-dict value.

  Returns:
    Normalized ``review`` dictionary with ``maxReviewRounds``,
    ``strictSixPillars``, and validated ``allowCriticHermeticTests``.
  """
  review_dict = copy.deepcopy(raw_review) if isinstance(raw_review, dict) else {}
  if not isinstance(review_dict.get("maxReviewRounds"), int) or isinstance(
      review_dict.get("maxReviewRounds"), bool
  ):
    review_dict["maxReviewRounds"] = 3
  if not isinstance(review_dict.get("strictSixPillars"), bool):
    review_dict["strictSixPillars"] = True
  raw_allow = review_dict.get("allowCriticHermeticTests")
  review_dict["allowCriticHermeticTests"] = (
      raw_allow if isinstance(raw_allow, bool) else False
  )
  return review_dict


def _normalize_testing_config(raw_testing):
  """Normalizes testing regression command and timeout settings.

  Args:
    raw_testing: Raw ``testing`` configuration mapping or invalid non-dict value.

  Returns:
    Dictionary with validated ``regressionCommand`` (str) and
    ``regressionTimeoutSeconds`` (positive finite number >= 1, default 300).
  """
  testing_dict = raw_testing if isinstance(raw_testing, dict) else {}
  raw_cmd = testing_dict.get("regressionCommand")
  reg_cmd = raw_cmd.strip() if isinstance(raw_cmd, str) else ""

  raw_timeout = testing_dict.get("regressionTimeoutSeconds")
  if (
      not isinstance(raw_timeout, bool)
      and isinstance(raw_timeout, (int, float))
      and math.isfinite(float(raw_timeout))
      and float(raw_timeout) >= 1
  ):
    reg_timeout = raw_timeout
  else:
    reg_timeout = 300

  return {
      "regressionCommand": reg_cmd,
      "regressionTimeoutSeconds": reg_timeout,
  }


def _normalize_str_list(items):
  if not isinstance(items, list):
    return []
  return [x.strip() for x in items if isinstance(x, str) and x.strip()]


def _union_str_lists(*lists):
  """Compute an order-preserving deduplicated union of string lists."""
  merged = []
  seen = set()
  for items in lists:
    for item in _normalize_str_list(items):
      if item not in seen:
        seen.add(item)
        merged.append(item)
  return merged


def expand_user_path(path: str, home_dir: Optional[str] = None) -> str:
  """Expands a leading tilde against ``home_dir`` when injected or ``os.path.expanduser``.

  Args:
    path: Filesystem path string that may start with ``~``.
    home_dir: Optional home directory override for hermetic testing.

  Returns:
    Path string with any leading tilde expanded.
  """
  cleaned = path.strip()
  if home_dir and (
      cleaned == "~"
      or cleaned.startswith("~/")
      or cleaned.startswith("~" + os.sep)
  ):
    base_home = os.path.abspath(os.path.expanduser(home_dir))
    if cleaned == "~":
      return base_home
    return os.path.join(base_home, cleaned[2:])
  return os.path.expanduser(cleaned)


def _union_path_lists(*lists, home_dir=None):
  """Compute an order-preserving deduplicated union of filesystem path lists."""
  merged = []
  seen = set()
  for items in lists:
    for item in _normalize_str_list(items):
      norm_key = os.path.normpath(expand_user_path(item, home_dir=home_dir))
      if norm_key not in seen:
        seen.add(norm_key)
        merged.append(item)
  return merged


def _normalize_scars_config(raw_scars, home_dir=None):
  """Normalize ``scars`` configuration block to an ordered ``databases`` list.

  Args:
    raw_scars: Raw ``scars`` configuration dictionary or invalid non-dict value.
    home_dir: Optional home directory override for tilde normalization.

  Returns:
    Dictionary with a single ordered ``databases`` list.
  """
  scars_dict = raw_scars if isinstance(raw_scars, dict) else {}
  raw_dbs = scars_dict.get("databases")
  if isinstance(raw_dbs, list):
    databases = _union_path_lists(raw_dbs, home_dir=home_dir)
  else:
    databases = list(DEFAULT_SCAR_DATABASES)
  return {
      "databases": databases,
  }


def expand_scar_database_path(
    raw_path: str,
    workspace_dir: Optional[str] = None,
    agent: Optional[str] = None,
    home_dir: Optional[str] = None,
    plugin_root: Optional[str] = None,
) -> Optional[str]:
  """Expands tokens and relative segments in a configured SQLite scar database path.

  Supports ``{workspace}``, ``{agent_data}``, ``{plugin_root}``, leading ``~``,
  and workspace-relative paths.

  Args:
    raw_path: Raw configured database path or token template string.
    workspace_dir: Optional project root directory for ``{workspace}`` and relative paths.
    agent: Optional active agent override for ``{agent_data}``.
    home_dir: Optional home directory override for tilde and agent paths.
    plugin_root: Optional Makeflow plugin root override for ``{plugin_root}``.

  Returns:
    Normalized absolute path string, or ``None`` if the path is empty or
    requires a workspace directory that was not provided.
  """
  if not isinstance(raw_path, str) or not raw_path.strip():
    return None
  expanded = raw_path.strip()

  ws_abs = (
      os.path.abspath(expand_user_path(workspace_dir, home_dir=home_dir))
      if workspace_dir
      else None
  )
  if "{workspace}" in expanded:
    if not ws_abs:
      return None
    expanded = expanded.replace("{workspace}", ws_abs)

  if "{agent_data}" in expanded:
    profile = AgentPathRegistry(home_dir=home_dir).get_profile(agent)
    expanded = expanded.replace("{agent_data}", profile.plugin_data_dir)

  if "{plugin_root}" in expanded:
    eff_plugin_root = (
        os.path.abspath(expand_user_path(plugin_root, home_dir=home_dir))
        if plugin_root
        else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    expanded = expanded.replace("{plugin_root}", eff_plugin_root)

  expanded = expand_user_path(expanded, home_dir=home_dir)
  if not os.path.isabs(expanded):
    if not ws_abs:
      return None
    expanded = os.path.join(ws_abs, expanded)
  return os.path.abspath(expanded)


def insert_scar_databases_before_bundled(
    databases: List[str],
    extra_dbs: List[str],
    workspace_dir: Optional[str] = None,
    agent: Optional[str] = None,
    home_dir: Optional[str] = None,
    plugin_root: Optional[str] = None,
) -> List[str]:
  """Inserts extra SQLite scar DB paths immediately before the bundled registry entry.

  Appends extra entries at the end when the bundled registry entry was omitted
  from ``databases``. Deduplicates entries idempotently while keeping existing
  precedence positions intact.

  Args:
    databases: Ordered list of raw or expanded scar database paths.
    extra_dbs: Additional scar database paths to insert.
    workspace_dir: Optional project root directory for path normalization.
    agent: Optional active agent override.
    home_dir: Optional home directory override for tilde normalization.
    plugin_root: Optional Makeflow plugin root directory override.

  Returns:
    Updated ordered list of scar database path strings.
  """
  cleaned_base = _union_path_lists(databases, home_dir=home_dir)
  cleaned_extra = _normalize_str_list(extra_dbs)
  if not cleaned_extra:
    return cleaned_base

  seen_norm = {
      os.path.normpath(expand_user_path(item, home_dir=home_dir))
      for item in cleaned_base
  }
  to_insert: List[str] = []
  for item in cleaned_extra:
    norm_key = os.path.normpath(expand_user_path(item, home_dir=home_dir))
    if norm_key not in seen_norm:
      seen_norm.add(norm_key)
      to_insert.append(item)
  if not to_insert:
    return cleaned_base

  eff_plugin_root = (
      os.path.abspath(expand_user_path(plugin_root, home_dir=home_dir))
      if plugin_root
      else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
  )
  bundled_real = os.path.realpath(
      os.path.abspath(
          os.path.join(
              eff_plugin_root,
              "skills",
              "makeflow",
              "data",
              "scars_registry.db",
          )
      )
  )

  insert_idx: Optional[int] = None
  for idx, entry in enumerate(cleaned_base):
    if entry.strip() == DEFAULT_BUNDLED_SCAR_DB_TOKEN:
      insert_idx = idx
      break
    expanded = expand_scar_database_path(
        entry,
        workspace_dir=workspace_dir,
        agent=agent,
        home_dir=home_dir,
        plugin_root=eff_plugin_root,
    )
    if expanded and os.path.realpath(expanded) == bundled_real:
      insert_idx = idx
      break

  if insert_idx is not None:
    return cleaned_base[:insert_idx] + to_insert + cleaned_base[insert_idx:]
  return cleaned_base + to_insert


def _normalize_planning_config(raw_planning):
  """Normalizes ``planning`` configuration block to a validated ``bddMode``.

  Args:
    raw_planning: Raw ``planning`` configuration mapping or invalid value.

  Returns:
    Dictionary with validated ``bddMode`` (one of ``VALID_PLANNING_BDD_MODES``
    or ``""`` when unset or invalid).
  """
  planning_dict = raw_planning if isinstance(raw_planning, dict) else {}
  raw_mode = planning_dict.get("bddMode")
  cleaned = raw_mode.strip().lower() if isinstance(raw_mode, str) else ""
  return {
      "bddMode": cleaned if cleaned in VALID_PLANNING_BDD_MODES else "",
  }


def _write_planning_mode_to_file(config_path: str, mode: str) -> str:
  """Updates ``planning.bddMode`` in one JSON config file atomically.

  Args:
    config_path: Destination JSON configuration file path.
    mode: Validated normalized BDD mode string.

  Returns:
    Absolute path to the updated configuration file.

  Raises:
    ValueError: If an existing file contains non-object JSON.
  """
  abs_cfg = os.path.abspath(os.path.expanduser(config_path))
  data: Dict[str, Any] = {}
  if os.path.isfile(abs_cfg):
    with open(abs_cfg, "r", encoding="utf-8") as fh:
      raw_content = fh.read().strip()
    if raw_content:
      loaded = json.loads(raw_content)
      if not isinstance(loaded, dict):
        raise ValueError(
            f"Configuration file '{abs_cfg}' must contain a JSON object."
        )
      data = loaded

  planning_section = data.get("planning")
  if not isinstance(planning_section, dict):
    planning_section = {}
    data["planning"] = planning_section
  planning_section["bddMode"] = mode

  parent_dir = os.path.dirname(abs_cfg)
  os.makedirs(parent_dir, exist_ok=True)
  tmp_path: Optional[str] = None
  try:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=parent_dir,
        prefix=".tmp_makeflow_cfg_",
        suffix=".json",
        delete=False,
    ) as tmp_file:
      tmp_path = tmp_file.name
      json.dump(data, tmp_file, indent=2)
      tmp_file.write("\n")
      tmp_file.flush()
      os.fsync(tmp_file.fileno())
    os.replace(tmp_path, abs_cfg)
    tmp_path = None
  finally:
    if tmp_path is not None and os.path.exists(tmp_path):
      try:
        os.unlink(tmp_path)
      except OSError:
        pass
  return abs_cfg


def save_planning_bdd_mode(
    mode: str,
    scope: str,
    workspace_dir: Optional[str] = None,
    agent: Optional[str] = None,
    home_dir: Optional[str] = None,
) -> Dict[str, Any]:
  """Persists ``planning.bddMode`` to ``user-global``, ``project-local``, or ``both``.

  Args:
    mode: BDD mode string (``adaptive``, ``full_gherkin``, ``planning_only``,
      or ``ask``).
    scope: Storage scope (``user-global``, ``project-local``, or ``both``).
    workspace_dir: Project root path (required for ``project-local`` and
      ``both`` scopes).
    agent: Optional agent name override for user-global config lookup.
    home_dir: Optional home directory override for isolated testing.

  Returns:
    Dictionary with ``bddMode``, ``scope``, and ``updatedFiles``.

  Raises:
    ValueError: If ``mode``, ``scope``, or required ``workspace_dir`` is invalid.
  """
  clean_mode = str(mode or "").strip().lower()
  if clean_mode not in VALID_PLANNING_BDD_MODES:
    valid = ", ".join(VALID_PLANNING_BDD_MODES)
    raise ValueError(
        f"Unsupported planning.bddMode '{mode}'. Expected one of: {valid}."
    )
  clean_scope = str(scope or "").strip().lower()
  if clean_scope not in ("user-global", "project-local", "both"):
    raise ValueError(
        f"Unsupported config scope '{scope}'. Expected user-global, project-local, or both."
    )
  if clean_scope in ("project-local", "both") and not (
      workspace_dir and isinstance(workspace_dir, str) and workspace_dir.strip()
  ):
    raise ValueError(
        "workspace_dir is required for project-local or both scope."
    )

  targets: List[str] = []
  if clean_scope in ("user-global", "both"):
    profile = AgentPathRegistry(home_dir=home_dir).get_profile(agent)
    targets.append(profile.global_config_path)
  if clean_scope in ("project-local", "both"):
    assert workspace_dir is not None
    ws_abs = os.path.abspath(
        expand_user_path(workspace_dir.strip(), home_dir=home_dir)
    )
    targets.append(os.path.join(ws_abs, ".workflow", "config.json"))

  updated_files = [
      _write_planning_mode_to_file(cfg_path, clean_mode)
      for cfg_path in targets
  ]
  return {
      "bddMode": clean_mode,
      "scope": clean_scope,
      "updatedFiles": updated_files,
  }


def _iter_cascaded_config_candidates(
    workspace_paths, agent=None, home_dir=None, allow_cwd_fallback=True
):
  """Build ordered ``(path, label, layer_kind)`` config candidates from lowest to highest precedence.

  Order:
    1. Legacy global file (``~/.gemini/config/makeflow.json``).
    2. Active agent user-global file (``<agent_plugin_data>/config.json``).
    3. Project-local workspace file (``<workspace>/.workflow/config.json``).
  """
  profile = AgentPathRegistry(home_dir=home_dir).get_profile(agent)
  candidates = []
  seen_paths = set()

  def _add(path, label, layer_kind):
    if not path:
      return
    abs_path = os.path.abspath(expand_user_path(path, home_dir=home_dir))
    if abs_path not in seen_paths:
      seen_paths.add(abs_path)
      candidates.append((abs_path, label or abs_path, layer_kind))

  for legacy_path in profile.legacy_config_paths:
    _add(legacy_path, "~/.gemini/config/makeflow.json", "legacy")

  _add(profile.global_config_path, profile.global_config_path, "user_global")

  local_candidates = []
  for ws in workspace_paths or []:
    local_candidates.append(os.path.join(ws, ".workflow", "config.json"))
  if allow_cwd_fallback and not workspace_paths and not is_plugin_dir(os.getcwd()):
    local_candidates.append(os.path.join(os.getcwd(), ".workflow", "config.json"))

  for local_path in local_candidates:
    if os.path.isfile(local_path):
      _add(local_path, local_path, "project_local")

  return candidates


def _load_cascaded_config_layers(
    workspace_paths, agent=None, home_dir=None, allow_cwd_fallback=True
):
  """Load existing config files in cascade order and compute merged layers.

  Returns:
    Tuple of ``(loaded_layers, loaded_sources, union_db_cfg)`` where
    ``loaded_layers`` is a list of ``(layer_kind, loaded_dict)`` and
    ``loaded_sources`` is a list of ``(abs_path, display_label)`` in cascade order.
  """
  candidates = _iter_cascaded_config_candidates(
      workspace_paths,
      agent=agent,
      home_dir=home_dir,
      allow_cwd_fallback=allow_cwd_fallback,
  )
  loaded_layers = []
  loaded_sources = []
  db_lists_by_key = {
      "prodDatabases": [],
      "testDatabases": [],
      "prodHosts": [],
  }

  loaded_project_local = False
  for cfg_path, label, layer_kind in candidates:
    if layer_kind == "project_local" and loaded_project_local:
      continue
    if not os.path.isfile(cfg_path):
      continue
    try:
      with open(cfg_path, "r", encoding="utf-8") as f:
        loaded = json.load(f)
      if isinstance(loaded, dict):
        if layer_kind == "project_local":
          loaded_project_local = True
        loaded_layers.append((layer_kind, loaded))
        loaded_sources.append((cfg_path, label))
        raw_db = loaded.get("database")
        if isinstance(raw_db, dict):
          for db_key in ("prodDatabases", "testDatabases", "prodHosts"):
            if db_key in raw_db:
              db_lists_by_key[db_key].append(raw_db[db_key])
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
      pass

  union_db_cfg = {
      db_key: _union_str_lists(*db_lists_by_key[db_key])
      for db_key in ("prodDatabases", "testDatabases", "prodHosts")
  }
  return loaded_layers, loaded_sources, union_db_cfg


def _parse_csv_env(env_name):
  raw = os.getenv(env_name, "")
  if not raw:
    return []
  return [item.strip() for item in raw.split(",") if item.strip()]


def deep_merge(base, override):
  """Recursively merges override dictionary into base dictionary without mutating base."""
  result = copy.deepcopy(base) if isinstance(base, dict) else {}
  if not isinstance(override, dict):
    return result
  for key, val in override.items():
    if key in result and isinstance(result[key], dict) and isinstance(val, dict):
      result[key] = deep_merge(result[key], val)
    else:
      result[key] = copy.deepcopy(val)
  return result


def load_default_config(plugin_root=None):
  """Loads canonical default_config.json from repository, plugin_root, or install root.

  Args:
    plugin_root: Optional Makeflow plugin root directory override.

  Returns:
    Parsed default workflow configuration dictionary.
  """
  if plugin_root:
    candidates = [
        os.path.join(
            os.path.abspath(os.path.expanduser(plugin_root)),
            "default_config.json",
        )
    ]
  else:
    candidates = [
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "default_config.json",
        ),
        os.path.join(os.getcwd(), "default_config.json"),
        os.path.expanduser(
            "~/.gemini/config/plugins/makeflow/default_config.json"
        ),
    ]
  for path in candidates:
    if os.path.isfile(path):
      try:
        with open(path, "r", encoding="utf-8") as f:
          data = json.load(f)
        if isinstance(data, dict):
          return data
      except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        pass
  return copy.deepcopy(DEFAULT_WORKFLOW_CONFIG)


def is_plugin_dir(path):
  """Checks whether a directory is inside a Makeflow plugin installation root.

  Checks both the expanded path and its resolved realpath against known agent
  plugin cache roots (Gemini, Claude, Codex, Cursor, Copilot) so symlinks into
  installed plugin caches are recognized, then walks upward on both the
  expanded path and resolved realpath for a non-Git Makeflow ``plugin.json``
  manifest (treating both ``.git`` directories and worktree ``.git`` files as
  Git checkouts).

  Args:
    path: Directory path string to inspect.

  Returns:
    True when ``path`` lies inside a known plugin cache directory or non-Git
    Makeflow plugin installation root, otherwise False.
  """
  if not path or not isinstance(path, str):
    return False
  norm_path = os.path.abspath(os.path.expanduser(path))
  real_path = os.path.realpath(norm_path)
  raw_plugin_prefixes = (
      "~/.gemini/config/plugins",
      "~/.gemini/plugins",
      "~/.claude/plugins",
      "~/.codex/plugins",
      "~/.cursor/plugins",
      "~/.copilot/plugins",
  )
  expanded_prefixes = {
      os.path.abspath(os.path.expanduser(p)) for p in raw_plugin_prefixes
  }
  known_plugin_prefixes = expanded_prefixes | {
      os.path.realpath(p) for p in expanded_prefixes
  }
  for cand in (norm_path, real_path):
    for prefix in known_plugin_prefixes:
      if cand == prefix or cand.startswith(prefix + os.sep):
        return True

  walk_roots = [norm_path]
  if real_path != norm_path:
    walk_roots.append(real_path)
  for start_path in walk_roots:
    curr = start_path
    while curr and curr != os.path.dirname(curr):
      if os.path.exists(os.path.join(curr, ".git")):
        break
      plugin_json_path = os.path.join(curr, "plugin.json")
      if os.path.isfile(plugin_json_path):
        try:
          with open(plugin_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
          if isinstance(data, dict) and data.get("name") == "makeflow":
            return True
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
          pass
      curr = os.path.dirname(curr)
  return False


def get_workflow_config(
    workspace_paths, agent=None, home_dir=None, plugin_root=None
):
  """Resolves normalized hook workflow configuration and source label.

  Cascades ``default_config.json`` -> legacy ``~/.gemini/config/makeflow.json``
  -> active agent user-global ``config.json`` -> project-local
  ``.workflow/config.json`` -> environment overrides.

  Args:
    workspace_paths: List of candidate workspace root paths.
    agent: Optional active agent override.
    home_dir: Optional home directory override for isolated testing.
    plugin_root: Optional plugin root directory override.

  Returns:
    Tuple of ``(config_dict, source_label)`` where ``source_label`` is ``None``
    when no config file or environment override applies.
  """
  merged_cfg = load_default_config(plugin_root=plugin_root)
  loaded_layers, loaded_sources, union_db_cfg = _load_cascaded_config_layers(
      workspace_paths, agent=agent, home_dir=home_dir
  )
  for _, layer_data in loaded_layers:
    merged_cfg = deep_merge(merged_cfg, layer_data)

  source = None
  if len(loaded_sources) == 1:
    source = loaded_sources[0][1]
  elif len(loaded_sources) > 1:
    source = " + ".join(label for _, label in loaded_sources)

  models = {}
  raw_models = merged_cfg.get("models")
  if isinstance(raw_models, dict):
    for m_key, m_val in raw_models.items():
      if isinstance(m_key, str) and isinstance(m_val, str) and m_val.strip():
        models[m_key] = m_val.strip()

  db_cfg = {
      "prodDatabases": list(union_db_cfg["prodDatabases"]),
      "testDatabases": list(union_db_cfg["testDatabases"]),
      "prodHosts": list(union_db_cfg["prodHosts"]),
  }
  scars_cfg = _normalize_scars_config(merged_cfg.get("scars"), home_dir=home_dir)

  ci_cfg = _normalize_ci_config(merged_cfg.get("ci"))
  planning_cfg = _normalize_planning_config(merged_cfg.get("planning"))

  env_applied = []
  for env_var, agent_key in ENV_MAPPINGS.items():
    val = os.getenv(env_var)
    if val and val.strip():
      models[agent_key] = val.strip()
      env_applied.append(env_var)

  for env_var, _ in _apply_ci_env_overrides(ci_cfg):
    env_applied.append(env_var)

  for env_var, db_key in DB_ENV_MAPPINGS.items():
    val = os.getenv(env_var)
    if val:
      items = [x.strip() for x in val.split(",") if x.strip()]
      if items:
        db_cfg[db_key] = _union_str_lists(db_cfg[db_key], items)
        env_applied.append(env_var)

  env_scar_dbs = _parse_csv_env(SCARS_ENV_VAR)
  if env_scar_dbs:
    first_ws = workspace_paths[0] if workspace_paths else None
    scars_cfg["databases"] = insert_scar_databases_before_bundled(
        scars_cfg["databases"],
        env_scar_dbs,
        workspace_dir=first_ws,
        agent=agent,
        home_dir=home_dir,
        plugin_root=plugin_root,
    )
    env_applied.append(SCARS_ENV_VAR)

  state_cfg = {"completedCardAction": "unlink"}
  raw_state = merged_cfg.get("state")
  if isinstance(raw_state, dict):
    action = raw_state.get("completedCardAction")
    if action in ("unlink", "archive"):
      state_cfg["completedCardAction"] = action

  env_card_action = os.getenv("MAKEFILE_WORKFLOW_COMPLETED_CARD_ACTION")
  if env_card_action and env_card_action.strip() in ("unlink", "archive"):
    state_cfg["completedCardAction"] = env_card_action.strip()
    env_applied.append("MAKEFILE_WORKFLOW_COMPLETED_CARD_ACTION")

  env_bdd_mode = os.getenv(PLANNING_BDD_MODE_ENV_VAR)
  if env_bdd_mode is not None:
    cleaned_bdd = env_bdd_mode.strip().lower()
    if cleaned_bdd in VALID_PLANNING_BDD_MODES:
      planning_cfg["bddMode"] = cleaned_bdd
      env_applied.append(PLANNING_BDD_MODE_ENV_VAR)

  if env_applied:
    env_label = "env (" + ", ".join(env_applied) + ")"
    source = (source + " + " + env_label) if source else env_label

  return {
      "models": models,
      "planning": planning_cfg,
      "ci": ci_cfg,
      "database": db_cfg,
      "state": state_cfg,
      "scars": scars_cfg,
      "configSources": [path for path, _ in loaded_sources],
  }, source


def load_database_config(payload, agent=None, home_dir=None):
  """Loads cascaded database isolation rules from global config, workspace config, and env."""
  workspace_paths = (payload or {}).get("workspacePaths") or []
  _, _, union_db_cfg = _load_cascaded_config_layers(
      workspace_paths, agent=agent, home_dir=home_dir
  )
  db_cfg = {
      "prodDatabases": list(union_db_cfg["prodDatabases"]),
      "testDatabases": list(union_db_cfg["testDatabases"]),
      "prodHosts": list(union_db_cfg["prodHosts"]),
  }

  for env_var, key in DB_ENV_MAPPINGS.items():
    vals = _parse_csv_env(env_var)
    if vals:
      db_cfg[key] = _union_str_lists(db_cfg[key], vals)

  return db_cfg


def is_ticket_active(state_dir):
  """Checks whether an active in-progress ticket exists in state_dir.

  Reads canonical ``active_target.json`` via ``ActiveStateStore`` first
  (SCAR-ARCH-10). Explicit ``ACTIVE``/``PARKED``/``COMPLETE`` lifecycle states
  are authoritative over ``99_next_action.md``. Falls back to minimal legacy
  Markdown checks only when ``active_target.json`` is absent or lacks explicit
  status fields (SCAR-ARCH-07).

  Args:
    state_dir: Path to the ``.workflow/state`` directory.

  Returns:
    True when an active ticket workflow is in progress, otherwise False.
  """
  if not state_dir or not os.path.isdir(state_dir):
    return False

  active_state = ActiveStateStore(state_dir).load()
  if active_state is not None:
    if active_state.is_complete():
      return False
    if active_state.has_explicit_status():
      return active_state.is_active()

  state_files = list_state_card_files(state_dir)
  if not state_files:
    return False

  next_action_file = os.path.join(state_dir, "99_next_action.md")
  if os.path.isfile(next_action_file):
    try:
      with open(next_action_file, "r", encoding="utf-8") as f:
        content = f.read()
      if (
          "# Target 90 Finished: COMPLETE" in content
          or "Status: COMPLETE" in content
          or "make finish satisfied" in content
      ):
        return False
      if state_files == ["99_next_action.md"] and (
          "- Ticket: Unassigned" in content
          or "- Active Target: Target 00" in content
      ):
        return False
    except (OSError, UnicodeDecodeError):
      pass

  return True


# Built-in technical English specifications live in skills/makeflow/references/resources/language/english/


def find_state_dir(workspace_paths, artifact_dir):
  if workspace_paths:
    for ws in workspace_paths:
      candidate = os.path.join(ws, ".workflow", "state")
      if os.path.isdir(candidate):
        return candidate
    return ""

  if not is_plugin_dir(os.getcwd()):
    cwd_state = os.path.join(os.getcwd(), ".workflow", "state")
    if os.path.isdir(cwd_state):
      return cwd_state

  if artifact_dir:
    brain_state = os.path.join(artifact_dir, "state")
    if os.path.isdir(brain_state):
      return brain_state

  return ""


def resolve_state_dir(payload):
  """Finds the active state directory from workspacePaths or artifactDirectoryPath.

  Filters blank, whitespace-only, and non-string entries from
  ``workspacePaths`` before probing for ``.workflow/state``.

  Args:
    payload: Hook payload dictionary with optional ``workspacePaths`` and
      ``artifactDirectoryPath`` keys.

  Returns:
    Resolved state directory path string, or ``""`` when none applies.
  """
  workspace_paths = [
      ws.strip()
      for ws in (payload.get("workspacePaths") or [])
      if isinstance(ws, str) and ws.strip()
  ]
  for ws in workspace_paths:
    candidate = os.path.join(ws, ".workflow", "state")
    if os.path.isdir(candidate):
      return candidate

  artifact_dir = payload.get("artifactDirectoryPath", "")
  if isinstance(artifact_dir, str) and artifact_dir.strip():
    brain_state = os.path.join(artifact_dir.strip(), "state")
    if os.path.isdir(brain_state):
      return brain_state

  if workspace_paths:
    return os.path.join(workspace_paths[0], ".workflow", "state")

  if not is_plugin_dir(os.getcwd()):
    return os.path.join(os.getcwd(), ".workflow", "state")

  return ""


def resolve_workspace_dir(arguments):
  ws = (arguments or {}).get("workspaceDir")
  if ws:
    return os.path.expanduser(ws)
  env_ws = os.getenv("MAKEFILE_WORKFLOW_WORKSPACE")
  if env_ws:
    return os.path.expanduser(env_ws)
  if is_plugin_dir(os.getcwd()):
    return None
  return os.getcwd()


def map_to_agy_tier(model_value):
  """Maps a concrete model identifier to an Antigravity subagent tier."""
  if not model_value:
    return "inherit"
  val = str(model_value).strip().lower()
  if val in {"inherit", "flash_lite", "flash", "pro"}:
    return val
  tokens = set(re.split(r"[-_./\s]+", val))
  if (
      "flash-lite" in val
      or "flash_lite" in val
      or any(tok in tokens for tok in ("lite", "haiku", "mini", "nano"))
  ):
    return "flash_lite"
  if any(tok in tokens or tok in val for tok in ("pro", "argon", "opus", "high", "o1", "o3")):
    return "pro"
  if any(tok in tokens or tok in val for tok in ("flash", "fast", "low")):
    return "flash"
  return "inherit"


def resolve_workflow_config(
    workspace_dir=None, agent=None, home_dir=None, plugin_root=None
):
  """Resolves cascaded workflow configuration from global, workspace, and env layers.

  Precedence order:
    ``default_config.json`` -> legacy ``~/.gemini/config/makeflow.json`` ->
    active agent user-global ``config.json`` -> project-local
    ``.workflow/config.json`` -> ``MAKEFILE_WORKFLOW_*`` environment variables.

  Args:
    workspace_dir: Optional project root directory path.
    agent: Optional active agent override.
    home_dir: Optional home directory override for isolated testing.
    plugin_root: Optional plugin root directory override.

  Returns:
    Normalized workflow configuration dictionary including ``configSource``,
    ``configSources``, ``envOverrides``, ``scars``, and ``agyModelTiers``.
  """
  allow_cwd_fallback = workspace_dir != ""
  ws = workspace_dir if workspace_dir is not None else resolve_workspace_dir({})
  ws_list = [ws] if ws else []

  config = load_default_config(plugin_root=plugin_root)
  loaded_layers, loaded_sources, union_db_cfg = _load_cascaded_config_layers(
      ws_list,
      agent=agent,
      home_dir=home_dir,
      allow_cwd_fallback=allow_cwd_fallback,
  )
  for _, layer_data in loaded_layers:
    config = deep_merge(config, layer_data)

  config["database"] = {
      "prodDatabases": list(union_db_cfg["prodDatabases"]),
      "testDatabases": list(union_db_cfg["testDatabases"]),
      "prodHosts": list(union_db_cfg["prodHosts"]),
  }
  config["scars"] = _normalize_scars_config(
      config.get("scars"), home_dir=home_dir
  )
  config["configSources"] = [path for path, _ in loaded_sources]
  config["configSource"] = loaded_sources[-1][0] if loaded_sources else "defaults"
  config["envOverrides"] = []

  config["ci"] = _normalize_ci_config(config.get("ci"))
  config["review"] = _normalize_review_config(config.get("review"))
  config["planning"] = _normalize_planning_config(config.get("planning"))
  config["testing"] = _normalize_testing_config(config.get("testing"))

  for env_var, agent_key in ENV_MAPPINGS.items():
    val = os.getenv(env_var)
    if val:
      config["models"][agent_key] = val.strip()
      config["envOverrides"].append(f"{env_var}={val.strip()}")

  for env_var, db_key in DB_ENV_MAPPINGS.items():
    vals = _parse_csv_env(env_var)
    if vals:
      config["database"][db_key] = _union_str_lists(
          config["database"][db_key], vals
      )
      config["envOverrides"].append(f"{env_var}={','.join(vals)}")

  env_scar_dbs = _parse_csv_env(SCARS_ENV_VAR)
  if env_scar_dbs:
    config["scars"]["databases"] = insert_scar_databases_before_bundled(
        config["scars"]["databases"],
        env_scar_dbs,
        workspace_dir=ws,
        agent=agent,
        home_dir=home_dir,
        plugin_root=plugin_root,
    )
    config["envOverrides"].append(
        f"{SCARS_ENV_VAR}={','.join(env_scar_dbs)}"
    )

  for env_var, num in _apply_ci_env_overrides(config["ci"]):
    config["envOverrides"].append(f"{env_var}={num}")

  if "state" not in config or not isinstance(config["state"], dict):
    config["state"] = {"completedCardAction": "unlink"}
  else:
    action = config["state"].get("completedCardAction", "unlink")
    if action not in ("unlink", "archive"):
      action = "unlink"
    config["state"]["completedCardAction"] = action

  env_card_action = os.getenv("MAKEFILE_WORKFLOW_COMPLETED_CARD_ACTION")
  if env_card_action and env_card_action.strip() in ("unlink", "archive"):
    config["state"]["completedCardAction"] = env_card_action.strip()
    config["envOverrides"].append(
        f"MAKEFILE_WORKFLOW_COMPLETED_CARD_ACTION={env_card_action.strip()}"
    )

  env_bdd_mode = os.getenv(PLANNING_BDD_MODE_ENV_VAR)
  if env_bdd_mode is not None:
    cleaned_bdd = env_bdd_mode.strip().lower()
    if cleaned_bdd in VALID_PLANNING_BDD_MODES:
      config["planning"]["bddMode"] = cleaned_bdd
      config["envOverrides"].append(
          f"{PLANNING_BDD_MODE_ENV_VAR}={cleaned_bdd}"
      )

  config["agyModelTiers"] = {
      role: map_to_agy_tier(val) for role, val in config["models"].items()
  }
  return config
