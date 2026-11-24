"""SessionStart hook runner implementing BaseScript (SCAR-ARCH-05).

Reads Memento state cards and user configuration from the active workspace
and emits a JSON system message payload for Antigravity SessionStart hooks.
"""

import json
import os
from typing import Any, Dict, List, Optional, Sequence, TextIO

from lib.base_script import BaseScript, SilentArgumentParser
from lib.config import (
    find_state_dir,
    get_workflow_config,
    is_plugin_dir,
    is_ticket_active,
)
from lib.parking_state import ParkingStateStore, format_parking_status_label
from lib.ticket_state import TICKET_JSON_FILENAME
from lib.workflow_gates import refresh_state_makefile_if_ignored


def set_dynamic_terminal_title(repo_root: str) -> None:
  """Sets the dynamic terminal title for an active ticket workspace.

  Ignores missing terminal title module or terminal I/O errors while letting
  unexpected programming errors propagate.

  Args:
    repo_root: Absolute or relative workspace root directory.
  """
  try:
    from lib.terminal_title import set_terminal_title

    set_terminal_title(repo_root=repo_root)
  except (ImportError, OSError):
    pass


class SessionStartScript(BaseScript):
  """CLI runner for the Makeflow SessionStart hook (SCAR-ARCH-05)."""

  def parse_args(self, args: Optional[List[str]] = None) -> None:
    """Parses CLI flags without consuming stdin.

    Args:
      args: Optional list of CLI arguments. Defaults to an empty list when None.
    """
    super().parse_args(args)
    parser = SilentArgumentParser(
        prog="session_start.py",
        description=(
            "SessionStart hook runner that restores Makeflow Memento state "
            "cards and workflow configuration from a JSON payload on stdin."
        ),
    )
    parser.parse_args(self.raw_args)

  def _read_payload(self) -> Dict[str, Any]:
    """Reads and decodes the JSON hook payload from ``self.stdin``.

    Returns:
      Decoded dictionary payload, or an empty dictionary when stdin is blank
      or decodes to a non-dict JSON value.
    """
    input_text = self.stdin.read()
    payload = json.loads(input_text) if input_text.strip() else {}
    if not isinstance(payload, dict):
      return {}
    return payload

  def _build_notes(
      self, workspace_paths: List[str], artifact_dir: str
  ) -> List[str]:
    """Refreshes ignored state Makefiles and builds SessionStart system notes.

    Args:
      workspace_paths: Non-empty workspace root directories from the payload.
      artifact_dir: Optional artifact directory path from the payload.

    Returns:
      List of human-readable status note strings to inject into the session.
    """
    candidate_roots = (
        workspace_paths
        if workspace_paths
        else ([os.getcwd()] if not is_plugin_dir(os.getcwd()) else [])
    )
    for root in candidate_roots:
      refresh_state_makefile_if_ignored(root)

    state_dir = find_state_dir(workspace_paths, artifact_dir)
    cfg, cfg_source = get_workflow_config(workspace_paths)
    models = cfg.get("models") if isinstance(cfg.get("models"), dict) else {}
    critic_model = models.get("adversarial-critic", "inherit")
    tdd_model = models.get("tdd-worker", "inherit")

    notes: List[str] = []
    if is_ticket_active(state_dir):
      state_files = sorted(
          f
          for f in os.listdir(state_dir)
          if f.endswith(".md") or f == TICKET_JSON_FILENAME
      )
      notes.append(
          f"Makefile Workflow found {len(state_files)} state card(s) in "
          f"{state_dir}. Read all state cards at Target 00 (read-init-d) "
          "before taking new action."
      )
      parking = ParkingStateStore(state_dir).load()
      if parking is not None and parking.parked:
        label = format_parking_status_label(parking)
        pr_info = f" (PR #{parking.pr_number})" if parking.pr_number else ""
        notes.append(
            f"Session Status: {label}{pr_info}. Run "
            "'make -f .workflow/state/Makefile resume-parked' at Target 00 "
            "to re-check status."
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
      ci_summary = (
          f", ci.quiescence={quiescence_sec}s, ci.poll={poll_interval_sec}s"
      )
      db_cfg = (
          cfg.get("database") if isinstance(cfg.get("database"), dict) else {}
      )
      prod_dbs = db_cfg.get("prodDatabases") or []
      test_dbs = db_cfg.get("testDatabases") or []
      prod_hosts = db_cfg.get("prodHosts") or []
      db_summary = ""
      if prod_dbs or test_dbs or prod_hosts:
        db_summary = (
            f", prodDatabases={prod_dbs}, testDatabases={test_dbs}, "
            f"prodHosts={prod_hosts}"
        )
      notes.append(
          f"Makefile Workflow loaded configuration from {cfg_source} "
          f"(critic={critic_model}, tdd={tdd_model}{ci_summary}{db_summary})."
      )

    return notes

  def run(self) -> int:
    """Executes the SessionStart hook fail-open lifecycle.

    Returns:
      Always returns ``0`` so hook errors never block session startup.
    """
    try:
      payload = self._read_payload()

      workspace_paths = payload.get("workspacePaths")
      if not isinstance(workspace_paths, list):
        workspace_paths = []
      workspace_paths = [
          p for p in workspace_paths if isinstance(p, str) and p
      ]

      artifact_dir = payload.get("artifactDirectoryPath", "")
      if not isinstance(artifact_dir, str):
        artifact_dir = ""

      notes = self._build_notes(workspace_paths, artifact_dir)
      if notes:
        output = {
            "injectSteps": [
                {"systemMessage": {"systemMessage": " ".join(notes)}}
            ]
        }
        self.stdout.write(json.dumps(output) + "\n")
        return 0

      self.stdout.write(json.dumps({}) + "\n")
      return 0
    except Exception:
      self.stdout.write(json.dumps({}) + "\n")
      return 0


def main(
    args: Optional[Sequence[str]] = None,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
    stdin: Optional[TextIO] = None,
) -> int:
  """Executes ``SessionStartScript`` with explicit or default streams.

  Defaults ``args`` to ``[]`` when omitted so direct Python callers (such as
  unit tests) do not accidentally parse test-runner flags from ``sys.argv``.

  Args:
    args: Optional CLI arguments sequence. Defaults to ``[]``.
    stdout: Optional output stream. Defaults to ``sys.stdout``.
    stderr: Optional error stream. Defaults to ``sys.stderr``.
    stdin: Optional input stream. Defaults to ``sys.stdin``.

  Returns:
    Integer exit code returned by ``SessionStartScript.execute()``.
  """
  return SessionStartScript().execute(
      args=[] if args is None else list(args),
      stdout=stdout,
      stderr=stderr,
      stdin=stdin,
  )
