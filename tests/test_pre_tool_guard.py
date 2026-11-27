#!/usr/bin/env python3
"""Unit tests for PreToolUse transcript turn-boundary guard (GH-186, SCAR-PROC-102)."""

import contextlib
import io
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
          with open(
              os.path.join(state_dir, "50_commit_msg.txt"),
              "w",
              encoding="utf-8",
          ) as f:
            f.write("feat(hooks): enforce turn boundary\n\nTicket: LOCAL-1\n")
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
          "70_bot_triage.md",
      ):
        with open(os.path.join(state_dir, card), "w", encoding="utf-8") as f:
          f.write("# Card\n")
      with open(
          os.path.join(state_dir, "50_commit_msg.txt"), "w", encoding="utf-8"
      ) as f:
        f.write("fix(git): land local task\n\nTicket: LOCAL-1\n")

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

  def test_promote_review_allowed_after_critic_wakeup_for_t42_and_t45(self):
    """GH-221: Verifies promote-review ROUND=1 passes pre_tool_guard after critic wakeup."""
    scenarios = (
        (
            "target_42_blockers",
            "VERDICT: REJECTED (2 BLOCKERS)",
            "make -f .workflow/state/Makefile triaged-blockers BLOCKERS=2",
            "T42",
        ),
        (
            "target_45_approved",
            "VERDICT: APPROVED (0 BLOCKERS)",
            "make -f .workflow/state/Makefile approved-review BLOCKERS=0",
            "T45",
        ),
    )
    for label, verdict_summary, gate_cmd, expected_gate in scenarios:
      with self.subTest(scenario=label):
        with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
          state_dir = os.path.join(ws_dir, ".workflow", "state")
          os.makedirs(state_dir)
          with open(
              os.path.join(state_dir, "active_target.json"),
              "w",
              encoding="utf-8",
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

          # Critic wakeup arrives via <SYSTEM_MESSAGE>
          steps.append({
              "step_index": 11,
              "type": "SYSTEM_MESSAGE",
              "content": f"[Message] sender=critic-1 {verdict_summary}",
          })
          _write_transcript(transcript_path, steps)

          # 1. Supervisor writes round 1 once
          round_card = os.path.join(state_dir, "40_critic_review_round1.md")
          write_round_res = evaluate_tool_call({
              "conversationId": "sup-1",
              "transcriptPath": transcript_path,
              "workspacePaths": [ws_dir],
              "toolCall": {
                  "name": "write_file",
                  "args": {
                      "Path": round_card,
                      "Content": f"# Round 1\n{verdict_summary}\n",
                  },
              },
          })
          self.assertEqual(write_round_res["decision"], "allow", write_round_res)
          self.assertIsNone(read_active_subagent(state_dir))

          # 2. Supervisor runs promote-review ROUND=1 cleanly
          promote_res = evaluate_tool_call({
              "conversationId": "sup-1",
              "transcriptPath": transcript_path,
              "workspacePaths": [ws_dir],
              "toolCall": {
                  "name": "run_command",
                  "args": {
                      "CommandLine": (
                          "make -f .workflow/state/Makefile promote-review"
                          " ROUND=1"
                      )
                  },
              },
          })
          self.assertEqual(promote_res["decision"], "allow", promote_res)
          self.assertFalse(
              os.path.exists(os.path.join(state_dir, "gate_entry.json"))
          )

          # 3. Supervisor enters Target 42 or Target 45 and seals gate_entry.json
          gate_res = evaluate_tool_call({
              "conversationId": "sup-1",
              "transcriptPath": transcript_path,
              "workspacePaths": [ws_dir],
              "toolCall": {
                  "name": "run_command",
                  "args": {"CommandLine": gate_cmd},
              },
          })
          self.assertEqual(gate_res["decision"], "allow", gate_res)
          with open(
              os.path.join(state_dir, "gate_entry.json"), "r", encoding="utf-8"
          ) as f:
            entry = json.load(f)
          self.assertEqual(entry["gate"], expected_gate)
          self.assertEqual(entry["fromTarget"], "T40")


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
      with open(os.path.join(ws_dir, "plugin.json"), "w", encoding="utf-8") as f:
        json.dump({"name": "makeflow", "version": "1.0.0"}, f)
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
      with open(
          os.path.join(state_dir, "50_commit_msg.txt"),
          "w",
          encoding="utf-8",
      ) as f:
        f.write("fix(hooks): strip continuations\n\nTicket: LOCAL-1\n")
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
          os.path.join(state_dir, "50_commit_msg.txt"),
          "w",
          encoding="utf-8",
      ) as f:
        f.write("fix(hooks): fallback gate entry\n\nTicket: LOCAL-1\n")
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



class TestPreToolGuardConsumerVsPluginSelfModification(unittest.TestCase):
  """Hermetic tests distinguishing external consumer repos from Makeflow plugin repos in shell self-modification guard (GH-242)."""

  def test_external_consumer_repo_allows_shell_mutations_on_consumer_governance_named_files(
      self,
  ):
    """External consumer repositories allow shell mutations on consumer lib/base_script.py, scripts/workflow_gates.py, and lib/scripts dirs (GH-242)."""
    consumer_commands = [
        "echo 'class BaseScript: pass' > lib/base_script.py",
        "echo '# update' >> scripts/workflow_gates.py",
        "rm lib/base_script.py",
        "unlink scripts/workflow_gates.py",
        "truncate -s 0 lib/base_script.py",
        "sed -i 's/a/b/' lib/base_script.py",
        "echo 'x' | tee lib/base_script.py",
        "mv tmp.py lib/base_script.py",
        "cp tmp.py scripts/workflow_gates.py",
        "mv base_script.py lib/",
        "rm -rf lib",
        "rm -rf scripts/",
    ]
    for non_makeflow_manifest in (None, '{"name": "consumer-app"}\n'):
      with (
          self.subTest(manifest=non_makeflow_manifest),
          _isolated_guard_env(),
          tempfile.TemporaryDirectory() as consumer_ws,
      ):
        os.makedirs(os.path.join(consumer_ws, "lib"), exist_ok=True)
        os.makedirs(os.path.join(consumer_ws, "scripts"), exist_ok=True)
        if non_makeflow_manifest is not None:
          with open(
              os.path.join(consumer_ws, "plugin.json"), "w", encoding="utf-8"
          ) as f:
            f.write(non_makeflow_manifest)
        for cmd in consumer_commands:
          res = evaluate_tool_call({
              "workspacePaths": [consumer_ws],
              "toolCall": {
                  "name": "run_command",
                  "args": {"CommandLine": cmd},
              },
          })
          self.assertEqual(
              res["decision"],
              "allow",
              f"Expected allow in external consumer repo for cmd={cmd!r}, got {res}",
          )

  def test_external_consumer_repo_still_denies_workflow_state_and_plugin_targets(
      self,
  ):
    """External consumer repositories still deny mutations targeting .workflow/state governance files, plugin cache paths, or Makeflow checkouts (GH-242)."""
    with (
        _isolated_guard_env() as (_hook_cwd, fake_home, _clean_env),
        tempfile.TemporaryDirectory() as consumer_ws,
        tempfile.TemporaryDirectory() as plugin_checkout,
    ):
      with open(
          os.path.join(plugin_checkout, "plugin.json"), "w", encoding="utf-8"
      ) as f:
        json.dump({"name": "makeflow", "version": "1.0.0"}, f)
      os.makedirs(os.path.join(plugin_checkout, "lib"), exist_ok=True)
      os.makedirs(os.path.join(plugin_checkout, "scripts"), exist_ok=True)

      cache_target = os.path.join(
          fake_home,
          ".gemini",
          "config",
          "plugins",
          "makeflow",
          "lib",
          "tdd_guard.py",
      )
      checkout_lib_target = os.path.join(
          plugin_checkout, "lib", "tdd_guard.py"
      )
      checkout_lib_dir = os.path.join(plugin_checkout, "lib")

      denied_commands = [
          "echo 'all:' > .workflow/state/Makefile",
          "rm -f .workflow/state/30_regression_receipt.json",
          "rm -f .workflow/state/30_tdd_receipt.json",
          "rm ~/.gemini/config/plugins/makeflow/lib/tdd_guard.py",
          f"echo '# hack' > {cache_target}",
          f"echo '# hack' > {checkout_lib_target}",
          f"rm -rf {checkout_lib_dir}",
          f"cd {plugin_checkout} && echo '# hack' > lib/tdd_guard.py",
      ]
      for cmd in denied_commands:
        res = evaluate_tool_call({
            "workspacePaths": [consumer_ws],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": cmd},
            },
        })
        self.assertEqual(
            res["decision"],
            "deny",
            f"Expected deny from consumer repo for cmd={cmd!r}, got {res}",
        )
        self.assertIn(
            "Blocked self-modification attempt on makeflow governance file",
            res.get("reason", ""),
        )

  def test_makeflow_plugin_workspace_still_denies_shell_mutations_on_governance_files(
      self,
  ):
    """Makeflow plugin workspace still denies shell mutations targeting governance files and lib/scripts directories (GH-242)."""
    with (
        _isolated_guard_env(),
        tempfile.TemporaryDirectory() as plugin_ws,
    ):
      with open(
          os.path.join(plugin_ws, "plugin.json"), "w", encoding="utf-8"
      ) as f:
        json.dump({"name": "makeflow", "version": "1.0.0"}, f)
      os.makedirs(os.path.join(plugin_ws, "lib"), exist_ok=True)
      os.makedirs(os.path.join(plugin_ws, "scripts"), exist_ok=True)
      os.makedirs(
          os.path.join(plugin_ws, ".workflow", "state"), exist_ok=True
      )

      denied_commands = [
          "echo '# hack' > lib/base_script.py",
          "rm scripts/tdd_guard.py",
          "truncate -s 0 lib/regression_gate.py",
          "sed -i 's/a/b/' lib/base_script.py",
          "echo 'x' | tee lib/base_script.py",
          "mv evil.py lib/base_script.py",
          "cp evil.py scripts/tdd_guard.py",
          "mv base_script.py lib/",
          "rm -rf lib",
          "rm -rf scripts/",
      ]
      for cmd in denied_commands:
        res = evaluate_tool_call({
            "workspacePaths": [plugin_ws],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": cmd},
            },
        })
        self.assertEqual(
            res["decision"],
            "deny",
            f"Expected deny in Makeflow plugin workspace for cmd={cmd!r}, got {res}",
        )
        self.assertIn(
            "Blocked self-modification attempt on makeflow governance file",
            res.get("reason", ""),
        )

  def test_blocker1_normalized_paths_subdir_cd_and_symlinks(self):
    """Denies dot-segments, subdir cd, and symlinks to Makeflow governance targets while allowing consumer equivalents (GH-242)."""
    with (
        _isolated_guard_env(),
        tempfile.TemporaryDirectory() as plugin_ws,
        tempfile.TemporaryDirectory() as consumer_ws,
    ):
      with open(
          os.path.join(plugin_ws, "plugin.json"), "w", encoding="utf-8"
      ) as f:
        json.dump({"name": "makeflow", "version": "1.0.0"}, f)
      os.makedirs(os.path.join(plugin_ws, "lib"), exist_ok=True)
      os.makedirs(os.path.join(plugin_ws, "scripts"), exist_ok=True)
      os.makedirs(
          os.path.join(plugin_ws, ".workflow", "state"), exist_ok=True
      )
      os.makedirs(os.path.join(consumer_ws, "lib"), exist_ok=True)
      os.makedirs(os.path.join(consumer_ws, "scripts"), exist_ok=True)
      os.makedirs(
          os.path.join(consumer_ws, ".workflow", "state"), exist_ok=True
      )

      file_link = os.path.join(consumer_ws, "alias_guard")
      dir_link = os.path.join(consumer_ws, "alias_lib")
      state_link = os.path.join(consumer_ws, "alias_mk")
      os.symlink(os.path.join(plugin_ws, "lib", "tdd_guard.py"), file_link)
      os.symlink(os.path.join(plugin_ws, "lib"), dir_link)
      os.symlink(
          os.path.join(consumer_ws, ".workflow", "state", "Makefile"),
          state_link,
      )

      plugin_denied = [
          "echo '# hack' > lib/./tdd_guard.py",
          "rm lib/../lib/base_script.py",
          "rm -rf lib/.",
          "rm -rf scripts/.",
          "cd lib && echo '# hack' > tdd_guard.py",
          "cd lib && rm -rf .",
          "cd .workflow/state && echo 'all:' > Makefile",
      ]
      for cmd in plugin_denied:
        res = evaluate_tool_call({
            "workspacePaths": [plugin_ws],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": cmd},
            },
        })
        self.assertEqual(
            res["decision"],
            "deny",
            f"Expected deny in plugin workspace for cmd={cmd!r}, got {res}",
        )

      consumer_denied = [
          "echo '# hack' > alias_guard",
          "rm -rf alias_lib",
          "echo 'all:' > alias_mk",
          "cd .workflow/state && echo 'all:' > Makefile",
      ]
      for cmd in consumer_denied:
        res = evaluate_tool_call({
            "workspacePaths": [consumer_ws],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": cmd},
            },
        })
        self.assertEqual(
            res["decision"],
            "deny",
            f"Expected deny in consumer workspace for cmd={cmd!r}, got {res}",
        )

      consumer_allowed = [
          "echo '# ok' > lib/./base_script.py",
          "cd lib && echo '# ok' > base_script.py",
          "rm -rf lib/.",
          "cd lib && rm -rf .",
      ]
      for cmd in consumer_allowed:
        res = evaluate_tool_call({
            "workspacePaths": [consumer_ws],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": cmd},
            },
        })
        self.assertEqual(
            res["decision"],
            "allow",
            f"Expected allow in consumer workspace for cmd={cmd!r}, got {res}",
        )

  def test_blocker2_chained_relative_cd_and_pushd_denied(self):
    """Denies chained relative cd and pushd commands escaping from a consumer repo into a Makeflow checkout (GH-242)."""
    with (
        _isolated_guard_env(),
        tempfile.TemporaryDirectory() as parent_dir,
    ):
      consumer_ws = os.path.join(parent_dir, "consumer_repo")
      plugin_dir = os.path.join(parent_dir, "makeflow_plugin")
      os.makedirs(os.path.join(consumer_ws, "lib"), exist_ok=True)
      os.makedirs(os.path.join(plugin_dir, "lib"), exist_ok=True)
      with open(
          os.path.join(plugin_dir, "plugin.json"), "w", encoding="utf-8"
      ) as f:
        json.dump({"name": "makeflow", "version": "1.0.0"}, f)

      chained_cmds = [
          "cd .. && cd makeflow_plugin && rm lib/tdd_guard.py",
          f"cd {parent_dir} && cd makeflow_plugin && echo '# hack' > lib/tdd_guard.py",
          "pushd .. && pushd makeflow_plugin && rm lib/tdd_guard.py",
          "cd .. && cd makeflow_plugin && cd lib && echo '# hack' > tdd_guard.py",
      ]
      for cmd in chained_cmds:
        res = evaluate_tool_call({
            "workspacePaths": [consumer_ws],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": cmd},
            },
        })
        self.assertEqual(
            res["decision"],
            "deny",
            f"Expected deny for chained cd/pushd cmd={cmd!r}, got {res}",
        )

  def test_blocker3_plugin_json_shell_protection_and_resilient_root_detection(
      self,
  ):
    """Protects Makeflow plugin.json from shell mutations and detects Makeflow roots even when plugin.json is missing or corrupted (GH-242)."""
    self.assertNotIn("plugin.json", GOVERNANCE_FILE_RELPATHS)

    with (
        _isolated_guard_env(),
        tempfile.TemporaryDirectory() as plugin_ws,
        tempfile.TemporaryDirectory() as consumer_ws,
    ):
      os.makedirs(os.path.join(plugin_ws, ".git"), exist_ok=True)
      os.makedirs(os.path.join(plugin_ws, "lib"), exist_ok=True)
      os.makedirs(os.path.join(plugin_ws, "scripts"), exist_ok=True)
      with open(
          os.path.join(plugin_ws, "plugin.json"), "w", encoding="utf-8"
      ) as f:
        json.dump({"name": "makeflow", "version": "1.0.0"}, f)
      with open(
          os.path.join(consumer_ws, "plugin.json"), "w", encoding="utf-8"
      ) as f:
        json.dump({"name": "consumer-app", "version": "1.0.0"}, f)

      for cmd in ("rm plugin.json", "echo '{}' > plugin.json"):
        plugin_res = evaluate_tool_call({
            "workspacePaths": [plugin_ws],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": cmd},
            },
        })
        self.assertEqual(
            plugin_res["decision"],
            "deny",
            f"Expected deny for plugin.json shell cmd={cmd!r}, got {plugin_res}",
        )

        consumer_res = evaluate_tool_call({
            "workspacePaths": [consumer_ws],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": cmd},
            },
        })
        self.assertEqual(
            consumer_res["decision"],
            "allow",
            f"Expected allow for consumer plugin.json cmd={cmd!r}, got {consumer_res}",
        )

      direct_write_res = evaluate_tool_call({
          "workspacePaths": [plugin_ws],
          "toolCall": {
              "name": "write_file",
              "args": {
                  "Path": os.path.join(plugin_ws, "plugin.json"),
                  "Content": '{"name": "makeflow", "version": "1.0.1"}\n',
              },
          },
      })
      self.assertEqual(
          direct_write_res["decision"],
          "allow",
          f"Expected direct write_file to Makeflow plugin.json allowed, got {direct_write_res}",
      )

    for manifest_content in (None, "{bad"):
      with (
          self.subTest(manifest_content=manifest_content),
          _isolated_guard_env(),
          tempfile.TemporaryDirectory() as marker_ws,
      ):
        os.makedirs(
            os.path.join(marker_ws, "skills", "makeflow"), exist_ok=True
        )
        os.makedirs(os.path.join(marker_ws, "rules"), exist_ok=True)
        os.makedirs(os.path.join(marker_ws, "lib"), exist_ok=True)
        os.makedirs(os.path.join(marker_ws, "scripts"), exist_ok=True)
        for marker_rel in (
            "hooks.json",
            os.path.join("skills", "makeflow", "SKILL.md"),
            os.path.join("rules", "AGENTS.md"),
        ):
          with open(
              os.path.join(marker_ws, marker_rel), "w", encoding="utf-8"
          ) as f:
            f.write("# marker\n")
        if manifest_content is not None:
          with open(
              os.path.join(marker_ws, "plugin.json"), "w", encoding="utf-8"
          ) as f:
            f.write(manifest_content)

        for cmd in (
            "echo '# hack' > lib/tdd_guard.py",
            "rm -rf scripts/",
        ):
          res = evaluate_tool_call({
              "workspacePaths": [marker_ws],
              "toolCall": {
                  "name": "run_command",
                  "args": {"CommandLine": cmd},
              },
          })
          self.assertEqual(
              res["decision"],
              "deny",
              f"Expected deny with markers and manifest={manifest_content!r} for cmd={cmd!r}, got {res}",
          )

    with mock.patch("os.path.isfile", return_value=False):
      ok_repo, _ = evaluate_self_modification_safety(
          "echo '# hack' > lib/tdd_guard.py",
          workspace_paths=[REPO_ROOT],
      )
      self.assertFalse(ok_repo)

  def test_nbo2_subshell_c_redirections_denied_for_protected_targets(self):
    """Denies output redirections inside bash -c and sh -c targeting protected governance files while allowing consumer targets (GH-242)."""
    with (
        _isolated_guard_env(),
        tempfile.TemporaryDirectory() as plugin_ws,
        tempfile.TemporaryDirectory() as consumer_ws,
    ):
      with open(
          os.path.join(plugin_ws, "plugin.json"), "w", encoding="utf-8"
      ) as f:
        json.dump({"name": "makeflow", "version": "1.0.0"}, f)
      os.makedirs(os.path.join(plugin_ws, "lib"), exist_ok=True)
      os.makedirs(os.path.join(consumer_ws, "lib"), exist_ok=True)

      for ws in (consumer_ws, plugin_ws):
        res = evaluate_tool_call({
            "workspacePaths": [ws],
            "toolCall": {
                "name": "run_command",
                "args": {
                    "CommandLine": (
                        'bash -c "echo x > .workflow/state/Makefile"'
                    ),
                },
            },
        })
        self.assertEqual(
            res["decision"],
            "deny",
            f"Expected deny for bash -c Makefile redir in {ws}, got {res}",
        )

      plugin_sh_res = evaluate_tool_call({
          "workspacePaths": [plugin_ws],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": 'sh -c "echo x > lib/tdd_guard.py"',
              },
          },
      })
      self.assertEqual(
          plugin_sh_res["decision"],
          "deny",
          f"Expected deny for sh -c lib/tdd_guard.py redir, got {plugin_sh_res}",
      )

      consumer_sh_res = evaluate_tool_call({
          "workspacePaths": [consumer_ws],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": 'bash -c "echo x > lib/base_script.py"',
              },
          },
      })
      self.assertEqual(
          consumer_sh_res["decision"],
          "allow",
          f"Expected allow for consumer bash -c redir, got {consumer_sh_res}",
      )

  def test_nbo4_tilde_workspace_mv_cp_directory_detection(self):
    """Expands tilde workspace roots when detecting mv and cp directory destinations (GH-242)."""
    with _isolated_guard_env() as (_hook_cwd, fake_home, _clean_env):
      plugin_ws = os.path.join(fake_home, "plugin_repo")
      os.makedirs(os.path.join(plugin_ws, "lib"), exist_ok=True)
      with open(
          os.path.join(plugin_ws, "plugin.json"), "w", encoding="utf-8"
      ) as f:
        json.dump({"name": "makeflow", "version": "1.0.0"}, f)
      os.symlink(
          os.path.join(plugin_ws, "lib"),
          os.path.join(plugin_ws, "lib_alias"),
      )

      for cmd in (
          "mv tdd_guard.py lib/.",
          "cp tdd_guard.py lib_alias",
      ):
        res = evaluate_tool_call({
            "workspacePaths": ["~/plugin_repo"],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": cmd},
            },
        })
        self.assertEqual(
            res["decision"],
            "deny",
            f"Expected deny for tilde workspace cmd={cmd!r}, got {res}",
        )

  def test_workflow_state_directory_deletion_denied_in_consumer_and_plugin_repos(
      self,
  ):
    """Denies deleting .workflow or .workflow/state directories in both consumer and plugin repos while allowing non-state subdirectories (GH-242)."""
    with (
        _isolated_guard_env(),
        tempfile.TemporaryDirectory() as consumer_ws,
        tempfile.TemporaryDirectory() as plugin_ws,
    ):
      os.makedirs(
          os.path.join(consumer_ws, ".workflow", "state"), exist_ok=True
      )
      os.makedirs(
          os.path.join(consumer_ws, ".workflow", "archive"), exist_ok=True
      )
      os.makedirs(os.path.join(consumer_ws, ".workflow", "tmp"), exist_ok=True)
      os.makedirs(os.path.join(consumer_ws, "lib"), exist_ok=True)
      state_dir_link = os.path.join(consumer_ws, "alias_workflow_state")
      os.symlink(
          os.path.join(consumer_ws, ".workflow", "state"), state_dir_link
      )

      with open(
          os.path.join(plugin_ws, "plugin.json"), "w", encoding="utf-8"
      ) as f:
        json.dump({"name": "makeflow", "version": "1.0.0"}, f)
      os.makedirs(os.path.join(plugin_ws, ".workflow", "state"), exist_ok=True)

      consumer_denied = [
          "rm -rf .workflow/state",
          "rm -rf .workflow/state/.",
          "rm -rf .workflow",
          "cd .workflow && rm -rf state",
          "rm -rf alias_workflow_state",
          f"rm -rf {state_dir_link}",
      ]
      for cmd in consumer_denied:
        res = evaluate_tool_call({
            "workspacePaths": [consumer_ws],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": cmd},
            },
        })
        self.assertEqual(
            res["decision"],
            "deny",
            f"Expected deny in consumer repo for cmd={cmd!r}, got {res}",
        )
        self.assertIn(
            "Blocked self-modification attempt on makeflow governance file",
            res.get("reason", ""),
        )

      plugin_denied = [
          "rm -rf .workflow/state",
          "rm -rf .workflow",
      ]
      for cmd in plugin_denied:
        res = evaluate_tool_call({
            "workspacePaths": [plugin_ws],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": cmd},
            },
        })
        self.assertEqual(
            res["decision"],
            "deny",
            f"Expected deny in plugin repo for cmd={cmd!r}, got {res}",
        )
        self.assertIn(
            "Blocked self-modification attempt on makeflow governance file",
            res.get("reason", ""),
        )

      consumer_allowed = [
          "rm -rf .workflow/archive",
          "rm -rf .workflow/tmp",
          "rm -rf lib",
      ]
      for cmd in consumer_allowed:
        res = evaluate_tool_call({
            "workspacePaths": [consumer_ws],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": cmd},
            },
        })
        self.assertEqual(
            res["decision"],
            "allow",
            f"Expected allow in consumer repo for cmd={cmd!r}, got {res}",
        )


class TestPreToolGuardScriptExecution(unittest.TestCase):
  """Hermetic in-memory tests for PreToolGuardScript(BaseScript) and lib/pre_tool_guard.py (GH-204)."""

  def _run_script_isolated(self, raw_stdin, args=None):
    """Executes PreToolGuardScript in memory with poisoned OS stdio streams."""
    from lib.pre_tool_guard import PreToolGuardScript

    inp = io.StringIO(raw_stdin)
    out = io.StringIO()
    err = io.StringIO()
    orig_stdin, orig_stdout, orig_stderr = sys.stdin, sys.stdout, sys.stderr
    try:
      sys.stdin = None
      sys.stdout = None
      sys.stderr = None
      code = PreToolGuardScript().execute(
          args=args if args is not None else [],
          stdout=out,
          stderr=err,
          stdin=inp,
      )
    finally:
      sys.stdin = orig_stdin
      sys.stdout = orig_stdout
      sys.stderr = orig_stderr
    return code, out.getvalue(), err.getvalue()

  def test_empty_and_whitespace_stdin_returns_allow(self):
    """Verifies GH-204 empty or whitespace stdin emits allow JSON on stdout with exit 0."""
    for raw in ("", "   \n\t  "):
      with self.subTest(raw=raw):
        code, stdout_text, stderr_text = self._run_script_isolated(raw)
        self.assertEqual(code, 0)
        self.assertEqual(stderr_text, "")
        self.assertEqual(json.loads(stdout_text), {"decision": "allow"})

  def test_valid_allow_payload_in_memory(self):
    """Verifies GH-204 safe payload executes hermetically and returns allow JSON."""
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      payload = {
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "read_file",
              "args": {"Path": os.path.join(ws_dir, "README.md")},
          },
      }
      code, stdout_text, stderr_text = self._run_script_isolated(
          json.dumps(payload)
      )
      self.assertEqual(code, 0)
      self.assertEqual(stderr_text, "")
      self.assertEqual(json.loads(stdout_text), {"decision": "allow"})

  def test_valid_deny_payload_in_memory(self):
    """Verifies GH-204 destructive git payload emits deny JSON on stdout with exit 0."""
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      payload = {
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {"CommandLine": "git reset --hard HEAD"},
          },
      }
      code, stdout_text, stderr_text = self._run_script_isolated(
          json.dumps(payload)
      )
      self.assertEqual(code, 0)
      self.assertEqual(stderr_text, "")
      parsed = json.loads(stdout_text)
      self.assertEqual(parsed["decision"], "deny")
      self.assertIn("git reset --hard", parsed["reason"])

  def test_malformed_json_fails_open_with_json_on_stdout(self):
    """Verifies GH-204 malformed JSON fails open with allow JSON and empty stderr."""
    code, stdout_text, stderr_text = self._run_script_isolated("{invalid json")
    self.assertEqual(code, 0)
    self.assertEqual(stderr_text, "")
    parsed = json.loads(stdout_text)
    self.assertEqual(parsed["decision"], "allow")
    self.assertTrue(
        parsed.get("reason", "").startswith("Hook error:"),
        f"Expected fail-open Hook error reason, got {parsed}",
    )

  def test_internal_evaluate_exception_fails_open_with_json_on_stdout(self):
    """Verifies GH-204 runtime exception inside evaluate_tool_call fails open cleanly."""
    import lib.pre_tool_guard as guard_lib

    payload = {"toolCall": {"name": "read_file", "args": {"Path": "README.md"}}}
    with mock.patch.object(
        guard_lib,
        "evaluate_tool_call",
        side_effect=RuntimeError("synthetic evaluation failure"),
    ):
      code, stdout_text, stderr_text = self._run_script_isolated(
          json.dumps(payload)
      )
    self.assertEqual(code, 0)
    self.assertEqual(stderr_text, "")
    parsed = json.loads(stdout_text)
    self.assertEqual(parsed["decision"], "allow")
    self.assertIn("Hook error: synthetic evaluation failure", parsed["reason"])

  def test_lib_pre_tool_guard_is_protected_governance_file(self):
    """Verifies GH-204 lib/pre_tool_guard.py is protected against shell and direct file writes."""
    import lib.pre_tool_guard as guard_lib

    self.assertIn("lib/pre_tool_guard.py", GOVERNANCE_FILE_RELPATHS)
    self.assertIn("lib/pre_tool_guard.py", guard_lib.GOVERNANCE_FILE_RELPATHS)

    with _isolated_guard_env(), tempfile.TemporaryDirectory() as plugin_ws:
      os.makedirs(os.path.join(plugin_ws, ".git"))
      os.makedirs(os.path.join(plugin_ws, "lib"))
      with open(
          os.path.join(plugin_ws, "plugin.json"), "w", encoding="utf-8"
      ) as f:
        json.dump({"name": "makeflow", "version": "1.0.0"}, f)

      for cmd in (
          "echo hacked > lib/pre_tool_guard.py",
          "rm -f lib/pre_tool_guard.py",
          "truncate -s 0 lib/pre_tool_guard.py",
      ):
        safe, reason = evaluate_self_modification_safety(cmd, [plugin_ws])
        self.assertFalse(safe, f"Expected deny for shell cmd={cmd!r}")
        self.assertIn("lib/pre_tool_guard.py", reason)

      target_file = os.path.join(plugin_ws, "lib", "pre_tool_guard.py")
      for tool_name in ("write_file", "edit_file"):
        res = evaluate_tool_call({
            "workspacePaths": [plugin_ws],
            "toolCall": {
                "name": tool_name,
                "args": {"Path": target_file},
            },
        })
        self.assertEqual(
            res["decision"],
            "deny",
            f"Expected deny for {tool_name} on lib/pre_tool_guard.py without active ticket, got {res}",
        )

      # With only 10_ticket.md (no approved 20_plan.md), direct edits remain denied (GH-331)
      state_dir = os.path.join(plugin_ws, ".workflow", "state")
      os.makedirs(state_dir)
      with open(
          os.path.join(state_dir, "10_ticket.md"), "w", encoding="utf-8"
      ) as f:
        f.write("# 10_ticket: GH-204\n")
      ticket_only_res = evaluate_tool_call({
          "workspacePaths": [plugin_ws],
          "toolCall": {
              "name": "edit_file",
              "args": {"Path": target_file},
          },
      })
      self.assertEqual(ticket_only_res["decision"], "deny")

      # With an unapproved 20_plan.md, direct edits remain denied
      with open(
          os.path.join(state_dir, "20_plan.md"), "w", encoding="utf-8"
      ) as f:
        f.write(
            "### Specific Edits to Existing Files\n\n"
            "- `lib/pre_tool_guard.py`: Update write guard.\n"
        )
      unapproved_res = evaluate_tool_call({
          "workspacePaths": [plugin_ws],
          "toolCall": {
              "name": "edit_file",
              "args": {"Path": target_file},
          },
      })
      self.assertEqual(unapproved_res["decision"], "deny")

      # With 20_plan.md + 20_plan_approved.md explicitly listing lib/pre_tool_guard.py, allow
      with open(
          os.path.join(state_dir, "20_plan_approved.md"), "w", encoding="utf-8"
      ) as f:
        f.write("APPROVED\n")
      active_res = evaluate_tool_call({
          "workspacePaths": [plugin_ws],
          "toolCall": {
              "name": "edit_file",
              "args": {"Path": target_file},
          },
      })
      self.assertEqual(active_res["decision"], "allow")

  def test_lib_harness_boundary_is_protected_governance_file(self):
    """Verifies GH-222 lib/harness_boundary.py is write-protected in GOVERNANCE_FILE_RELPATHS."""
    import lib.pre_tool_guard as guard_lib

    self.assertIn("lib/harness_boundary.py", GOVERNANCE_FILE_RELPATHS)
    self.assertIn("lib/harness_boundary.py", guard_lib.GOVERNANCE_FILE_RELPATHS)

    with _isolated_guard_env(), tempfile.TemporaryDirectory() as plugin_ws:
      os.makedirs(os.path.join(plugin_ws, ".git"))
      os.makedirs(os.path.join(plugin_ws, "lib"))
      with open(
          os.path.join(plugin_ws, "plugin.json"), "w", encoding="utf-8"
      ) as f:
        json.dump({"name": "makeflow", "version": "1.0.0"}, f)

      safe, reason = evaluate_self_modification_safety(
          "echo hacked > lib/harness_boundary.py", [plugin_ws]
      )
      self.assertFalse(safe)
      self.assertIn("lib/harness_boundary.py", reason)

      target_file = os.path.join(plugin_ws, "lib", "harness_boundary.py")
      res = evaluate_tool_call({
          "workspacePaths": [plugin_ws],
          "toolCall": {
              "name": "write_file",
              "args": {"Path": target_file},
          },
      })
      self.assertEqual(res["decision"], "deny")

  def test_read_file_harness_boundary_wire_and_fast_path(self):
    """Verifies GH-222 PreToolGuardScript JSON deny on out-of-scope read_file and fast-path allow."""
    import lib.pre_tool_guard as guard_lib

    with _isolated_guard_env(), tempfile.TemporaryDirectory() as plugin_ws:
      os.makedirs(os.path.join(plugin_ws, "lib"))
      state_dir = os.path.join(plugin_ws, ".workflow", "state")
      os.makedirs(state_dir)
      with open(
          os.path.join(plugin_ws, "plugin.json"), "w", encoding="utf-8"
      ) as f:
        json.dump({"name": "makeflow", "version": "1.0.0"}, f)
      with open(
          os.path.join(plugin_ws, "lib", "turn_boundary.py"),
          "w",
          encoding="utf-8",
      ) as f:
        f.write("# turn boundary\n")
      with open(
          os.path.join(plugin_ws, "README.md"), "w", encoding="utf-8"
      ) as f:
        f.write("# README\n")
      with open(
          os.path.join(state_dir, "active_target.json"), "w", encoding="utf-8"
      ) as f:
        json.dump({"target": "T30", "previousTarget": "T20"}, f)
      with open(
          os.path.join(state_dir, "20_plan.md"), "w", encoding="utf-8"
      ) as f:
        f.write("# Plan\n\nEdit `README.md` only.\n")

      # Out-of-scope governance read denied via PreToolGuardScript JSON wire output
      deny_payload = {
          "workspacePaths": [plugin_ws],
          "conversationId": "sup-1",
          "toolCall": {
              "name": "read_file",
              "args": {
                  "Path": os.path.join(plugin_ws, "lib", "turn_boundary.py")
              },
          },
      }
      code, stdout_text, stderr_text = self._run_script_isolated(
          json.dumps(deny_payload)
      )
      self.assertEqual(code, 0)
      self.assertEqual(stderr_text, "")
      parsed = json.loads(stdout_text)
      self.assertEqual(parsed["decision"], "deny")
      self.assertIn("[SCAR-ID: PROC-124]", parsed["reason"])
      self.assertIn("OUT_OF_SCOPE_HARNESS_INSPECTION", parsed["reason"])
      self.assertIn("make -f .workflow/state/Makefile", parsed["reason"])

      # Fast-path read_file skips write governance and advance_target extraction
      allow_payload = {
          "workspacePaths": [plugin_ws],
          "conversationId": "sup-1",
          "toolCall": {
              "name": "read_file",
              "args": {"Path": os.path.join(plugin_ws, "README.md")},
          },
      }
      with (
          mock.patch.object(
              guard_lib,
              "_check_direct_file_governance",
              side_effect=AssertionError("read_file must bypass write guard"),
          ),
          mock.patch.object(
              guard_lib,
              "extract_advance_target_calls",
              side_effect=AssertionError(
                  "read_file must bypass advance extraction"
              ),
          ),
      ):
        res = evaluate_tool_call(allow_payload)
      self.assertEqual(res, {"decision": "allow"})


class TestPreToolGuardCommitMessageEnforcement(unittest.TestCase):
  """Tests PreToolUse Conventional Commits enforcement on git commit (GH-292)."""

  def _init_repo(
      self,
      repo_dir: str,
      branch: str = "feat/292-hooks",
      github_remote: bool = True,
      initial_msg: str = "chore: initial commit",
  ) -> str:
    """Initializes an isolated git repo with active workflow state directory."""
    subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Makeflow Test"],
        cwd=repo_dir,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_dir,
        check=True,
    )
    if github_remote:
      subprocess.run(
          ["git", "remote", "add", "origin", "git@github.com:owner/repo.git"],
          cwd=repo_dir,
          check=True,
      )
    subprocess.run(
        ["git", "checkout", "-q", "-b", branch], cwd=repo_dir, check=True
    )
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", initial_msg],
        cwd=repo_dir,
        check=True,
    )
    state_dir = os.path.join(repo_dir, ".workflow", "state")
    os.makedirs(state_dir, exist_ok=True)
    with open(
        os.path.join(state_dir, "45_review_approved.md"), "w", encoding="utf-8"
    ) as f:
      f.write("APPROVED\n")
    return state_dir

  def test_active_remote_ticket_validates_m_multi_m_and_message_equals(self):
    """Allows valid remote -m / multi--m commits and denies malformed or trailer-less commits."""
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      self._init_repo(ws_dir, branch="feat/292-hooks", github_remote=True)

      for valid_cmd in (
          "git commit -m 'feat(hooks): enforce cc (closes #292)'",
          "git commit -m 'feat(hooks): enforce cc' -m 'Issue: #292'",
          "git commit --message='fix(gates): validate head' --message='closes #292'",
      ):
        res = evaluate_tool_call({
            "workspacePaths": [ws_dir],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": valid_cmd},
            },
        })
        self.assertEqual(res["decision"], "force_ask", (valid_cmd, res))
        self.assertIn("Target 50 Commit Gate", res["reason"])

      for invalid_cmd in (
          "git commit -m 'random non conventional message'",
          "git commit -m 'feat(hooks): missing issue trailer'",
          "git commit -m 'feat(hooks): trailing period. (closes #292)'",
          "git commit -m 'feat(hooks): wrong trailer' -m 'Ticket: LOCAL-1'",
          "git commit -m 'feat(hooks): wrong gh trailer' -m 'Ticket: GH-292'",
      ):
        res = evaluate_tool_call({
            "workspacePaths": [ws_dir],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": invalid_cmd},
            },
        })
        self.assertEqual(res["decision"], "deny", (invalid_cmd, res))
        self.assertIn("SCAR-GIT-20", res["reason"])
        self.assertIn("INVALID_COMMIT_MESSAGE_CONTRACT", res["reason"])

  def test_active_local_ticket_enforces_local_trailer_and_blocks_closes_issue(self):
    """Enforces Ticket: LOCAL-<N> and blocks closes #<N> on local session commits."""
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      self._init_repo(
          ws_dir, branch="fix/local-7-offline", github_remote=False
      )

      ok_res = evaluate_tool_call({
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": (
                      "git commit -m 'fix(hooks): offline check'"
                      " -m 'Ticket: LOCAL-7'"
                  )
              },
          },
      })
      self.assertEqual(ok_res["decision"], "force_ask", ok_res)

      for bad_cmd in (
          "git commit -m 'fix(hooks): offline check (closes #7)' -m 'Ticket: LOCAL-7'",
          "git commit -m 'fix(hooks): missing local trailer'",
          "git commit -m 'fix(hooks): wrong local num' -m 'Ticket: LOCAL-9'",
      ):
        res = evaluate_tool_call({
            "workspacePaths": [ws_dir],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": bad_cmd},
            },
        })
        self.assertEqual(res["decision"], "deny", (bad_cmd, res))
        self.assertIn("INVALID_COMMIT_MESSAGE_CONTRACT", res["reason"])

  def test_file_flag_dash_c_stdin_and_missing_file_handling(self):
    """Validates -F / --file= with workspace and -C resolution, and denies missing or stdin files."""
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      state_dir = self._init_repo(
          ws_dir, branch="feat/292-file-flag", github_remote=True
      )
      msg_path = os.path.join(state_dir, "50_commit_msg.txt")
      with open(msg_path, "w", encoding="utf-8") as f:
        f.write("feat(hooks): validate file flag\n\nIssue: #292\n")

      for ok_cmd in (
          "git commit -F .workflow/state/50_commit_msg.txt",
          "git commit --file=.workflow/state/50_commit_msg.txt",
      ):
        res = evaluate_tool_call({
            "workspacePaths": [ws_dir],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": ok_cmd},
            },
        })
        self.assertEqual(res["decision"], "force_ask", (ok_cmd, res))

      # Nested -C <subdir> resolves relative -F path inside subdir
      subdir = os.path.join(ws_dir, "subrepo")
      os.makedirs(subdir)
      with open(os.path.join(subdir, "commit.txt"), "w", encoding="utf-8") as f:
        f.write("feat(sub): nested repo commit\n\nIssue: #292\n")
      nested_ok = evaluate_tool_call({
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {"CommandLine": "git -C subrepo commit -F commit.txt"},
          },
      })
      self.assertEqual(nested_ok["decision"], "force_ask", nested_ok)

      # Invalid file content, missing file, and stdin '-F -' / '--file=-' are denied
      with open(msg_path, "w", encoding="utf-8") as f:
        f.write("not conventional\n")
      for bad_cmd in (
          "git commit -F .workflow/state/50_commit_msg.txt",
          "git commit -F .workflow/state/does_not_exist.txt",
          "git commit -F - <<'EOF'\nfeat: stdin\n\nIssue: #292\nEOF",
          "git commit --file=-",
      ):
        res = evaluate_tool_call({
            "workspacePaths": [ws_dir],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": bad_cmd},
            },
        })
        self.assertEqual(res["decision"], "deny", (bad_cmd, res))
        self.assertIn("INVALID_COMMIT_MESSAGE_CONTRACT", res["reason"])

      # Combining -m and -F is rejected (conflicting commit message sources)
      with open(msg_path, "w", encoding="utf-8") as f:
        f.write("feat(hooks): valid file\n\nIssue: #292\n")
      mixed_res = evaluate_tool_call({
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": (
                      "git commit -m 'bad inline' -F"
                      " .workflow/state/50_commit_msg.txt"
                  )
              },
          },
      })
      self.assertEqual(mixed_res["decision"], "deny", mixed_res)
      self.assertIn("INVALID_COMMIT_MESSAGE_CONTRACT", mixed_res["reason"])

  def test_bare_commit_and_amend_no_edit_validation(self):
    """Denies bare git commit / --amend without --no-edit, and validates HEAD for --amend --no-edit."""
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      self._init_repo(
          ws_dir,
          branch="feat/292-amend",
          github_remote=True,
          initial_msg="bad initial message",
      )
      for bad_cmd in (
          "git commit",
          "git commit --amend",
          "git commit --amend --no-edit",
      ):
        res = evaluate_tool_call({
            "workspacePaths": [ws_dir],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": bad_cmd},
            },
        })
        self.assertEqual(res["decision"], "deny", (bad_cmd, res))
        self.assertIn("INVALID_COMMIT_MESSAGE_CONTRACT", res["reason"])

      # Amend HEAD to valid Conventional Commit -> --amend --no-edit passes to force_ask
      subprocess.run(
          [
              "git",
              "commit",
              "--amend",
              "--allow-empty",
              "-q",
              "-m",
              "feat(hooks): valid head commit\n\nIssue: #292\n",
          ],
          cwd=ws_dir,
          check=True,
      )
      ok_amend = evaluate_tool_call({
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {"CommandLine": "git commit --amend --no-edit"},
          },
      })
      self.assertEqual(ok_amend["decision"], "force_ask", ok_amend)
      self.assertIn("Target 50 Commit Gate", ok_amend["reason"])

  def test_target90_target80_and_inactive_workspace_bypass_commit_validation(self):
    """Preserves Target 90, Target 80 landing, and inactive non-Makeflow workspace behavior."""
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as inactive_ws:
      res_inactive = evaluate_tool_call({
          "workspacePaths": [inactive_ws],
          "toolCall": {
              "name": "run_command",
              "args": {"CommandLine": "git commit -m 'wip non conventional'"},
          },
      })
      self.assertEqual(res_inactive["decision"], "force_ask", res_inactive)

    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      state_dir = self._init_repo(
          ws_dir, branch="feat/292-bypass", github_remote=True
      )
      # Target 90 history commit stays on Target 90 gate
      res_t90_msg = evaluate_tool_call({
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": "git commit -m 'docs(history): record GH-292'"
              },
          },
      })
      self.assertEqual(res_t90_msg["decision"], "force_ask", res_t90_msg)
      self.assertIn("Target 90 History Ledger Gate", res_t90_msg["reason"])

      # Target 80 landing commit bypasses Target 50 CC check
      with open(
          os.path.join(state_dir, "active_target.json"), "w", encoding="utf-8"
      ) as f:
        json.dump(
            {"previousTarget": "T75", "target": "T80", "targetName": "land"}, f
        )
      res_t80 = evaluate_tool_call({
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": "git commit -m 'Merge branch feat/292-bypass'"
              },
          },
      })
      self.assertEqual(res_t80["decision"], "force_ask", res_t80)

  def test_scar_proc_102_bundling_and_same_turn_precedence_over_invalid_commit_msg(
      self,
  ):
    """SCAR-PROC-102 pause-gate bundling and same-turn denials fire before commit validation."""
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      self._init_repo(
          ws_dir, branch="feat/292-precedence", github_remote=True
      )
      transcript_path = os.path.join(ws_dir, "transcript.jsonl")
      _write_transcript(
          transcript_path,
          [
              {"step_index": 1, "type": "USER_INPUT", "content": "Review ok"},
              {
                  "step_index": 2,
                  "type": "PLANNER_RESPONSE",
                  "content": "Entering T50",
              },
          ],
      )

      # 1. Bundled make commit && git commit -m "bad" -> SCAR-PROC-102 wins
      bundled = evaluate_tool_call({
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": (
                      "make -f .workflow/state/Makefile commit FROM=T45"
                      ' APPROVED=1 && git commit -m "bad msg"'
                  )
              },
          },
      })
      self.assertEqual(bundled["decision"], "deny", bundled)
      self.assertIn("SCAR-PROC-102", bundled["reason"])

      # 2. Enter T50 cleanly, then same-turn git commit -m "bad" -> SCAR-PROC-102 wins
      enter_t50 = evaluate_tool_call({
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": (
                      "make -f .workflow/state/Makefile commit FROM=T45"
                      " APPROVED=1"
                  )
              },
          },
      })
      self.assertEqual(enter_t50["decision"], "allow", enter_t50)

      same_turn = evaluate_tool_call({
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {"CommandLine": 'git commit -m "bad msg"'},
          },
      })
      self.assertEqual(same_turn["decision"], "deny", same_turn)
      self.assertIn("SCAR-PROC-102", same_turn["reason"])

      # 3. After human approval turn, invalid message is denied by SCAR-GIT-20
      _write_transcript(
          transcript_path,
          [
              {"step_index": 1, "type": "USER_INPUT", "content": "Review ok"},
              {
                  "step_index": 2,
                  "type": "PLANNER_RESPONSE",
                  "content": "Entering T50",
              },
              {
                  "step_index": 3,
                  "type": "USER_INPUT",
                  "content": "Commit approved.",
              },
          ],
      )
      after_human = evaluate_tool_call({
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {"CommandLine": 'git commit -m "bad msg"'},
          },
      })
      self.assertEqual(after_human["decision"], "deny", after_human)
      self.assertIn("SCAR-GIT-20", after_human["reason"])
      self.assertIn("INVALID_COMMIT_MESSAGE_CONTRACT", after_human["reason"])

  def test_amend_no_edit_rejects_alternate_message_sources_and_interactive_edit(
      self,
  ):
    """Denies --amend --no-edit with -C/-c/--reuse-message/--fixup/--squash/-t/--edit/-e and honors last-flag-wins (BLOCKER-1)."""
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      state_dir = self._init_repo(
          ws_dir,
          branch="feat/292-alt-sources",
          github_remote=True,
          initial_msg="wip invalid parent commit",
      )
      # Add a valid Conventional Commit as HEAD above invalid HEAD~1
      subprocess.run(
          [
              "git",
              "commit",
              "--allow-empty",
              "-q",
              "-m",
              "feat(hooks): valid head commit\n\nIssue: #292\n",
          ],
          cwd=ws_dir,
          check=True,
      )
      with open(
          os.path.join(state_dir, "50_commit_msg.txt"), "w", encoding="utf-8"
      ) as f:
        f.write("feat(hooks): valid file msg\n\nIssue: #292\n")

      for bad_cmd in (
          "git commit --amend --no-edit -C HEAD~1",
          "git commit --amend --no-edit -CHEAD~1",
          "git commit --amend --no-edit -c HEAD~1",
          "git commit --amend --no-edit --reuse-message HEAD~1",
          "git commit --amend --no-edit --reuse-message=HEAD~1",
          "git commit --amend --no-edit --reedit-message=HEAD~1",
          "git commit --amend --no-edit --fixup=HEAD",
          "git commit --amend --no-edit --squash=HEAD",
          "git commit --amend --no-edit -t .workflow/state/50_commit_msg.txt",
          "git commit --amend --no-edit --template=.workflow/state/50_commit_msg.txt",
          "git commit --amend --no-edit -e",
          "git commit --amend --no-edit --edit",
          "git commit --amend --no-edit -ae",
          "git commit -m 'feat(hooks): valid (closes #292)' --edit",
          "git commit -F .workflow/state/50_commit_msg.txt -e",
      ):
        res = evaluate_tool_call({
            "workspacePaths": [ws_dir],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": bad_cmd},
            },
        })
        self.assertEqual(res["decision"], "deny", (bad_cmd, res))
        self.assertIn("SCAR-GIT-20", res["reason"], (bad_cmd, res))
        self.assertIn(
            "INVALID_COMMIT_MESSAGE_CONTRACT", res["reason"], (bad_cmd, res)
        )

      # Last-flag-wins: --edit/--e followed by --no-edit validates HEAD and passes
      for ok_cmd in (
          "git commit --amend --edit --no-edit",
          "git commit --amend -e --no-edit",
          "git commit --amend -ae --no-edit",
      ):
        res = evaluate_tool_call({
            "workspacePaths": [ws_dir],
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": ok_cmd},
            },
        })
        self.assertEqual(res["decision"], "force_ask", (ok_cmd, res))

  def test_amend_no_edit_fails_closed_for_nonexistent_dash_c_directory(self):
    """Denies git -C <nonexistent> commit --amend --no-edit without falling back to process CWD (BLOCKER-2)."""
    real_cwd = os.getcwd()
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as outer_dir:
      host_repo = os.path.join(outer_dir, "host_repo")
      os.makedirs(host_repo)
      self._init_repo(
          host_repo,
          branch="feat/292-host",
          github_remote=True,
          initial_msg="feat(host): valid host head\n\nIssue: #292\n",
      )
      ws_dir = os.path.join(outer_dir, "workspace")
      os.makedirs(ws_dir)
      ws_state = self._init_repo(
          ws_dir,
          branch="feat/292-ws",
          github_remote=True,
          initial_msg="feat(ws): valid ws head\n\nIssue: #292\n",
      )
      with open(
          os.path.join(ws_state, "10_ticket.json"), "w", encoding="utf-8"
      ) as f:
        json.dump(
            {"ticket_id": "GH-292", "title": "Enforce CC", "source": "github"},
            f,
        )

      try:
        os.chdir(host_repo)
        res = evaluate_tool_call({
            "workspacePaths": [ws_dir],
            "toolCall": {
                "name": "run_command",
                "args": {
                    "CommandLine": (
                        "git -C nonexistent_subdir commit --amend --no-edit"
                    )
                },
            },
        })
      finally:
        os.chdir(real_cwd)

      self.assertEqual(res["decision"], "deny", res)
      self.assertIn("SCAR-GIT-20", res["reason"])
      self.assertIn("INVALID_COMMIT_MESSAGE_CONTRACT", res["reason"])

  def test_active_target_json_is_authoritative_over_stale_next_action_and_fallbacks(
      self,
  ):
    """Protected active_target.json overrides stale 99_next_action.md T80; covers T90, T80 fallback, and chained add (BLOCKER-3, NBO-2)."""
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      state_dir = self._init_repo(
          ws_dir, branch="feat/292-authoritative", github_remote=True
      )
      # active_target.json says T50 while 99_next_action.md is stale/tampered with Target 80
      active_path = os.path.join(state_dir, "active_target.json")
      next_path = os.path.join(state_dir, "99_next_action.md")
      with open(active_path, "w", encoding="utf-8") as f:
        json.dump(
            {"previousTarget": "T45", "target": "T50", "targetName": "commit"},
            f,
        )
      with open(next_path, "w", encoding="utf-8") as f:
        f.write("# 99_next_action\n- Active Target: Target 80 (land)\n")

      denied = evaluate_tool_call({
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {"CommandLine": "git commit -m 'wip bypass attempt'"},
          },
      })
      self.assertEqual(denied["decision"], "deny", denied)
      self.assertIn("INVALID_COMMIT_MESSAGE_CONTRACT", denied["reason"])

      # Chained git add && git commit validates commit subcommand
      chained_bad = evaluate_tool_call({
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": "git add app.py && git commit -m 'bad msg'"
              },
          },
      })
      self.assertEqual(chained_bad["decision"], "deny", chained_bad)
      self.assertIn("INVALID_COMMIT_MESSAGE_CONTRACT", chained_bad["reason"])

      chained_ok = evaluate_tool_call({
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {
                  "CommandLine": (
                      "git add app.py && git commit -m 'feat(hooks): chained"
                      " commit (closes #292)'"
                  )
              },
          },
      })
      self.assertEqual(chained_ok["decision"], "force_ask", chained_ok)

      # active_target.json T90 bypasses T50 CC validation
      with open(active_path, "w", encoding="utf-8") as f:
        json.dump(
            {"previousTarget": "T80", "target": "T90", "targetName": "finish"},
            f,
        )
      t90_res = evaluate_tool_call({
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {"CommandLine": "git commit -m 'Merge release branch'"},
          },
      })
      self.assertEqual(t90_res["decision"], "force_ask", t90_res)

      # When active_target.json is absent, 99_next_action.md Target 80 fallback applies
      os.unlink(active_path)
      t80_fallback = evaluate_tool_call({
          "workspacePaths": [ws_dir],
          "toolCall": {
              "name": "run_command",
              "args": {"CommandLine": "git commit -m 'Merge branch feat/292'"},
          },
      })
      self.assertEqual(t80_fallback["decision"], "force_ask", t80_fallback)

  def test_lib_commit_msg_is_protected_governance_file(self):
    """Verifies lib/commit_msg.py is protected against shell and direct self-modification (NBO-1)."""
    import lib.pre_tool_guard as guard_lib

    self.assertIn("lib/commit_msg.py", GOVERNANCE_FILE_RELPATHS)
    self.assertIn("lib/commit_msg.py", guard_lib.GOVERNANCE_FILE_RELPATHS)

    with _isolated_guard_env(), tempfile.TemporaryDirectory() as plugin_ws:
      os.makedirs(os.path.join(plugin_ws, ".git"))
      os.makedirs(os.path.join(plugin_ws, "lib"))
      with open(
          os.path.join(plugin_ws, "plugin.json"), "w", encoding="utf-8"
      ) as f:
        json.dump({"name": "makeflow", "version": "1.0.0"}, f)

      for cmd in (
          "echo hacked > lib/commit_msg.py",
          "rm -f lib/commit_msg.py",
      ):
        safe, reason = evaluate_self_modification_safety(cmd, [plugin_ws])
        self.assertFalse(safe, f"Expected deny for shell cmd={cmd!r}")
        self.assertIn("lib/commit_msg.py", reason)

      target_file = os.path.join(plugin_ws, "lib", "commit_msg.py")
      res = evaluate_tool_call({
          "workspacePaths": [plugin_ws],
          "toolCall": {
              "name": "edit_file",
              "args": {"Path": target_file},
          },
      })
      self.assertEqual(res["decision"], "deny", res)

  def test_ask_question_denied_at_all_six_pause_gates_scar_proc_161(self):
    """GH-270: Denies ask_question with SCAR-PROC-161 at T20, T42, T45, T50, T60, and T75."""
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      state_dir = os.path.join(ws_dir, ".workflow", "state")
      os.makedirs(state_dir)
      transcript_path = os.path.join(ws_dir, "transcript.jsonl")
      _write_transcript(
          transcript_path,
          [
              {"step_index": 1, "type": "USER_INPUT", "content": "Continue"},
              {
                  "step_index": 2,
                  "type": "PLANNER_RESPONSE",
                  "content": "Full proposal summary with [plan](file:///tmp/plan.md).",
                  "tool_calls": [],
              },
          ],
      )

      for gate_id in ("T20", "T42", "T45", "T50", "T60", "T75"):
        with self.subTest(source="active_target", gate=gate_id):
          active_path = os.path.join(state_dir, "active_target.json")
          with open(active_path, "w", encoding="utf-8") as fh:
            json.dump({"target": gate_id, "previousTarget": "T15"}, fh)
          res = evaluate_tool_call({
              "transcriptPath": transcript_path,
              "workspacePaths": [ws_dir],
              "toolCall": {"name": "ask_question", "args": {}},
          })
          self.assertEqual(res["decision"], "deny", res)
          reason = res.get("reason", "")
          self.assertIn("[SCAR-ID: PROC-161]", reason)
          self.assertIn("ASK_QUESTION_AT_PAUSE_GATE", reason)
          os.unlink(active_path)

        with self.subTest(source="gate_entry", gate=gate_id):
          gate_path = os.path.join(state_dir, "gate_entry.json")
          with open(gate_path, "w", encoding="utf-8") as fh:
            json.dump({"gate": gate_id, "fromTarget": "T15"}, fh)
          res = evaluate_tool_call({
              "transcriptPath": transcript_path,
              "workspacePaths": [ws_dir],
              "toolCall": {"name": "ask_question", "args": {}},
          })
          self.assertEqual(res["decision"], "deny", res)
          reason = res.get("reason", "")
          self.assertIn("[SCAR-ID: PROC-161]", reason)
          self.assertIn("ASK_QUESTION_AT_PAUSE_GATE", reason)
          os.unlink(gate_path)

  def test_ask_question_denied_when_bundled_with_plan_write_or_gate_entry_in_same_turn(
      self,
  ):
    """GH-270: Denies ask_question in the same turn after writing 20_plan.md or advancing to T20."""
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      state_dir = os.path.join(ws_dir, ".workflow", "state")
      os.makedirs(state_dir)
      transcript_path = os.path.join(ws_dir, "transcript.jsonl")

      # Case 1: Writing 20_plan.md in the same turn then calling ask_question
      _write_transcript(
          transcript_path,
          [
              {"step_index": 1, "type": "USER_INPUT", "content": "Draft plan"},
              {
                  "step_index": 2,
                  "type": "PLANNER_RESPONSE",
                  "content": "Drafted plan at [20_plan.md](file:///tmp/20_plan.md).",
                  "tool_calls": [{
                      "name": "write_file",
                      "args": {"Path": os.path.join(state_dir, "20_plan.md")},
                  }],
              },
              {
                  "step_index": 3,
                  "type": "PLANNER_RESPONSE",
                  "content": "Should we approve?",
                  "tool_calls": [{"name": "ask_question", "args": {}}],
              },
          ],
      )
      res_write = evaluate_tool_call({
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {"name": "ask_question", "args": {}},
      })
      self.assertEqual(res_write["decision"], "deny", res_write)
      self.assertIn("[SCAR-ID: PROC-161]", res_write.get("reason", ""))
      self.assertIn("ASK_QUESTION_BUNDLED_WITH_PAUSE_GATE", res_write.get("reason", ""))

      # Case 2: Advancing to T20 in the same turn then calling ask_question
      _write_transcript(
          transcript_path,
          [
              {"step_index": 1, "type": "USER_INPUT", "content": "Enter T20"},
              {
                  "step_index": 2,
                  "type": "PLANNER_RESPONSE",
                  "content": "Entering Target 20 with [20_plan.md](file:///tmp/20_plan.md).",
                  "tool_calls": [{
                      "name": "run_command",
                      "args": {
                          "CommandLine": "make -f .workflow/state/Makefile approved-plan"
                      },
                  }],
              },
          ],
      )
      res_adv = evaluate_tool_call({
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {"name": "ask_question", "args": {}},
      })
      self.assertEqual(res_adv["decision"], "deny", res_adv)
      self.assertIn("[SCAR-ID: PROC-161]", res_adv.get("reason", ""))
      self.assertIn("ASK_QUESTION_BUNDLED_WITH_PAUSE_GATE", res_adv.get("reason", ""))

  def test_ask_question_allowed_in_t15_t90_and_after_t20_to_t15_recovery(self):
    """GH-270: Allows ask_question during T15 pre-plan discovery, T90 release, and T20->T15 recovery."""
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      state_dir = os.path.join(ws_dir, ".workflow", "state")
      os.makedirs(state_dir)
      transcript_path = os.path.join(ws_dir, "transcript.jsonl")
      _write_transcript(
          transcript_path,
          [
              {"step_index": 1, "type": "USER_INPUT", "content": "Clarify BDD scope"},
              {
                  "step_index": 2,
                  "type": "PLANNER_RESPONSE",
                  "content": "Discovery context with [receipt](file:///tmp/15_archaeology_receipt.md).",
                  "tool_calls": [],
              },
          ],
      )

      for target_id, prev_id in (("T15", "T10"), ("T90", "T80"), ("T15", "T20")):
        with self.subTest(target=target_id, prev=prev_id):
          with open(
              os.path.join(state_dir, "active_target.json"), "w", encoding="utf-8"
          ) as fh:
            json.dump({"target": target_id, "previousTarget": prev_id}, fh)
          res = evaluate_tool_call({
              "transcriptPath": transcript_path,
              "workspacePaths": [ws_dir],
              "toolCall": {"name": "ask_question", "args": {}},
          })
          self.assertEqual(res["decision"], "allow", res)

  def test_ask_question_active_target_precedence_over_stale_gate_entry_scar_proc_161(
      self,
  ):
    """GH-270 BLOCKER-1: active_target.json overrides stale gate_entry.json; fallback still denies."""
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      state_dir = os.path.join(ws_dir, ".workflow", "state")
      os.makedirs(state_dir)
      transcript_path = os.path.join(ws_dir, "transcript.jsonl")
      _write_transcript(
          transcript_path,
          [
              {"step_index": 1, "type": "USER_INPUT", "content": "Clarify scope"},
              {
                  "step_index": 2,
                  "type": "PLANNER_RESPONSE",
                  "content": "Context with [receipt](file:///tmp/15_archaeology_receipt.md).",
                  "tool_calls": [],
              },
          ],
      )

      active_path = os.path.join(state_dir, "active_target.json")
      gate_path = os.path.join(state_dir, "gate_entry.json")

      # Stale gate_entry.json (T20 left behind after failed check-plan) with authoritative T15 or T90 -> ALLOW
      for active_id, stale_gate in (("T15", "T20"), ("T90", "T20"), ("T90", "T50")):
        with self.subTest(active=active_id, stale_gate=stale_gate):
          with open(active_path, "w", encoding="utf-8") as fh:
            json.dump({"target": active_id, "previousTarget": "T10"}, fh)
          with open(gate_path, "w", encoding="utf-8") as fh:
            json.dump({"gate": stale_gate, "fromTarget": "T15"}, fh)
          res = evaluate_tool_call({
              "transcriptPath": transcript_path,
              "workspacePaths": [ws_dir],
              "toolCall": {"name": "ask_question", "args": {}},
          })
          self.assertEqual(res["decision"], "allow", res)

      # When active_target.json is missing or lacks a valid target, fallback to gate_entry.json -> DENY
      with open(gate_path, "w", encoding="utf-8") as fh:
        json.dump({"gate": "T20", "fromTarget": "T15"}, fh)

      for label, active_raw in (
          ("absent", None),
          ("empty_obj", "{}"),
          ("empty_target", '{"target": ""}'),
          ("null_target", '{"target": null}'),
          ("invalid_target", '{"target": "NOT_A_TARGET"}'),
          ("corrupt_json", "{not-json"),
      ):
        with self.subTest(active_case=label):
          if active_raw is None:
            if os.path.isfile(active_path):
              os.unlink(active_path)
          else:
            with open(active_path, "w", encoding="utf-8") as fh:
              fh.write(active_raw)
          res = evaluate_tool_call({
              "transcriptPath": transcript_path,
              "workspacePaths": [ws_dir],
              "toolCall": {"name": "ask_question", "args": {}},
          })
          self.assertEqual(res["decision"], "deny", res)
          self.assertIn("[SCAR-ID: PROC-161]", res.get("reason", ""))
          self.assertIn("ASK_QUESTION_AT_PAUSE_GATE", res.get("reason", ""))

  def test_ask_question_same_turn_t20_to_t15_recovery_clears_pause_gate_block(
      self,
  ):
    """GH-270 BLOCKER-1: Same-turn T20 -> T15 recovery clears pause-gate block while late write re-blocks."""
    with _isolated_guard_env(), tempfile.TemporaryDirectory() as ws_dir:
      state_dir = os.path.join(ws_dir, ".workflow", "state")
      os.makedirs(state_dir)
      transcript_path = os.path.join(ws_dir, "transcript.jsonl")
      with open(
          os.path.join(state_dir, "active_target.json"), "w", encoding="utf-8"
      ) as fh:
        json.dump({"target": "T15", "previousTarget": "T20"}, fh)
      with open(
          os.path.join(state_dir, "gate_entry.json"), "w", encoding="utf-8"
      ) as fh:
        json.dump({"gate": "T20", "fromTarget": "T15"}, fh)

      recovery_cases = (
          (
              "make_archaeology_verified",
              {
                  "name": "run_command",
                  "args": {
                      "CommandLine": "make -f .workflow/state/Makefile archaeology-verified"
                  },
              },
          ),
          (
              "workflow_gates_advance_t15",
              {
                  "name": "run_command",
                  "args": {
                      "CommandLine": "python3 scripts/workflow_gates.py advance --to T15"
                  },
              },
          ),
          (
              "mcp_workflow_advance_target_t15",
              {
                  "name": "call_mcp_tool",
                  "args": {
                      "ServerName": "makeflow_makeflow",
                      "ToolName": "workflow_advance_target",
                      "Arguments": {"currentTarget": "T20", "nextTarget": "T15"},
                  },
              },
          ),
      )

      for label, recovery_call in recovery_cases:
        with self.subTest(recovery=label):
          _write_transcript(
              transcript_path,
              [
                  {"step_index": 1, "type": "USER_INPUT", "content": "Revise plan"},
                  {
                      "step_index": 2,
                      "type": "PLANNER_RESPONSE",
                      "content": "Drafted [20_plan.md](file:///tmp/20_plan.md).",
                      "tool_calls": [
                          {
                              "name": "write_file",
                              "args": {"Path": os.path.join(state_dir, "20_plan.md")},
                          },
                          {
                              "name": "run_command",
                              "args": {
                                  "CommandLine": (
                                      "make -f .workflow/state/Makefile approved-plan"
                                  )
                              },
                          },
                      ],
                  },
                  {
                      "step_index": 3,
                      "type": "PLANNER_RESPONSE",
                      "content": "Recovered to Target 15 for discovery.",
                      "tool_calls": [recovery_call],
                  },
              ],
          )
          res = evaluate_tool_call({
              "transcriptPath": transcript_path,
              "workspacePaths": [ws_dir],
              "toolCall": {"name": "ask_question", "args": {}},
          })
          self.assertEqual(res["decision"], "allow", res)

      # Single-step sequence where recovery happens BEFORE writing 20_plan.md MUST still DENY
      _write_transcript(
          transcript_path,
          [
              {"step_index": 1, "type": "USER_INPUT", "content": "Re-enter"},
              {
                  "step_index": 2,
                  "type": "PLANNER_RESPONSE",
                  "content": "Returned to T15 then wrote [20_plan.md](file:///tmp/20_plan.md).",
                  "tool_calls": [
                      {
                          "name": "run_command",
                          "args": {
                              "CommandLine": (
                                  "make -f .workflow/state/Makefile archaeology-verified"
                              )
                          },
                      },
                      {
                          "name": "write_file",
                          "args": {"Path": os.path.join(state_dir, "20_plan.md")},
                      },
                  ],
              },
          ],
      )
      res_late_write = evaluate_tool_call({
          "transcriptPath": transcript_path,
          "workspacePaths": [ws_dir],
          "toolCall": {"name": "ask_question", "args": {}},
      })
      self.assertEqual(res_late_write["decision"], "deny", res_late_write)
      self.assertIn("[SCAR-ID: PROC-161]", res_late_write.get("reason", ""))
      self.assertIn(
          "ASK_QUESTION_BUNDLED_WITH_PAUSE_GATE",
          res_late_write.get("reason", ""),
      )

  def test_evaluate_ask_question_turn_boundary_keeps_single_arg_compatibility(self):
    """SCAR-API-0002: evaluate_ask_question_turn_boundary(transcript_path) still works with 1 arg."""
    from lib.turn_boundary import evaluate_ask_question_turn_boundary

    with tempfile.TemporaryDirectory() as tmpdir:
      tr = os.path.join(tmpdir, "transcript.jsonl")
      _write_transcript(
          tr,
          [
              {"step_index": 1, "type": "USER_INPUT", "content": "Hi"},
              {
                  "step_index": 2,
                  "type": "PLANNER_RESPONSE",
                  "content": "Context provided.",
                  "tool_calls": [],
              },
          ],
      )
      ok, reason = evaluate_ask_question_turn_boundary(tr)
      self.assertTrue(ok, reason)

  def test_direct_file_write_guard_plan_scope_symlinks_and_cross_workspace_hardening(
      self,
  ):
    """GH-331 Scenario 4: Enforces plan scope, symlink realpath, expanded caches, cross-workspace, and arg keys."""
    with (
        _isolated_guard_env() as (_hook_cwd, fake_home, _clean_env),
        tempfile.TemporaryDirectory() as plugin_ws,
        tempfile.TemporaryDirectory() as consumer_ws,
    ):
      os.makedirs(os.path.join(plugin_ws, ".git"))
      os.makedirs(os.path.join(plugin_ws, "lib"))
      state_dir = os.path.join(plugin_ws, ".workflow", "state")
      os.makedirs(state_dir)
      with open(
          os.path.join(plugin_ws, "plugin.json"), "w", encoding="utf-8"
      ) as f:
        json.dump({"name": "makeflow", "version": "1.0.0"}, f)

      plan_text = (
          "# Plan\n\n"
          "### Specific Edits to Existing Files\n\n"
          "- `lib/intake_gates.py`: Planned edit.\n"
          "```markdown\n"
          "- `lib/tdd_guard.py`: Fenced example only.\n"
          "```\n\n"
          "### New Files to Create\n\n"
          "- None.\n\n"
          "### Untouched Files and Scope Quarantine\n\n"
          "- `lib/commit_gates.py`: Untouched.\n"
      )
      with open(
          os.path.join(state_dir, "20_plan.md"), "w", encoding="utf-8"
      ) as f:
        f.write(plan_text)
      with open(
          os.path.join(state_dir, "20_plan_approved.md"), "w", encoding="utf-8"
      ) as f:
        f.write("APPROVED\n")

      planned_file = os.path.join(plugin_ws, "lib", "intake_gates.py")
      quarantined_file = os.path.join(plugin_ws, "lib", "commit_gates.py")
      fenced_file = os.path.join(plugin_ws, "lib", "tdd_guard.py")
      unplanned_file = os.path.join(plugin_ws, "lib", "config.py")
      for fp in (planned_file, quarantined_file, fenced_file, unplanned_file):
        with open(fp, "w", encoding="utf-8") as f:
          f.write("# stub\n")

      # 1. Planned file allowed across all 4 write tools and all 4 path arg keys
      for tool_name, arg_key in (
          ("write_file", "Path"),
          ("edit_file", "TargetFile"),
          ("ReplaceFileContent", "file_path"),
          ("MultiReplaceFileContent", "path"),
      ):
        with self.subTest(case="planned_allow", tool=tool_name, key=arg_key):
          res = evaluate_tool_call({
              "workspacePaths": [plugin_ws],
              "toolCall": {
                  "name": tool_name,
                  "args": {arg_key: planned_file},
              },
          })
          self.assertEqual(res["decision"], "allow", res)

      # 2. Quarantined, fenced-only, and unplanned files denied across path arg keys
      for denied_path, arg_key in (
          (quarantined_file, "Path"),
          (fenced_file, "file_path"),
          (unplanned_file, "path"),
      ):
        with self.subTest(case="unplanned_deny", path=denied_path, key=arg_key):
          res = evaluate_tool_call({
              "workspacePaths": [plugin_ws],
              "toolCall": {
                  "name": "edit_file",
                  "args": {arg_key: denied_path},
              },
          })
          self.assertEqual(res["decision"], "deny", res)

      # 3. Symlink to .workflow/state/30_tdd_receipt.json denied
      receipt_target = os.path.join(state_dir, "30_tdd_receipt.json")
      with open(receipt_target, "w", encoding="utf-8") as f:
        f.write("{}\n")
      receipt_link = os.path.join(plugin_ws, "innocent_receipt_alias.json")
      os.symlink(receipt_target, receipt_link)
      res_state_link = evaluate_tool_call({
          "workspacePaths": [plugin_ws],
          "toolCall": {"name": "write_file", "args": {"file_path": receipt_link}},
      })
      self.assertEqual(res_state_link["decision"], "deny", res_state_link)

      # 4. Expanded plugin caches and symlinks into plugin caches denied
      for rel_cache in (
          os.path.join(".gemini", "config", "plugins", "makeflow", "lib", "intake_gates.py"),
          os.path.join(".codex", "plugins", "makeflow", "lib", "intake_gates.py"),
          os.path.join(".cursor", "plugins", "makeflow", "lib", "intake_gates.py"),
          os.path.join(".copilot", "plugins", "makeflow", "lib", "intake_gates.py"),
      ):
        cache_file = os.path.join(fake_home, rel_cache)
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "w", encoding="utf-8") as f:
          f.write("# installed\n")
        res_cache = evaluate_tool_call({
            "workspacePaths": [plugin_ws],
            "toolCall": {"name": "write_file", "args": {"path": cache_file}},
        })
        self.assertEqual(res_cache["decision"], "deny", f"Expected deny for {cache_file}")

        cache_link = os.path.join(
            plugin_ws, f"cache_link_{rel_cache.split(os.sep)[0].lstrip('.')}.py"
        )
        os.symlink(cache_file, cache_link)
        res_link = evaluate_tool_call({
            "workspacePaths": [plugin_ws],
            "toolCall": {"name": "edit_file", "args": {"Path": cache_link}},
        })
        self.assertEqual(
            res_link["decision"],
            "deny",
            f"Expected deny for symlink {cache_link} -> {cache_file}",
        )

      # 5. Cross-workspace edit into Makeflow checkout outside workspacePaths denied
      res_cross = evaluate_tool_call({
          "workspacePaths": [consumer_ws],
          "toolCall": {"name": "edit_file", "args": {"Path": planned_file}},
      })
      self.assertEqual(res_cross["decision"], "deny", res_cross)

      # 6. Write resolving under _MAKEFLOW_REPO_ROOT_REAL outside workspacePaths denied
      real_repo_target = os.path.join(REPO_ROOT, "lib", "intake_gates.py")
      res_real_repo = evaluate_tool_call({
          "workspacePaths": [consumer_ws],
          "toolCall": {
              "name": "write_file",
              "args": {"file_path": real_repo_target},
          },
      })
      self.assertEqual(res_real_repo["decision"], "deny", res_real_repo)

      # Symlink inside consumer workspace pointing into _MAKEFLOW_REPO_ROOT_REAL denied
      repo_link = os.path.join(consumer_ws, "local_alias.py")
      os.symlink(real_repo_target, repo_link)
      res_repo_link = evaluate_tool_call({
          "workspacePaths": [consumer_ws],
          "toolCall": {"name": "edit_file", "args": {"Path": repo_link}},
      })
      self.assertEqual(res_repo_link["decision"], "deny", res_repo_link)

  def test_direct_file_write_guard_blank_and_non_string_workspace_paths_fallback(
      self,
  ):
    """GH-331 NBO-1: Blank and non-string workspacePaths fall back cleanly instead of returning [] or crashing."""
    import lib.pre_tool_guard as guard_lib

    with (
        _isolated_guard_env() as (hook_cwd, _fake_home, _clean_env),
        tempfile.TemporaryDirectory() as plugin_ws,
    ):
      # Make hook_cwd a Makeflow checkout without an approved plan
      os.makedirs(os.path.join(hook_cwd, ".git"))
      os.makedirs(os.path.join(hook_cwd, "lib"))
      with open(
          os.path.join(hook_cwd, "plugin.json"), "w", encoding="utf-8"
      ) as f:
        json.dump({"name": "makeflow", "version": "1.0.0"}, f)
      with open(
          os.path.join(hook_cwd, "lib", "intake_gates.py"),
          "w",
          encoding="utf-8",
      ) as f:
        f.write("# stub\n")

      expected_cwd_target = os.path.abspath("lib/intake_gates.py")
      for bad_ws in ([""], ["   "], [None], [123, "   ", None]):
        with self.subTest(case="fallback", workspacePaths=bad_ws):
          resolved = guard_lib._resolve_direct_target_paths(
              "lib/intake_gates.py", bad_ws
          )
          self.assertEqual(resolved, [expected_cwd_target])

          res = evaluate_tool_call({
              "workspacePaths": bad_ws,
              "toolCall": {
                  "name": "edit_file",
                  "args": {"Path": "lib/intake_gates.py"},
              },
          })
          self.assertEqual(res["decision"], "deny", res)

      # Mixed valid + blank/non-string workspacePaths filters junk and resolves valid root
      os.makedirs(os.path.join(plugin_ws, ".git"))
      os.makedirs(os.path.join(plugin_ws, "lib"))
      state_dir = os.path.join(plugin_ws, ".workflow", "state")
      os.makedirs(state_dir)
      with open(
          os.path.join(plugin_ws, "plugin.json"), "w", encoding="utf-8"
      ) as f:
        json.dump({"name": "makeflow", "version": "1.0.0"}, f)
      with open(
          os.path.join(plugin_ws, "lib", "intake_gates.py"),
          "w",
          encoding="utf-8",
      ) as f:
        f.write("# stub\n")
      with open(
          os.path.join(state_dir, "20_plan.md"), "w", encoding="utf-8"
      ) as f:
        f.write(
            "### Specific Edits to Existing Files\n\n"
            "- `lib/intake_gates.py`: Planned edit.\n"
        )
      with open(
          os.path.join(state_dir, "20_plan_approved.md"), "w", encoding="utf-8"
      ) as f:
        f.write("APPROVED\n")

      mixed_ws = ["", "   ", None, 42, f"  {plugin_ws}  "]
      self.assertEqual(
          guard_lib._resolve_direct_target_paths(
              "lib/intake_gates.py", mixed_ws
          ),
          [os.path.abspath(os.path.join(plugin_ws, "lib", "intake_gates.py"))],
      )
      res_mixed = evaluate_tool_call({
          "workspacePaths": mixed_ws,
          "toolCall": {
              "name": "edit_file",
              "args": {"Path": "lib/intake_gates.py"},
          },
      })
      self.assertEqual(res_mixed["decision"], "allow", res_mixed)


if __name__ == "__main__":
  unittest.main()
