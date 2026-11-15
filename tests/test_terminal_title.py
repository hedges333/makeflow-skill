#!/usr/bin/env python3
"""Hermetic unit tests for terminal title detection and setting (GH-103)."""

import importlib
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
        "makeflow-skill/: GH-103",
    )
    self.assertEqual(
        format_terminal_title("LOCAL-42", "engine"),
        "engine/: LOCAL-42",
    )
    self.assertEqual(format_terminal_title("GH-103", ""), "GH-103")
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
        "engine/: offline mode",
    )
    self.assertEqual(
        format_terminal_title("GH-113", "", "enhance terminal title"),
        "GH-113 enhance terminal title",
    )
    self.assertEqual(
        format_terminal_title("", "", "enhance terminal title"),
        "enhance terminal title",
    )
    # Trailing slashes and root path normalization when dir_name passed directly
    self.assertEqual(
        format_terminal_title("GH-103", "foo/", "bar"),
        "foo/: GH-103 bar",
    )
    self.assertEqual(
        format_terminal_title("GH-103", "/", "bar"),
        "GH-103 bar",
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


class TestDirectoryNameDetection(unittest.TestCase):
  """Tests for bare unqualified directory name extraction (GH-224)."""

  def setUp(self):
    self.temp_dir = tempfile.mkdtemp(prefix="makeflow_dir_test_")

  def tearDown(self):
    shutil.rmtree(self.temp_dir, ignore_errors=True)

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
    temp_dir = tempfile.mkdtemp(prefix="tmp")
    try:
      self.assertEqual(
          terminal_title.detect_directory_name(repo_root=temp_dir), ""
      )
    finally:
      shutil.rmtree(temp_dir, ignore_errors=True)

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
    temp_parent = tempfile.mkdtemp(prefix="makeflow_gh224_test_")
    try:
      repo_dir = os.path.join(temp_parent, "makeflow-skill-dir3")
      state_dir = os.path.join(repo_dir, ".workflow", "state")
      os.makedirs(state_dir, exist_ok=True)
      with open(
          os.path.join(repo_dir, "plugin.json"), "w", encoding="utf-8"
      ) as f:
        json.dump({"name": "makeflow"}, f)
      with open(
          os.path.join(state_dir, "10_ticket.md"), "w", encoding="utf-8"
      ) as f:
        f.write(
            "# Ticket Intake: GH-1234\n\n"
            "- Ticket: [GH-1234: [FEAT] Add frobnosticator to reticulator subnodule](https://example.com)\n"
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
    finally:
      shutil.rmtree(temp_parent, ignore_errors=True)

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
    temp_dir = tempfile.mkdtemp(prefix="makeflow_release_test_")
    try:
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
    finally:
      shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
  unittest.main()
