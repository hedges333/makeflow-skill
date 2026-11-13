#!/usr/bin/env python3
"""Hermetic unit tests for Conventional Commits helper and validator (GH-112).

Tests:
1. Header parsing for Conventional Commits 1.0.0 (types, scopes, breaking indicator, descriptions).
2. Remote issue mode trailer validation (Issue: #<N>, closes #<N>, (closes #<N>) in header, Reviewed-by).
3. Local session mode trailer validation (Ticket: LOCAL-<N>, anti-leak of closes #<N> or bare #<N>).
4. Changed files 2-line structure parsing and validation.
5. Message formatting helper (format_commit_message).
6. CLI execution lifecycle (CommitMsgScript validate and format subcommands).
"""

import io
import json
import os
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS_DIR = os.path.join(_REPO_ROOT, "scripts")
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

from lib.commit_msg import (
    CANONICAL_TYPES,
    CommitMsgScript,
    FileChange,
    HeaderInfo,
    ValidationResult,
    format_commit_message,
    parse_commit_header,
    validate_commit_message,
)
from lib.workflow_gates import WorkflowGatesScript


class TestConventionalCommitsHeaderParsing(unittest.TestCase):
  """Tests parsing and validation of Conventional Commits 1.0.0 header line."""

  def test_canonical_eleven_types_accepted(self):
    for ctype in CANONICAL_TYPES:
      line = f"{ctype}: concise summary description"
      info = parse_commit_header(line)
      self.assertIsNotNone(info, f"Failed for canonical type: {ctype}")
      self.assertEqual(info.type_name, ctype)
      self.assertIsNone(info.scope)
      self.assertFalse(info.is_breaking)
      self.assertEqual(info.description, "concise summary description")

  def test_optional_scope_accepted(self):
    line = "feat(gates): add deterministic workflow gate checks"
    info = parse_commit_header(line)
    self.assertIsNotNone(info)
    self.assertEqual(info.type_name, "feat")
    self.assertEqual(info.scope, "gates")
    self.assertFalse(info.is_breaking)
    self.assertEqual(info.description, "add deterministic workflow gate checks")

  def test_breaking_change_exclamation_without_scope(self):
    line = "refactor!: redesign core state machine transition protocol"
    info = parse_commit_header(line)
    self.assertIsNotNone(info)
    self.assertEqual(info.type_name, "refactor")
    self.assertIsNone(info.scope)
    self.assertTrue(info.is_breaking)
    self.assertEqual(
        info.description, "redesign core state machine transition protocol"
    )

  def test_breaking_change_exclamation_with_scope(self):
    line = "feat(api)!: remove deprecated v1 endpoints"
    info = parse_commit_header(line)
    self.assertIsNotNone(info)
    self.assertEqual(info.type_name, "feat")
    self.assertEqual(info.scope, "api")
    self.assertTrue(info.is_breaking)
    self.assertEqual(info.description, "remove deprecated v1 endpoints")

  def test_header_with_inline_closes_issue(self):
    line = "fix(intake): prevent race condition during intake (closes #112)"
    info = parse_commit_header(line)
    self.assertIsNotNone(info)
    self.assertEqual(info.type_name, "fix")
    self.assertEqual(info.scope, "intake")
    self.assertEqual(
        info.description,
        "prevent race condition during intake (closes #112)",
    )

  def test_reject_invalid_types(self):
    invalid_types = ["badtype", "FEAT", "Fix", "FEATURE", "bugfix", "cleanup"]
    for bad in invalid_types:
      line = f"{bad}: short description"
      info = parse_commit_header(line)
      self.assertIsNone(info, f"Should reject invalid type: {bad}")

  def test_reject_missing_colon_or_space(self):
    bad_headers = [
        "feat missing colon",
        "feat:no space after colon",
        "feat(scope):no space after colon",
        "feat : space before colon",
    ]
    for bad in bad_headers:
      self.assertIsNone(parse_commit_header(bad), f"Should reject: {bad}")

  def test_reject_empty_or_whitespace_description(self):
    bad_headers = [
        "feat:",
        "feat:   ",
        "feat(scope):",
        "feat(scope): ",
    ]
    for bad in bad_headers:
      self.assertIsNone(parse_commit_header(bad), f"Should reject: {bad}")

  def test_reject_trailing_period_in_description(self):
    line = "feat: add commit helper script."
    res = validate_commit_message(line, is_local=False, ticket_id="112")
    self.assertFalse(res.valid)
    self.assertTrue(
        any("trailing period" in err.lower() for err in res.errors),
        f"Errors did not mention trailing period: {res.errors}",
    )


class TestRemoteIssueModeTrailerValidation(unittest.TestCase):
  """Tests commit message validation for remote tracked issues (is_local=False)."""

  def test_valid_remote_with_issue_trailer(self):
    msg = (
        "feat(msg): add commit message helper\n\n"
        "Formats and validates commit messages according to CC 1.0.0.\n\n"
        "Issue: #112\n"
        "Reviewed-by: adversarial-critic\n"
    )
    res = validate_commit_message(msg, is_local=False, ticket_id="112")
    self.assertTrue(res.valid, f"Validation failed: {res.errors}")

  def test_valid_remote_with_closes_trailer(self):
    msg = (
        "fix(intake): sanitize input arguments\n\n"
        "Ensures intake flags are checked properly.\n\n"
        "closes #112\n"
        "Reviewed-by: adversarial-critic\n"
    )
    res = validate_commit_message(msg, is_local=False, ticket_id="112")
    self.assertTrue(res.valid, f"Validation failed: {res.errors}")

  def test_valid_remote_with_inline_header_closes(self):
    msg = (
        "docs: update pre-commit hygiene documentation (closes #112)\n\n"
        "Clarifies remote issue trailers and local task trailers.\n\n"
        "Reviewed-by: adversarial-critic\n"
    )
    res = validate_commit_message(msg, is_local=False, ticket_id="112")
    self.assertTrue(res.valid, f"Validation failed: {res.errors}")

  def test_valid_remote_with_capitalized_closes_trailer(self):
    msg = (
        "feat: add vector 4 reflex gates\n\n"
        "Validates commit trailers deterministically.\n\n"
        "Closes #57\n"
    )
    res = validate_commit_message(msg, is_local=False, ticket_id="57")
    self.assertTrue(res.valid, f"Validation failed: {res.errors}")

  def test_reject_remote_missing_issue_reference(self):
    msg = (
        "feat: add commit message helper\n\n"
        "Formats and validates commit messages.\n\n"
        "Reviewed-by: adversarial-critic\n"
    )
    res = validate_commit_message(msg, is_local=False, ticket_id="112")
    self.assertFalse(res.valid)
    self.assertTrue(
        any("missing" in err.lower() and "issue" in err.lower() for err in res.errors),
        f"Expected missing issue error: {res.errors}",
    )

  def test_reject_remote_with_ticket_gh_prefix(self):
    msg = (
        "feat: add commit message helper\n\n"
        "Formats and validates commit messages.\n\n"
        "Ticket: GH-112\n"
        "Reviewed-by: adversarial-critic\n"
    )
    res = validate_commit_message(msg, is_local=False, ticket_id="112")
    self.assertFalse(res.valid)
    self.assertTrue(
        any("ticket: gh-" in err.lower() for err in res.errors),
        f"Expected rejection of Ticket: GH-<N>: {res.errors}",
    )

  def test_reject_remote_with_local_ticket_trailer(self):
    msg = (
        "feat: add commit message helper\n\n"
        "Formats and validates commit messages.\n\n"
        "Ticket: LOCAL-5\n"
        "Reviewed-by: adversarial-critic\n"
    )
    res = validate_commit_message(msg, is_local=False, ticket_id="112")
    self.assertFalse(res.valid)
    self.assertTrue(
        any("ticket: local-" in err.lower() for err in res.errors),
        f"Expected rejection of Ticket: LOCAL-<N> in remote mode: {res.errors}",
    )


class TestLocalSessionModeTrailerValidation(unittest.TestCase):
  """Tests commit message validation for local session tasks (is_local=True)."""

  def test_valid_local_task(self):
    msg = (
        "feat: add local scratchpad utility\n\n"
        "Provides simple scratch buffer for experimental scripts.\n\n"
        "Ticket: LOCAL-7\n"
        "Reviewed-by: adversarial-critic\n"
    )
    res = validate_commit_message(msg, is_local=True, ticket_id="LOCAL-7")
    self.assertTrue(res.valid, f"Validation failed: {res.errors}")

  def test_valid_local_task_without_critic_trailer(self):
    msg = (
        "feat: local demo\n\n"
        "Ticket: LOCAL-7\n"
    )
    res = validate_commit_message(msg, is_local=True, ticket_id="LOCAL-7")
    self.assertTrue(res.valid, f"Validation failed: {res.errors}")

  def test_reject_local_with_closes_issue(self):
    msg = (
        "feat: local demo\n\n"
        "closes #57\n"
        "Ticket: LOCAL-7\n"
    )
    res = validate_commit_message(msg, is_local=True, ticket_id="LOCAL-7")
    self.assertFalse(res.valid)
    self.assertTrue(
        any("closes" in err.lower() or "github issue" in err.lower() for err in res.errors),
        f"Expected rejection of closes in local mode: {res.errors}",
    )

  def test_reject_local_with_bare_issue_number(self):
    msg = (
        "feat: local demo (#57)\n\n"
        "Ticket: LOCAL-7\n"
    )
    res = validate_commit_message(msg, is_local=True, ticket_id="LOCAL-7")
    self.assertFalse(res.valid)
    self.assertTrue(
        any("bare" in err.lower() or "#" in err for err in res.errors),
        f"Expected rejection of bare #57: {res.errors}",
    )

  def test_reject_local_with_fixes_issue(self):
    msg = (
        "fix: local quickfix\n\n"
        "fixes #42\n"
        "Ticket: LOCAL-2\n"
    )
    res = validate_commit_message(msg, is_local=True, ticket_id="LOCAL-2")
    self.assertFalse(res.valid)
    self.assertTrue(
        any("fixes" in err.lower() or "github issue" in err.lower() for err in res.errors),
        f"Expected rejection of fixes in local mode: {res.errors}",
    )

  def test_reject_local_missing_ticket_trailer(self):
    msg = (
        "feat: local demo\n\n"
        "Clean body without ticket trailer.\n"
    )
    res = validate_commit_message(msg, is_local=True, ticket_id="LOCAL-7")
    self.assertFalse(res.valid)
    self.assertTrue(
        any("ticket: local-" in err.lower() for err in res.errors),
        f"Expected missing Ticket: LOCAL-7 error: {res.errors}",
    )


class TestChangedFilesTwoLineStructureValidation(unittest.TestCase):
  """Tests parsing and validation of the 2-line changed files section."""

  def test_valid_changed_files_section(self):
    msg = (
        "feat(msg): add conventional commits helper\n\n"
        "Provides parsing, formatting, and validation helper.\n\n"
        "Files changed:\n"
        "- scripts/commit_msg.py:\n"
        "  Add Conventional Commits parser and validator.\n"
        "  Fulfills ticket requirement for commit message helper.\n"
        "- scripts/workflow_gates.py:\n"
        "  Delegate commit-msg gate to commit_msg validator.\n"
        "  Connects new validation logic to workflow gates.\n\n"
        "Issue: #112\n"
        "Reviewed-by: adversarial-critic\n"
    )
    res = validate_commit_message(msg, is_local=False, ticket_id="112")
    self.assertTrue(res.valid, f"Validation failed: {res.errors}")
    self.assertEqual(len(res.changed_files), 2)
    self.assertEqual(res.changed_files[0].path, "scripts/commit_msg.py")
    self.assertEqual(
        res.changed_files[0].description,
        "Add Conventional Commits parser and validator.",
    )
    self.assertEqual(
        res.changed_files[0].relation,
        "Fulfills ticket requirement for commit message helper.",
    )

  def test_reject_changed_files_with_only_one_indented_line(self):
    msg = (
        "feat: add helper\n\n"
        "Files changed:\n"
        "- scripts/commit_msg.py:\n"
        "  Only one line description without relation.\n\n"
        "Issue: #112\n"
    )
    res = validate_commit_message(msg, is_local=False, ticket_id="112")
    self.assertFalse(res.valid)
    self.assertTrue(
        any("2 lines" in err.lower() or "exactly 2" in err.lower() or "relation" in err.lower() for err in res.errors),
        f"Expected 2-line structure error: {res.errors}",
    )

  def test_reject_changed_files_with_three_indented_lines(self):
    msg = (
        "feat: add helper\n\n"
        "Files changed:\n"
        "- scripts/commit_msg.py:\n"
        "  Line 1 description.\n"
        "  Line 2 relation.\n"
        "  Line 3 extra line.\n\n"
        "Issue: #112\n"
    )
    res = validate_commit_message(msg, is_local=False, ticket_id="112")
    self.assertFalse(res.valid)
    self.assertTrue(
        any("2 lines" in err.lower() or "exactly 2" in err.lower() or "extra" in err.lower() for err in res.errors),
        f"Expected 2-line structure error: {res.errors}",
    )

  def test_reject_changed_files_with_non_indented_lines(self):
    msg = (
        "feat: add helper\n\n"
        "Files changed:\n"
        "- scripts/commit_msg.py:\n"
        "Non-indented description line.\n"
        "Non-indented relation line.\n\n"
        "Issue: #112\n"
    )
    res = validate_commit_message(msg, is_local=False, ticket_id="112")
    self.assertFalse(res.valid)
    self.assertTrue(
        any("indent" in err.lower() or "2 lines" in err.lower() for err in res.errors),
        f"Expected indentation error: {res.errors}",
    )


class TestCommitMessageFormatter(unittest.TestCase):
  """Tests format_commit_message helper function."""

  def test_format_remote_issue_commit_message(self):
    changes = [
        FileChange(
            path="scripts/commit_msg.py",
            description="Add Conventional Commits helper script.",
            relation="Standardizes commit message generation for GH-112.",
        ),
        FileChange(
            path="scripts/workflow_gates.py",
            description="Integrate commit_msg validator into commit-msg gate.",
            relation="Enforces Conventional Commits in pre-commit hygiene checks.",
        ),
    ]
    formatted = format_commit_message(
        type_name="feat",
        scope="gates",
        description="add Conventional Commits helper script",
        body="Implements CC 1.0.0 parser, validator, and formatter.",
        changed_files=changes,
        issue_id="112",
        is_local=False,
        reviewed_by="adversarial-critic",
    )
    self.assertIn("feat(gates): add Conventional Commits helper script (closes #112)", formatted)
    self.assertIn("Files changed:\n- scripts/commit_msg.py:\n  Add Conventional Commits helper script.\n  Standardizes commit message generation for GH-112.", formatted)
    self.assertIn("Issue: #112", formatted)
    self.assertIn("Reviewed-by: adversarial-critic", formatted)

    # Formatted message must validate successfully
    res = validate_commit_message(formatted, is_local=False, ticket_id="112")
    self.assertTrue(res.valid, f"Formatted message failed validation: {res.errors}")

  def test_format_local_session_commit_message(self):
    changes = [
        FileChange(
            path="scripts/scratch.py",
            description="Temporary scratchpad script.",
            relation="Aids local performance experiments.",
        )
    ]
    formatted = format_commit_message(
        type_name="chore",
        scope=None,
        description="add scratchpad experiment",
        body="Local temporary benchmark script.",
        changed_files=changes,
        ticket_id="LOCAL-9",
        is_local=True,
        reviewed_by="adversarial-critic",
    )
    self.assertTrue(formatted.startswith("chore: add scratchpad experiment\n"))
    self.assertIn("Ticket: LOCAL-9", formatted)
    self.assertNotIn("closes #", formatted)
    self.assertNotIn("Issue: #", formatted)

    # Formatted message must validate successfully
    res = validate_commit_message(formatted, is_local=True, ticket_id="LOCAL-9")
    self.assertTrue(res.valid, f"Formatted message failed validation: {res.errors}")


class TestCommitMsgScriptCLI(unittest.TestCase):
  """Tests CommitMsgScript execution lifecycle, arguments, and exit codes."""

  def test_cli_validate_success(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      msg_file = os.path.join(tmpdir, "50_commit_msg.txt")
      with open(msg_file, "w", encoding="utf-8") as f:
        f.write(
            "feat(gates): add helper script\n\n"
            "Body description.\n\n"
            "Issue: #112\n"
            "Reviewed-by: adversarial-critic\n"
        )
      out = io.StringIO()
      err = io.StringIO()
      code = CommitMsgScript().execute(
          args=["validate", "--file", msg_file, "--ticket", "112"],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(code, 0, f"Expected 0, got {code}. stderr: {err.getvalue()}")

  def test_cli_validate_failure_emits_reflex_exit_2(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      msg_file = os.path.join(tmpdir, "50_commit_msg.txt")
      with open(msg_file, "w", encoding="utf-8") as f:
        f.write("invalid header without colon\n\nBody.\n")
      out = io.StringIO()
      err = io.StringIO()
      code = CommitMsgScript().execute(
          args=["validate", "--file", msg_file, "--ticket", "112"],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(code, 2)
      self.assertIn("[SCAR-ID: GIT-20] INVALID_COMMIT_MESSAGE_CONTRACT", err.getvalue())

  def test_cli_validate_json_mode(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      msg_file = os.path.join(tmpdir, "50_commit_msg.txt")
      with open(msg_file, "w", encoding="utf-8") as f:
        f.write("bad header\n")
      out = io.StringIO()
      err = io.StringIO()
      code = CommitMsgScript().execute(
          args=["validate", "--file", msg_file, "--json"],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(code, 2)
      payload = json.loads(err.getvalue().strip())
      self.assertEqual(payload["violation_code"], "INVALID_COMMIT_MESSAGE_CONTRACT")
      self.assertEqual(payload["exit_code"], 2)


class TestWorkflowGatesCommitMsgIntegration(unittest.TestCase):
  """Tests commit-msg gate inside WorkflowGatesScript integrating Conventional Commits."""

  def test_workflow_gates_remote_commit_msg_success(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      # Initialize git repo with github origin
      import subprocess
      subprocess.run(["git", "init", "-q"], cwd=tmpdir, check=True)
      subprocess.run(
          ["git", "remote", "add", "origin", "git@github.com:owner/repo.git"],
          cwd=tmpdir,
          check=True,
      )
      subprocess.run(
          ["git", "checkout", "-q", "-b", "feat/112-test-integration"],
          cwd=tmpdir,
          check=True,
      )
      state_dir = os.path.join(tmpdir, ".workflow", "state")
      os.makedirs(state_dir, exist_ok=True)
      msg_file = os.path.join(state_dir, "50_commit_msg.txt")
      with open(msg_file, "w", encoding="utf-8") as f:
        f.write(
            "feat(gates): add conventional commits gate validation\n\n"
            "Ensures commits follow CC 1.0.0 and remote issue trailers.\n\n"
            "Issue: #112\n"
            "Reviewed-by: adversarial-critic\n"
        )
      out = io.StringIO()
      err = io.StringIO()
      code = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "commit-msg"],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(code, 0, f"Expected 0, got {code}. stderr: {err.getvalue()}")

  def test_workflow_gates_remote_commit_msg_rejects_bad_type(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      import subprocess
      subprocess.run(["git", "init", "-q"], cwd=tmpdir, check=True)
      subprocess.run(
          ["git", "remote", "add", "origin", "git@github.com:owner/repo.git"],
          cwd=tmpdir,
          check=True,
      )
      subprocess.run(
          ["git", "checkout", "-q", "-b", "feat/112-test-integration"],
          cwd=tmpdir,
          check=True,
      )
      state_dir = os.path.join(tmpdir, ".workflow", "state")
      os.makedirs(state_dir, exist_ok=True)
      msg_file = os.path.join(state_dir, "50_commit_msg.txt")
      with open(msg_file, "w", encoding="utf-8") as f:
        f.write(
            "invalidtype: add conventional commits gate validation\n\n"
            "Issue: #112\n"
        )
      out = io.StringIO()
      err = io.StringIO()
      code = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "commit-msg"],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(code, 2)
      self.assertIn("INVALID_COMMIT_MESSAGE_CONTRACT", err.getvalue())

  def test_workflow_gates_remote_commit_msg_rejects_ticket_gh(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      import subprocess
      subprocess.run(["git", "init", "-q"], cwd=tmpdir, check=True)
      subprocess.run(
          ["git", "remote", "add", "origin", "git@github.com:owner/repo.git"],
          cwd=tmpdir,
          check=True,
      )
      subprocess.run(
          ["git", "checkout", "-q", "-b", "feat/112-test-integration"],
          cwd=tmpdir,
          check=True,
      )
      state_dir = os.path.join(tmpdir, ".workflow", "state")
      os.makedirs(state_dir, exist_ok=True)
      msg_file = os.path.join(state_dir, "50_commit_msg.txt")
      with open(msg_file, "w", encoding="utf-8") as f:
        f.write(
            "feat: add conventional commits gate validation\n\n"
            "Ticket: GH-112\n"
            "Reviewed-by: adversarial-critic\n"
        )
      out = io.StringIO()
      err = io.StringIO()
      code = WorkflowGatesScript().execute(
          args=["--repo-root", tmpdir, "commit-msg"],
          stdout=out,
          stderr=err,
      )
      self.assertEqual(code, 2)
      self.assertIn("INVALID_COMMIT_MESSAGE_CONTRACT", err.getvalue())
      self.assertIn("Ticket: GH-", err.getvalue())


if __name__ == "__main__":
  unittest.main()
