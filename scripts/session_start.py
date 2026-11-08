#!/usr/bin/env python3
"""SessionStart hook script for Antigravity.

Reads Memento state cards and user configuration from the active workspace.
"""

import copy
import json
import os
import sys

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


def _parse_positive_int(val, default):
  if isinstance(val, int) and val > 0:
    return val
  if isinstance(val, str) and val.strip().isdigit():
    num = int(val.strip())
    if num > 0:
      return num
  return default


def _normalize_str_list(items):
  if not isinstance(items, list):
    return []
  return [x.strip() for x in items if isinstance(x, str) and x.strip()]


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

  ci_cfg = {
      "pollIntervalSeconds": 30,
      "quiescenceSeconds": 180,
  }
  raw_ci = merged_cfg.get("ci")
  if isinstance(raw_ci, dict):
    if "pollIntervalSeconds" in raw_ci:
      ci_cfg["pollIntervalSeconds"] = _parse_positive_int(
          raw_ci["pollIntervalSeconds"], 30
      )
    if "quiescenceSeconds" in raw_ci:
      ci_cfg["quiescenceSeconds"] = _parse_positive_int(
          raw_ci["quiescenceSeconds"], 180
      )

  env_applied = []
  for env_var, agent_key in ENV_MAPPINGS.items():
    val = os.getenv(env_var)
    if val and val.strip():
      models[agent_key] = val.strip()
      env_applied.append(env_var)

  for env_var, ci_key in CI_ENV_MAPPINGS.items():
    val = os.getenv(env_var)
    if val and val.strip().isdigit():
      num = int(val.strip())
      if num > 0:
        ci_cfg[ci_key] = num
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
    except (OSError, UnicodeDecodeError):
      pass

  return True


def check_simple_english(workspace_paths):
  candidates = [
      os.path.expanduser("~/.gemini/config/plugins/simple-english"),
      os.path.expanduser("~/.gemini/plugins/simple-english"),
  ]
  for ws in workspace_paths:
    candidates.append(os.path.join(ws, "plugins", "simple-english"))
    candidates.append(os.path.join(ws, ".agents", "plugins", "simple-english"))
  for p in candidates:
    if os.path.isdir(p) and (
        os.path.exists(os.path.join(p, "plugin.json"))
        or os.path.exists(os.path.join(p, "skills", "simple-english", "SKILL.md"))
    ):
      return True
  return False


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


def set_dynamic_terminal_title(repo_root):
  """Attempts to set terminal title for active ticket; ignores missing module or tty errors."""
  try:
    from terminal_title import set_terminal_title
    set_terminal_title(repo_root=repo_root)
  except (ImportError, OSError):
    pass


def main():
  try:
    input_text = sys.stdin.read()
    payload = json.loads(input_text) if input_text.strip() else {}
    if not isinstance(payload, dict):
      payload = {}

    workspace_paths = payload.get("workspacePaths")
    if not isinstance(workspace_paths, list):
      workspace_paths = []
    workspace_paths = [p for p in workspace_paths if isinstance(p, str) and p]

    artifact_dir = payload.get("artifactDirectoryPath", "")
    if not isinstance(artifact_dir, str):
      artifact_dir = ""

    state_dir = find_state_dir(workspace_paths, artifact_dir)
    cfg, cfg_source = get_workflow_config(workspace_paths)
    models = cfg.get("models") if isinstance(cfg.get("models"), dict) else {}
    critic_model = models.get("adversarial-critic", "inherit")
    tdd_model = models.get("tdd-worker", "inherit")

    notes = []
    if is_ticket_active(state_dir):
      state_files = sorted(f for f in os.listdir(state_dir) if f.endswith(".md"))
      notes.append(
          f"Makefile Workflow found {len(state_files)} state card(s) in {state_dir}. "
          "Read all state cards at Target 00 (read-init-d) before taking new action."
      )
      ws_root = workspace_paths[0] if workspace_paths else os.getcwd()
      set_dynamic_terminal_title(ws_root)
    else:
      notes.append(
          "Makefile Workflow: Workspace is clean and ready for Target 10 intake."
      )

    if cfg_source:
      ci_cfg = cfg.get("ci") if isinstance(cfg.get("ci"), dict) else {}
      quiescence_sec = ci_cfg.get("quiescenceSeconds", 180)
      poll_interval_sec = ci_cfg.get("pollIntervalSeconds", 30)
      ci_summary = f", ci.quiescence={quiescence_sec}s, ci.poll={poll_interval_sec}s"
      db_cfg = cfg.get("database") if isinstance(cfg.get("database"), dict) else {}
      prod_dbs = db_cfg.get("prodDatabases") or []
      test_dbs = db_cfg.get("testDatabases") or []
      prod_hosts = db_cfg.get("prodHosts") or []
      db_summary = ""
      if prod_dbs or test_dbs or prod_hosts:
        db_summary = (
            f", prodDatabases={prod_dbs}, testDatabases={test_dbs}, prodHosts={prod_hosts}"
        )
      notes.append(
          f"Makefile Workflow loaded configuration from {cfg_source} "
          f"(critic={critic_model}, tdd={tdd_model}{ci_summary}{db_summary})."
      )

    has_se = check_simple_english(workspace_paths)
    if not has_se:
      notes.append(
          "Makefile Workflow note: simple-english plugin is recommended for "
          "ASD-STE100 Simplified Technical English compliance but was not found in "
          "~/.gemini/config/plugins/simple-english."
      )

    if notes:
      output = {
          "injectSteps": [{"systemMessage": {"systemMessage": " ".join(notes)}}]
      }
      print(json.dumps(output))
      return

    print(json.dumps({}))
  except Exception:
    print(json.dumps({}))


if __name__ == "__main__":
  main()
