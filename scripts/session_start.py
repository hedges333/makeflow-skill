#!/usr/bin/env python3
"""SessionStart hook script for Antigravity.

Reads Memento state cards and user configuration from the active workspace.
"""

import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.config import (  # noqa: E402
    CI_ENV_MAPPINGS,
    DB_ENV_MAPPINGS,
    ENV_MAPPINGS,
    _normalize_str_list,
    _parse_positive_int,
    deep_merge,
    find_state_dir,
    get_workflow_config,
    is_plugin_dir,
    is_ticket_active,
    load_default_config,
)


# Built-in technical English specifications live in skills/makeflow/references/resources/language/english/


def set_dynamic_terminal_title(repo_root):
  """Attempts to set terminal title for active ticket; ignores missing module or tty errors."""
  try:
    from lib.terminal_title import set_terminal_title

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
