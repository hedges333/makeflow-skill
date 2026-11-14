#!/usr/bin/env python3
"""Unit tests for PreToolUse transcript turn-boundary guard (GH-186, SCAR-PROC-102)."""

import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
  sys.path.insert(0, REPO_ROOT)

from scripts.pre_tool_guard import (
    GOVERNANCE_FILE_RELPATHS,
    evaluate_self_modification_safety,
    evaluate_tool_call,
)


def _write_transcript(path, steps):
  with open(path, "w", encoding="utf-8") as f:
    for step in steps:
      f.write(json.dumps(step) + "\n")


class TestPreToolGuardTurnBoundary(unittest.TestCase):
  """Tests for PreToolUse turn-boundary enforcement across all 6 pause gates."""

  def test_hooks_json_matcher_includes_call_mcp_tool(self):
    hooks_path = os.path.join(REPO_ROOT, "hooks.json")
    with open(hooks_path, "r", encoding="utf-8") as f:
      data = json.load(f)
    pre_hooks = data["makeflow-guard"]["PreToolUse"]
    self.assertEqual(len(pre_hooks), 1)
    self.assertEqual(pre_hooks[0]["matcher"], "run_command|call_mcp_tool")

  def test_gh145_same_turn_t70_t75_t80_and_merge_denied(self):
    """Reproduces the exact GH-145 step 151 -> 155 autonomous self-landing trajectory."""
    with tempfile.TemporaryDirectory() as ws_dir:
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
        with tempfile.TemporaryDirectory() as ws_dir:
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
    with tempfile.TemporaryDirectory() as ws_dir:
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
        with tempfile.TemporaryDirectory() as ws_dir:
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

    with tempfile.TemporaryDirectory() as ws_dir:
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
    """Regression test for BLOCKER-1: post-approval 99_next_action.md and prose must not self-lock."""
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
    with tempfile.TemporaryDirectory() as ws_dir:
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
    """Regression test for BLOCKER-2: gate_entry.json enforcement on commands and relative stateDir resolution."""
    for gate_t, cmd_str in (
        ("T50", "git commit -m 'feat: test'"),
        ("T60", "git push -u origin feat/test"),
        ("T75", "gh pr merge 186 --squash"),
    ):
      with tempfile.TemporaryDirectory() as ws_dir:
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
    """Verifies T75 -> T80 -> T90 autonomous completion after a single USER_INPUT at Target 75."""
    with tempfile.TemporaryDirectory() as ws_dir:
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


if __name__ == "__main__":
  unittest.main()
