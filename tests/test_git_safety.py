#!/usr/bin/env python3
"""Unit tests for shared Git/gh safety classification in lib/git_safety.py (GH-213)."""

import ast
from dataclasses import FrozenInstanceError
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
  sys.path.insert(0, REPO_ROOT)

import lib.git_safety as git_safety  # noqa: E402
from lib.git_safety import (  # noqa: E402
    GitTokenVerdict,
    _evaluate_git_tokens_safety,
    classify_git_tokens,
    evaluate_git_safety,
)
import mcp.server as mcp_server  # noqa: E402


class TestGitTokenClassification(unittest.TestCase):
  """Tests structured GitTokenVerdict classification across all 14 Git/gh rules (GH-213)."""

  def test_verdict_is_immutable_frozen_dataclass(self):
    """Verifies GitTokenVerdict is a frozen value object preventing field mutation."""
    verdict = GitTokenVerdict(decision="allow", rule="ok")
    self.assertEqual(verdict.decision, "allow")
    self.assertEqual(verdict.rule, "ok")
    self.assertEqual(verdict.detail, "")
    self.assertEqual(verdict.positionals, ())
    with self.assertRaises((FrozenInstanceError, AttributeError)):
      verdict.decision = "deny"  # type: ignore[misc]

  def test_non_git_empty_and_shell_c_commands_allow(self):
    """Verifies empty, non-git, bare git, and SHELL_C tokens classify as allow/ok."""
    for tokens in (
        [],
        ["echo", "hello"],
        ["git"],
        ["git", "--version"],
        ["sh", "-c", "git status"],
    ):
      with self.subTest(tokens=tokens):
        verdict = classify_git_tokens(tokens)
        self.assertEqual(verdict.decision, "allow")
        self.assertEqual(verdict.rule, "ok")

  def test_gh_pr_merge_gated_and_safe_gh_allowed(self):
    """Verifies gh pr merge returns force_ask and non-merge gh commands allow."""
    for tokens in (
        ["gh", "pr", "merge", "42", "--squash"],
        ["gh", "-R", "org/repo", "pr", "merge", "42"],
        ["gh", "--repo", "org/repo", "--hostname", "github.com", "pr", "merge"],
    ):
      with self.subTest(tokens=tokens):
        verdict = classify_git_tokens(tokens)
        self.assertEqual(verdict.decision, "force_ask")
        self.assertEqual(verdict.rule, "gh_pr_merge")

    for safe_tokens in (
        ["gh", "pr", "view", "42"],
        ["gh", "issue", "list"],
        ["gh", "auth", "status"],
    ):
      with self.subTest(safe_tokens=safe_tokens):
        verdict = classify_git_tokens(safe_tokens)
        self.assertEqual(verdict.decision, "allow")
        self.assertEqual(verdict.rule, "ok")

  def test_git_reset_destructive_and_safe_flags(self):
    """Verifies git reset --hard/--merge/--keep deny while --soft allows."""
    for bad_flag in ("--hard", "--merge", "--keep"):
      with self.subTest(bad_flag=bad_flag):
        verdict = classify_git_tokens(["git", "reset", bad_flag, "HEAD~1"])
        self.assertEqual(verdict.decision, "deny")
        self.assertEqual(verdict.rule, "git_reset")
        self.assertEqual(verdict.detail, bad_flag)

    safe_verdict = classify_git_tokens(["git", "reset", "--soft", "HEAD~1"])
    self.assertEqual(safe_verdict.decision, "allow")
    self.assertEqual(safe_verdict.rule, "ok")

  def test_git_clean_force_interactive_config_and_dry_run_precedence(self):
    """Verifies git clean respects ordered -n/--no-dry-run/-f/-i and clean.requireForce=false."""
    for bad_tokens in (
        ["git", "clean", "-fd"],
        ["git", "clean", "-i"],
        ["git", "clean", "-n", "--no-dry-run", "-fd"],
        ["git", "-c", "clean.requireForce=false", "clean"],
        ["git", "-c", "clean.requireForce=false", "clean", "--no-force"],
    ):
      with self.subTest(bad_tokens=bad_tokens):
        verdict = classify_git_tokens(bad_tokens)
        self.assertEqual(verdict.decision, "deny")
        self.assertEqual(verdict.rule, "git_clean")

    for safe_tokens in (
        ["git", "clean", "-nd"],
        ["git", "clean", "-f", "--dry-run"],
        ["git", "clean", "-i", "--no-interactive"],
        ["git", "clean", "-f", "--no-force"],
    ):
      with self.subTest(safe_tokens=safe_tokens):
        verdict = classify_git_tokens(safe_tokens)
        self.assertEqual(verdict.decision, "allow")
        self.assertEqual(verdict.rule, "ok")

  def test_git_checkout_destructive_and_safe_variants(self):
    """Verifies all four destructive git checkout rules and safe branch switching."""
    cases = (
        (["git", "checkout", "-f", "main"], "git_checkout_force"),
        (["git", "checkout", "--force", "main"], "git_checkout_force"),
        (["git", "checkout", "--discard-changes", "main"], "git_checkout_force"),
        (["git", "checkout", "-p", "README.md"], "git_checkout_force"),
        (["git", "checkout", "--ours", "README.md"], "git_checkout_force"),
        (["git", "checkout", "--theirs", "README.md"], "git_checkout_force"),
        (["git", "checkout", "--pathspec-from-file=list.txt"], "git_checkout_force"),
        (["git", "checkout", "--", "README.md"], "git_checkout_double_dash"),
        (["git", "checkout", "."], "git_checkout_path"),
        (["git", "checkout", "./README.md"], "git_checkout_path"),
        (["git", "checkout", "*.py"], "git_checkout_path"),
        (["git", "checkout", "HEAD", "README.md"], "git_checkout_tree_path"),
    )
    for tokens, expected_rule in cases:
      with self.subTest(tokens=tokens):
        verdict = classify_git_tokens(tokens)
        self.assertEqual(verdict.decision, "deny")
        self.assertEqual(verdict.rule, expected_rule)

    for safe_tokens in (
        ["git", "checkout", "main"],
        ["git", "checkout", "-b", "feat/new", "main"],
        ["git", "checkout", "--orphan", "gh-pages"],
    ):
      with self.subTest(safe_tokens=safe_tokens):
        verdict = classify_git_tokens(safe_tokens)
        self.assertEqual(verdict.decision, "allow")
        self.assertEqual(verdict.rule, "ok")

  def test_git_switch_and_restore_rules(self):
    """Verifies git switch force flags and working-tree git restore denial."""
    for bad_switch in (
        ["git", "switch", "-f", "main"],
        ["git", "switch", "--force", "main"],
        ["git", "switch", "--discard-changes", "main"],
    ):
      with self.subTest(bad_switch=bad_switch):
        verdict = classify_git_tokens(bad_switch)
        self.assertEqual(verdict.decision, "deny")
        self.assertEqual(verdict.rule, "git_switch_force")

    self.assertEqual(
        classify_git_tokens(["git", "switch", "-c", "feat/x"]).decision, "allow"
    )

    for bad_restore in (
        ["git", "restore", "README.md"],
        ["git", "restore", "--staged", "--worktree", "README.md"],
        ["git", "restore", "--staged", "--no-staged", "README.md"],
    ):
      with self.subTest(bad_restore=bad_restore):
        verdict = classify_git_tokens(bad_restore)
        self.assertEqual(verdict.decision, "deny")
        self.assertEqual(verdict.rule, "git_restore_worktree")

    for safe_restore in (
        ["git", "restore", "--staged", "README.md"],
        ["git", "restore", "-S", "--worktree", "--no-worktree", "README.md"],
    ):
      with self.subTest(safe_restore=safe_restore):
        verdict = classify_git_tokens(safe_restore)
        self.assertEqual(verdict.decision, "allow")

  def test_git_stash_filter_reflog_and_update_ref_rules(self):
    """Verifies destructive stash, filter-branch/repo, reflog, and update-ref rules."""
    for sub in ("drop", "clear"):
      verdict = classify_git_tokens(["git", "stash", sub])
      self.assertEqual(verdict.decision, "deny")
      self.assertEqual(verdict.rule, "git_stash")
      self.assertEqual(verdict.detail, sub)
    self.assertEqual(
        classify_git_tokens(["git", "stash", "push", "-m", "wip"]).decision,
        "allow",
    )

    for sub in ("filter-branch", "filter-repo"):
      verdict = classify_git_tokens(["git", sub, "--all"])
      self.assertEqual(verdict.decision, "deny")
      self.assertEqual(verdict.rule, "git_filter")
      self.assertEqual(verdict.detail, sub)

    for sub in ("delete", "expire"):
      verdict = classify_git_tokens(["git", "reflog", sub, "--all"])
      self.assertEqual(verdict.decision, "deny")
      self.assertEqual(verdict.rule, "git_reflog")
      self.assertEqual(verdict.detail, sub)
    self.assertEqual(classify_git_tokens(["git", "reflog", "show"]).decision, "allow")

    for bad_ref in (
        ["git", "update-ref", "-d", "refs/heads/old"],
        ["git", "update-ref", "--stdin"],
    ):
      verdict = classify_git_tokens(bad_ref)
      self.assertEqual(verdict.decision, "deny")
      self.assertEqual(verdict.rule, "git_update_ref")
    self.assertEqual(
        classify_git_tokens(["git", "update-ref", "refs/heads/a", "HEAD"]).decision,
        "allow",
    )

  def test_git_push_rebase_branch_and_commit_rules(self):
    """Verifies force-push denial, regular push positionals, rebase, branch force, and commit gates."""
    for bad_push in (
        ["git", "push", "-f", "origin", "feat/x"],
        ["git", "push", "--force", "origin", "feat/x"],
        ["git", "push", "--force-with-lease", "origin", "feat/x"],
        ["git", "push", "--force-with-lease=feat/x", "origin", "feat/x"],
        ["git", "push", "--force-if-includes", "origin", "feat/x"],
        ["git", "push", "--mirror", "origin"],
        ["git", "push", "origin", "+HEAD:main"],
        ["git", "-c", "remote.origin.push=+refs/heads/main:refs/heads/main", "push"],
    ):
      with self.subTest(bad_push=bad_push):
        verdict = classify_git_tokens(bad_push)
        self.assertEqual(verdict.decision, "deny")
        self.assertEqual(verdict.rule, "git_push_force")

    push_verdict = classify_git_tokens(["git", "push", "-u", "origin", "main"])
    self.assertEqual(push_verdict.decision, "force_ask")
    self.assertEqual(push_verdict.rule, "git_push")
    self.assertEqual(push_verdict.positionals, ("origin", "main"))

    rebase_verdict = classify_git_tokens(["git", "rebase", "main"])
    self.assertEqual(rebase_verdict.decision, "force_ask")
    self.assertEqual(rebase_verdict.rule, "git_rebase")

    for branch_flag in ("-D", "-M", "-C", "-f", "--force"):
      verdict = classify_git_tokens(["git", "branch", branch_flag, "feat/old"])
      self.assertEqual(verdict.decision, "force_ask")
      self.assertEqual(verdict.rule, "git_branch_force")
    self.assertEqual(
        classify_git_tokens(["git", "branch", "-d", "feat/done"]).decision,
        "allow",
    )

    commit_verdict = classify_git_tokens(["git", "commit", "-m", "feat: test"])
    self.assertEqual(commit_verdict.decision, "force_ask")
    self.assertEqual(commit_verdict.rule, "git_commit")


class TestGitSafetyEvaluationAndReExports(unittest.TestCase):
  """Tests MCP safety evaluators, Target 90 routing, and mcp.server re-export identity (GH-213)."""

  def test_evaluate_git_tokens_safety_preserves_mcp_reasons(self):
    """Verifies _evaluate_git_tokens_safety emits exact MCP tuples for deny, gate, and allow."""
    self.assertEqual(
        _evaluate_git_tokens_safety(["git", "reset", "--hard"]),
        (False, "Destructive Git command blocked: git reset --hard"),
    )
    self.assertEqual(
        _evaluate_git_tokens_safety(["git", "push", "--force", "origin", "main"]),
        (False, "Destructive Git command blocked: force-push is forbidden"),
    )
    self.assertEqual(
        _evaluate_git_tokens_safety(["gh", "pr", "merge", "10"]),
        (True, "Requires human operator approval at Target 75 Landing Gate"),
    )
    self.assertEqual(
        _evaluate_git_tokens_safety(["git", "push", "origin", "feat/x"]),
        (True, "Requires human operator approval at Target 60 Push Gate"),
    )
    self.assertEqual(
        _evaluate_git_tokens_safety(["git", "commit", "-m", "feat: x"], is_t90_commit=False),
        (True, "Requires human operator approval at Target 50 Commit Gate"),
    )
    self.assertEqual(
        _evaluate_git_tokens_safety(["git", "commit", "-m", "docs(history): x"], is_t90_commit=True),
        (True, "Requires human operator approval at Target 90 History Ledger Gate"),
    )
    self.assertEqual(
        _evaluate_git_tokens_safety(["git", "status"]),
        (True, "OK"),
    )

  def test_evaluate_git_safety_target90_and_precedence_with_temp_state_dir(self):
    """Verifies evaluate_git_safety Target 90 routing and deny-over-gate precedence."""
    with tempfile.TemporaryDirectory() as state_dir:
      next_action = os.path.join(state_dir, "99_next_action.md")
      with open(next_action, "w", encoding="utf-8") as f:
        f.write("# 99_next_action\n- Active Target: Target 90 (finish)\n")

      safe_t90, reason_t90 = evaluate_git_safety(
          "git commit -m 'chore: release notes' CHANGELOG.md",
          state_dir=state_dir,
      )
      self.assertTrue(safe_t90)
      self.assertIn("Target 90 History Ledger Gate", reason_t90)

      safe_mixed, reason_mixed = evaluate_git_safety(
          "git commit -m 'docs(changelog): a' && git commit -m 'feat: b'",
          state_dir=state_dir,
      )
      self.assertTrue(safe_mixed)
      self.assertIn("Target 50 Commit Gate", reason_mixed)

      safe_deny, reason_deny = evaluate_git_safety(
          "git commit -m 'feat: a' && git reset --hard",
          state_dir=state_dir,
      )
      self.assertFalse(safe_deny)
      self.assertIn("git reset --hard", reason_deny)

  def test_mcp_server_reexports_git_safety_by_identity_and_has_no_inline_defs(self):
    """Verifies SCAR-GENUI-08 re-export identity and zero inline helper defs in mcp/server.py."""
    self.assertIs(
        mcp_server._evaluate_git_tokens_safety,
        git_safety._evaluate_git_tokens_safety,
    )
    self.assertIs(
        mcp_server.evaluate_git_safety,
        git_safety.evaluate_git_safety,
    )

    server_path = os.path.join(REPO_ROOT, "mcp", "server.py")
    with open(server_path, "r", encoding="utf-8") as f:
      tree = ast.parse(f.read(), filename=server_path)
    top_level_defs = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for extracted in (
        "_evaluate_git_tokens_safety",
        "evaluate_git_safety",
        "detect_hosting_mode",
        "poll_ci_status",
    ):
      self.assertNotIn(
          extracted,
          top_level_defs,
          f"mcp/server.py must not define {extracted} inline after GH-213 extraction",
      )

  def test_lib_git_safety_has_no_shebang_header(self):
    """Verifies lib/git_safety.py complies with SCAR-ARCH-05 zero-shebang library rule."""
    mod_path = os.path.join(REPO_ROOT, "lib", "git_safety.py")
    with open(mod_path, "r", encoding="utf-8") as f:
      first_line = f.readline()
    self.assertFalse(first_line.startswith("#!"))


if __name__ == "__main__":
  unittest.main()
