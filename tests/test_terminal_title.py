#!/usr/bin/env python3
"""Hermetic unit tests for terminal title detection and setting (GH-103)."""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS_DIR = os.path.join(_REPO_ROOT, "scripts")
for _p in (_REPO_ROOT, _SCRIPTS_DIR):
  if _p not in sys.path:
    sys.path.insert(0, _p)

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


class TestProjectAndTicketDetection(unittest.TestCase):
  """Tests for detecting project name and ticket ID."""

  def setUp(self):
    self.temp_dir = tempfile.mkdtemp(prefix="makeflow_title_test_")

  def tearDown(self):
    shutil.rmtree(self.temp_dir, ignore_errors=True)

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
    card_path = os.path.join(state_dir, "10_ticket.md")
    with open(card_path, "w", encoding="utf-8") as f:
      f.write("# Ticket Intake: GH-103\n- **Ticket ID**: GH-103\n")
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

  def test_ticket_id_from_next_action_card(self):
    state_dir = os.path.join(self.temp_dir, ".workflow", "state")
    os.makedirs(state_dir, exist_ok=True)
    card_path = os.path.join(state_dir, "99_next_action.md")
    with open(card_path, "w", encoding="utf-8") as f:
      f.write("- Ticket: [LOCAL-456: Test local ticket](url)\n")
    ticket = detect_ticket_id(repo_root=self.temp_dir)
    self.assertEqual(ticket, "LOCAL-456")

  def test_format_terminal_title(self):
    self.assertEqual(
        format_terminal_title("GH-103", "makeflow-skill"),
        "GH-103 makeflow-skill",
    )
    self.assertEqual(
        format_terminal_title("LOCAL-42", "engine"),
        "LOCAL-42 engine",
    )
    self.assertEqual(format_terminal_title("GH-103", ""), "GH-103")
    self.assertEqual(format_terminal_title("", "engine"), "engine")
    self.assertEqual(format_terminal_title("", ""), "")

  def test_format_terminal_title_with_issue_tag(self):
    self.assertEqual(
        format_terminal_title(
            "GH-113", "makeflow-skill", "enhance terminal title"
        ),
        "GH-113 makeflow-skill: enhance terminal title",
    )
    self.assertEqual(
        format_terminal_title("LOCAL-42", "engine", "offline mode"),
        "LOCAL-42 engine: offline mode",
    )
    self.assertEqual(
        format_terminal_title("", "engine", "offline mode"),
        "engine: offline mode",
    )
    self.assertEqual(
        format_terminal_title("GH-113", "", "enhance terminal title"),
        "GH-113: enhance terminal title",
    )
    self.assertEqual(
        format_terminal_title("", "", "enhance terminal title"),
        "enhance terminal title",
    )
    # Backwards compatibility with None and empty issue_tag
    self.assertEqual(
        format_terminal_title("GH-113", "makeflow-skill", None),
        "GH-113 makeflow-skill",
    )
    self.assertEqual(
        format_terminal_title("GH-113", "makeflow-skill", ""),
        "GH-113 makeflow-skill",
    )

  def test_issue_tag_preserves_full_title(self):
    long_tag = (
        "Fix authentication timeout on redirect and retry when session expires after idle period"  # 87 chars
    )
    result = format_terminal_title("GH-42", "backend", long_tag)
    tag_part = result.split(": ", 1)[1]
    self.assertEqual(tag_part, long_tag)
    self.assertEqual(result, f"GH-42 backend: {long_tag}")

  def test_detect_issue_tag_preserves_full_title_from_state_cards(self):
    state_dir = os.path.join(self.temp_dir, ".workflow", "state")
    os.makedirs(state_dir, exist_ok=True)
    long_desc = "refactor broad exception handlers and preserve full terminal title in session start"
    card_10 = os.path.join(state_dir, "10_ticket.md")
    with open(card_10, "w", encoding="utf-8") as f:
      f.write(
          f"# Ticket Intake: GH-130\n\n"
          f"- Ticket: [GH-130: [FEAT] {long_desc}](https://github.com/...)\n"
      )
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
    card_10 = os.path.join(state_dir, "10_ticket.md")
    with open(card_10, "wb") as f:
      f.write(b"\xff\xfe\x00\x01")
    card_99 = os.path.join(state_dir, "99_next_action.md")
    with open(card_99, "wb") as f:
      f.write(b"\xff\xfe\x00\x01")
    ticket = detect_ticket_id(repo_root=self.temp_dir, branch="")
    self.assertEqual(ticket, "")

  def test_detect_issue_tag_handles_unicode_decode_error(self):
    state_dir = os.path.join(self.temp_dir, ".workflow", "state")
    os.makedirs(state_dir, exist_ok=True)
    card_10 = os.path.join(state_dir, "10_ticket.md")
    with open(card_10, "wb") as f:
      f.write(b"\xff\xfe\x00\x01")
    card_99 = os.path.join(state_dir, "99_next_action.md")
    with open(card_99, "wb") as f:
      f.write(b"\xff\xfe\x00\x01")
    tag = detect_issue_tag(repo_root=self.temp_dir, branch="")
    self.assertEqual(tag, "")

  def test_detect_issue_tag_from_state_cards_and_branch(self):
    self.assertIsNotNone(
        detect_issue_tag, "detect_issue_tag must be exported from terminal_title"
    )
    state_dir = os.path.join(self.temp_dir, ".workflow", "state")
    os.makedirs(state_dir, exist_ok=True)

    # 1. State card 10_ticket.md with - Ticket: [GH-113: [FEAT] enhance terminal title](url)
    card_10 = os.path.join(state_dir, "10_ticket.md")
    with open(card_10, "w", encoding="utf-8") as f:
      f.write(
          "# Ticket Intake: GH-113\n\n"
          "- Ticket: [GH-113: [FEAT] enhance terminal title](https://github.com/...)\n"
          "- Branch: `feat/113-enhance-terminal-title`\n"
      )
    tag = detect_issue_tag(repo_root=self.temp_dir)
    self.assertEqual(tag, "enhance terminal title")

    # 2. State card 10_ticket.md with # Ticket Intake: GH-113: enhance terminal title
    with open(card_10, "w", encoding="utf-8") as f:
      f.write("# Ticket Intake: GH-113: enhance terminal title\n")
    tag = detect_issue_tag(repo_root=self.temp_dir)
    self.assertEqual(tag, "enhance terminal title")

    # 3. State card 10_ticket.md with ## Description fallback
    with open(card_10, "w", encoding="utf-8") as f:
      f.write("# Ticket Intake: GH-113\n\n## Description\n\nenhance terminal title\n")
    tag = detect_issue_tag(repo_root=self.temp_dir)
    self.assertEqual(tag, "enhance terminal title")

    # 4. State card 99_next_action.md
    os.remove(card_10)
    card_99 = os.path.join(state_dir, "99_next_action.md")
    with open(card_99, "w", encoding="utf-8") as f:
      f.write(
          "- Ticket: [LOCAL-456: Test local ticket](url) (In Progress)\n"
      )
    tag = detect_issue_tag(repo_root=self.temp_dir)
    self.assertEqual(tag, "Test local ticket")

    # 5. Git branch slug fallback
    os.remove(card_99)
    tag = detect_issue_tag(
        repo_root=self.temp_dir, branch="feat/113-enhance-terminal-title"
    )
    self.assertEqual(tag, "enhance terminal title")
    tag = detect_issue_tag(
        repo_root=self.temp_dir, branch="fix/local-42-login-redirect-timeout"
    )
    self.assertEqual(tag, "login redirect timeout")

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
    temp_dir = tempfile.mkdtemp(prefix="tmp")
    try:
      name = detect_project_name(repo_root=temp_dir, git_remote_url="")
      self.assertEqual(name, "")
    finally:
      shutil.rmtree(temp_dir, ignore_errors=True)

    # Synthetic temporary path fallback check
    name = detect_project_name(repo_root="/tmp/tmpabcdef1", git_remote_url="")
    self.assertEqual(name, "")
    name = detect_project_name(repo_root="/tmp/tmp_99887766", git_remote_url="")
    self.assertEqual(name, "")


class TestSetTerminalTitle(unittest.TestCase):
  """Tests for set_terminal_title integration function."""

  def test_successful_title_set_on_xterm(self):
    stream = MockTtyStream()
    ok, applied = set_terminal_title(
        ticket_id="GH-103",
        project_name="makeflow-skill",
        stream=stream,
        env={"TERM": "xterm-256color"},
    )
    self.assertTrue(ok)
    self.assertEqual(applied, "GH-103 makeflow-skill")
    self.assertEqual(stream.getvalue(), "\033]0;GH-103 makeflow-skill\007")

  def test_successful_title_set_on_tmux(self):
    stream = MockTtyStream()
    ok, applied = set_terminal_title(
        ticket_id="LOCAL-77",
        project_name="core-service",
        stream=stream,
        env={"TERM": "screen-256color", "TMUX": "/tmp/tmux/1,0,0"},
    )
    self.assertTrue(ok)
    self.assertEqual(applied, "LOCAL-77 core-service")
    self.assertEqual(
        stream.getvalue(), "\033Ptmux;\033\033]0;LOCAL-77 core-service\007\033\\"
    )

  def test_no_output_when_capability_is_false(self):
    stream = MockNonTtyStream()
    ok, applied = set_terminal_title(
        ticket_id="GH-103",
        project_name="makeflow-skill",
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
    temp_dir = tempfile.mkdtemp(prefix="tmp")
    try:
      ok, applied = set_terminal_title(
          repo_root=temp_dir,
          stream=stream,
          env={"TERM": "xterm-256color"},
      )
      self.assertFalse(ok)
      self.assertEqual(applied, "")
      self.assertEqual(stream.getvalue(), "")
    finally:
      shutil.rmtree(temp_dir, ignore_errors=True)

  def test_set_terminal_title_with_issue_tag(self):
    stream = MockTtyStream()
    ok, applied = set_terminal_title(
        ticket_id="GH-113",
        project_name="makeflow-skill",
        issue_tag="enhance terminal title",
        stream=stream,
        env={"TERM": "xterm-256color"},
    )
    self.assertTrue(ok)
    self.assertEqual(applied, "GH-113 makeflow-skill: enhance terminal title")
    self.assertEqual(
        stream.getvalue(),
        "\033]0;GH-113 makeflow-skill: enhance terminal title\007",
    )

  def test_set_terminal_title_catches_oserror_and_unicode_encode_error(self):
    stream = MockTtyStream()
    with patch.object(stream, "write", side_effect=OSError("Disk/TTY write error")):
      ok, applied = set_terminal_title(
          ticket_id="GH-130",
          project_name="makeflow-skill",
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
          project_name="makeflow-skill",
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
            project_name="makeflow-skill",
            stream=stream,
            env={"TERM": "xterm-256color"},
        )

    with patch.object(
        stream, "write", side_effect=RuntimeError("Synthetic RuntimeError")
    ):
      with self.assertRaises(RuntimeError):
        set_terminal_title(
            ticket_id="GH-130",
            project_name="makeflow-skill",
            stream=stream,
            env={"TERM": "xterm-256color"},
        )


class TestTerminalTitleScriptLifecycle(unittest.TestCase):
  """Tests for TerminalTitleScript BaseScript CLI lifecycle."""

  def setUp(self):
    self.temp_dir = tempfile.mkdtemp(prefix="makeflow_cli_test_")

  def tearDown(self):
    shutil.rmtree(self.temp_dir, ignore_errors=True)

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
            "--project",
            "makeflow-skill",
            "--json",
        ],
        env_override={"TERM": "xterm-256color"},
        mock_tty=True,
    )
    self.assertEqual(exit_code, 0)
    data = json.loads(out.getvalue())
    self.assertEqual(data["ticket_id"], "GH-103")
    self.assertEqual(data["project_name"], "makeflow-skill")
    self.assertEqual(data["title"], "GH-103 makeflow-skill")
    self.assertTrue(data["can_change"])

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
            "--project",
            "super-tool",
        ],
        env_override={"TERM": "xterm-256color"},
        mock_tty=True,
    )
    self.assertEqual(exit_code, 0)
    self.assertIn("\033]0;LOCAL-999 super-tool\007", err.getvalue())

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
            "--project",
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
    self.assertEqual(data["project_name"], "makeflow-skill")
    self.assertEqual(data["issue_tag"], "enhance terminal title")
    self.assertEqual(
        data["title"], "GH-113 makeflow-skill: enhance terminal title"
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
            "--project",
            "makeflow-skill",
            "--issue-tag",
            "enhance terminal title",
        ],
        env_override={"TERM": "xterm-256color"},
        mock_tty=True,
    )
    self.assertEqual(exit_code, 0)
    self.assertIn(
        "\033]0;GH-113 makeflow-skill: enhance terminal title\007",
        err.getvalue(),
    )

  def test_reset_command_restores_project_title(self):
    script = TerminalTitleScript()
    out = io.StringIO()
    err = io.StringIO()
    script.stdout = out
    script.stderr = err
    exit_code = script.execute(
        [
            "reset",
            "--project",
            "makeflow-skill",
        ],
        env_override={"TERM": "xterm-256color"},
        mock_tty=True,
    )
    self.assertEqual(exit_code, 0)
    self.assertIn("\033]0;makeflow-skill\007", err.getvalue())

  def test_reset_command_rejects_temporary_project_title(self):
    script = TerminalTitleScript()
    out = io.StringIO()
    err = io.StringIO()
    script.stdout = out
    script.stderr = err
    exit_code = script.execute(
        [
            "reset",
            "--project",
            "tmpa1b2c3d4",
        ],
        env_override={"TERM": "xterm-256color"},
        mock_tty=True,
    )
    self.assertEqual(exit_code, 0)
    self.assertEqual(err.getvalue(), "")


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
              "--project",
              "makeflow-skill",
              "--json",
          ],
          stdout=out,
          stderr=err,
      )
    self.assertEqual(exit_code, 0)
    data = json.loads(out.getvalue())
    self.assertEqual(data["title"], "GH-103 makeflow-skill")
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
              "--project",
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
        data["title"], "GH-113 makeflow-skill: enhance terminal title"
    )
    self.assertTrue(data["success"])

  def test_workflow_gates_release_intake_restores_title(self):
    from lib.workflow_gates import WorkflowGatesScript

    script = WorkflowGatesScript()
    out = io.StringIO()
    err = MockTtyStream()
    temp_dir = tempfile.mkdtemp(prefix="makeflow_release_test_")
    try:
      manifest = os.path.join(temp_dir, "plugin.json")
      with open(manifest, "w", encoding="utf-8") as f:
        json.dump({"name": "restored-project"}, f)

      with patch.dict(os.environ, {"TERM": "xterm-256color"}):
        exit_code = script.execute(
            [
                "--repo-root",
                temp_dir,
                "release-intake",
            ],
            stdout=out,
            stderr=err,
        )
      self.assertEqual(exit_code, 0)
      # Must set terminal title to restored-project
      self.assertIn("\033]0;restored-project\007", err.getvalue())
    finally:
      shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
  unittest.main()
