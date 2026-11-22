#!/usr/bin/env python3
"""Unit tests for PreToolUse transcript turn-boundary guard (GH-186, SCAR-PROC-102)."""

import contextlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
  sys.path.insert(0, REPO_ROOT)

from lib.turn_boundary import (
    ACTIVE_SUBAGENT_FILENAME,
    MAKEFLOW_PHASE_WORKERS,
    SUPERVISOR_INSPECTION_TOOLS,
    clear_active_subagent,
    evaluate_subagent_airlock,
    is_human_user_input_step,
    is_subagent_wakeup_step,
    read_active_subagent,
    read_gate_entry,
    record_active_subagent,
    step_enters_gate,
)
from scripts.pre_tool_guard import (
    GOVERNANCE_FILE_RELPATHS,
    evaluate_self_modification_safety,
    evaluate_tool_call,
)


def _write_transcript(path, steps):
  with open(path, "w", encoding="utf-8") as f:
    for step in steps:
      f.write(json.dumps(step) + "\n")


@contextlib.contextmanager
def _isolated_guard_env():
  """Isolates HOME, cwd, and MAKEFILE_WORKFLOW_* variables for guard tests (GH-269).

  Yields:
    Tuple of ``(hook_cwd, fake_home, clean_env)``.
  """
  with (
      tempfile.TemporaryDirectory() as hook_cwd,
      tempfile.TemporaryDirectory() as fake_home,
  ):
    clean_env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("MAKEFILE_WORKFLOW_")
    }
    clean_env["HOME"] = fake_home
    with (
        mock.patch.dict(os.environ, clean_env, clear=True),
        mock.patch("os.getcwd", return_value=hook_cwd),
    ):
      yield hook_cwd, fake_home, clean_env


class TestPreToolGuardTurnBoundary(unittest.TestCase):
  """Tests for PreToolUse turn-boundary enforcement across all 6 pause gates."""

  def test_hooks_json_matcher_includes_call_mcp_tool(self):
    hooks_path = os.path.join(REPO_ROOT, "hooks.json")
    with open(hooks_path, "r", encoding="utf-8") as f:
      data = json.load(f)
    pre_hooks = data["makeflow-guard"]["PreToolUse"]
    self.assertEqual(len(pre_hooks), 1)
    self.assertEqual(
        pre_hooks[0]["matcher"],
        "invoke_subagent|read_file|write_file|edit_file|run_command|call_mcp_tool|manage_subagents|ask_question",
    )

  def test_gh145_same_turn_t70_t75_t80_and_merge_denied(self):
    """Reproduces the exact GH-145 step 151 -> 155 trajectory in isolation (GH-269)."""
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      state_dir = os.path.join(ws_dir, ".workflow", "state")
      os.makedirs(state_dir)
      with open(
          os.path.join(state_dir, "70_bot_triage.md"), "w", encoding="utf-8"
      ) as f:
        f.write("# 70_bot_triage\n")

      transcript_path = os.path.join(ws_dir, "transcript.jsonl")
      steps = [
          {
              "step_index": 118,
              "type": "USER_INPUT",
              "content": "Approved. Proceed to push.",
          },
          {
              "step_index": 151,
              "type": "PLANNER_RESPONSE",
              "content": "Advancing to Target 75.",
              "tool_calls": [
                  {
                      "id": "call_151",
                      "name": "call_mcp_tool",
                      "args": {
                          "ServerName": json.dumps("makeflow_makeflow"),
                          "ToolName": json.dumps("workflow_advance_target"),
                          "Arguments": json.dumps({
                              "currentTarget": "T70",
                              "nextTarget": "T75",
                              "workspaceDir": ws_dir,
                          }),
                      },
                  },
                  {
                      "id": "call_151_write",
                      "name": "write_file",
                      "args": {
                          "Path": json.dumps(
                              os.path.join(state_dir, "99_next_action.md")
                          ),
                          "Content": json.dumps(
                              "- Active Target: Target 75 (ready-to-land)\n"
                              "human-landing-approval\n"
                          ),
                      },
                  },
              ],
          },
          {
              "step_index": 155,
              "type": "PLANNER_RESPONSE",
              "content": (
                  "### Target 75 (`ready-to-land`) — Landing Approval Proposal\n"
                  "Reply to approve landing (`human-landing-approval`) so we can"
                  " merge PR #185."
              ),
              "tool_calls": [
                  {
                      "id": "call_155_read",
                      "name": "read_file",
                      "args": {
                          "Path": json.dumps(
                              os.path.join(state_dir, "70_bot_triage.md")
                          ),
                      },
                  },
                  {
                      "id": "call_155_adv",
                      "name": "call_mcp_tool",
                      "args": {
                          "ServerName": json.dumps("makeflow_makeflow"),
                          "ToolName": json.dumps("workflow_advance_target"),
                          "Arguments": json.dumps({
                              "currentTarget": "T75",
                              "nextTarget": "T80",
                              "evidence": {"humanLandingApproved": True},
                              "workspaceDir": ws_dir,
                          }),
                      },
                  },
                  {
                      "id": "call_155_merge",
                      "name": "run_command",
                      "args": {
                          "CommandLine": json.dumps(
                              "gh pr merge 185 --squash --delete-branch"
                          ),
                      },
                  },
              ],
          },
      ]
      _write_transcript(transcript_path, steps)

      mcp_res = evaluate_tool_call({
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "call_mcp_tool",
              "args": {
                  "ServerName": "makeflow_makeflow",
                  "ToolName": "workflow_advance_target",
                  "Arguments": {
                      "currentTarget": "T75",
                      "nextTarget": "T80",
                      "evidence": {"humanLandingApproved": True},
                      "workspaceDir": ws_dir,
                  },
              },
          },
      })
      self.assertEqual(mcp_res["decision"], "deny")
      self.assertIn("SCAR-PROC-102", mcp_res["reason"])

      merge_res = evaluate_tool_call({
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": "gh pr merge 185 --squash --delete-branch",
              },
          },
      })
      self.assertEqual(merge_res["decision"], "deny")
      self.assertIn("SCAR-PROC-102", merge_res["reason"])

  def test_all_six_pause_gate_exits_blocked_without_intervening_user_input(self):
    """Verifies all six pause-gate exits block without intervening USER_INPUT in isolation (GH-269)."""
    gate_cases = [
        ("T15", "T20", "T30", "humanPlanApproved"),
        ("T40", "T42", "T30", "humanBlockerTriageApproved"),
        ("T40", "T45", "T50", "humanReviewApproved"),
        ("T45", "T50", "T60", "humanCommitApproved"),
        ("T50", "T60", "T70", "humanPushApproved"),
        ("T70", "T75", "T80", "humanLandingApproved"),
    ]
    for from_t, gate_t, next_t, ev_key in gate_cases:
      with self.subTest(gate=gate_t, transition=f"{gate_t}->{next_t}"):
        with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
          state_dir = os.path.join(ws_dir, ".workflow", "state")
          os.makedirs(state_dir)
          transcript_path = os.path.join(ws_dir, "transcript.jsonl")

          # Same-turn exit without intervening USER_INPUT
          _write_transcript(
              transcript_path,
              [
                  {"step_index": 1, "type": "USER_INPUT", "content": "Go"},
                  {
                      "step_index": 2,
                      "type": "PLANNER_RESPONSE",
                      "content": f"Entering {gate_t}",
                      "tool_calls": [{
                          "name": "call_mcp_tool",
                          "args": {
                              "ServerName": "makeflow_makeflow",
                              "ToolName": "workflow_advance_target",
                              "Arguments": {
                                  "currentTarget": from_t,
                                  "nextTarget": gate_t,
                                  "workspaceDir": ws_dir,
                              },
                          },
                      }],
                  },
              ],
          )
          deny_res = evaluate_tool_call({
              "transcriptPath": transcript_path,
              "workspacePaths": [ws_dir],
              "toolCall": {
                  "name": "call_mcp_tool",
                  "args": {
                      "ServerName": "makeflow_makeflow",
                      "ToolName": "workflow_advance_target",
                      "Arguments": {
                          "currentTarget": gate_t,
                          "nextTarget": next_t,
                          "evidence": {ev_key: True},
                          "workspaceDir": ws_dir,
                      },
                  },
              },
          })
          self.assertEqual(deny_res["decision"], "deny")
          self.assertIn("SCAR-PROC-102", deny_res["reason"])

          # Now append an intervening USER_INPUT step and retry
          _write_transcript(
              transcript_path,
              [
                  {"step_index": 1, "type": "USER_INPUT", "content": "Go"},
                  {
                      "step_index": 2,
                      "type": "PLANNER_RESPONSE",
                      "content": f"Entering {gate_t}",
                      "tool_calls": [{
                          "name": "call_mcp_tool",
                          "args": {
                              "ServerName": "makeflow_makeflow",
                              "ToolName": "workflow_advance_target",
                              "Arguments": {
                                  "currentTarget": from_t,
                                  "nextTarget": gate_t,
                                  "workspaceDir": ws_dir,
                              },
                          },
                      }],
                  },
                  {
                      "step_index": 3,
                      "type": "USER_INPUT",
                      "content": "Approved.",
                  },
                  {
                      "step_index": 4,
                      "type": "PLANNER_RESPONSE",
                      "content": f"Advancing {gate_t} -> {next_t}",
                      "tool_calls": [{
                          "name": "call_mcp_tool",
                          "args": {
                              "ServerName": "makeflow_makeflow",
                              "ToolName": "workflow_advance_target",
                              "Arguments": {
                                  "currentTarget": gate_t,
                                  "nextTarget": next_t,
                                  "evidence": {ev_key: True},
                                  "workspaceDir": ws_dir,
                              },
                          },
                      }],
                  },
              ],
          )
          allow_res = evaluate_tool_call({
              "transcriptPath": transcript_path,
              "workspacePaths": [ws_dir],
              "toolCall": {
                  "name": "call_mcp_tool",
                  "args": {
                      "ServerName": "makeflow_makeflow",
                      "ToolName": "workflow_advance_target",
                      "Arguments": {
                          "currentTarget": gate_t,
                          "nextTarget": next_t,
                          "evidence": {ev_key: True},
                          "workspaceDir": ws_dir,
                      },
                  },
              },
          })
          self.assertEqual(allow_res["decision"], "allow")

  def test_target_20_artifact_approval_allows_t20_to_t30(self):
    """Verifies Target 20 artifact approval allows T20->T30 in isolated env (GH-269)."""
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      state_dir = os.path.join(ws_dir, ".workflow", "state")
      os.makedirs(state_dir)
      transcript_path = os.path.join(ws_dir, "transcript.jsonl")
      _write_transcript(
          transcript_path,
          [
              {"step_index": 1, "type": "USER_INPUT", "content": "Fix GH-186"},
              {
                  "step_index": 2,
                  "type": "PLANNER_RESPONSE",
                  "content": "Target 20 plan ready.",
                  "tool_calls": [{
                      "name": "call_mcp_tool",
                      "args": {
                          "ServerName": "makeflow_makeflow",
                          "ToolName": "workflow_advance_target",
                          "Arguments": {
                              "currentTarget": "T15",
                              "nextTarget": "T20",
                              "workspaceDir": ws_dir,
                          },
                      },
                  }],
              },
              {
                  "step_index": 3,
                  "type": "SYSTEM_MESSAGE",
                  "content": "Feedback on plan.md: [Approved]",
              },
          ],
      )
      res = evaluate_tool_call({
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "call_mcp_tool",
              "args": {
                  "ServerName": "makeflow_makeflow",
                  "ToolName": "workflow_advance_target",
                  "Arguments": {
                      "currentTarget": "T20",
                      "nextTarget": "T30",
                      "evidence": {"humanPlanApproved": True},
                      "workspaceDir": ws_dir,
                  },
              },
          },
      })
      self.assertEqual(res["decision"], "allow")

  def test_pause_gated_commands_denied_in_same_turn_and_force_ask_after_user_input(self):
    """Verifies pause-gated commands deny same-turn and force_ask after input in isolation (GH-269)."""
    cmd_cases = [
        (
            "T45",
            "T50",
            "git commit -F .workflow/state/50_commit_msg.txt",
            "Target 50 Commit Gate",
        ),
        (
            "T50",
            "T60",
            "git push -u origin fix/186-test",
            "Target 60 Push Gate",
        ),
        (
            "T70",
            "T75",
            "gh pr merge 186 --squash --delete-branch",
            "Target 75 Landing Gate",
        ),
    ]
    for from_t, gate_t, cmd_str, expected_gate_label in cmd_cases:
      with self.subTest(gate=gate_t, cmd=cmd_str):
        with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
          state_dir = os.path.join(ws_dir, ".workflow", "state")
          os.makedirs(state_dir)
          with open(
              os.path.join(state_dir, "45_review_approved.md"),
              "w",
              encoding="utf-8",
          ) as f:
            f.write("# Approved\n")
          transcript_path = os.path.join(ws_dir, "transcript.jsonl")

          # Same turn as entering gate_t -> deny
          _write_transcript(
              transcript_path,
              [
                  {"step_index": 1, "type": "USER_INPUT", "content": "Proceed"},
                  {
                      "step_index": 2,
                      "type": "PLANNER_RESPONSE",
                      "content": f"Entering {gate_t}",
                      "tool_calls": [{
                          "name": "call_mcp_tool",
                          "args": {
                              "ServerName": "makeflow_makeflow",
                              "ToolName": "workflow_advance_target",
                              "Arguments": {
                                  "currentTarget": from_t,
                                  "nextTarget": gate_t,
                                  "workspaceDir": ws_dir,
                              },
                          },
                      }],
                  },
              ],
          )
          deny_res = evaluate_tool_call({
              "transcriptPath": transcript_path,
              "workspacePaths": [ws_dir],
              "toolCall": {
                  "name": "run_command",
                  "args": {"CommandLine": cmd_str},
              },
          })
          self.assertEqual(deny_res["decision"], "deny")
          self.assertIn("SCAR-PROC-102", deny_res["reason"])

          # After intervening USER_INPUT -> force_ask
          _write_transcript(
              transcript_path,
              [
                  {"step_index": 1, "type": "USER_INPUT", "content": "Proceed"},
                  {
                      "step_index": 2,
                      "type": "PLANNER_RESPONSE",
                      "content": f"Entering {gate_t}",
                      "tool_calls": [{
                          "name": "call_mcp_tool",
                          "args": {
                              "ServerName": "makeflow_makeflow",
                              "ToolName": "workflow_advance_target",
                              "Arguments": {
                                  "currentTarget": from_t,
                                  "nextTarget": gate_t,
                                  "workspaceDir": ws_dir,
                              },
                          },
                      }],
                  },
                  {
                      "step_index": 3,
                      "type": "USER_INPUT",
                      "content": "Approved.",
                  },
              ],
          )
          ask_res = evaluate_tool_call({
              "transcriptPath": transcript_path,
              "workspacePaths": [ws_dir],
              "toolCall": {
                  "name": "run_command",
                  "args": {"CommandLine": cmd_str},
              },
          })
          self.assertEqual(ask_res["decision"], "force_ask")
          self.assertIn(expected_gate_label, ask_res["reason"])

  def test_gate_entry_json_recorded_and_protected_from_self_modification(self):
    """Verifies gate_entry.json persistence and self-modification protection in isolation (GH-269)."""
    self.assertIn("lib/turn_boundary.py", GOVERNANCE_FILE_RELPATHS)
    self.assertIn(".workflow/state/gate_entry.json", GOVERNANCE_FILE_RELPATHS)

    for bad_cmd in (
        "rm -f .workflow/state/gate_entry.json",
        "echo '{}' > .workflow/state/gate_entry.json",
        "sed -i 's/False/True/' lib/turn_boundary.py",
    ):
      safe, reason = evaluate_self_modification_safety(bad_cmd)
      self.assertFalse(safe)
      self.assertIn("governance file", reason)

    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      state_dir = os.path.join(ws_dir, ".workflow", "state")
      os.makedirs(state_dir)
      transcript_path = os.path.join(ws_dir, "transcript.jsonl")
      _write_transcript(
          transcript_path,
          [{"step_index": 5, "type": "USER_INPUT", "content": "Start"}],
      )
      res = evaluate_tool_call({
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "call_mcp_tool",
              "args": {
                  "ServerName": "makeflow_makeflow",
                  "ToolName": "workflow_advance_target",
                  "Arguments": {
                      "currentTarget": "T70",
                      "nextTarget": "T75",
                      "workspaceDir": ws_dir,
                  },
              },
          },
      })
      self.assertEqual(res["decision"], "allow")
      gate_entry_file = os.path.join(state_dir, "gate_entry.json")
      self.assertTrue(os.path.isfile(gate_entry_file))
      with open(gate_entry_file, "r", encoding="utf-8") as f:
        entry = json.load(f)
      self.assertEqual(entry["gate"], "T75")
      self.assertEqual(entry["fromTarget"], "T70")
      self.assertFalse(entry["humanTurnVerified"])

  def test_declarative_targets_config_and_no_approval_token_scraping(self):
    import lib.turn_boundary as turn_boundary

    self.assertFalse(hasattr(turn_boundary, "_GATE_APPROVAL_TOKENS"))
    self.assertTrue(hasattr(turn_boundary, "DEFAULT_TARGETS_CONFIG"))

    default_cfg_path = os.path.join(REPO_ROOT, "default_config.json")
    with open(default_cfg_path, "r", encoding="utf-8") as f:
      cfg = json.load(f)
    self.assertIn("targets", cfg)
    targets = cfg["targets"]
    for tid in (
        "T00",
        "T10",
        "T15",
        "T20",
        "T30",
        "T40",
        "T42",
        "T45",
        "T50",
        "T60",
        "T70",
        "T75",
        "T80",
        "T90",
    ):
      self.assertIn(tid, targets)
      for key in (
          "next",
          "recoveryNext",
          "pauseGate",
          "evidenceKey",
          "entryCards",
          "gatedCommands",
      ):
        self.assertIn(key, targets[tid])

  def test_blocker1_post_approval_checklist_and_prose_do_not_false_positive_deny(
      self,
  ):
    """Regression test for BLOCKER-1 under isolated guard environment (GH-269)."""
    checklist_body = (
        "# 99_next_action: Live Memento Readerboard\n"
        "- Active Target: Target 50 (commit)\n"
        "- Last Completed Step: Target 45 (approved-review)\n\n"
        "## Milestone Progress\n"
        "- [x] T20: approved-plan\n"
        "- [x] T42: triaged-blockers\n"
        "- [x] T45: approved-review\n"
        "- [>] T50: commit\n"
        "- [ ] T60: push\n"
        "- [ ] T75: ready-to-land\n"
    )
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      state_dir = os.path.join(ws_dir, ".workflow", "state")
      os.makedirs(state_dir)
      with open(
          os.path.join(state_dir, "45_review_approved.md"),
          "w",
          encoding="utf-8",
      ) as f:
        f.write("# Approved\n")
      transcript_path = os.path.join(ws_dir, "transcript.jsonl")

      # Step 1: Enter T45; Step 2: USER_INPUT approves; Step 3: PLANNER_RESPONSE
      # mentions Target 45/50/75 in content, writes 99_next_action.md with checklist,
      # and calls T45 -> T50.
      _write_transcript(
          transcript_path,
          [
              {
                  "step_index": 1,
                  "type": "PLANNER_RESPONSE",
                  "content": "Entering Target 45.",
                  "tool_calls": [{
                      "name": "call_mcp_tool",
                      "args": {
                          "ServerName": "makeflow_makeflow",
                          "ToolName": "workflow_advance_target",
                          "Arguments": {
                              "currentTarget": "T40",
                              "nextTarget": "T45",
                              "workspaceDir": ws_dir,
                          },
                      },
                  }],
              },
              {
                  "step_index": 2,
                  "type": "USER_INPUT",
                  "content": "Approved.",
              },
              {
                  "step_index": 3,
                  "type": "PLANNER_RESPONSE",
                  "content": (
                      "Target 45 (`approved-review`) is approved via"
                      " human-review-approval. Advancing to Target 50."
                  ),
                  "tool_calls": [
                      {
                          "name": "write_file",
                          "args": {
                              "Path": os.path.join(
                                  state_dir, "99_next_action.md"
                              ),
                              "Content": checklist_body,
                          },
                      },
                      {
                          "name": "call_mcp_tool",
                          "args": {
                              "ServerName": "makeflow_makeflow",
                              "ToolName": "workflow_advance_target",
                              "Arguments": {
                                  "currentTarget": "T45",
                                  "nextTarget": "T50",
                                  "evidence": {"humanReviewApproved": True},
                                  "workspaceDir": ws_dir,
                              },
                          },
                      },
                  ],
              },
          ],
      )
      adv_res = evaluate_tool_call({
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "call_mcp_tool",
              "args": {
                  "ServerName": "makeflow_makeflow",
                  "ToolName": "workflow_advance_target",
                  "Arguments": {
                      "currentTarget": "T45",
                      "nextTarget": "T50",
                      "evidence": {"humanReviewApproved": True},
                      "workspaceDir": ws_dir,
                  },
              },
          },
      })
      self.assertEqual(adv_res["decision"], "allow")

      # Now test post-approval T75 -> T80 + gh pr merge with Target 75 prose and 99_next_action.md write
      _write_transcript(
          transcript_path,
          [
              {
                  "step_index": 10,
                  "type": "PLANNER_RESPONSE",
                  "content": "Entering Target 75.",
                  "tool_calls": [{
                      "name": "call_mcp_tool",
                      "args": {
                          "ServerName": "makeflow_makeflow",
                          "ToolName": "workflow_advance_target",
                          "Arguments": {
                              "currentTarget": "T70",
                              "nextTarget": "T75",
                              "workspaceDir": ws_dir,
                          },
                      },
                  }],
              },
              {
                  "step_index": 11,
                  "type": "USER_INPUT",
                  "content": "Approve landing.",
              },
              {
                  "step_index": 12,
                  "type": "PLANNER_RESPONSE",
                  "content": (
                      "Target 75 (`ready-to-land`) approved via"
                      " human-landing-approval. Advancing T75 -> T80 and"
                      " merging."
                  ),
                  "tool_calls": [
                      {
                          "name": "write_file",
                          "args": {
                              "Path": os.path.join(
                                  state_dir, "99_next_action.md"
                              ),
                              "Content": checklist_body,
                          },
                      },
                      {
                          "name": "call_mcp_tool",
                          "args": {
                              "ServerName": "makeflow_makeflow",
                              "ToolName": "workflow_advance_target",
                              "Arguments": {
                                  "currentTarget": "T75",
                                  "nextTarget": "T80",
                                  "evidence": {"humanLandingApproved": True},
                                  "workspaceDir": ws_dir,
                              },
                          },
                      },
                      {
                          "name": "run_command",
                          "args": {
                              "CommandLine": (
                                  "gh pr merge 186 --squash --delete-branch"
                              ),
                          },
                      },
                  ],
              },
          ],
      )
      t80_res = evaluate_tool_call({
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "call_mcp_tool",
              "args": {
                  "ServerName": "makeflow_makeflow",
                  "ToolName": "workflow_advance_target",
                  "Arguments": {
                      "currentTarget": "T75",
                      "nextTarget": "T80",
                      "evidence": {"humanLandingApproved": True},
                      "workspaceDir": ws_dir,
                  },
              },
          },
      })
      self.assertEqual(t80_res["decision"], "allow")

      merge_res = evaluate_tool_call({
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": "gh pr merge 186 --squash --delete-branch",
              },
          },
      })
      self.assertEqual(merge_res["decision"], "force_ask")

  def test_blocker2_gate_entry_json_enforced_in_command_boundary_and_relative_state_dir(
      self,
  ):
    """Verifies BLOCKER-2 gate_entry.json enforcement and relative stateDir resolution in isolation (GH-186, GH-269)."""
    for gate_t, cmd_str in (
        ("T50", "git commit -m 'feat: test'"),
        ("T60", "git push -u origin feat/test"),
        ("T75", "gh pr merge 186 --squash"),
    ):
      with (
          _isolated_guard_env(),
          tempfile.TemporaryDirectory() as ws_dir,
      ):
        state_dir = os.path.join(ws_dir, ".workflow", "state")
        os.makedirs(state_dir)
        transcript_path = os.path.join(ws_dir, "transcript.jsonl")
        _write_transcript(
            transcript_path,
            [{"step_index": 5, "type": "USER_INPUT", "content": "Go"}],
        )
        # Record gate entry with relative stateDir=".workflow/state" and workspaceDir=ws_dir
        from_map = {"T50": "T45", "T60": "T50", "T75": "T70"}
        evaluate_tool_call({
            "transcriptPath": transcript_path,
            "toolCall": {
                "name": "call_mcp_tool",
                "args": {
                    "ServerName": "makeflow_makeflow",
                    "ToolName": "workflow_advance_target",
                    "Arguments": {
                        "currentTarget": from_map[gate_t],
                        "nextTarget": gate_t,
                        "workspaceDir": ws_dir,
                        "stateDir": ".workflow/state",
                    },
                },
            },
        })
        gate_entry_file = os.path.join(state_dir, "gate_entry.json")
        self.assertTrue(os.path.isfile(gate_entry_file))

        # Command in same turn (even if transcript has no PLANNER_RESPONSE entry step yet) -> deny via gate_entry.json
        cmd_res = evaluate_tool_call({
            "transcriptPath": transcript_path,
            "workspacePaths": [ws_dir],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": cmd_str},
            },
        })
        self.assertEqual(cmd_res["decision"], "deny")
        self.assertIn("SCAR-PROC-102", cmd_res["reason"])

  def test_end_to_end_post_target_75_t80_t90_autonomous_completion(self):
    """Verifies T75 -> T80 -> T90 autonomous completion in isolated env (GH-269)."""
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      state_dir = os.path.join(ws_dir, ".workflow", "state")
      os.makedirs(state_dir)
      with open(
          os.path.join(state_dir, "45_review_approved.md"),
          "w",
          encoding="utf-8",
      ) as f:
        f.write("# Approved\n")
      transcript_path = os.path.join(ws_dir, "transcript.jsonl")

      # Step 1: Enter T75; Step 2: Single USER_INPUT approving landing;
      # Step 3: Autonomous turn advances T75 -> T80 -> T90 and commits/pushes history.
      _write_transcript(
          transcript_path,
          [
              {
                  "step_index": 1,
                  "type": "PLANNER_RESPONSE",
                  "content": "Entering T75",
                  "tool_calls": [{
                      "name": "call_mcp_tool",
                      "args": {
                          "ServerName": "makeflow_makeflow",
                          "ToolName": "workflow_advance_target",
                          "Arguments": {
                              "currentTarget": "T70",
                              "nextTarget": "T75",
                              "workspaceDir": ws_dir,
                          },
                      },
                  }],
              },
              {
                  "step_index": 2,
                  "type": "USER_INPUT",
                  "content": "Approved. Land it.",
              },
              {
                  "step_index": 3,
                  "type": "PLANNER_RESPONSE",
                  "content": "Advancing T75 -> T80 and merging.",
                  "tool_calls": [
                      {
                          "name": "call_mcp_tool",
                          "args": {
                              "ServerName": "makeflow_makeflow",
                              "ToolName": "workflow_advance_target",
                              "Arguments": {
                                  "currentTarget": "T75",
                                  "nextTarget": "T80",
                                  "evidence": {"humanLandingApproved": True},
                                  "workspaceDir": ws_dir,
                              },
                          },
                      },
                      {
                          "name": "run_command",
                          "args": {
                              "CommandLine": (
                                  "gh pr merge 186 --squash --delete-branch"
                              ),
                          },
                      },
                  ],
              },
              {
                  "step_index": 4,
                  "type": "PLANNER_RESPONSE",
                  "content": "Advancing T80 -> T90 and updating history.",
                  "tool_calls": [
                      {
                          "name": "call_mcp_tool",
                          "args": {
                              "ServerName": "makeflow_makeflow",
                              "ToolName": "workflow_advance_target",
                              "Arguments": {
                                  "currentTarget": "T80",
                                  "nextTarget": "T90",
                                  "workspaceDir": ws_dir,
                              },
                          },
                      },
                      {
                          "name": "run_command",
                          "args": {
                              "CommandLine": (
                                  "git commit -m 'docs(history): record GH-186'"
                              ),
                          },
                      },
                  ],
              },
          ],
      )

      for cmd_str in (
          "gh pr merge 186 --squash --delete-branch",
          "git commit -m 'docs(history): record GH-186'",
          "git commit -m 'Merge branch fix/186'",
          "git push origin main",
          "git push",
      ):
        res = evaluate_tool_call({
            "transcriptPath": transcript_path,
            "workspacePaths": [ws_dir],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": cmd_str},
            },
        })
        self.assertEqual(
            res["decision"],
            "force_ask",
            f"Expected force_ask during T80/T90 for {cmd_str!r}, got {res}",
        )

  def test_ask_question_denied_when_no_assistant_content_since_latest_user_input(
      self,
  ):
    """Denies ask_question with SCAR-HCI-08 Vector 4 Reflex in isolated env (GH-269)."""
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      transcript_path = os.path.join(ws_dir, "transcript.jsonl")
      for empty_val in ("", None, "   \n\t  ", json.dumps(""), json.dumps("  ")):
        with self.subTest(content=empty_val):
          _write_transcript(
              transcript_path,
              [
                  {
                      "step_index": 1,
                      "type": "USER_INPUT",
                      "content": "Triage GH-196",
                  },
                  {
                      "step_index": 2,
                      "type": "PLANNER_RESPONSE",
                      "content": empty_val,
                      "tool_calls": [{
                          "name": "ask_question",
                          "args": {
                              "Questions": [{
                                  "Header": "Choice",
                                  "Question": "Which option?",
                                  "Options": [
                                      {"Label": "A", "Description": "Opt A"},
                                      {"Label": "B", "Description": "Opt B"},
                                  ],
                              }]
                          },
                      }],
                  },
              ],
          )
          res = evaluate_tool_call({
              "transcriptPath": transcript_path,
              "workspacePaths": [ws_dir],
              "toolCall": {
                  "name": "ask_question",
                  "args": {
                      "Questions": [{
                          "Header": "Choice",
                          "Question": "Which option?",
                      }]
                  },
              },
          })
          self.assertEqual(res["decision"], "deny")
          reason = res.get("reason", "")
          self.assertIn("[SCAR-ID: HCI-08]", reason)
          self.assertIn("TRIGGER (THIS):", reason)
          self.assertIn("REJECTED (TRAP):", reason)
          self.assertIn("REFLEX (ACTION):", reason)
          self.assertIn("SOCRATIC PROBE:", reason)
          self.assertIn("content", reason)
          self.assertIn("file://", reason)
          self.assertIn("ask_question", reason)

  def test_ask_question_denied_when_only_prior_turn_had_assistant_content(self):
    """Denies ask_question when only a prior turn emitted content in isolated env (GH-269)."""
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      transcript_path = os.path.join(ws_dir, "transcript.jsonl")
      _write_transcript(
          transcript_path,
          [
              {
                  "step_index": 1,
                  "type": "USER_INPUT",
                  "content": "Start task",
              },
              {
                  "step_index": 2,
                  "type": "PLANNER_RESPONSE",
                  "content": "Prior turn explanation with [plan.md](file:///tmp/plan.md).",
                  "tool_calls": [],
              },
              {
                  "step_index": 3,
                  "type": "USER_INPUT",
                  "content": "Now choose an approach",
              },
              {
                  "step_index": 4,
                  "type": "PLANNER_RESPONSE",
                  "content": "",
                  "tool_calls": [{"name": "read_file", "args": {"Path": "/tmp/a"}}],
              },
              {
                  "step_index": 5,
                  "type": "PLANNER_RESPONSE",
                  "content": "",
                  "tool_calls": [{"name": "ask_question", "args": {}}],
              },
          ],
      )
      res = evaluate_tool_call({
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "ask_question",
              "args": {},
          },
      })
      self.assertEqual(res["decision"], "deny")
      self.assertIn("[SCAR-ID: HCI-08]", res.get("reason", ""))

  def test_ask_question_allowed_when_same_planner_response_step_has_content(
      self,
  ):
    """Allows ask_question when the same PLANNER_RESPONSE step has content in isolated env (GH-269)."""
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      transcript_path = os.path.join(ws_dir, "transcript.jsonl")
      for valid_content in (
          "Full markdown facts with [plan.md](file:///tmp/plan.md).",
          json.dumps("Full markdown facts with [plan.md](file:///tmp/plan.md)."),
      ):
        with self.subTest(content=valid_content):
          _write_transcript(
              transcript_path,
              [
                  {
                      "step_index": 1,
                      "type": "USER_INPUT",
                      "content": "Which layout should we use?",
                  },
                  {
                      "step_index": 2,
                      "type": "PLANNER_RESPONSE",
                      "content": valid_content,
                      "tool_calls": [{"name": "ask_question", "args": {}}],
                  },
              ],
          )
          res = evaluate_tool_call({
              "transcriptPath": transcript_path,
              "workspacePaths": [ws_dir],
              "toolCall": {
                  "name": "ask_question",
                  "args": {},
              },
          })
          self.assertEqual(res["decision"], "allow")

  def test_ask_question_allowed_when_earlier_step_in_same_turn_emitted_content(
      self,
  ):
    """Allows ask_question when an earlier step in the turn emitted content in isolated env (GH-269)."""
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      transcript_path = os.path.join(ws_dir, "transcript.jsonl")
      _write_transcript(
          transcript_path,
          [
              {
                  "step_index": 1,
                  "type": "USER_INPUT",
                  "content": "Inspect and ask",
              },
              {
                  "step_index": 2,
                  "type": "PLANNER_RESPONSE",
                  "content": "Detailed analysis of [file.py](file:///tmp/file.py).",
                  "tool_calls": [{"name": "read_file", "args": {"Path": "/tmp/file.py"}}],
              },
              {
                  "step_index": 3,
                  "type": "PLANNER_RESPONSE",
                  "content": "",
                  "tool_calls": [{"name": "ask_question", "args": {}}],
              },
          ],
      )
      res = evaluate_tool_call({
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "ask_question",
              "args": {},
          },
      })
      self.assertEqual(res["decision"], "allow")

  def test_rules_agents_md_rule_1_2_documents_ask_question_content_requirement(
      self,
  ):
    """Verifies rules/AGENTS.md Section 1.2 documents the ask_question content presentation rules."""
    agents_path = os.path.join(REPO_ROOT, "rules", "AGENTS.md")
    with open(agents_path, "r", encoding="utf-8") as f:
      text = f.read()
    sec_1_2 = text.split("### 1.2 ")[1].split("### 1.3 ")[0]
    self.assertIn("`ask_question`", sec_1_2)
    self.assertIn("`content`", sec_1_2)
    self.assertIn("`PLANNER_RESPONSE`", sec_1_2)
    self.assertIn("`file://`", sec_1_2)
    self.assertIn("in chat first", sec_1_2.lower())

  def test_pure_git_t50_to_t70_turn_boundary_and_t80_squash_cleanup(self):
    """GH-248, GH-269: Verifies T50->T70 turn boundary and T80 Pure Git guards in isolation."""
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      state_dir = os.path.join(ws_dir, ".workflow", "state")
      os.makedirs(state_dir)
      for card in (
          "45_review_approved.md",
          "50_commit_msg.txt",
          "70_bot_triage.md",
      ):
        with open(os.path.join(state_dir, card), "w", encoding="utf-8") as f:
          f.write("# Card\n")

      transcript_path = os.path.join(ws_dir, "transcript.jsonl")
      steps = [
          {
              "step_index": 1,
              "type": "USER_INPUT",
              "content": "Review approved.",
          },
          {
              "step_index": 2,
              "type": "PLANNER_RESPONSE",
              "content": "Entering Target 50 commit gate.",
              "tool_calls": [{
                  "name": "run_command",
                  "args": {
                      "CommandLine": (
                          "make -f .workflow/state/Makefile commit"
                          " FROM=T45 APPROVED=1"
                      )
                  },
              }],
          },
      ]
      _write_transcript(transcript_path, steps)

      # Enter T50 -> allow and record gate_entry.json for T50
      enter_t50 = evaluate_tool_call({
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": (
                      "make -f .workflow/state/Makefile commit"
                      " FROM=T45 APPROVED=1"
                  )
              },
          },
      })
      self.assertEqual(enter_t50["decision"], "allow", enter_t50)

      # Same-turn T50 -> T70 without intervening USER_INPUT -> denied (SCAR-PROC-102)
      same_turn_t70 = evaluate_tool_call({
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": (
                      "make -f .workflow/state/Makefile bot-quiescence APPROVED=1"
                  )
              },
          },
      })
      self.assertEqual(same_turn_t70["decision"], "deny", same_turn_t70)
      self.assertIn("SCAR-PROC-102", same_turn_t70["reason"])

      # Intervening human USER_INPUT -> T50 -> T70 allowed
      steps.append({
          "step_index": 3,
          "type": "USER_INPUT",
          "content": "Commit approved.",
      })
      _write_transcript(transcript_path, steps)
      ok_t70 = evaluate_tool_call({
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": (
                      "make -f .workflow/state/Makefile bot-quiescence APPROVED=1"
                  )
              },
          },
      })
      self.assertEqual(ok_t70["decision"], "allow", ok_t70)

      # Advance T70 -> T75 -> USER_INPUT -> T80
      steps.append({
          "step_index": 4,
          "type": "PLANNER_RESPONSE",
          "content": "Entering T75.",
          "tool_calls": [
              {
                  "name": "run_command",
                  "args": {
                      "CommandLine": (
                          "make -f .workflow/state/Makefile bot-quiescence"
                          " APPROVED=1"
                      )
                  },
              },
              {
                  "name": "run_command",
                  "args": {
                      "CommandLine": (
                          "make -f .workflow/state/Makefile ready-to-land"
                      )
                  },
              },
          ],
      })
      _write_transcript(transcript_path, steps)
      ok_t75 = evaluate_tool_call({
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": (
                      "make -f .workflow/state/Makefile ready-to-land"
                  )
              },
          },
      })
      self.assertEqual(ok_t75["decision"], "allow", ok_t75)

      steps.append({
          "step_index": 5,
          "type": "USER_INPUT",
          "content": "Approved. Land locally.",
      })
      steps.append({
          "step_index": 6,
          "type": "PLANNER_RESPONSE",
          "content": "Advancing to T80 and squash-merging.",
          "tool_calls": [{
              "name": "run_command",
              "args": {
                  "CommandLine": (
                      "make -f .workflow/state/Makefile land APPROVED=1"
                  )
              },
          }],
      })
      _write_transcript(transcript_path, steps)
      ok_t80 = evaluate_tool_call({
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": (
                      "make -f .workflow/state/Makefile land APPROVED=1"
                  )
              },
          },
      })
      self.assertEqual(ok_t80["decision"], "allow", ok_t80)

      # Target 80 Pure Git cleanup guards
      squash_res = evaluate_tool_call({
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": (
                      "git checkout main && git merge --squash fix/local-1-demo"
                  )
              },
          },
      })
      self.assertEqual(squash_res["decision"], "allow", squash_res)

      commit_res = evaluate_tool_call({
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": "git commit -F .workflow/state/50_commit_msg.txt"
              },
          },
      })
      self.assertEqual(commit_res["decision"], "force_ask", commit_res)

      branch_del_res = evaluate_tool_call({
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {"CommandLine": "git branch -D fix/local-1-demo"},
          },
      })
      self.assertEqual(branch_del_res["decision"], "force_ask", branch_del_res)

  def test_target_42_bounded_wakeup_single_human_pause_lifecycle(self):
    """GH-247: Verifies critic blocker wakeup enters T42 once and exits after one USER_INPUT."""
    with tempfile.TemporaryDirectory() as ws_dir:
      state_dir = os.path.join(ws_dir, ".workflow", "state")
      os.makedirs(state_dir)
      with open(
          os.path.join(state_dir, "active_target.json"), "w", encoding="utf-8"
      ) as f:
        json.dump(
            {
                "previousTarget": "T30",
                "target": "T40",
                "targetName": "adversarial-review",
            },
            f,
        )

      transcript_path = os.path.join(ws_dir, "transcript.jsonl")
      steps = [
          {
              "step_index": 5,
              "type": "USER_INPUT",
              "content": "Approved Target 20 plan.",
          },
          {
              "step_index": 10,
              "type": "PLANNER_RESPONSE",
              "content": "Dispatching adversarial-critic for Round 1.",
          },
      ]
      _write_transcript(transcript_path, steps)
      record_active_subagent(
          state_dir,
          "sup-1",
          "adversarial-critic",
          transcript_path=transcript_path,
      )
      self.assertIsNotNone(read_active_subagent(state_dir))

      # Critic blocker wakeup arrives via <SYSTEM_MESSAGE>
      steps.append({
          "step_index": 11,
          "type": "SYSTEM_MESSAGE",
          "content": (
              "[Message] sender=critic-1 VERDICT: REJECTED (2 BLOCKERS)"
          ),
      })
      _write_transcript(transcript_path, steps)

      # 1. Bounded wakeup action: write round-specific and canonical critic review cards
      round_card = os.path.join(state_dir, "40_critic_review_round1.md")
      write_round_res = evaluate_tool_call({
          "conversationId": "sup-1",
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "write_file",
              "args": {
                  "Path": round_card,
                  "Content": "# Round 1\nVERDICT: REJECTED (2 BLOCKERS)\n",
              },
          },
      })
      self.assertEqual(write_round_res["decision"], "allow", write_round_res)
      self.assertIsNone(read_active_subagent(state_dir))
      with open(round_card, "w", encoding="utf-8") as f:
        f.write("# Round 1\nVERDICT: REJECTED (2 BLOCKERS)\n")

      canon_card = os.path.join(state_dir, "40_critic_review.md")
      write_canon_res = evaluate_tool_call({
          "conversationId": "sup-1",
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "write_file",
              "args": {
                  "Path": canon_card,
                  "Content": "# Round 1\nVERDICT: REJECTED (2 BLOCKERS)\n",
              },
          },
      })
      self.assertEqual(write_canon_res["decision"], "allow", write_canon_res)
      with open(canon_card, "w", encoding="utf-8") as f:
        f.write("# Round 1\nVERDICT: REJECTED (2 BLOCKERS)\n")

      # 2. Bounded wakeup action: enter Target 42 (T40 -> T42) and seal gate_entry.json
      enter_t42_cmd = (
          "make -f .workflow/state/Makefile triaged-blockers BLOCKERS=2"
      )
      enter_t42_res = evaluate_tool_call({
          "conversationId": "sup-1",
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {"CommandLine": enter_t42_cmd},
          },
      })
      self.assertEqual(enter_t42_res["decision"], "allow", enter_t42_res)

      gate_entry_file = os.path.join(state_dir, "gate_entry.json")
      self.assertTrue(os.path.isfile(gate_entry_file))
      with open(gate_entry_file, "r", encoding="utf-8") as f:
        entry = json.load(f)
      self.assertEqual(entry["gate"], "T42")
      self.assertEqual(entry["fromTarget"], "T40")
      self.assertFalse(entry["humanTurnVerified"])
      self.assertEqual(entry["userInputCountAtEntry"], 1)

      with open(
          os.path.join(state_dir, "active_target.json"), "w", encoding="utf-8"
      ) as f:
        json.dump(
            {
                "previousTarget": "T40",
                "target": "T42",
                "targetName": "triaged-blockers",
            },
            f,
        )

      steps.append({
          "step_index": 12,
          "type": "PLANNER_RESPONSE",
          "content": "Target 42 Blocker Digest presented. Stopping tool calls.",
          "tool_calls": [{
              "name": "run_command",
              "args": {"CommandLine": enter_t42_cmd},
          }],
      })
      _write_transcript(transcript_path, steps)

      # 3. Same wakeup turn attempting T42 -> T30 before human reply -> DENIED (SCAR-PROC-102)
      exit_t42_cmd = (
          "make -f .workflow/state/Makefile implementation-diff APPROVED=1"
      )
      same_turn_exit = evaluate_tool_call({
          "conversationId": "sup-1",
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {"CommandLine": exit_t42_cmd},
          },
      })
      self.assertEqual(same_turn_exit["decision"], "deny", same_turn_exit)
      self.assertIn("SCAR-PROC-102", same_turn_exit["reason"])
      self.assertIn("same autonomous turn", same_turn_exit["reason"])
      self.assertNotIn(
          "no human USER_INPUT turn exists", same_turn_exit["reason"]
      )

      # 4. Single human USER_INPUT reply authorizes T42 -> T30 on the first attempt
      steps.append({
          "step_index": 13,
          "type": "USER_INPUT",
          "content": "Proceed with remediation.",
      })
      _write_transcript(transcript_path, steps)

      post_human_exit = evaluate_tool_call({
          "conversationId": "sup-1",
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {"CommandLine": exit_t42_cmd},
          },
      })
      self.assertEqual(post_human_exit["decision"], "allow", post_human_exit)


class TestSubagentHaltAirlock(unittest.TestCase):
  """Hermetic unit and boundary tests for SCAR-PROC-95 subagent halt airlock (GH-220)."""

  def _guarded_supervisor_calls(self, ws_dir):
    return [
        ("read_file", {"Path": os.path.join(ws_dir, "README.md")}),
        (
            "write_file",
            {"Path": os.path.join(ws_dir, "notes.txt"), "Content": "hello"},
        ),
        (
            "edit_file",
            {
                "Path": os.path.join(ws_dir, "notes.txt"),
                "Target": "a",
                "Replacement": "b",
            },
        ),
        ("run_command", {"CommandLine": "git status --short"}),
        (
            "call_mcp_tool",
            {
                "ServerName": "makeflow_makeflow",
                "ToolName": "workflow_get_state",
                "Arguments": {},
            },
        ),
        ("manage_subagents", {"Action": "list"}),
        (
            "ask_question",
            {
                "Questions": [
                    {"Header": "Check", "Question": "Continue?"},
                ]
            },
        ),
        (
            "invoke_subagent",
            {"TypeName": "tdd-worker", "Task": "Duplicate phase worker"},
        ),
    ]

  def test_phase_workers_record_active_subagent_lock(self):
    """Verifies phase worker dispatches record active_subagent.json in isolated env (GH-269)."""
    expected_workers = {
        "archaeology-worker",
        "tdd-worker",
        "adversarial-critic",
        "triage-worker",
    }
    self.assertEqual(MAKEFLOW_PHASE_WORKERS, expected_workers)
    self.assertEqual(ACTIVE_SUBAGENT_FILENAME, "active_subagent.json")

    for worker in sorted(expected_workers):
      with self.subTest(worker=worker):
        with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
          state_dir = os.path.join(ws_dir, ".workflow", "state")
          os.makedirs(state_dir)
          transcript_path = os.path.join(ws_dir, "transcript.jsonl")
          _write_transcript(
              transcript_path,
              [
                  {"step_index": 9, "type": "USER_INPUT", "content": "Go"},
                  {
                      "step_index": 10,
                      "type": "PLANNER_RESPONSE",
                      "content": f"Dispatching {worker}.",
                  },
              ],
          )
          res = evaluate_tool_call({
              "conversationId": "sup-1",
              "transcriptPath": transcript_path,
              "workspacePaths": [ws_dir],
              "toolCall": {
                  "name": "invoke_subagent",
                  "args": {"TypeName": worker, "Task": "Execute phase"},
              },
          })
          self.assertEqual(res["decision"], "allow")
          lock_path = os.path.join(state_dir, "active_subagent.json")
          self.assertTrue(os.path.isfile(lock_path))
          lock_data = read_active_subagent(state_dir)
          self.assertIsNotNone(lock_data)
          self.assertEqual(lock_data["supervisorConversationId"], "sup-1")
          self.assertEqual(lock_data["typeName"], worker)
          self.assertEqual(lock_data["dispatchedStepIdx"], 10)
          self.assertIsInstance(lock_data["dispatchedAt"], (int, float))
          self.assertEqual(lock_data["transcriptPath"], transcript_path)

  def test_non_phase_worker_and_missing_state_dir_do_not_write_lock(self):
    """Verifies non-phase workers and missing state dirs skip lock creation in isolated env (GH-269)."""
    with _isolated_guard_env():
      for non_phase in ("synthetic-scar-importer", "helper-agent", ""):
        with self.subTest(worker=non_phase):
          with tempfile.TemporaryDirectory() as ws_dir:
            state_dir = os.path.join(ws_dir, ".workflow", "state")
            os.makedirs(state_dir)
            transcript_path = os.path.join(ws_dir, "transcript.jsonl")
            _write_transcript(
                transcript_path,
                [{"step_index": 3, "type": "USER_INPUT", "content": "Import"}],
            )
            res = evaluate_tool_call({
                "conversationId": "sup-1",
                "transcriptPath": transcript_path,
                "workspacePaths": [ws_dir],
                "toolCall": {
                    "name": "invoke_subagent",
                    "args": {"TypeName": non_phase, "Task": "Helper work"},
                },
            })
            self.assertEqual(res["decision"], "allow")
            self.assertFalse(
                os.path.exists(os.path.join(state_dir, "active_subagent.json"))
            )

      with tempfile.TemporaryDirectory() as non_makeflow_ws:
        res_no_state = evaluate_tool_call({
            "conversationId": "sup-1",
            "workspacePaths": [non_makeflow_ws],
            "toolCall": {
                "name": "invoke_subagent",
                "args": {"TypeName": "archaeology-worker", "Task": "Scan"},
            },
        })
        self.assertEqual(res_no_state["decision"], "allow")
        self.assertFalse(
            os.path.exists(os.path.join(non_makeflow_ws, ".workflow", "state"))
        )

  def test_same_turn_bundling_denied_across_all_guarded_tools(self):
    """Verifies same-turn bundling with phase workers is denied across guarded tools in isolation (GH-269)."""
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      state_dir = os.path.join(ws_dir, ".workflow", "state")
      os.makedirs(state_dir)
      transcript_path = os.path.join(ws_dir, "transcript.jsonl")
      _write_transcript(
          transcript_path,
          [
              {"step_index": 9, "type": "USER_INPUT", "content": "Start T15"},
              {
                  "step_index": 10,
                  "type": "PLANNER_RESPONSE",
                  "content": "Forking archaeology-worker with [10_ticket.md](file:///tmp/10_ticket.md).",
              },
          ],
      )
      dispatch_res = evaluate_tool_call({
          "conversationId": "sup-1",
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "invoke_subagent",
              "args": {"TypeName": "archaeology-worker", "Task": "Scan"},
          },
      })
      self.assertEqual(dispatch_res["decision"], "allow")

      for tool_name, tool_args in self._guarded_supervisor_calls(ws_dir):
        for cid in ("sup-1", ""):
          with self.subTest(tool=tool_name, cid=cid):
            res = evaluate_tool_call({
                "conversationId": cid,
                "transcriptPath": transcript_path,
                "workspacePaths": [ws_dir],
                "toolCall": {"name": tool_name, "args": tool_args},
            })
            self.assertEqual(res["decision"], "deny")
            reason = res.get("reason", "")
            self.assertIn("SCAR-PROC-95", reason)
            self.assertIn("[SCAR-ID: PROC-95]", reason)
            self.assertIn("CONCURRENT_SUPERVISOR_TOOL_CALL", reason)
            self.assertIn("SOCRATIC PROBE:", reason)

  def test_subsequent_concurrent_step_and_reminder_nudge_denied(self):
    """Verifies concurrent supervisor steps and system nudges remain blocked in isolation (GH-269)."""
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      state_dir = os.path.join(ws_dir, ".workflow", "state")
      os.makedirs(state_dir)
      transcript_path = os.path.join(ws_dir, "transcript.jsonl")
      _write_transcript(
          transcript_path,
          [
              {"step_index": 10, "type": "PLANNER_RESPONSE", "content": "Fork"},
          ],
      )
      record_active_subagent(
          state_dir, "sup-1", "tdd-worker", transcript_path=transcript_path
      )

      _write_transcript(
          transcript_path,
          [
              {"step_index": 10, "type": "PLANNER_RESPONSE", "content": "Fork"},
              {
                  "step_index": 11,
                  "type": "PLANNER_RESPONSE",
                  "content": "Let me read [f.py](file:///tmp/f.py) in parallel.",
              },
              {
                  "step_index": 12,
                  "type": "SYSTEM_MESSAGE",
                  "content": "Reminder: follow Simple English.",
              },
              {
                  "step_index": 13,
                  "type": "USER_INPUT",
                  "source": "SYSTEM",
                  "content": "Reminder: check background tasks.",
              },
          ],
      )

      for tool_name, tool_args in self._guarded_supervisor_calls(ws_dir):
        with self.subTest(tool=tool_name):
          res = evaluate_tool_call({
              "conversationId": "sup-1",
              "transcriptPath": transcript_path,
              "workspacePaths": [ws_dir],
              "toolCall": {"name": tool_name, "args": tool_args},
          })
          self.assertEqual(res["decision"], "deny")
          self.assertIn("SCAR-PROC-95", res.get("reason", ""))
          self.assertIn(
              "CONCURRENT_SUPERVISOR_TOOL_CALL", res.get("reason", "")
          )
          self.assertIsNotNone(read_active_subagent(state_dir))

  def test_child_subagent_passthrough_preserves_downstream_safety_guards(self):
    """Verifies child subagent passthrough preserves safety guards in isolation (GH-269)."""
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      state_dir = os.path.join(ws_dir, ".workflow", "state")
      os.makedirs(state_dir)
      record_active_subagent(
          state_dir,
          "sup-1",
          "tdd-worker",
          transcript_path=os.path.join(ws_dir, "missing_sup_transcript.jsonl"),
      )

      for tool_name, tool_args in (
          ("read_file", {"Path": os.path.join(ws_dir, "src", "app.py")}),
          (
              "write_file",
              {
                  "Path": os.path.join(ws_dir, "src", "app.py"),
                  "Content": "x = 1\n",
              },
          ),
          ("run_command", {"CommandLine": "python3 -m unittest"}),
      ):
        with self.subTest(allowed_worker_tool=tool_name):
          res = evaluate_tool_call({
              "conversationId": "worker-2",
              "transcriptPath": os.path.join(
                  ws_dir, "unreadable_nonexistent.jsonl"
              ),
              "workspacePaths": [ws_dir],
              "toolCall": {"name": tool_name, "args": tool_args},
          })
          self.assertEqual(res["decision"], "allow")

      for bad_name, bad_args in (
          (
              "write_file",
              {
                  "Path": os.path.join(state_dir, "active_subagent.json"),
                  "Content": "{}",
              },
          ),
          (
              "run_command",
              {"CommandLine": "rm .workflow/state/30_tdd_receipt.json"},
          ),
          ("run_command", {"CommandLine": "git reset --hard"}),
      ):
        with self.subTest(denied_worker_tool=bad_name, args=bad_args):
          deny_res = evaluate_tool_call({
              "conversationId": "worker-2",
              "workspacePaths": [ws_dir],
              "toolCall": {"name": bad_name, "args": bad_args},
          })
          self.assertEqual(deny_res["decision"], "deny")
          self.assertNotIn("SCAR-PROC-95", deny_res.get("reason", ""))

  def test_human_user_input_override_allows_inspection_blocks_mutations_and_clears_on_kill(
      self,
  ):
    """Verifies human override allows inspection and clears lock on kill in isolation (GH-269)."""
    self.assertEqual(
        SUPERVISOR_INSPECTION_TOOLS,
        {
            "read_file",
            "manage_subagents",
            "ask_question",
            "call_mcp_tool",
            "run_command",
        },
    )
    user_steps = [
        {"step_index": 11, "type": "USER_INPUT", "content": "Check worker"},
        {
            "step_index": 11,
            "source": "USER_EXPLICIT",
            "content": "Inspect progress",
        },
    ]
    for user_step in user_steps:
      with self.subTest(user_step=user_step):
        with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
          state_dir = os.path.join(ws_dir, ".workflow", "state")
          os.makedirs(state_dir)
          transcript_path = os.path.join(ws_dir, "transcript.jsonl")
          _write_transcript(
              transcript_path,
              [{"step_index": 10, "type": "PLANNER_RESPONSE", "content": "Go"}],
          )
          record_active_subagent(
              state_dir, "sup-1", "triage-worker", transcript_path=transcript_path
          )
          _write_transcript(
              transcript_path,
              [
                  {
                      "step_index": 10,
                      "type": "PLANNER_RESPONSE",
                      "content": "Go",
                  },
                  user_step,
                  {
                      "step_index": 12,
                      "type": "PLANNER_RESPONSE",
                      "content": (
                          "Worker status details in"
                          " [10_ticket.md](file:///tmp/10_ticket.md)."
                      ),
                  },
              ],
          )

          for insp_name, insp_args in (
              ("read_file", {"Path": os.path.join(ws_dir, "README.md")}),
              ("manage_subagents", {"Action": "list"}),
              (
                  "ask_question",
                  {"Questions": [{"Header": "Op", "Question": "Kill?"}]},
              ),
              (
                  "call_mcp_tool",
                  {
                      "ServerName": "makeflow_makeflow",
                      "ToolName": "workflow_get_state",
                      "Arguments": {},
                  },
              ),
              ("run_command", {"CommandLine": "git status --short"}),
          ):
            res_insp = evaluate_tool_call({
                "conversationId": "sup-1",
                "transcriptPath": transcript_path,
                "workspacePaths": [ws_dir],
                "toolCall": {"name": insp_name, "args": insp_args},
            })
            self.assertEqual(res_insp["decision"], "allow")
            self.assertIsNotNone(read_active_subagent(state_dir))

          for mut_name, mut_args in (
              (
                  "write_file",
                  {"Path": os.path.join(ws_dir, "a.txt"), "Content": "x"},
              ),
              (
                  "edit_file",
                  {
                      "Path": os.path.join(ws_dir, "a.txt"),
                      "Target": "x",
                      "Replacement": "y",
                  },
              ),
              (
                  "invoke_subagent",
                  {"TypeName": "tdd-worker", "Task": "Duplicate"},
              ),
          ):
            res_mut = evaluate_tool_call({
                "conversationId": "sup-1",
                "transcriptPath": transcript_path,
                "workspacePaths": [ws_dir],
                "toolCall": {"name": mut_name, "args": mut_args},
            })
            self.assertEqual(res_mut["decision"], "deny")
            self.assertIn("SCAR-PROC-95", res_mut.get("reason", ""))
            self.assertIsNotNone(read_active_subagent(state_dir))

          for kill_action in ("kill", "kill_all"):
            _write_transcript(
                transcript_path,
                [
                    {
                        "step_index": 10,
                        "type": "PLANNER_RESPONSE",
                        "content": "Go",
                    }
                ],
            )
            record_active_subagent(
                state_dir,
                "sup-1",
                "triage-worker",
                transcript_path=transcript_path,
            )
            _write_transcript(
                transcript_path,
                [
                    {
                        "step_index": 10,
                        "type": "PLANNER_RESPONSE",
                        "content": "Go",
                    },
                    user_step,
                    {
                        "step_index": 12,
                        "type": "PLANNER_RESPONSE",
                        "content": (
                            "Worker status details in"
                            " [10_ticket.md](file:///tmp/10_ticket.md)."
                        ),
                    },
                ],
            )
            res_kill = evaluate_tool_call({
                "conversationId": "sup-1",
                "transcriptPath": transcript_path,
                "workspacePaths": [ws_dir],
                "toolCall": {
                    "name": "manage_subagents",
                    "args": {"Action": kill_action, "SubagentId": "sub-9"},
                },
            })
            self.assertEqual(res_kill["decision"], "allow")
            self.assertIsNone(read_active_subagent(state_dir))

  def test_system_message_wakeup_clears_lock_and_boolean_step_index_guard(self):
    """Verifies wakeup clears subagent lock and ignores boolean step_index in isolation (GH-269)."""
    self.assertTrue(
        is_subagent_wakeup_step(
            {"type": "SYSTEM_MESSAGE", "content": "[Message] worker done"}
        )
    )
    self.assertFalse(
        is_subagent_wakeup_step(
            {"type": "SYSTEM_MESSAGE", "content": "Reminder: stay concise"}
        )
    )
    self.assertTrue(
        is_subagent_wakeup_step({
            "type": "USER_INPUT",
            "source": "SYSTEM",
            "content": "<SYSTEM_MESSAGE>\n[Message] sender=w1",
        })
    )
    self.assertFalse(
        is_human_user_input_step({
            "type": "USER_INPUT",
            "source": "SYSTEM",
            "content": "<SYSTEM_MESSAGE>\n[Message] sender=w1",
        })
    )

    # Boolean step_index: True at idx=0 must NOT be treated as int 1 > dispatchedStepIdx 0
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      state_dir = os.path.join(ws_dir, ".workflow", "state")
      os.makedirs(state_dir)
      transcript_path = os.path.join(ws_dir, "transcript.jsonl")
      _write_transcript(
          transcript_path,
          [
              {
                  "step_index": True,
                  "type": "SYSTEM_MESSAGE",
                  "content": "[Message] prior wakeup before dispatch",
              },
          ],
      )
      record_active_subagent(
          state_dir,
          "sup-1",
          "archaeology-worker",
          transcript_path=transcript_path,
      )
      self.assertEqual(
          read_active_subagent(state_dir)["dispatchedStepIdx"], 0
      )
      bool_res = evaluate_tool_call({
          "conversationId": "sup-1",
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {"name": "read_file", "args": {"Path": "README.md"}},
      })
      self.assertEqual(bool_res["decision"], "deny")
      self.assertIn("SCAR-PROC-95", bool_res.get("reason", ""))

    wakeup_steps = [
        {
            "step_index": 11,
            "type": "SYSTEM_MESSAGE",
            "content": "[Message] sender=arch-1 findings complete",
        },
        {
            "step_index": 11,
            "type": "USER_INPUT",
            "source": "SYSTEM",
            "content": "<SYSTEM_MESSAGE>\n[Message] sender=arch-1 done",
        },
    ]
    for wakeup in wakeup_steps:
      with self.subTest(wakeup=wakeup):
        with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
          state_dir = os.path.join(ws_dir, ".workflow", "state")
          os.makedirs(state_dir)
          transcript_path = os.path.join(ws_dir, "transcript.jsonl")
          _write_transcript(
              transcript_path,
              [{"step_index": 10, "type": "PLANNER_RESPONSE", "content": "Go"}],
          )
          record_active_subagent(
              state_dir,
              "sup-1",
              "archaeology-worker",
              transcript_path=transcript_path,
          )
          _write_transcript(
              transcript_path,
              [
                  {
                      "step_index": 10,
                      "type": "PLANNER_RESPONSE",
                      "content": "Go",
                  },
                  wakeup,
              ],
          )
          res = evaluate_tool_call({
              "conversationId": "sup-1",
              "transcriptPath": transcript_path,
              "workspacePaths": [ws_dir],
              "toolCall": {
                  "name": "write_file",
                  "args": {
                      "Path": os.path.join(
                          state_dir, "15_archaeology_receipt.md"
                      ),
                      "Content": "# Receipt\n",
                  },
              },
          })
          self.assertEqual(res["decision"], "allow")
          self.assertIsNone(read_active_subagent(state_dir))

          # Also verify wakeup followed immediately by next phase worker dispatch records new lock
          record_active_subagent(
              state_dir,
              "sup-1",
              "archaeology-worker",
              transcript_path=transcript_path,
          )
          _write_transcript(
              transcript_path,
              [
                  {
                      "step_index": 10,
                      "type": "PLANNER_RESPONSE",
                      "content": "Go",
                  },
                  {
                      "step_index": 12,
                      "type": "SYSTEM_MESSAGE",
                      "content": "[Message] arch done",
                  },
                  {
                      "step_index": 13,
                      "type": "PLANNER_RESPONSE",
                      "content": "Now dispatching tdd-worker",
                  },
              ],
          )
          res_next = evaluate_tool_call({
              "conversationId": "sup-1",
              "transcriptPath": transcript_path,
              "workspacePaths": [ws_dir],
              "toolCall": {
                  "name": "invoke_subagent",
                  "args": {"TypeName": "tdd-worker", "Task": "Run TDD"},
              },
          })
          self.assertEqual(res_next["decision"], "allow")
          new_lock = read_active_subagent(state_dir)
          self.assertIsNotNone(new_lock)
          self.assertEqual(new_lock["typeName"], "tdd-worker")
          self.assertEqual(new_lock["dispatchedStepIdx"], 13)
          clear_active_subagent(state_dir)
          self.assertIsNone(read_active_subagent(state_dir))

  def test_fast_early_exit_without_active_lock_skips_transcript_under_2ms(self):
    """Verifies unlocked fast early-exit skips transcript reads under 2ms in isolation (GH-269)."""
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      state_dir = os.path.join(ws_dir, ".workflow", "state")
      os.makedirs(state_dir)
      payload = {
          "conversationId": "sup-1",
          "transcriptPath": ws_dir,  # Directory path: opening as file would fail
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "read_file",
              "args": {"Path": os.path.join(ws_dir, "README.md")},
          },
      }
      # Warmup once, then measure median across 5 calls (< 2ms budget)
      self.assertEqual(evaluate_tool_call(payload)["decision"], "allow")
      samples = []
      for _ in range(5):
        t0 = time.perf_counter()
        ok, reason = evaluate_subagent_airlock(payload, state_dir)
        dt = time.perf_counter() - t0
        self.assertTrue(ok)
        self.assertEqual(reason, "")
        samples.append(dt)
      samples.sort()
      self.assertLess(
          samples[2],
          0.002,
          f"Expected fast early-exit < 2ms, got median {samples[2] * 1000:.3f}ms",
      )

  def test_active_subagent_json_protected_by_governance_guards(self):
    """Verifies active_subagent.json cannot be overwritten or deleted in isolated env (GH-269)."""
    self.assertIn(
        ".workflow/state/active_subagent.json", GOVERNANCE_FILE_RELPATHS
    )
    ok_rm, reason_rm = evaluate_self_modification_safety(
        "rm -f .workflow/state/active_subagent.json"
    )
    self.assertFalse(ok_rm)
    self.assertIn("active_subagent.json", reason_rm)

    ok_redir, reason_redir = evaluate_self_modification_safety(
        "echo '{}' > .workflow/state/active_subagent.json"
    )
    self.assertFalse(ok_redir)
    self.assertIn("active_subagent.json", reason_redir)

    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      state_dir = os.path.join(ws_dir, ".workflow", "state")
      os.makedirs(state_dir)
      for cid in ("sup-1", "worker-2"):
        for tool_name in ("write_file", "edit_file"):
          res = evaluate_tool_call({
              "conversationId": cid,
              "workspacePaths": [ws_dir],
              "toolCall": {
                  "name": tool_name,
                  "args": {
                      "Path": os.path.join(state_dir, "active_subagent.json"),
                      "Content": "{}",
                      "Target": "a",
                      "Replacement": "b",
                  },
              },
          })
          self.assertEqual(res["decision"], "deny")
          self.assertIn("active_subagent.json", res.get("reason", ""))

  def test_cli_wire_smoke_denies_concurrent_supervisor_call(self):
    """Verifies pre_tool_guard CLI denies concurrent supervisor calls under isolated env and cwd (GH-269)."""
    guard_script = os.path.join(REPO_ROOT, "scripts", "pre_tool_guard.py")
    with (
        _isolated_guard_env() as (hook_cwd, _fake_home, clean_env),
        tempfile.TemporaryDirectory() as ws_dir,
    ):
      state_dir = os.path.join(ws_dir, ".workflow", "state")
      os.makedirs(state_dir)
      transcript_path = os.path.join(ws_dir, "transcript.jsonl")
      _write_transcript(
          transcript_path,
          [
              {
                  "step_index": 10,
                  "type": "PLANNER_RESPONSE",
                  "content": "Dispatching adversarial-critic",
              }
          ],
      )
      record_active_subagent(
          state_dir,
          "sup-wire",
          "adversarial-critic",
          transcript_path=transcript_path,
      )
      wire_payload = {
          "conversationId": "sup-wire",
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "read_file",
              "args": {"Path": os.path.join(ws_dir, "README.md")},
          },
      }
      proc = subprocess.run(
          [sys.executable, guard_script],
          input=json.dumps(wire_payload),
          text=True,
          capture_output=True,
          cwd=hook_cwd,
          env=clean_env,
          check=False,
      )
      self.assertEqual(proc.returncode, 0, proc.stderr)
      out = json.loads(proc.stdout)
      self.assertEqual(out["decision"], "deny")
      self.assertIn("SCAR-PROC-95", out["reason"])
      self.assertIn("CONCURRENT_SUPERVISOR_TOOL_CALL", out["reason"])


class TestPosixLineContinuationGuards(unittest.TestCase):
  """Boundary tests for POSIX line continuations and CLI helper entry-card detection (GH-279)."""

  def test_multiline_line_continuations_blocked_for_git_db_and_self_modification(
      self,
  ):
    """Blocks destructive Git, DB, and governance self-modification across line continuations (GH-279)."""
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      denied_commands = (
          "echo ok && \\\ngit reset --hard",
          "echo ok && \\\r\ngit push --force origin main",
          "gi\\\nt reset --hard",
          "# comment \\\ngit reset --hard",
          "echo ok\\\\\ngit reset --hard",
          "ps\\\nql -h prod.db.internal myapp",
          "echo ok && \\\nrm -f hooks.json",
          "echo x > scripts/\\\ntdd_guard.py",
          "echo x > scripts/\\\r\ntdd_guard.py",
          "# Don't touch \\\necho x > scripts/\\\ntdd_guard.py",
      )
      for cmd in denied_commands:
        res = evaluate_tool_call({
            "workspacePaths": [ws_dir],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": cmd},
            },
        })
        self.assertEqual(
            res["decision"],
            "deny",
            f"Expected deny for multiline command {cmd!r}, got {res}",
        )

  def test_multiline_chained_target50_entry_records_gate_and_enforces_turn_boundary(
      self,
  ):
    """Records Target 50 gate_entry.json on chained multiline make commit and unblocks post-approval (GH-279, SCAR-PROC-102)."""
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      state_dir = os.path.join(ws_dir, ".workflow", "state")
      os.makedirs(state_dir)
      with open(
          os.path.join(state_dir, "45_review_approved.md"),
          "w",
          encoding="utf-8",
      ) as f:
        f.write("# Approved\n")
      transcript_path = os.path.join(ws_dir, "transcript.jsonl")
      steps = [
          {
              "step_index": 10,
              "type": "USER_INPUT",
              "content": "Review approved. Prepare commit.",
          },
          {
              "step_index": 11,
              "type": "PLANNER_RESPONSE",
              "content": "Formatting commit message and entering Target 50.",
          },
      ]
      _write_transcript(transcript_path, steps)

      chained_cmd = (
          "python3 scripts/commit_msg.py format \\\n"
          '  --type fix --scope hooks --desc "strip continuations" \\\n'
          "  --issue 279 --out .workflow/state/50_commit_msg.txt && \\\n"
          "make -f .workflow/state/Makefile commit APPROVED=1"
      )
      enter_res = evaluate_tool_call({
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {"CommandLine": chained_cmd},
          },
      })
      self.assertEqual(enter_res["decision"], "allow", enter_res)

      entry = read_gate_entry(state_dir)
      self.assertIsNotNone(entry)
      self.assertEqual(entry["gate"], "T50")
      self.assertEqual(entry["transcriptPath"], transcript_path)
      self.assertEqual(entry["entryStepIndex"], 11)
      self.assertFalse(entry["humanTurnVerified"])

      # Same-turn git commit MUST be blocked.
      steps.append({
          "step_index": 12,
          "type": "PLANNER_RESPONSE",
          "tool_calls": [{
              "name": "run_command",
              "args": {"CommandLine": chained_cmd},
          }],
      })
      _write_transcript(transcript_path, steps)
      same_turn_commit = evaluate_tool_call({
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": "git commit -F .workflow/state/50_commit_msg.txt"
              },
          },
      })
      self.assertEqual(same_turn_commit["decision"], "deny", same_turn_commit)
      self.assertIn("SCAR-PROC-102", same_turn_commit["reason"])

      # After explicit human approval, git commit -F prompts and make push APPROVED=1 succeeds.
      steps.append({
          "step_index": 13,
          "type": "USER_INPUT",
          "content": "Approved. Run the commit.",
      })
      _write_transcript(transcript_path, steps)
      approved_commit = evaluate_tool_call({
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": "git commit -F .workflow/state/50_commit_msg.txt"
              },
          },
      })
      self.assertEqual(
          approved_commit["decision"], "force_ask", approved_commit
      )

      steps.append({
          "step_index": 14,
          "type": "PLANNER_RESPONSE",
          "tool_calls": [{
              "name": "run_command",
              "args": {
                  "CommandLine": "git commit -F .workflow/state/50_commit_msg.txt"
              },
          }],
      })
      _write_transcript(transcript_path, steps)
      push_advance = evaluate_tool_call({
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": (
                      "make -f .workflow/state/Makefile push APPROVED=1"
                  )
              },
          },
      })
      self.assertEqual(push_advance["decision"], "allow", push_advance)

  def test_step_enters_gate_cli_helper_fallback_and_readonly_negatives(self):
    """Detects CLI helpers writing entryCards while ignoring read-only commands (GH-279, SCAR-ARCH-07)."""
    def _shell_step(cmd, tool_name="run_command"):
      return {
          "type": "PLANNER_RESPONSE",
          "tool_calls": [{
              "name": tool_name,
              "args": {"CommandLine": cmd},
          }],
      }

    positive_cmds = (
        "python3 scripts/commit_msg.py format --type fix --desc 'x' --out .workflow/state/50_commit_msg.txt",
        "python3 scripts/commit_msg.py format --type fix --desc 'x' --out=.workflow/state/50_commit_msg.txt",
        "python3 -m scripts.commit_msg format --type fix --desc 'x' --out .workflow/state/50_commit_msg.txt",
        "python3 -m lib.commit_msg format --type fix --desc 'x' --out .workflow/state/50_commit_msg.txt",
        "scripts/commit_msg.py format --type fix --desc 'x' --out .workflow/state/50_commit_msg.txt",
        "python3 scripts/commit_msg.py --repo-root . format \\\n  --type fix --desc 'x' --out .workflow/state/50_commit_msg.txt",
        "echo 'feat: x' > .workflow/state/50_commit_msg.txt",
        "echo 'feat: x' >> .workflow/state/50_commit_msg.txt",
        "echo 'feat: x' >| .workflow/state/50_commit_msg.txt",
        "echo 'feat: x' | tee .workflow/state/50_commit_msg.txt",
    )
    for cmd in positive_cmds:
      self.assertTrue(
          step_enters_gate(_shell_step(cmd), "T50"),
          f"Expected step_enters_gate True for {cmd!r}",
      )

    readonly_cmds = (
        "git commit -F .workflow/state/50_commit_msg.txt",
        "python3 scripts/commit_msg.py validate --file .workflow/state/50_commit_msg.txt",
        "cat .workflow/state/50_commit_msg.txt",
        "cat < .workflow/state/50_commit_msg.txt",
    )
    for cmd in readonly_cmds:
      self.assertFalse(
          step_enters_gate(_shell_step(cmd), "T50"),
          f"Expected step_enters_gate False for read-only command {cmd!r}",
      )

    # End-to-end fallback when gate_entry.json has entryStepIndex == -1.
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      state_dir = os.path.join(ws_dir, ".workflow", "state")
      os.makedirs(state_dir)
      with open(
          os.path.join(state_dir, "45_review_approved.md"),
          "w",
          encoding="utf-8",
      ) as f:
        f.write("# Approved\n")
      with open(
          os.path.join(state_dir, "gate_entry.json"),
          "w",
          encoding="utf-8",
      ) as f:
        json.dump(
            {
                "gate": "T50",
                "fromTarget": "T45",
                "enteredAt": 1.0,
                "entryStepIndex": -1,
                "userInputCountAtEntry": 0,
                "transcriptPath": "",
                "humanTurnVerified": False,
            },
            f,
        )
      transcript_path = os.path.join(ws_dir, "transcript.jsonl")
      steps = [
          {
              "step_index": 20,
              "type": "PLANNER_RESPONSE",
              "tool_calls": [{
                  "name": "run_command",
                  "args": {
                      "CommandLine": (
                          "python3 scripts/commit_msg.py format --type fix"
                          " --desc 'x' --out .workflow/state/50_commit_msg.txt"
                      )
                  },
              }],
          },
      ]
      _write_transcript(transcript_path, steps)

      pre_approval = evaluate_tool_call({
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": "git commit -F .workflow/state/50_commit_msg.txt"
              },
          },
      })
      self.assertEqual(pre_approval["decision"], "deny", pre_approval)

      steps.append({
          "step_index": 21,
          "type": "USER_INPUT",
          "content": "Commit approved.",
      })
      _write_transcript(transcript_path, steps)
      post_approval = evaluate_tool_call({
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": "git commit -F .workflow/state/50_commit_msg.txt"
              },
          },
      })
      self.assertEqual(post_approval["decision"], "force_ask", post_approval)

      steps.append({
          "step_index": 22,
          "type": "PLANNER_RESPONSE",
          "tool_calls": [{
              "name": "run_command",
              "args": {
                  "CommandLine": "git commit -F .workflow/state/50_commit_msg.txt"
              },
          }],
      })
      _write_transcript(transcript_path, steps)
      advance_push = evaluate_tool_call({
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": (
                      "make -f .workflow/state/Makefile push APPROVED=1"
                  )
              },
          },
      })
      self.assertEqual(advance_push["decision"], "allow", advance_push)


if __name__ == "__main__":
  unittest.main()
