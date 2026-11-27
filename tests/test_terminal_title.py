#!/usr/bin/env python3
"""Hermetic unit tests for terminal title detection and setting (GH-103)."""

import importlib
import io
import json
import os
import pty
import select
import stat
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS_DIR = os.path.join(_REPO_ROOT, "scripts")
for _p in (_REPO_ROOT, _SCRIPTS_DIR):
  if _p not in sys.path:
    sys.path.insert(0, _p)

from lib import terminal_title
from lib.base_script import BaseScript
from lib.terminal_title import (
    TerminalTitleScript,
    detect_issue_tag,
    detect_project_name,
    detect_terminal_capability,
    detect_ticket_id,
    format_terminal_title,
    generate_escape_sequence,
    is_temporary_directory_name,
    sanitize_terminal_title,
    set_terminal_title,
)


class MockTtyStream(io.StringIO):
  """StringIO stream pretending to be an interactive TTY."""

  def isatty(self) -> bool:
    return True


class MockNonTtyStream(io.StringIO):
  """StringIO stream pretending to be a non-interactive pipe."""

  def isatty(self) -> bool:
    return False


class TestTerminalTitleCapability(unittest.TestCase):
  """Tests for terminal title capability detection."""

  def test_non_tty_stream_rejected(self):
    stream = MockNonTtyStream()
    can_change, mechanism = detect_terminal_capability(
        stream=stream, env={"TERM": "xterm-256color"}
    )
    self.assertFalse(can_change)
    self.assertEqual(mechanism, "non_tty")

  def test_disabled_by_environment_variable(self):
    stream = MockTtyStream()
    for val in ("1", "true", "TRUE", "yes"):
      can_change, mechanism = detect_terminal_capability(
          stream=stream,
          env={"TERM": "xterm-256color", "MAKEFLOW_DISABLE_TERMINAL_TITLE": val},
      )
      self.assertFalse(can_change)
      self.assertEqual(mechanism, "disabled")

  def test_dumb_terminal_rejected(self):
    stream = MockTtyStream()
    can_change, mechanism = detect_terminal_capability(
        stream=stream, env={"TERM": "dumb"}
    )
    self.assertFalse(can_change)
    self.assertEqual(mechanism, "dumb_term")

  def test_linux_console_rejected(self):
    stream = MockTtyStream()
    can_change, mechanism = detect_terminal_capability(
        stream=stream, env={"TERM": "linux"}
    )
    self.assertFalse(can_change)
    self.assertEqual(mechanism, "unsupported_term")

  def test_missing_or_empty_term_rejected(self):
    stream = MockTtyStream()
    can_change, mechanism = detect_terminal_capability(
        stream=stream, env={}
    )
    self.assertFalse(can_change)
    self.assertEqual(mechanism, "missing_term")

  def test_tmux_environment_detected(self):
    stream = MockTtyStream()
    can_change, mechanism = detect_terminal_capability(
        stream=stream,
        env={"TERM": "screen-256color", "TMUX": "/tmp/tmux-1000/default,1234,0"},
    )
    self.assertTrue(can_change)
    self.assertEqual(mechanism, "tmux")

  def test_screen_environment_detected(self):
    stream = MockTtyStream()
    can_change, mechanism = detect_terminal_capability(
        stream=stream,
        env={"TERM": "screen", "STY": "12345.pts-1.host"},
    )
    self.assertTrue(can_change)
    self.assertEqual(mechanism, "screen")

  def test_xterm_and_derivatives_detected(self):
    stream = MockTtyStream()
    terms = [
        "xterm",
        "xterm-256color",
        "xterm-color",
        "rxvt-unicode-256color",
        "alacritty",
        "kitty",
        "foot",
        "contour",
        "st-256color",
    ]
    for term in terms:
      can_change, mechanism = detect_terminal_capability(
          stream=stream, env={"TERM": term}
      )
      self.assertTrue(can_change, f"Failed for TERM={term}")
      self.assertEqual(mechanism, "xterm_osc", f"Failed for TERM={term}")

  def test_term_program_and_modern_guis(self):
    stream = MockTtyStream()
    programs = ["iTerm.app", "Apple_Terminal", "vscode", "WezTerm", "Hyper", "ghostty"]
    for prog in programs:
      can_change, mechanism = detect_terminal_capability(
          stream=stream, env={"TERM": "unknown", "TERM_PROGRAM": prog}
      )
      self.assertTrue(can_change, f"Failed for TERM_PROGRAM={prog}")
      self.assertEqual(mechanism, "xterm_osc")

  def test_windows_terminal_detected(self):
    stream = MockTtyStream()
    can_change, mechanism = detect_terminal_capability(
        stream=stream,
        env={"TERM": "unknown", "WT_SESSION": "00000000-0000-0000-0000-000000000000"},
    )
    self.assertTrue(can_change)
    self.assertEqual(mechanism, "xterm_osc")

  def test_detect_capability_gh235_dumb_term_with_outer_emulator_hints(self):
    """GH-235: recognizes outer emulator hints even when subshell sets TERM=dumb."""
    stream = MockTtyStream()
    cases = [
        ({"TERM": "dumb", "TMUX": "/tmp/tmux-1000/default,123,0"}, "tmux"),
        ({"TERM": "dumb", "STY": "12345.pts-1.host"}, "screen"),
        ({"TERM": "dumb", "TERM_PROGRAM": "vscode"}, "xterm_osc"),
        ({"TERM": "dumb", "TERM_PROGRAM": "iTerm.app"}, "xterm_osc"),
        (
            {
                "TERM": "dumb",
                "WT_SESSION": "00000000-0000-0000-0000-000000000000",
            },
            "xterm_osc",
        ),
        ({"TERM": "dumb", "KITTY_WINDOW_ID": "1"}, "xterm_osc"),
        ({"TERM": "dumb", "ALACRITTY_LOG": "/tmp/alacritty.log"}, "xterm_osc"),
        ({"TERM": "dumb", "KONSOLE_VERSION": "230801"}, "xterm_osc"),
        ({"TERM": "dumb", "VTE_VERSION": "8001"}, "xterm_osc"),
    ]
    for env_case, expected_mech in cases:
      with self.subTest(env_case=env_case):
        can_change, mechanism = detect_terminal_capability(
            stream=stream, env=env_case
        )
        self.assertTrue(can_change, f"Expected True for {env_case}")
        self.assertEqual(mechanism, expected_mech)

    # Bare TERM=dumb without hints still rejects
    can_change, mechanism = detect_terminal_capability(
        stream=stream, env={"TERM": "dumb"}
    )
    self.assertFalse(can_change)
    self.assertEqual(mechanism, "dumb_term")

    # Opt-out overrides hints even when TERM=dumb
    can_change, mechanism = detect_terminal_capability(
        stream=stream,
        env={
            "TERM": "dumb",
            "VTE_VERSION": "8001",
            "MAKEFLOW_DISABLE_TERMINAL_TITLE": "1",
        },
    )
    self.assertFalse(can_change)
    self.assertEqual(mechanism, "disabled")

    # Unsupported Linux console stays unsupported even with hints
    for env_linux in (
        {"TERM": "linux"},
        {"TERM": "linux", "VTE_VERSION": "8001"},
    ):
      can_change, mechanism = detect_terminal_capability(
          stream=stream, env=env_linux
      )
      self.assertFalse(can_change)
      self.assertEqual(mechanism, "unsupported_term")


class TestEscapeSequenceGeneration(unittest.TestCase):
  """Tests for escape sequence formatting."""

  def test_xterm_osc_sequence(self):
    seq = generate_escape_sequence("GH-103 makeflow-skill", "xterm_osc")
    self.assertEqual(seq, "\033]0;GH-103 makeflow-skill\007")

  def test_tmux_dcs_passthrough_sequence(self):
    seq = generate_escape_sequence("GH-103 makeflow-skill", "tmux")
    self.assertEqual(
        seq, "\033Ptmux;\033\033]0;GH-103 makeflow-skill\007\033\\"
    )

  def test_screen_window_sequence(self):
    seq = generate_escape_sequence("GH-103 makeflow-skill", "screen")
    self.assertEqual(seq, "\033kGH-103 makeflow-skill\033\\")

  def test_unsupported_returns_empty_string(self):
    seq = generate_escape_sequence("GH-103 makeflow-skill", "unsupported")
    self.assertEqual(seq, "")

  def test_control_characters_stripped_from_escape_sequences(self):
    malicious = "GH-103\x07\x1b]0;hacked\x07\r\nmakeflow\t"
    seq = generate_escape_sequence(malicious, "xterm_osc")
    self.assertEqual(seq, "\033]0;GH-103]0;hacked makeflow\007")

  def test_sanitize_terminal_title(self):
    self.assertEqual(
        sanitize_terminal_title("  GH-103\r\n\tproject\x00\x07\x1b  "),
        "GH-103 project",
    )
    self.assertEqual(sanitize_terminal_title(""), "")
    self.assertEqual(sanitize_terminal_title(None), "")

  def test_sanitize_terminal_title_gh235_strips_backticks(self):
    """GH-235: backticks are stripped cleanly from terminal title strings."""
    self.assertEqual(
        sanitize_terminal_title(
            "`[BUG] Terminal title reset at Target 90 fails via `scripts/terminal_title.py``"
        ),
        "[BUG] Terminal title reset at Target 90 fails via scripts/terminal_title.py",
    )
    self.assertEqual(sanitize_terminal_title("`GH-235`"), "GH-235")
    self.assertEqual(sanitize_terminal_title("```"), "")
    self.assertEqual(
        generate_escape_sequence(
            "`GH-235` `scripts/terminal_title.py`", "xterm_osc"
        ),
        "\033]0;GH-235 scripts/terminal_title.py\007",
    )


class TestProjectAndTicketDetection(unittest.TestCase):
  """Tests for detecting project name and ticket ID."""

  def setUp(self):
    self._tmp = tempfile.TemporaryDirectory(prefix="makeflow_title_test_")
    self.addCleanup(self._tmp.cleanup)
    self.temp_dir = self._tmp.name

  def test_project_name_from_git_remote_ssh(self):
    name = detect_project_name(
        repo_root=self.temp_dir,
        git_remote_url="git@github.com:hedges333/makeflow-skill.git",
    )
    self.assertEqual(name, "makeflow-skill")

  def test_project_name_from_git_remote_https(self):
    name = detect_project_name(
        repo_root=self.temp_dir,
        git_remote_url="https://github.com/my-org/custom-service.git",
    )
    self.assertEqual(name, "custom-service")

  def test_project_name_from_git_remote_without_dot_git(self):
    name = detect_project_name(
        repo_root=self.temp_dir,
        git_remote_url="https://gitlab.com/group/subgroup/backend",
    )
    self.assertEqual(name, "backend")

  def test_project_name_from_manifest_file(self):
    plugin_json = os.path.join(self.temp_dir, "plugin.json")
    with open(plugin_json, "w", encoding="utf-8") as f:
      json.dump({"name": "manifest-project"}, f)
    name = detect_project_name(repo_root=self.temp_dir, git_remote_url="")
    self.assertEqual(name, "manifest-project")

  def test_project_name_fallback_to_dir_basename(self):
    name = detect_project_name(repo_root=self.temp_dir, git_remote_url="")
    self.assertEqual(name, os.path.basename(self.temp_dir))

  def test_ticket_id_from_state_card(self):
    state_dir = os.path.join(self.temp_dir, ".workflow", "state")
    os.makedirs(state_dir, exist_ok=True)
    card_path = os.path.join(state_dir, "10_ticket.json")
    with open(card_path, "w", encoding="utf-8") as f:
      json.dump({"ticket_id": "GH-103", "title": "Terminal title"}, f)
    ticket = detect_ticket_id(repo_root=self.temp_dir)
    self.assertEqual(ticket, "GH-103")

  def test_ticket_id_from_branch_name(self):
    for branch, expected in [
        ("feat/103-terminal-title", "GH-103"),
        ("fix/42-auth-timeout", "GH-42"),
        ("ticket/88-refactor", "GH-88"),
        ("feat/local-12-offline-mode", "LOCAL-12"),
        ("fix/local-99-cleanup", "LOCAL-99"),
    ]:
      ticket = detect_ticket_id(repo_root=self.temp_dir, branch=branch)
      self.assertEqual(ticket, expected)

  def test_ticket_id_does_not_scrape_next_action_markdown(self):
    state_dir = os.path.join(self.temp_dir, ".workflow", "state")
    os.makedirs(state_dir, exist_ok=True)
    card_path = os.path.join(state_dir, "99_next_action.md")
    with open(card_path, "w", encoding="utf-8") as f:
      f.write("- Ticket: [LOCAL-456: Test local ticket](url)\n")
    self.assertEqual(detect_ticket_id(repo_root=self.temp_dir, branch=""), "")
    self.assertEqual(
        detect_ticket_id(repo_root=self.temp_dir, branch="feat/local-456-test"),
        "LOCAL-456",
    )

  def test_format_terminal_title(self):
    self.assertEqual(
        format_terminal_title("GH-103", "makeflow-skill"),
        "makeflow-skill/: GH-103",
    )
    self.assertEqual(
        format_terminal_title("LOCAL-42", "engine"),
        "engine/: LOCAL-42",
    )
    self.assertEqual(format_terminal_title("GH-103", ""), "")
    self.assertEqual(format_terminal_title("", "engine"), "engine/")
    self.assertEqual(format_terminal_title("", ""), "")

  def test_format_terminal_title_with_issue_tag(self):
    self.assertEqual(
        format_terminal_title(
            "GH-1234",
            "makeflow-skill-dir3",
            "Add frobnosticator to reticulator subnodule",
        ),
        "makeflow-skill-dir3/: GH-1234 Add frobnosticator to reticulator subnodule",
    )
    self.assertEqual(
        format_terminal_title(
            "GH-113", "makeflow-skill", "enhance terminal title"
        ),
        "makeflow-skill/: GH-113 enhance terminal title",
    )
    self.assertEqual(
        format_terminal_title("LOCAL-42", "engine", "offline mode"),
        "engine/: LOCAL-42 offline mode",
    )
    self.assertEqual(
        format_terminal_title("", "engine", "offline mode"),
        "engine/",
    )
    self.assertEqual(
        format_terminal_title("GH-113", "", "enhance terminal title"),
        "",
    )
    self.assertEqual(
        format_terminal_title("", "", "enhance terminal title"),
        "",
    )
    # Trailing slashes and root path normalization when dir_name passed directly
    self.assertEqual(
        format_terminal_title("GH-103", "foo/", "bar"),
        "foo/: GH-103 bar",
    )
    self.assertEqual(
        format_terminal_title("GH-103", "/", "bar"),
        "",
    )
    self.assertEqual(
        format_terminal_title("", "foo/", ""),
        "foo/",
    )
    # Backwards compatibility with None and empty issue_tag
    self.assertEqual(
        format_terminal_title("GH-113", "makeflow-skill", None),
        "makeflow-skill/: GH-113",
    )
    self.assertEqual(
        format_terminal_title("GH-113", "makeflow-skill", ""),
        "makeflow-skill/: GH-113",
    )

  def test_issue_tag_preserves_full_title(self):
    long_tag = (
        "Fix authentication timeout on redirect and retry when session expires after idle period"  # 87 chars
    )
    result = format_terminal_title("GH-42", "backend", long_tag)
    tag_part = result.split(": GH-42 ", 1)[1]
    self.assertEqual(tag_part, long_tag)
    self.assertEqual(result, f"backend/: GH-42 {long_tag}")

  def test_detect_issue_tag_preserves_full_title_from_state_cards(self):
    state_dir = os.path.join(self.temp_dir, ".workflow", "state")
    os.makedirs(state_dir, exist_ok=True)
    long_desc = "refactor broad exception handlers and preserve full terminal title in session start"
    card_10 = os.path.join(state_dir, "10_ticket.json")
    with open(card_10, "w", encoding="utf-8") as f:
      json.dump({"ticket_id": "GH-130", "title": f"[FEAT] {long_desc}"}, f)
    tag = detect_issue_tag(repo_root=self.temp_dir)
    self.assertEqual(tag, long_desc)

  def test_detect_project_name_handles_unicode_decode_error(self):
    plugin_path = os.path.join(self.temp_dir, "plugin.json")
    with open(plugin_path, "wb") as f:
      f.write(b"\xff\xfe\x00\x01")
    name = detect_project_name(repo_root=self.temp_dir, git_remote_url="")
    self.assertEqual(name, os.path.basename(self.temp_dir))

  def test_detect_ticket_id_handles_unicode_decode_error(self):
    state_dir = os.path.join(self.temp_dir, ".workflow", "state")
    os.makedirs(state_dir, exist_ok=True)
    card_10 = os.path.join(state_dir, "10_ticket.json")
    with open(card_10, "wb") as f:
      f.write(b"\xff\xfe\x00\x01")
    ticket = detect_ticket_id(repo_root=self.temp_dir, branch="")
    self.assertEqual(ticket, "")

  def test_detect_issue_tag_handles_unicode_decode_error(self):
    state_dir = os.path.join(self.temp_dir, ".workflow", "state")
    os.makedirs(state_dir, exist_ok=True)
    card_10 = os.path.join(state_dir, "10_ticket.json")
    with open(card_10, "wb") as f:
      f.write(b"\xff\xfe\x00\x01")
    tag = detect_issue_tag(repo_root=self.temp_dir, branch="")
    self.assertEqual(tag, "")

  def test_detect_issue_tag_from_state_cards_and_branch(self):
    self.assertIsNotNone(
        detect_issue_tag, "detect_issue_tag must be exported from terminal_title"
    )
    state_dir = os.path.join(self.temp_dir, ".workflow", "state")
    os.makedirs(state_dir, exist_ok=True)

    # 1. Canonical 10_ticket.json
    card_10 = os.path.join(state_dir, "10_ticket.json")
    with open(card_10, "w", encoding="utf-8") as f:
      json.dump(
          {
              "ticket_id": "GH-113",
              "title": "[FEAT] enhance terminal title",
              "url": "https://github.com/example/repo/issues/113",
          },
          f,
      )
    tag = detect_issue_tag(repo_root=self.temp_dir)
    self.assertEqual(tag, "enhance terminal title")

    # 2. Raw gh issue view --json saved directly to 10_ticket.json
    with open(card_10, "w", encoding="utf-8") as f:
      json.dump(
          {
              "number": 240,
              "title": "[BUG] terminal title formatting",
              "url": "https://github.com/example/repo/issues/240",
              "labels": [{"name": "bug"}],
          },
          f,
      )
    self.assertEqual(detect_ticket_id(repo_root=self.temp_dir, branch=""), "GH-240")
    self.assertEqual(
        detect_issue_tag(repo_root=self.temp_dir, branch=""),
        "terminal title formatting",
    )

    # 3. Local ticket schema with 'id'
    with open(card_10, "w", encoding="utf-8") as f:
      json.dump({"id": "LOCAL-456", "title": "Test local ticket"}, f)
    self.assertEqual(
        detect_ticket_id(repo_root=self.temp_dir, branch=""), "LOCAL-456"
    )
    self.assertEqual(
        detect_issue_tag(repo_root=self.temp_dir, branch=""),
        "Test local ticket",
    )

    # 4. Git branch slug fallback when 10_ticket.json absent
    os.remove(card_10)
    tag = detect_issue_tag(
        repo_root=self.temp_dir, branch="feat/113-enhance-terminal-title"
    )
    self.assertEqual(tag, "enhance terminal title")
    tag = detect_issue_tag(
        repo_root=self.temp_dir, branch="fix/local-42-login-redirect-timeout"
    )
    self.assertEqual(tag, "login redirect timeout")

  def test_detect_issue_tag_gh235_extracts_title_field_and_strips_backticks_and_prefix(
      self,
  ):
    """GH-235 / GH-240: extracts title from 10_ticket.json, strips backticks and [TYPE] prefix."""
    state_dir = os.path.join(self.temp_dir, ".workflow", "state")
    os.makedirs(state_dir, exist_ok=True)
    card_10 = os.path.join(state_dir, "10_ticket.json")

    with open(card_10, "w", encoding="utf-8") as f:
      json.dump(
          {
              "ticket_id": "GH-235",
              "title": "`[BUG] Terminal title reset at Target 90 fails via `scripts/terminal_title.py``",
          },
          f,
      )
    self.assertEqual(
        detect_issue_tag(repo_root=self.temp_dir, branch=""),
        "Terminal title reset at Target 90 fails via scripts/terminal_title.py",
    )
    self.assertEqual(
        detect_ticket_id(repo_root=self.temp_dir, branch=""),
        "GH-235",
    )

    with open(card_10, "w", encoding="utf-8") as f:
      json.dump(
          {
              "ticket_id": "GH-219",
              "title": "[FEAT] Execute [DAG] transitions",
          },
          f,
      )
    self.assertEqual(
        detect_issue_tag(repo_root=self.temp_dir, branch=""),
        "Execute [DAG] transitions",
    )

  def test_gh240_prose_quoting_in_ticket_md_never_hijacks_issue_tag(self):
    """GH-240: quoted ticket bullets in 10_ticket.md cannot hijack 10_ticket.json metadata."""
    state_dir = os.path.join(self.temp_dir, ".workflow", "state")
    os.makedirs(state_dir, exist_ok=True)
    with open(
        os.path.join(state_dir, "10_ticket.json"), "w", encoding="utf-8"
    ) as f:
      json.dump(
          {
              "ticket_id": "GH-240",
              "title": "[BUG] terminal title formatting",
              "url": "https://github.com/hedges333/makeflow-skill/issues/240",
          },
          f,
      )
    with open(
        os.path.join(state_dir, "10_ticket.md"), "w", encoding="utf-8"
    ) as f:
      f.write(
          "# 10_ticket: GH-240\n\n"
          "Repro notes: writing `- Ticket: [GH-233: GH-233](https://example.com) (In Progress)`\n"
      )
    tid = detect_ticket_id(repo_root=self.temp_dir, branch="fix/240-title")
    tag = detect_issue_tag(repo_root=self.temp_dir, branch="fix/240-title")
    self.assertEqual(tid, "GH-240")
    self.assertEqual(tag, "terminal title formatting")
    self.assertEqual(
        format_terminal_title(tid, "makeflow-skill-dir3", tag),
        "makeflow-skill-dir3/: GH-240 terminal title formatting",
    )

  def test_gh240_suppresses_duplicate_ticket_id_in_tag_and_format(self):
    """GH-240: duplicate ticket_id in title or issue_tag never produces '{dir}/: GH-233 GH-233'."""
    self.assertEqual(
        format_terminal_title("GH-233", "makeflow-skill-dir2", "GH-233"),
        "makeflow-skill-dir2/: GH-233",
    )
    self.assertEqual(
        format_terminal_title("GH-233", "makeflow-skill-dir2", "gh-233"),
        "makeflow-skill-dir2/: GH-233",
    )
    state_dir = os.path.join(self.temp_dir, ".workflow", "state")
    os.makedirs(state_dir, exist_ok=True)
    card_10 = os.path.join(state_dir, "10_ticket.json")
    for dup_title in ("GH-233", "gh-233", "[BUG] GH-233", ""):
      with open(card_10, "w", encoding="utf-8") as f:
        json.dump({"ticket_id": "GH-233", "title": dup_title}, f)
      self.assertEqual(
          detect_issue_tag(
              repo_root=self.temp_dir, branch="fix/233-regression-gate"
          ),
          "regression gate",
      )
      self.assertEqual(
          detect_issue_tag(repo_root=self.temp_dir, branch=""),
          "",
      )

  def test_gh240_corrupt_or_non_dict_ticket_json_falls_back_to_branch(self):
    """GH-240: corrupt or non-dict 10_ticket.json degrades cleanly to Git branch fallback."""
    state_dir = os.path.join(self.temp_dir, ".workflow", "state")
    os.makedirs(state_dir, exist_ok=True)
    card_10 = os.path.join(state_dir, "10_ticket.json")
    for raw_payload in ("{broken json", "[1, 2, 3]", '"just-a-string"'):
      with open(card_10, "w", encoding="utf-8") as f:
        f.write(raw_payload)
      self.assertEqual(
          detect_ticket_id(
              repo_root=self.temp_dir, branch="fix/240-terminal-title"
          ),
          "GH-240",
      )
      self.assertEqual(
          detect_issue_tag(
              repo_root=self.temp_dir, branch="fix/240-terminal-title"
          ),
          "terminal title",
      )

  def test_detect_project_name_rejects_temporary_directory_patterns(self):
    self.assertIsNotNone(
        is_temporary_directory_name,
        "is_temporary_directory_name must be exported from terminal_title",
    )
    # Python tempfile standard name tmp[a-z0-5]{8}
    self.assertTrue(is_temporary_directory_name("tmp1a2b3c4d"))
    self.assertTrue(is_temporary_directory_name("/tmp/tmp1a2b3c4d"))
    self.assertTrue(is_temporary_directory_name("tmpabcdef0"))

    # Hex/alphanumeric tempfile pattern tmp[a-z0-9_]{6,}
    self.assertTrue(is_temporary_directory_name("tmp_123456"))
    self.assertTrue(is_temporary_directory_name("tmp_abcdef123"))

    # Non-temp project names should NOT be rejected
    self.assertFalse(is_temporary_directory_name("makeflow-skill"))
    self.assertFalse(is_temporary_directory_name("my-cool-project"))

    # detect_project_name on temporary directories must return ""
    with tempfile.TemporaryDirectory(prefix="tmp") as temp_dir:
      name = detect_project_name(repo_root=temp_dir, git_remote_url="")
      self.assertEqual(name, "")

    # Synthetic temporary path fallback check
    name = detect_project_name(repo_root="/tmp/tmpabcdef1", git_remote_url="")
    self.assertEqual(name, "")
    name = detect_project_name(repo_root="/tmp/tmp_99887766", git_remote_url="")
    self.assertEqual(name, "")


class TestDirectoryNameDetection(unittest.TestCase):
  """Tests for bare unqualified directory name extraction (GH-224)."""

  def setUp(self):
    self._tmp = tempfile.TemporaryDirectory(prefix="makeflow_dir_test_")
    self.addCleanup(self._tmp.cleanup)
    self.temp_dir = self._tmp.name

  def test_detect_directory_name_gh224_ignores_git_remote_and_manifests(self):
    """GH-224: detect_directory_name ignores plugin.json, package.json, and git remote."""
    repo_dir = os.path.join(self.temp_dir, "makeflow-skill-dir3")
    os.makedirs(os.path.join(repo_dir, ".git"), exist_ok=True)
    with open(
        os.path.join(repo_dir, ".git", "config"), "w", encoding="utf-8"
    ) as f:
      f.write('[remote "origin"]\n\turl = git@github.com:org/other-repo.git\n')
    with open(
        os.path.join(repo_dir, "plugin.json"), "w", encoding="utf-8"
    ) as f:
      json.dump({"name": "manifest-project"}, f)
    with open(
        os.path.join(repo_dir, "package.json"), "w", encoding="utf-8"
    ) as f:
      json.dump({"name": "npm-package"}, f)

    self.assertEqual(
        detect_project_name(repo_root=repo_dir, git_remote_url=""),
        "manifest-project",
    )
    self.assertEqual(
        terminal_title.detect_directory_name(repo_root=repo_dir),
        "makeflow-skill-dir3",
    )

  def test_detect_directory_name_gh224_normalizes_trailing_slashes_and_root(
      self,
  ):
    """GH-224: trailing slashes are stripped and filesystem root returns empty string."""
    self.assertEqual(
        terminal_title.detect_directory_name(repo_root="/tmp/work/my-repo/"),
        "my-repo",
    )
    self.assertEqual(
        terminal_title.detect_directory_name(repo_root="/tmp/work/my-repo///"),
        "my-repo",
    )
    self.assertEqual(terminal_title.detect_directory_name(repo_root="/"), "")

  def test_detect_directory_name_gh224_rejects_temporary_directories(self):
    """GH-224: temporary directory paths return empty string."""
    with tempfile.TemporaryDirectory(prefix="tmp") as temp_dir:
      self.assertEqual(
          terminal_title.detect_directory_name(repo_root=temp_dir), ""
      )

    self.assertEqual(
        terminal_title.detect_directory_name(repo_root="/tmp/tmpabcdef1"), ""
    )
    self.assertEqual(
        terminal_title.detect_directory_name(repo_root="/tmp/tmp_99887766"), ""
    )
    self.assertEqual(
        terminal_title.detect_directory_name(repo_root="/var/tmp/tmp1a2b3c4d"),
        "",
    )

  def test_is_temporary_directory_name_gh235_rejects_custom_tmpdir_subdirectories(
      self,
  ):
    """GH-235: rejects subdirs under tmp... parents inside tempfile.gettempdir() for non-/tmp TMPDIR."""
    custom_tmp_root = "/var/tmp/custom_runner_scratch"
    with patch("lib.terminal_title.tempfile.gettempdir", return_value=custom_tmp_root):
      self.assertTrue(
          is_temporary_directory_name(
              os.path.join(custom_tmp_root, "tmp1a2b3c4d", "repo")
          )
      )
      self.assertTrue(
          is_temporary_directory_name(
              os.path.join(custom_tmp_root, "tmp_nested", "sub", "makeflow-skill")
          )
      )
      self.assertEqual(
          terminal_title.detect_directory_name(
              repo_root=os.path.join(custom_tmp_root, "tmp1a2b3c4d", "makeflow-skill")
          ),
          "",
      )
      # Non-tmp... work dir under custom_tmp_root stays allowed (parity with /tmp/work/my-repo)
      self.assertFalse(
          is_temporary_directory_name(
              os.path.join(custom_tmp_root, "work", "my-repo")
          )
      )
      self.assertEqual(
          terminal_title.detect_directory_name(
              repo_root=os.path.join(custom_tmp_root, "work", "my-repo")
          ),
          "my-repo",
      )

  def test_detect_directory_name_gh224_sanitizes_control_characters(self):
    """GH-224 / SCAR-SEC-01: control characters and escapes are stripped from dir name."""
    self.assertEqual(
        terminal_title.detect_directory_name(
            repo_root="/tmp/work/evil\x07dir\x1b[31m"
        ),
        "evildir[31m",
    )
    self.assertEqual(
        terminal_title.detect_directory_name(
            repo_root="/tmp/work/my\r\nrepo\t"
        ),
        "my repo",
    )
    self.assertEqual(
        terminal_title.detect_directory_name(repo_root="/tmp/work/\x07\x1b"), ""
    )

  def test_scripts_terminal_title_gh224_exports_detect_directory_name(self):
    """GH-224: scripts/terminal_title.py exports detect_directory_name and detect_project_name."""
    cli_mod = importlib.import_module("scripts.terminal_title")
    self.assertIn("detect_directory_name", cli_mod.__all__)
    self.assertIn("detect_project_name", cli_mod.__all__)
    self.assertTrue(callable(getattr(cli_mod, "detect_directory_name", None)))
    self.assertTrue(callable(getattr(cli_mod, "detect_project_name", None)))


class TestSetTerminalTitle(unittest.TestCase):
  """Tests for set_terminal_title integration function."""

  def test_successful_title_set_on_xterm(self):
    stream = MockTtyStream()
    ok, applied = set_terminal_title(
        ticket_id="GH-103",
        dir_name="makeflow-skill",
        stream=stream,
        env={"TERM": "xterm-256color"},
    )
    self.assertTrue(ok)
    self.assertEqual(applied, "makeflow-skill/: GH-103")
    self.assertEqual(stream.getvalue(), "\033]0;makeflow-skill/: GH-103\007")

  def test_successful_title_set_on_tmux(self):
    stream = MockTtyStream()
    ok, applied = set_terminal_title(
        ticket_id="LOCAL-77",
        dir_name="core-service",
        stream=stream,
        env={"TERM": "screen-256color", "TMUX": "/tmp/tmux/1,0,0"},
    )
    self.assertTrue(ok)
    self.assertEqual(applied, "core-service/: LOCAL-77")
    self.assertEqual(
        stream.getvalue(), "\033Ptmux;\033\033]0;core-service/: LOCAL-77\007\033\\"
    )

  def test_no_output_when_capability_is_false(self):
    stream = MockNonTtyStream()
    ok, applied = set_terminal_title(
        ticket_id="GH-103",
        dir_name="makeflow-skill",
        stream=stream,
        env={"TERM": "xterm-256color"},
    )
    self.assertFalse(ok)
    self.assertEqual(applied, "")
    self.assertEqual(stream.getvalue(), "")

  def test_set_terminal_title_rejects_temp_titles(self):
    stream = MockTtyStream()
    # Explicit temporary title
    ok, applied = set_terminal_title(
        title="tmpa1b2c3d4",
        stream=stream,
        env={"TERM": "xterm-256color"},
    )
    self.assertFalse(ok)
    self.assertEqual(applied, "")
    self.assertEqual(stream.getvalue(), "")

    # Temporary directory as repo_root with no ticket or remote
    with tempfile.TemporaryDirectory(prefix="tmp") as temp_dir:
      ok, applied = set_terminal_title(
          repo_root=temp_dir,
          stream=stream,
          env={"TERM": "xterm-256color"},
      )
      self.assertFalse(ok)
      self.assertEqual(applied, "")
      self.assertEqual(stream.getvalue(), "")

  def test_set_terminal_title_gh224_rejects_explicit_temp_dir_name(self):
    """GH-224: set_terminal_title rejects temporary directory patterns in dir_name."""
    stream = MockTtyStream()
    for kwargs in (
        {"dir_name": "tmpa1b2c3d4"},
        {"dir_name": "tmpa1b2c3d4", "ticket_id": "GH-103"},
    ):
      ok, applied = set_terminal_title(
          stream=stream,
          env={"TERM": "xterm-256color"},
          **kwargs,
      )
      self.assertFalse(ok)
      self.assertEqual(applied, "")
      self.assertEqual(stream.getvalue(), "")

  def test_set_terminal_title_with_issue_tag(self):
    stream = MockTtyStream()
    ok, applied = set_terminal_title(
        ticket_id="GH-113",
        dir_name="makeflow-skill",
        issue_tag="enhance terminal title",
        stream=stream,
        env={"TERM": "xterm-256color"},
    )
    self.assertTrue(ok)
    self.assertEqual(applied, "makeflow-skill/: GH-113 enhance terminal title")
    self.assertEqual(
        stream.getvalue(),
        "\033]0;makeflow-skill/: GH-113 enhance terminal title\007",
    )

  def test_set_terminal_title_gh224_canonical_example(self):
    """GH-224: auto-detects bare directory name and ignores plugin.json."""
    with tempfile.TemporaryDirectory(
        prefix="makeflow_gh224_test_"
    ) as temp_parent:
      repo_dir = os.path.join(temp_parent, "makeflow-skill-dir3")
      state_dir = os.path.join(repo_dir, ".workflow", "state")
      os.makedirs(state_dir, exist_ok=True)
      with open(
          os.path.join(repo_dir, "plugin.json"), "w", encoding="utf-8"
      ) as f:
        json.dump({"name": "makeflow"}, f)
      with open(
          os.path.join(state_dir, "10_ticket.json"), "w", encoding="utf-8"
      ) as f:
        json.dump(
            {
                "ticket_id": "GH-1234",
                "title": "[FEAT] Add frobnosticator to reticulator subnodule",
                "url": "https://example.com",
            },
            f,
        )

      stream = MockTtyStream()
      ok, applied = set_terminal_title(
          repo_root=repo_dir,
          stream=stream,
          env={"TERM": "xterm-256color"},
      )
      expected = (
          "makeflow-skill-dir3/: GH-1234 Add frobnosticator to reticulator subnodule"
      )
      self.assertTrue(ok)
      self.assertEqual(applied, expected)
      self.assertEqual(stream.getvalue(), f"\033]0;{expected}\007")

  def test_set_terminal_title_gh224_rejects_project_name_kwarg(self):
    """GH-224: project_name parameter is removed from set_terminal_title."""
    stream = MockTtyStream()
    with self.assertRaises(TypeError):
      set_terminal_title(
          ticket_id="GH-103",
          project_name="makeflow-skill",
          stream=stream,
          env={"TERM": "xterm-256color"},
      )

  def test_set_terminal_title_gh224_preserves_issue_tag_guard(self):
    """GH-224 / SCAR-TEST-24: preserves `if i_tag is None and ticket_id is None:` guard."""
    stream = MockTtyStream()
    with patch(
        "lib.terminal_title.detect_issue_tag", return_value="auto tag"
    ) as mock_detect_tag:
      ok, applied = set_terminal_title(
          ticket_id="GH-103",
          dir_name="makeflow-skill",
          stream=stream,
          env={"TERM": "xterm-256color"},
      )
      self.assertTrue(ok)
      self.assertEqual(applied, "makeflow-skill/: GH-103")
      mock_detect_tag.assert_not_called()

  def test_set_terminal_title_catches_oserror_and_unicode_encode_error(self):
    stream = MockTtyStream()
    with patch.object(stream, "write", side_effect=OSError("Disk/TTY write error")):
      ok, applied = set_terminal_title(
          ticket_id="GH-130",
          dir_name="makeflow-skill",
          stream=stream,
          env={"TERM": "xterm-256color"},
      )
      self.assertFalse(ok)
      self.assertEqual(applied, "")

    with patch.object(
        stream,
        "write",
        side_effect=UnicodeEncodeError("ascii", "test", 0, 1, "ordinal not in range"),
    ):
      ok, applied = set_terminal_title(
          ticket_id="GH-130",
          dir_name="makeflow-skill",
          stream=stream,
          env={"TERM": "xterm-256color"},
      )
      self.assertFalse(ok)
      self.assertEqual(applied, "")

  def test_set_terminal_title_propagates_unrelated_exceptions(self):
    stream = MockTtyStream()
    with patch.object(stream, "write", side_effect=TypeError("Synthetic TypeError")):
      with self.assertRaises(TypeError):
        set_terminal_title(
            ticket_id="GH-130",
            dir_name="makeflow-skill",
            stream=stream,
            env={"TERM": "xterm-256color"},
        )

    with patch.object(
        stream, "write", side_effect=RuntimeError("Synthetic RuntimeError")
    ):
      with self.assertRaises(RuntimeError):
        set_terminal_title(
            ticket_id="GH-130",
            dir_name="makeflow-skill",
            stream=stream,
            env={"TERM": "xterm-256color"},
        )


class TestTerminalTitleScriptLifecycle(unittest.TestCase):
  """Tests for TerminalTitleScript BaseScript CLI lifecycle."""

  def setUp(self):
    self._tmp = tempfile.TemporaryDirectory(prefix="makeflow_cli_test_")
    self.addCleanup(self._tmp.cleanup)
    self.temp_dir = self._tmp.name

  def test_inherits_base_script(self):
    script = TerminalTitleScript()
    self.assertIsInstance(script, BaseScript)

  def test_detect_command_json_output(self):
    script = TerminalTitleScript()
    out = io.StringIO()
    err = io.StringIO()
    script.stdout = out
    script.stderr = err
    exit_code = script.execute(
        ["detect", "--json"],
        env_override={"TERM": "xterm-256color"},
        mock_tty=True,
    )
    self.assertEqual(exit_code, 0)
    data = json.loads(out.getvalue())
    self.assertTrue(data["can_change"])
    self.assertEqual(data["mechanism"], "xterm_osc")

  def test_info_command_json_output(self):
    script = TerminalTitleScript()
    out = io.StringIO()
    err = io.StringIO()
    script.stdout = out
    script.stderr = err
    exit_code = script.execute(
        [
            "info",
            "--ticket",
            "GH-103",
            "--directory",
            "makeflow-skill",
            "--json",
        ],
        env_override={"TERM": "xterm-256color"},
        mock_tty=True,
    )
    self.assertEqual(exit_code, 0)
    data = json.loads(out.getvalue())
    self.assertEqual(data["ticket_id"], "GH-103")
    self.assertEqual(data["directory_name"], "makeflow-skill")
    self.assertNotIn("project_name", data)
    self.assertEqual(data["title"], "makeflow-skill/: GH-103")
    self.assertTrue(data["can_change"])

  def test_info_command_plaintext_gh224_outputs_directory_label(self):
    """GH-224: info plaintext output uses 'Directory:' instead of 'Project:'."""
    script = TerminalTitleScript()
    out = io.StringIO()
    err = io.StringIO()
    script.stdout = out
    script.stderr = err
    exit_code = script.execute(
        [
            "info",
            "--ticket",
            "GH-103",
            "--directory",
            "makeflow-skill",
        ],
        env_override={"TERM": "xterm-256color"},
        mock_tty=True,
    )
    self.assertEqual(exit_code, 0)
    output = out.getvalue()
    self.assertIn("Directory: makeflow-skill\n", output)
    self.assertIn("Title: makeflow-skill/: GH-103\n", output)
    self.assertNotIn("Project:", output)

  def test_set_command_updates_title(self):
    script = TerminalTitleScript()
    out = io.StringIO()
    err = io.StringIO()
    script.stdout = out
    script.stderr = err
    exit_code = script.execute(
        [
            "set",
            "--ticket",
            "LOCAL-999",
            "--directory",
            "super-tool",
        ],
        env_override={"TERM": "xterm-256color"},
        mock_tty=True,
    )
    self.assertEqual(exit_code, 0)
    self.assertIn("\033]0;super-tool/: LOCAL-999\007", err.getvalue())

  def test_info_command_with_issue_tag(self):
    script = TerminalTitleScript()
    out = io.StringIO()
    err = io.StringIO()
    script.stdout = out
    script.stderr = err
    exit_code = script.execute(
        [
            "info",
            "--ticket",
            "GH-113",
            "--directory",
            "makeflow-skill",
            "--issue-tag",
            "enhance terminal title",
            "--json",
        ],
        env_override={"TERM": "xterm-256color"},
        mock_tty=True,
    )
    self.assertEqual(exit_code, 0)
    data = json.loads(out.getvalue())
    self.assertEqual(data["ticket_id"], "GH-113")
    self.assertEqual(data["directory_name"], "makeflow-skill")
    self.assertNotIn("project_name", data)
    self.assertEqual(data["issue_tag"], "enhance terminal title")
    self.assertEqual(
        data["title"], "makeflow-skill/: GH-113 enhance terminal title"
    )

  def test_set_command_with_issue_tag(self):
    script = TerminalTitleScript()
    out = io.StringIO()
    err = io.StringIO()
    script.stdout = out
    script.stderr = err
    exit_code = script.execute(
        [
            "set",
            "--ticket",
            "GH-113",
            "--directory",
            "makeflow-skill",
            "--issue-tag",
            "enhance terminal title",
        ],
        env_override={"TERM": "xterm-256color"},
        mock_tty=True,
    )
    self.assertEqual(exit_code, 0)
    self.assertIn(
        "\033]0;makeflow-skill/: GH-113 enhance terminal title\007",
        err.getvalue(),
    )

  def test_reset_command_restores_directory_title(self):
    script = TerminalTitleScript()
    out = io.StringIO()
    err = io.StringIO()
    script.stdout = out
    script.stderr = err
    exit_code = script.execute(
        [
            "reset",
            "--directory",
            "makeflow-skill",
        ],
        env_override={"TERM": "xterm-256color"},
        mock_tty=True,
    )
    self.assertEqual(exit_code, 0)
    self.assertIn("\033]0;makeflow-skill/\007", err.getvalue())

  def test_reset_command_gh224_auto_detects_directory_and_ignores_plugin_json(
      self,
  ):
    """GH-224: reset uses directory basename with trailing slash and ignores plugin.json."""
    repo_dir = os.path.join(self.temp_dir, "my-dir-repo")
    os.makedirs(repo_dir, exist_ok=True)
    with open(
        os.path.join(repo_dir, "plugin.json"), "w", encoding="utf-8"
    ) as f:
      json.dump({"name": "manifest-proj"}, f)

    script = TerminalTitleScript()
    out = io.StringIO()
    err = io.StringIO()
    script.stdout = out
    script.stderr = err
    exit_code = script.execute(
        [
            "reset",
            "--repo-root",
            repo_dir,
            "--json",
        ],
        env_override={"TERM": "xterm-256color"},
        mock_tty=True,
    )
    self.assertEqual(exit_code, 0)
    data = json.loads(out.getvalue())
    self.assertTrue(data["success"])
    self.assertEqual(data["title"], "my-dir-repo/")
    self.assertIn("\033]0;my-dir-repo/\007", err.getvalue())
    self.assertNotIn("manifest-proj", err.getvalue())

  def test_reset_command_rejects_temporary_directory_title(self):
    script = TerminalTitleScript()
    out = io.StringIO()
    err = io.StringIO()
    script.stdout = out
    script.stderr = err
    exit_code = script.execute(
        [
            "reset",
            "--directory",
            "tmpa1b2c3d4",
        ],
        env_override={"TERM": "xterm-256color"},
        mock_tty=True,
    )
    self.assertEqual(exit_code, 0)
    self.assertEqual(err.getvalue(), "")

  def test_legacy_project_flag_rejected_gh224(self):
    """GH-224: --project flag is replaced by --directory on TerminalTitleScript."""
    for subcmd in ("info", "set", "reset"):
      with self.subTest(subcmd=subcmd):
        script = TerminalTitleScript()
        out = io.StringIO()
        err = io.StringIO()
        exit_code = script.execute(
            [subcmd, "--project", "makeflow-skill"],
            stdout=out,
            stderr=err,
        )
        self.assertEqual(exit_code, 1)


class TestWorkflowGatesTerminalTitleIntegration(unittest.TestCase):
  """Tests for workflow_gates.py terminal-title delegation and option forwarding."""

  def test_workflow_gates_forwards_options_to_terminal_title(self):
    from lib.workflow_gates import WorkflowGatesScript

    script = WorkflowGatesScript()
    out = io.StringIO()
    err = MockTtyStream()
    with patch.dict(os.environ, {"TERM": "xterm-256color"}):
      exit_code = script.execute(
          [
              "terminal-title",
              "--ticket",
              "GH-103",
              "--directory",
              "makeflow-skill",
              "--json",
          ],
          stdout=out,
          stderr=err,
      )
    self.assertEqual(exit_code, 0)
    data = json.loads(out.getvalue())
    self.assertEqual(data["title"], "makeflow-skill/: GH-103")
    self.assertTrue(data["success"])

  def test_workflow_gates_forwards_issue_tag(self):
    from lib.workflow_gates import WorkflowGatesScript

    script = WorkflowGatesScript()
    out = io.StringIO()
    err = MockTtyStream()
    with patch.dict(os.environ, {"TERM": "xterm-256color"}):
      exit_code = script.execute(
          [
              "terminal-title",
              "--ticket",
              "GH-113",
              "--directory",
              "makeflow-skill",
              "--issue-tag",
              "enhance terminal title",
              "--json",
          ],
          stdout=out,
          stderr=err,
      )
    self.assertEqual(exit_code, 0)
    data = json.loads(out.getvalue())
    self.assertEqual(
        data["title"], "makeflow-skill/: GH-113 enhance terminal title"
    )
    self.assertTrue(data["success"])

  def test_workflow_gates_release_intake_restores_title(self):
    from lib.workflow_gates import WorkflowGatesScript

    script = WorkflowGatesScript()
    out = io.StringIO()
    err = MockTtyStream()
    with tempfile.TemporaryDirectory(
        prefix="makeflow_release_test_"
    ) as temp_dir:
      repo_dir = os.path.join(temp_dir, "restored-dir")
      os.makedirs(repo_dir, exist_ok=True)
      manifest = os.path.join(repo_dir, "plugin.json")
      with open(manifest, "w", encoding="utf-8") as f:
        json.dump({"name": "restored-project"}, f)

      with patch.dict(os.environ, {"TERM": "xterm-256color"}):
        exit_code = script.execute(
            [
                "--repo-root",
                repo_dir,
                "release-intake",
            ],
            stdout=out,
            stderr=err,
        )
      self.assertEqual(exit_code, 0)
      # Must ignore plugin.json and restore terminal title to restored-dir/
      self.assertIn("\033]0;restored-dir/\007", err.getvalue())
      self.assertNotIn("restored-project", err.getvalue())

  def test_release_intake_preserves_title_when_active_or_complete(self):
    """GH-325: release-intake skips terminal title reset during active or complete states."""
    from lib.active_state import ActiveStateStore, ActiveTargetState
    from lib.intake_gates import ReleaseIntakeRequest, run_release_intake_gate
    from lib.workflow_gates import WorkflowGatesScript
    from lib.workspace_mutex import WorkspaceMutex

    with tempfile.TemporaryDirectory(
        prefix="makeflow_release_title_"
    ) as temp_dir:
      repo_dir = os.path.join(temp_dir, "active-repo")
      state_dir = os.path.join(repo_dir, ".workflow", "state")
      lock_dir = os.path.join(temp_dir, "locks")
      os.makedirs(state_dir, exist_ok=True)
      self.assertFalse(is_temporary_directory_name(repo_dir))

      script = WorkflowGatesScript()
      mutex = WorkspaceMutex(repo_root=repo_dir, lock_dir=lock_dir)

      # 1. Active T80 and active T90 in active_target.json release locks and preserve title
      for active_target, target_name in (
          ("T80", "land"),
          ("T90", "codified-scars-consolidated"),
      ):
        acquired = mutex.acquire(
            ticket_id="GH-325",
            workspace_path=repo_dir,
            branch_name="fix/325-terminal-title-finish",
        )
        self.assertTrue(acquired.get("acquired"))
        self.assertTrue(os.path.isdir(mutex.lock_path("GH-325")))
        ActiveStateStore(state_dir).save(
            ActiveTargetState(
                target=active_target,
                target_name=target_name,
                previous_target="T75",
                status="ACTIVE",
                complete=False,
                ticket_id="GH-325",
            )
        )
        err = MockTtyStream()
        with patch.dict(os.environ, {"TERM": "xterm-256color"}):
          exit_code = script.execute(
              [
                  "--repo-root",
                  repo_dir,
                  "--lock-dir",
                  lock_dir,
                  "release-intake",
              ],
              stdout=io.StringIO(),
              stderr=err,
          )
        self.assertEqual(exit_code, 0)
        self.assertFalse(os.path.exists(mutex.lock_path("GH-325")))
        self.assertEqual(err.getvalue(), "")

      # 2. Completed state in active_target.json must preserve DONE title
      ActiveStateStore(state_dir).save(
          ActiveTargetState(
              target="T90",
              target_name="finish",
              previous_target="T80",
              status="COMPLETE",
              complete=True,
              ticket_id="GH-325",
          )
      )
      err_complete = MockTtyStream()
      with patch.dict(os.environ, {"TERM": "xterm-256color"}):
        rc_complete = run_release_intake_gate(
            ReleaseIntakeRequest(
                repo_root=repo_dir,
                lock_dir=lock_dir,
                stderr=err_complete,
            )
        )
      self.assertEqual(rc_complete, 0)
      self.assertEqual(err_complete.getvalue(), "")

      # 3. Legacy active 99_next_action.md without active_target.json must preserve title
      ActiveStateStore(state_dir).clear()
      next_action_path = os.path.join(state_dir, "99_next_action.md")
      with open(next_action_path, "w", encoding="utf-8") as f:
        f.write(
            "# Active Workflow: Target 80\n"
            "- Active Target: Target 80 (land)\n"
            "- Ticket: [GH-325: Fix title](https://example.com) (In Progress)\n"
        )
      err_legacy = MockTtyStream()
      with patch.dict(os.environ, {"TERM": "xterm-256color"}):
        rc_legacy = script.execute(
            [
                "--repo-root",
                repo_dir,
                "--lock-dir",
                lock_dir,
                "release-intake",
            ],
            stdout=io.StringIO(),
            stderr=err_legacy,
        )
      self.assertEqual(rc_legacy, 0)
      self.assertEqual(err_legacy.getvalue(), "")

      # 4. Inactive Target 00 / unassigned workspace still resets title to active-repo/
      with open(next_action_path, "w", encoding="utf-8") as f:
        f.write(
            "# Target 00: Ready for Intake\n"
            "- Ticket: Unassigned (Ready for Intake)\n"
            "- Active Target: Target 00\n"
        )
      err_reset = MockTtyStream()
      with patch.dict(os.environ, {"TERM": "xterm-256color"}):
        rc_reset = script.execute(
            [
                "--repo-root",
                repo_dir,
                "--lock-dir",
                lock_dir,
                "release-intake",
            ],
            stdout=io.StringIO(),
            stderr=err_reset,
        )
      self.assertEqual(rc_reset, 0)
      self.assertEqual(err_reset.getvalue(), "\033]0;active-repo/\007")

  def test_release_intake_help_documents_idle_only_title_restoration(self):
    """GH-325: release-intake CLI help states terminal title restoration runs only when idle."""
    from lib.workflow_gates import WorkflowGatesScript
    from lib.workflow_gates_cli import build_workflow_gates_parser

    parser = build_workflow_gates_parser("/nonexistent/repo")
    top_help = " ".join(parser.format_help().split()).lower()
    self.assertIn("release-intake", top_help)
    self.assertIn("restore terminal title when idle", top_help)
    self.assertNotIn("reset terminal title upon completion", top_help)

    out = io.StringIO()
    err = io.StringIO()
    exit_code = WorkflowGatesScript().execute(
        ["release-intake", "--help"],
        stdout=out,
        stderr=err,
    )
    self.assertEqual(exit_code, 0)
    self.assertEqual(err.getvalue(), "")
    sub_help = " ".join(out.getvalue().split()).lower()
    self.assertIn("restore the terminal title when idle", sub_help)
    self.assertNotIn("upon completion", sub_help)
    self.assertNotIn("and reset the terminal title.", sub_help)


class TestAncestorPtyResolutionAndDelivery(unittest.TestCase):
  """Hermetic tests for ancestor PTY lookup and real PTY delivery (GH-235)."""

  def setUp(self):
    self._tmp = tempfile.TemporaryDirectory(prefix="makeflow_pty_test_")
    self.addCleanup(self._tmp.cleanup)
    self.temp_dir = self._tmp.name

  def _open_pty_pair(self):
    master_fd, slave_fd = pty.openpty()
    slave_path = os.ttyname(slave_fd)

    def _close_fds():
      for fd in (master_fd, slave_fd):
        try:
          os.close(fd)
        except OSError:
          pass

    self.addCleanup(_close_fds)
    return master_fd, slave_fd, slave_path

  def _read_pty_nonblocking(self, master_fd: int) -> str:
    ready, _, _ = select.select([master_fd], [], [], 0.05)
    if not ready:
      return ""
    chunks = []
    while True:
      ready, _, _ = select.select([master_fd], [], [], 0.01)
      if not ready:
        break
      try:
        data = os.read(master_fd, 4096)
      except OSError:
        break
      if not data:
        break
      chunks.append(data)
    return b"".join(chunks).decode("utf-8", errors="replace")

  def test_find_ancestor_pty_gh235_linux_proc_parsing_and_nested_parens(self):
    """GH-235: parses /proc/<pid>/stat with spaces/nested parens and decodes tty_nr."""
    _, _, slave_path = self._open_pty_pair()
    slave_rdev = os.lstat(slave_path).st_rdev
    child_rdev = os.makedev(136, os.minor(slave_rdev) + 1000)
    vt_rdev = os.makedev(4, 2)

    proc_root = os.path.join(self.temp_dir, "proc")
    entries = {
        1000: f"1000 (python3) R 950 1000 1000 {child_rdev} 1000 0 0 0 0 0 0 0 0 20 0 1 0 1 0 0 0\n",
        950: f"950 (bash) S 900 950 950 {child_rdev} 950 0 0 0 0 0 0 0 0 20 0 1 0 1 0 0 0\n",
        900: f"900 (Web Content (1)) S 800 900 800 {slave_rdev} 900 0 0 0 0 0 0 0 0 20 0 1 0 1 0 0 0\n",
        800: f"800 (mate-terminal) S 1 800 800 {vt_rdev} 800 0 0 0 0 0 0 0 0 20 0 1 0 1 0 0 0\n",
    }
    for pid, stat_line in entries.items():
      pid_dir = os.path.join(proc_root, str(pid))
      os.makedirs(pid_dir, exist_ok=True)
      with open(os.path.join(pid_dir, "stat"), "w", encoding="utf-8") as f:
        f.write(stat_line)

    resolved = terminal_title.find_ancestor_pty(
        start_pid=1000, proc_dir=proc_root
    )
    self.assertEqual(resolved, slave_path)

  def test_find_ancestor_pty_gh235_security_lstat_uid_regex_cycles_and_depth(
      self,
  ):
    """GH-235: enforces S_ISCHR, same UID, PTY regex, cycle guard, and max_depth."""
    _, _, slave_path = self._open_pty_pair()
    slave_rdev = os.lstat(slave_path).st_rdev
    vt_rdev = os.makedev(4, 2)
    my_uid = os.getuid()

    # 1. Rejects non-PTY VTs (/dev/tty2)
    vt_stats = {
        100: "100 (python3) R 90 100 100 0 100\n",
        90: f"90 (mate-terminal) S 1 90 90 {vt_rdev} 90\n",
    }
    self.assertIsNone(
        terminal_title.find_ancestor_pty(
            start_pid=100,
            stat_reader=lambda p: vt_stats.get(p),
            uid=my_uid,
        )
    )

    # 2. Rejects symlink / non-character device (S_ISCHR false)
    fake_lnk = os.stat_result((stat.S_IFLNK | 0o777, 0, 0, 1, my_uid, 0, 0, 0, 0, 0))
    pts_stats = {
        100: "100 (python3) R 90 100 100 0 100\n",
        90: f"90 (agy) S 1 90 90 {slave_rdev} 90\n",
    }
    self.assertIsNone(
        terminal_title.find_ancestor_pty(
            start_pid=100,
            stat_reader=lambda p: pts_stats.get(p),
            lstat_fn=lambda _p: fake_lnk,
            uid=my_uid,
        )
    )

    # 3. Rejects different UID owner
    other_uid_chr = os.stat_result(
        (stat.S_IFCHR | 0o600, 0, 0, 1, my_uid + 1, 0, 0, 0, 0, 0)
    )
    self.assertIsNone(
        terminal_title.find_ancestor_pty(
            start_pid=100,
            stat_reader=lambda p: pts_stats.get(p),
            lstat_fn=lambda _p: other_uid_chr,
            uid=my_uid,
        )
    )

    # 4. Stops cleanly on PPID cycle without hanging
    cyclic_stats = {
        100: "100 (a) R 90 100 100 0 100\n",
        90: "90 (b) S 100 90 90 0 90\n",
    }
    self.assertIsNone(
        terminal_title.find_ancestor_pty(
            start_pid=100,
            stat_reader=lambda p: cyclic_stats.get(p),
            uid=my_uid,
        )
    )

    # 5. Respects max_depth
    deep_stats = {
        100: "100 (a) R 90 100 100 0 100\n",
        90: "90 (b) S 80 90 90 0 90\n",
        80: f"80 (agy) S 1 80 80 {slave_rdev} 80\n",
    }
    self.assertIsNone(
        terminal_title.find_ancestor_pty(
            start_pid=100,
            max_depth=1,
            stat_reader=lambda p: deep_stats.get(p),
            uid=my_uid,
        )
    )
    self.assertEqual(
        terminal_title.find_ancestor_pty(
            start_pid=100,
            max_depth=2,
            stat_reader=lambda p: deep_stats.get(p),
            uid=my_uid,
        ),
        slave_path,
    )

  def test_find_ancestor_pty_gh235_posix_ps_fallback_when_proc_absent(self):
    """GH-235: falls back to POSIX ps lookup when /proc is absent."""
    _, _, slave_path = self._open_pty_pair()
    slave_short = slave_path[len("/dev/") :]
    my_uid = os.getuid()
    absent_proc = os.path.join(self.temp_dir, "missing_proc")

    ps_map = {
        100: "90 ?\n",
        90: f"80 {slave_short}\n",
        80: "1 tty2\n",
    }
    self.assertEqual(
        terminal_title.find_ancestor_pty(
            start_pid=100,
            proc_dir=absent_proc,
            ps_runner=lambda p: ps_map.get(p),
            uid=my_uid,
        ),
        slave_path,
    )

    # Verify macOS /dev/ttys001 and BSD /dev/ptyp0 pass regex while /dev/tty2 is rejected
    valid_chr = os.stat_result((stat.S_IFCHR | 0o600, 0, 0, 1, my_uid, 0, 0, 0, 0, 0))
    for dev_short in ("ttys001", "ptyp0"):
      self.assertEqual(
          terminal_title.find_ancestor_pty(
              start_pid=100,
              proc_dir=absent_proc,
              ps_runner=lambda p, d=dev_short: {100: "90 ?\n", 90: f"1 {d}\n"}.get(p),
              lstat_fn=lambda _p: valid_chr,
              uid=my_uid,
          ),
          f"/dev/{dev_short}",
      )
    self.assertIsNone(
        terminal_title.find_ancestor_pty(
            start_pid=100,
            proc_dir=absent_proc,
            ps_runner=lambda p: {100: "90 ?\n", 90: "1 tty2\n"}.get(p),
            lstat_fn=lambda _p: valid_chr,
            uid=my_uid,
        )
    )

  def test_set_terminal_title_and_cli_deliver_to_openpty_ancestor_with_dumb_term(
      self,
  ):
    """GH-235: set_terminal_title and TerminalTitleScript deliver OSC bytes to ancestor PTY."""
    master_fd, _, slave_path = self._open_pty_pair()
    repo_dir = os.path.join(self.temp_dir, "makeflow-skill")
    state_dir = os.path.join(repo_dir, ".workflow", "state")
    os.makedirs(state_dir, exist_ok=True)
    with open(
        os.path.join(state_dir, "10_ticket.json"), "w", encoding="utf-8"
    ) as f:
      json.dump(
          {
              "ticket_id": "GH-235",
              "title": "`[BUG] Reset title via `scripts/terminal_title.py``",
          },
          f,
      )

    with patch(
        "lib.terminal_title.find_ancestor_pty", return_value=slave_path
    ):
      # 1. set_terminal_title(stream=None) under TERM=dumb (clamped subshell)
      ok, applied = set_terminal_title(
          repo_root=repo_dir, stream=None, env={"TERM": "dumb"}
      )
      expected_title = "makeflow-skill/: GH-235 Reset title via scripts/terminal_title.py"
      self.assertTrue(ok)
      self.assertEqual(applied, expected_title)
      self.assertEqual(
          self._read_pty_nonblocking(master_fd),
          f"\033]0;{expected_title}\007",
      )

      # 2. set_terminal_title(stream=sys.__stderr__) simulating WorkflowGatesScript
      ok_std, applied_std = set_terminal_title(
          title="makeflow-skill/",
          repo_root=repo_dir,
          stream=sys.__stderr__,
          env={"TERM": "dumb"},
      )
      self.assertTrue(ok_std)
      self.assertEqual(applied_std, "makeflow-skill/")
      self.assertEqual(
          self._read_pty_nonblocking(master_fd),
          "\033]0;makeflow-skill/\007",
      )

      # 3. tmux inference when TMUX is set with TERM=dumb
      ok_tmux, _ = set_terminal_title(
          title="makeflow-skill/",
          repo_root=repo_dir,
          stream=None,
          env={"TERM": "dumb", "TMUX": "/tmp/tmux-1000/default,1,0"},
      )
      self.assertTrue(ok_tmux)
      self.assertEqual(
          self._read_pty_nonblocking(master_fd),
          "\033Ptmux;\033\033]0;makeflow-skill/\007\033\\",
      )

      # 4. TerminalTitleScript detect, info, set, reset with unmocked stderr (stderr=sys.__stderr__)
      out_detect = io.StringIO()
      rc_detect = TerminalTitleScript().execute(
          ["detect", "--json"],
          stdout=out_detect,
          stderr=sys.__stderr__,
          env_override={"TERM": "dumb"},
      )
      self.assertEqual(rc_detect, 0)
      self.assertEqual(
          json.loads(out_detect.getvalue()),
          {"can_change": True, "mechanism": "xterm_osc"},
      )

      out_info = io.StringIO()
      rc_info = TerminalTitleScript().execute(
          ["info", "--repo-root", repo_dir, "--json"],
          stdout=out_info,
          stderr=sys.__stderr__,
          env_override={"TERM": "dumb"},
      )
      self.assertEqual(rc_info, 0)
      info_data = json.loads(out_info.getvalue())
      self.assertTrue(info_data["can_change"])
      self.assertEqual(info_data["mechanism"], "xterm_osc")
      self.assertEqual(info_data["title"], expected_title)
      self.assertNotIn("`", info_data["title"])

      out_set = io.StringIO()
      rc_set = TerminalTitleScript().execute(
          ["set", "--repo-root", repo_dir, "--json"],
          stdout=out_set,
          stderr=sys.__stderr__,
          env_override={"TERM": "dumb"},
      )
      self.assertEqual(rc_set, 0)
      set_data = json.loads(out_set.getvalue())
      self.assertTrue(set_data["success"])
      self.assertEqual(set_data["mechanism"], "xterm_osc")
      self.assertEqual(set_data["title"], expected_title)
      self.assertEqual(
          self._read_pty_nonblocking(master_fd),
          f"\033]0;{expected_title}\007",
      )

      out_reset = io.StringIO()
      rc_reset = TerminalTitleScript().execute(
          ["reset", "--repo-root", repo_dir, "--json"],
          stdout=out_reset,
          stderr=sys.__stderr__,
          env_override={"TERM": "dumb"},
      )
      self.assertEqual(rc_reset, 0)
      reset_data = json.loads(out_reset.getvalue())
      self.assertTrue(reset_data["success"])
      self.assertEqual(reset_data["mechanism"], "xterm_osc")
      self.assertEqual(reset_data["title"], "makeflow-skill/")
      self.assertEqual(
          self._read_pty_nonblocking(master_fd),
          "\033]0;makeflow-skill/\007",
      )

  def test_injected_streams_preserve_exact_behavior_and_never_probe_proc(self):
    """GH-235: MockNonTtyStream, MockTtyStream, and io.StringIO never probe /proc or ancestor PTY."""
    with patch(
        "lib.terminal_title.find_ancestor_pty",
        side_effect=AssertionError("Must not probe ancestor PTY for injected stream"),
    ):
      tty_stream = MockTtyStream()
      ok_tty, applied_tty = set_terminal_title(
          title="makeflow-skill/: GH-235",
          stream=tty_stream,
          env={"TERM": "xterm-256color"},
      )
      self.assertTrue(ok_tty)
      self.assertEqual(applied_tty, "makeflow-skill/: GH-235")
      self.assertEqual(tty_stream.getvalue(), "\033]0;makeflow-skill/: GH-235\007")

      non_tty = MockNonTtyStream()
      ok, applied = set_terminal_title(
          title="makeflow-skill/: GH-235",
          stream=non_tty,
          env={"TERM": "xterm-256color"},
      )
      self.assertFalse(ok)
      self.assertEqual(applied, "")
      self.assertEqual(non_tty.getvalue(), "")

      out = io.StringIO()
      rc = TerminalTitleScript().execute(
          ["detect", "--json"],
          stdout=out,
          stderr=non_tty,
          env_override={"TERM": "dumb"},
      )
      self.assertEqual(rc, 0)
      self.assertEqual(
          json.loads(out.getvalue()),
          {"can_change": False, "mechanism": "non_tty"},
      )

      out_set = io.StringIO()
      rc_set = TerminalTitleScript().execute(
          ["set", "--title", "makeflow-skill/: GH-235", "--json"],
          stdout=out_set,
          stderr=non_tty,
          env_override={"TERM": "dumb"},
      )
      self.assertEqual(rc_set, 0)
      self.assertFalse(json.loads(out_set.getvalue())["success"])
      self.assertEqual(non_tty.getvalue(), "")

      out_mock = io.StringIO()
      err_mock = io.StringIO()
      rc_mock = TerminalTitleScript().execute(
          ["detect", "--json"],
          stdout=out_mock,
          stderr=err_mock,
          env_override={"TERM": "dumb"},
          mock_tty=True,
      )
      self.assertEqual(rc_mock, 0)
      self.assertEqual(
          json.loads(out_mock.getvalue()),
          {"can_change": False, "mechanism": "dumb_term"},
      )

  def test_ancestor_pty_failure_injection_and_exception_boundaries(self):
    """GH-235 / SCAR-TEST-24 / SCAR-AST-01: OSError/PermissionError falls back; TypeError/RuntimeError propagates."""
    _, _, slave_path = self._open_pty_pair()
    with patch("lib.terminal_title.find_ancestor_pty", return_value=slave_path):
      for exc in (
          PermissionError("EACCES on /dev/pts"),
          OSError("EIO on /dev/pts"),
      ):
        with patch("lib.terminal_title.os.open", side_effect=exc):
          ok, applied = set_terminal_title(
              title="makeflow-skill/: GH-235",
              stream=None,
              env={"TERM": "dumb"},
          )
          self.assertFalse(ok)
          self.assertEqual(applied, "")

          out = io.StringIO()
          rc = TerminalTitleScript().execute(
              ["set", "--title", "makeflow-skill/: GH-235", "--json"],
              stdout=out,
              stderr=sys.__stderr__,
              env_override={"TERM": "dumb"},
          )
          self.assertEqual(rc, 0)
          self.assertFalse(json.loads(out.getvalue())["success"])

      # os.write OSError must return (False, "") and close fd cleanly
      with patch("lib.terminal_title.os.write", side_effect=OSError("EAGAIN")):
        ok_w, applied_w = set_terminal_title(
            title="makeflow-skill/: GH-235",
            stream=None,
            env={"TERM": "dumb"},
        )
        self.assertFalse(ok_w)
        self.assertEqual(applied_w, "")

      # os.isatty False after open must return (False, "") without writing
      with patch("lib.terminal_title.os.isatty", return_value=False):
        ok_tty, applied_tty = set_terminal_title(
            title="makeflow-skill/: GH-235",
            stream=None,
            env={"TERM": "dumb"},
        )
        self.assertFalse(ok_tty)
        self.assertEqual(applied_tty, "")

      # Unrelated programming bugs must propagate cleanly (SCAR-AST-01)
      for fatal_exc in (
          TypeError("Synthetic TypeError"),
          RuntimeError("Synthetic RuntimeError"),
      ):
        with patch("lib.terminal_title.os.open", side_effect=fatal_exc):
          with self.assertRaises(type(fatal_exc)):
            set_terminal_title(
                title="makeflow-skill/: GH-235",
                stream=None,
                env={"TERM": "dumb"},
            )
        with patch("lib.terminal_title.os.write", side_effect=fatal_exc):
          with self.assertRaises(type(fatal_exc)):
            set_terminal_title(
                title="makeflow-skill/: GH-235",
                stream=None,
                env={"TERM": "dumb"},
            )


class TestTerminalTitleSubparserRepoRootGH243(unittest.TestCase):
  """Hermetic tests for TerminalTitleScript subparser --repo-root resolution (GH-243)."""

  def setUp(self):
    """Create isolated non-temporary-named workspace directories."""
    self.temp_dir = tempfile.TemporaryDirectory(prefix="makeflow_gh243_")
    self.target_repo = os.path.join(self.temp_dir.name, "target-project-dir")
    self.post_repo = os.path.join(self.temp_dir.name, "post-project-dir")
    self.cwd_repo = os.path.join(self.temp_dir.name, "cwd-project-dir")
    self.other_repo = os.path.join(self.temp_dir.name, "other-project-dir")
    self._seed_workspace(
        self.target_repo, "GH-301", "Target workspace feature"
    )
    self._seed_workspace(self.post_repo, "GH-302", "Post workspace feature")
    self._seed_workspace(self.cwd_repo, "GH-101", "CWD workspace bug")
    self._seed_workspace(self.other_repo, "GH-404", "Other workspace task")
    self.assertFalse(is_temporary_directory_name(self.target_repo))

  def tearDown(self):
    """Clean temporary directories."""
    self.temp_dir.cleanup()

  def _seed_workspace(self, repo_dir, ticket_id, issue_title):
    """Populate minimal 10_ticket.json state card for deterministic title detection.

    Args:
      repo_dir: Target workspace directory path.
      ticket_id: Ticket ID string (for example 'GH-301').
      issue_title: Ticket title text without type prefix.
    """
    state_dir = os.path.join(repo_dir, ".workflow", "state")
    os.makedirs(state_dir, exist_ok=True)
    with open(
        os.path.join(state_dir, "10_ticket.json"), "w", encoding="utf-8"
    ) as f:
      json.dump(
          {
              "ticket_id": ticket_id,
              "title": f"[FEAT] {issue_title}",
          },
          f,
      )

  def _run_cli(self, args, cwd=None, script=None):
    """Execute TerminalTitleScript in-process with patched os.getcwd and mock TTY.

    Args:
      args: CLI argument list passed to TerminalTitleScript.execute().
      cwd: Optional working directory path for os.getcwd mock.
      script: Optional existing TerminalTitleScript instance to reuse.

    Returns:
      Tuple of (exit_code, stdout_text, stderr_text, script_instance).
    """
    target_script = script if script is not None else TerminalTitleScript()
    out = io.StringIO()
    err = io.StringIO()
    with patch("os.getcwd", return_value=cwd or self.cwd_repo):
      code = target_script.execute(
          args=args,
          stdout=out,
          stderr=err,
          env_override={"TERM": "xterm-256color"},
          mock_tty=True,
      )
    return code, out.getvalue(), err.getvalue(), target_script

  def _assert_title_subcommands(
      self,
      build_args,
      expected_dir,
      expected_ticket,
      expected_tag,
      script=None,
  ):
    """Exercise info, set, and reset and assert expected directory and title outputs.

    Args:
      build_args: Callable accepting subcommand args and returning full CLI args.
      expected_dir: Expected directory basename for the resolved repository root.
      expected_ticket: Expected ticket ID detected from the resolved repository root.
      expected_tag: Expected issue tag detected from the resolved repository root.
      script: Optional existing TerminalTitleScript instance to reuse.
    """
    expected_title = f"{expected_dir}/: {expected_ticket} {expected_tag}"

    code_info, out_info, err_info, _ = self._run_cli(
        build_args(["info", "--json"]), script=script
    )
    self.assertEqual(code_info, 0, err_info)
    info_data = json.loads(out_info)
    self.assertEqual(info_data["directory_name"], expected_dir)
    self.assertEqual(info_data["ticket_id"], expected_ticket)
    self.assertEqual(info_data["issue_tag"], expected_tag)
    self.assertEqual(info_data["title"], expected_title)
    self.assertTrue(info_data["can_change"])

    code_set, out_set, err_set, _ = self._run_cli(
        build_args(["set", "--json"]), script=script
    )
    self.assertEqual(code_set, 0, err_set)
    set_data = json.loads(out_set)
    self.assertTrue(set_data["success"])
    self.assertEqual(set_data["title"], expected_title)
    self.assertEqual(err_set, f"\033]0;{expected_title}\007")

    code_reset, out_reset, err_reset, _ = self._run_cli(
        build_args(["reset", "--json"]), script=script
    )
    self.assertEqual(code_reset, 0, err_reset)
    reset_data = json.loads(out_reset)
    self.assertTrue(reset_data["success"])
    self.assertEqual(reset_data["title"], f"{expected_dir}/")
    self.assertEqual(err_reset, f"\033]0;{expected_dir}/\007")

  def test_pre_subcommand_repo_root_info_set_reset_gh243(self):
    """Pre-subcommand --repo-root targets the specified repo across info, set, and reset (GH-243)."""
    self._assert_title_subcommands(
        build_args=lambda sub: ["--repo-root", self.target_repo, *sub],
        expected_dir="target-project-dir",
        expected_ticket="GH-301",
        expected_tag="Target workspace feature",
    )

  def test_post_subcommand_repo_root_info_set_reset_gh243(self):
    """Post-subcommand --repo-root targets the specified repo across info, set, and reset (GH-243)."""
    self._assert_title_subcommands(
        build_args=lambda sub: [*sub, "--repo-root", self.post_repo],
        expected_dir="post-project-dir",
        expected_ticket="GH-302",
        expected_tag="Post workspace feature",
    )

  def test_omitted_repo_root_defaults_to_cwd_gh243(self):
    """Omitting --repo-root resolves dynamically to os.getcwd() across info, set, and reset (GH-243)."""
    self._assert_title_subcommands(
        build_args=lambda sub: list(sub),
        expected_dir="cwd-project-dir",
        expected_ticket="GH-101",
        expected_tag="CWD workspace bug",
    )

    with patch("os.getcwd", return_value=self.other_repo):
      pre_built = TerminalTitleScript()
    self._assert_title_subcommands(
        build_args=lambda sub: list(sub),
        expected_dir="cwd-project-dir",
        expected_ticket="GH-101",
        expected_tag="CWD workspace bug",
        script=pre_built,
    )
    self.assertEqual(pre_built.repo_root, os.path.abspath(self.cwd_repo))

  def test_subcommand_repo_root_precedence_gh243(self):
    """Subcommand --repo-root overrides parent --repo-root across info, set, and reset (GH-243)."""
    self._assert_title_subcommands(
        build_args=lambda sub: [
            "--repo-root",
            self.other_repo,
            *sub,
            "--repo-root",
            self.target_repo,
        ],
        expected_dir="target-project-dir",
        expected_ticket="GH-301",
        expected_tag="Target workspace feature",
    )

  def test_reused_instance_resets_repo_root_to_cwd_when_omitted_gh243(self):
    """Reusing one TerminalTitleScript resets self.repo_root to os.getcwd() when --repo-root is omitted (GH-243)."""
    reused = TerminalTitleScript()

    code_first, out_first, err_first, _ = self._run_cli(
        ["--repo-root", self.target_repo, "info", "--json"], script=reused
    )
    self.assertEqual(code_first, 0, err_first)
    first_data = json.loads(out_first)
    self.assertEqual(first_data["directory_name"], "target-project-dir")
    self.assertEqual(first_data["ticket_id"], "GH-301")
    self.assertEqual(reused.repo_root, os.path.abspath(self.target_repo))

    code_second, out_second, err_second, _ = self._run_cli(
        ["info", "--json"], script=reused
    )
    self.assertEqual(code_second, 0, err_second)
    second_data = json.loads(out_second)
    self.assertEqual(reused.repo_root, os.path.abspath(self.cwd_repo))
    self.assertEqual(second_data["directory_name"], "cwd-project-dir")
    self.assertEqual(second_data["ticket_id"], "GH-101")
    self.assertEqual(
        second_data["title"], "cwd-project-dir/: GH-101 CWD workspace bug"
    )

    code_third, out_third, err_third, _ = self._run_cli(
        ["set", "--repo-root", self.post_repo, "--json"], script=reused
    )
    self.assertEqual(code_third, 0, err_third)
    self.assertEqual(reused.repo_root, os.path.abspath(self.post_repo))
    self.assertEqual(
        json.loads(out_third)["title"],
        "post-project-dir/: GH-302 Post workspace feature",
    )
    self.assertEqual(
        err_third, "\033]0;post-project-dir/: GH-302 Post workspace feature\007"
    )

    code_fourth, out_fourth, err_fourth, _ = self._run_cli(
        ["reset", "--json"], script=reused
    )
    self.assertEqual(code_fourth, 0, err_fourth)
    self.assertEqual(reused.repo_root, os.path.abspath(self.cwd_repo))
    self.assertEqual(json.loads(out_fourth)["title"], "cwd-project-dir/")
    self.assertEqual(err_fourth, "\033]0;cwd-project-dir/\007")


if __name__ == "__main__":
  unittest.main()
