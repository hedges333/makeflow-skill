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
}

_CI_FIELD_SPECS = {
    "pollIntervalSeconds": (30, 1),
    "quiescenceSeconds": (180, 0),
}


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


def _normalize_ci_config(raw_ci):
  """Normalizes CI configuration values to validated integer bounds.

  Args:
    raw_ci: Raw ``ci`` configuration mapping or invalid non-dict value.

  Returns:
    Dictionary with validated ``pollIntervalSeconds`` (>= 1) and
    ``quiescenceSeconds`` (>= 0) integers.
  """
  ci_dict = raw_ci if isinstance(raw_ci, dict) else {}
  return {
      key: _parse_int_min(ci_dict.get(key), default_val, min_val)
      for key, (default_val, min_val) in _CI_FIELD_SPECS.items()
  }


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


def load_default_config():
  """Loads canonical default_config.json from repository or install root."""
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
  return {
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
      "testing": {
          "regressionCommand": "",
          "regressionTimeoutSeconds": 300,
      },
      "ci": {
          "pollIntervalSeconds": 30,
          "quiescenceSeconds": 180,
      },
      "database": {
          "prodDatabases": [],
          "testDatabases": [],
          "prodHosts": [],
      },
      "state": {
          "completedCardAction": "unlink",
      },
  }


def is_plugin_dir(path):
  """Checks whether a directory is inside a makeflow plugin installation root."""
  if not path or not isinstance(path, str):
    return False
  norm_path = os.path.abspath(os.path.expanduser(path))
  known_plugin_prefixes = (
      os.path.abspath(os.path.expanduser("~/.gemini/config/plugins")),
      os.path.abspath(os.path.expanduser("~/.gemini/plugins")),
      os.path.abspath(os.path.expanduser("~/.claude/plugins")),
  )
  for prefix in known_plugin_prefixes:
    if norm_path == prefix or norm_path.startswith(prefix + os.sep):
      return True

  curr = norm_path
  while curr and curr != os.path.dirname(curr):
    if os.path.isdir(os.path.join(curr, ".git")):
      return False
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


def get_workflow_config(workspace_paths):
  """Resolves normalized hook workflow configuration and source label.

  Args:
    workspace_paths: List of candidate workspace root paths.

  Returns:
    Tuple of ``(config_dict, source_label)`` where ``source_label`` is ``None``
    when no config file or environment override applies.
  """
  default_cfg = load_default_config()
  raw_cfg = {}
  source = None

  candidates = []
  for ws in workspace_paths:
    candidates.append((os.path.join(ws, ".workflow", "config.json"), None))
  if not workspace_paths and not is_plugin_dir(os.getcwd()):
    candidates.append((os.path.join(os.getcwd(), ".workflow", "config.json"), None))
  candidates.append((
      os.path.expanduser("~/.gemini/config/makeflow.json"),
      "~/.gemini/config/makeflow.json",
  ))

  for cfg_path, label in candidates:
    if os.path.isfile(cfg_path):
      try:
        with open(cfg_path, "r", encoding="utf-8") as f:
          loaded = json.load(f)
        if isinstance(loaded, dict):
          raw_cfg = loaded
          source = label or cfg_path
          break
      except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        pass

  merged_cfg = deep_merge(default_cfg, raw_cfg)

  models = {}
  raw_models = merged_cfg.get("models")
  if isinstance(raw_models, dict):
    for m_key, m_val in raw_models.items():
      if isinstance(m_key, str) and isinstance(m_val, str) and m_val.strip():
        models[m_key] = m_val.strip()

  db_cfg = {
      "prodDatabases": [],
      "testDatabases": [],
      "prodHosts": [],
  }
  raw_db = merged_cfg.get("database")
  if isinstance(raw_db, dict):
    for db_key in ("prodDatabases", "testDatabases", "prodHosts"):
      if db_key in raw_db:
        db_cfg[db_key] = _normalize_str_list(raw_db[db_key])

  ci_cfg = _normalize_ci_config(merged_cfg.get("ci"))

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
        merged = list(db_cfg[db_key])
        for item in items:
          if item not in merged:
            merged.append(item)
        db_cfg[db_key] = merged
        env_applied.append(env_var)

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

  if env_applied:
    env_label = "env (" + ", ".join(env_applied) + ")"
    source = (source + " + " + env_label) if source else env_label

  return {
      "models": models,
      "ci": ci_cfg,
      "database": db_cfg,
      "state": state_cfg,
  }, source


def load_database_config(payload):
  """Loads database isolation rules from workspace config, global config, and env."""
  db_cfg = {
      "prodDatabases": [],
      "testDatabases": [],
      "prodHosts": [],
  }

  candidates = []
  workspace_paths = payload.get("workspacePaths") or []
  for ws in workspace_paths:
    candidates.append(os.path.join(ws, ".workflow", "config.json"))
  if not workspace_paths and not is_plugin_dir(os.getcwd()):
    candidates.append(os.path.join(os.getcwd(), ".workflow", "config.json"))
  candidates.append(os.path.expanduser("~/.gemini/config/makeflow.json"))

  for cfg_path in candidates:
    if os.path.isfile(cfg_path):
      try:
        with open(cfg_path, "r", encoding="utf-8") as f:
          loaded = json.load(f)
        if isinstance(loaded, dict):
          if isinstance(loaded.get("database"), dict):
            raw_db = loaded["database"]
            for key in ("prodDatabases", "testDatabases", "prodHosts"):
              if key in raw_db:
                db_cfg[key] = _normalize_str_list(raw_db[key])
          break
      except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        pass

  for env_var, key in DB_ENV_MAPPINGS.items():
    vals = _parse_csv_env(env_var)
    if vals:
      merged = list(db_cfg[key])
      for v in vals:
        if v not in merged:
          merged.append(v)
      db_cfg[key] = merged

  return db_cfg


def is_ticket_active(state_dir):
  """Checks whether an active in-progress ticket exists in state_dir."""
  if not state_dir or not os.path.isdir(state_dir):
    return False
  state_files = [f for f in os.listdir(state_dir) if f.endswith(".md")]
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
  """Finds the active state directory from workspacePaths or artifactDirectoryPath."""
  workspace_paths = payload.get("workspacePaths") or []
  for ws in workspace_paths:
    candidate = os.path.join(ws, ".workflow", "state")
    if os.path.isdir(candidate):
      return candidate

  artifact_dir = payload.get("artifactDirectoryPath", "")
  if artifact_dir:
    brain_state = os.path.join(artifact_dir, "state")
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


def resolve_workflow_config(workspace_dir=None):
  """Resolves workflow configuration from workspace, global path, or environment.

  Args:
    workspace_dir: Optional project root directory path.

  Returns:
    Normalized workflow configuration dictionary including ``configSource``,
    ``envOverrides``, and ``agyModelTiers``.
  """
  ws = workspace_dir if workspace_dir is not None else resolve_workspace_dir({})
  local_cfg_path = os.path.join(ws, ".workflow", "config.json") if ws else None
  global_cfg_path = os.path.expanduser("~/.gemini/config/makeflow.json")

  config = load_default_config()
  config["configSource"] = "defaults"
  config["envOverrides"] = []

  candidates = [p for p in (local_cfg_path, global_cfg_path) if p]
  for cfg_path in candidates:
    if os.path.isfile(cfg_path):
      try:
        with open(cfg_path, "r", encoding="utf-8") as f:
          loaded = json.load(f)
        if isinstance(loaded, dict):
          config = deep_merge(config, loaded)
          if "database" in config and isinstance(config["database"], dict):
            for db_key in ("prodDatabases", "testDatabases", "prodHosts"):
              if db_key in config["database"]:
                config["database"][db_key] = _normalize_str_list(
                    config["database"][db_key]
                )
          config["configSource"] = cfg_path
          break
      except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        pass

  config["ci"] = _normalize_ci_config(config.get("ci"))
  config["review"] = _normalize_review_config(config.get("review"))
  config["testing"] = _normalize_testing_config(config.get("testing"))

  for env_var, agent_key in ENV_MAPPINGS.items():
    val = os.getenv(env_var)
    if val:
      config["models"][agent_key] = val.strip()
      config["envOverrides"].append(f"{env_var}={val.strip()}")

  for env_var, db_key in DB_ENV_MAPPINGS.items():
    vals = _parse_csv_env(env_var)
    if vals:
      merged = list(config["database"][db_key])
      for v in vals:
        if v not in merged:
          merged.append(v)
      config["database"][db_key] = merged
      config["envOverrides"].append(f"{env_var}={','.join(vals)}")

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

  config["agyModelTiers"] = {
      role: map_to_agy_tier(val) for role, val in config["models"].items()
  }
  return config
