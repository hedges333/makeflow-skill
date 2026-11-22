#!/usr/bin/env python3
"""Unit tests for POSIX line continuation handling in lib/shell_safety.py (GH-279)."""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
  sys.path.insert(0, REPO_ROOT)

import lib.shell_safety as shell_safety  # noqa: E402


class TestShellSafetyLineContinuations(unittest.TestCase):
  """Tests POSIX backslash-newline stripping across quoting and comment contexts (GH-279)."""

  def test_strip_line_continuations_unquoted_lf_and_crlf(self):
    """Strips unquoted LF and CRLF line continuations including mid-word joins (GH-279)."""
    strip = shell_safety._strip_line_continuations
    self.assertEqual(
        strip("echo ok && \\\nmake -f .workflow/state/Makefile commit APPROVED=1"),
        "echo ok && make -f .workflow/state/Makefile commit APPROVED=1",
    )
    self.assertEqual(
        strip(
            "echo ok && \\\r\nmake -f .workflow/state/Makefile commit APPROVED=1"
        ),
        "echo ok && make -f .workflow/state/Makefile commit APPROVED=1",
    )
    self.assertEqual(strip("gi\\\nt reset --hard"), "git reset --hard")
    self.assertEqual(
        strip("ps\\\r\nql -h prod.db.internal myapp"),
        "psql -h prod.db.internal myapp",
    )
    self.assertEqual(strip("echo \\\n\\\nhello"), "echo hello")
    self.assertEqual(strip("echo trailing\\"), "echo trailing\\")

  def test_strip_line_continuations_double_quoted_stripped_and_single_quoted_preserved(
      self,
  ):
    """Strips continuations inside double quotes and preserves single and ANSI-C quotes (GH-279)."""
    strip = shell_safety._strip_line_continuations
    self.assertEqual(strip('echo "foo\\\nbar"'), 'echo "foobar"')
    self.assertEqual(strip('echo "foo\\\r\nbar"'), 'echo "foobar"')
    self.assertEqual(strip("echo 'a\\\nb'"), "echo 'a\\\nb'")
    self.assertEqual(strip("echo 'a\\\r\nb'"), "echo 'a\\\r\nb'")
    self.assertEqual(strip("echo $'a\\\nb'"), "echo $'a\\\nb'")
    self.assertEqual(strip("echo $'a\\\r\nb'"), "echo $'a\\\r\nb'")
    self.assertEqual(
        strip("echo $'a\\'b\\\nc' && \\\ngit status"),
        "echo $'a\\'b\\\nc' && git status",
    )

    # End-to-end preprocessing and tokenization preserve literal single/ANSI-C text.
    self.assertEqual(
        shell_safety._tokenize_subcommand(
            shell_safety._preprocess_shell("echo 'a\\\nb'")
        ),
        [["echo", "a\\\nb"]],
    )
    self.assertEqual(
        shell_safety._tokenize_subcommand(
            shell_safety._preprocess_shell("echo $'a\\\nb'")
        ),
        [["echo", "a\\\nb"]],
    )
    self.assertEqual(
        shell_safety._tokenize_subcommand(
            shell_safety._preprocess_shell('echo "foo\\\nbar"')
        ),
        [["echo", "foobar"]],
    )

  def test_strip_line_continuations_even_and_odd_backslash_runs(self):
    """Distinguishes even escaped-backslash newlines from odd continuation runs (GH-279)."""
    strip = shell_safety._strip_line_continuations
    # Even (2 backslashes): literal \\ followed by real newline separator.
    self.assertEqual(
        strip("echo ok\\\\\ngit reset --hard"),
        "echo ok\\\\\ngit reset --hard",
    )
    self.assertEqual(
        strip("echo ok\\\\\r\ngit reset --hard"),
        "echo ok\\\\\r\ngit reset --hard",
    )
    self.assertEqual(
        strip('echo "foo\\\\\nbar"'),
        'echo "foo\\\\\nbar"',
    )
    # Odd (3 backslashes): literal \\ followed by stripped line continuation.
    self.assertEqual(
        strip("echo ok\\\\\\\ngit reset --hard"),
        "echo ok\\\\git reset --hard",
    )
    self.assertEqual(
        strip("echo ok\\\\\\\r\ngit reset --hard"),
        "echo ok\\\\git reset --hard",
    )
    self.assertEqual(
        strip('echo "foo\\\\\\\nbar"'),
        'echo "foo\\\\bar"',
    )

    # Subcommand expansion splits on real newline after even backslashes,
    # and joins onto one command after odd backslashes.
    self.assertEqual(
        shell_safety._expand_shell_subcommands("echo ok\\\\\ngit reset --hard"),
        ["echo ok\\\\", "git reset --hard"],
    )
    self.assertEqual(
        shell_safety._expand_shell_subcommands(
            "echo ok\\\\\\\ngit reset --hard"
        ),
        ["echo ok\\\\git reset --hard"],
    )

  def test_preprocess_and_expand_comment_anti_bypass_and_midword_hash(self):
    """Terminates # comments at newline even after backslash and keeps mid-word # (GH-279, SCAR-SH-03)."""
    # Trailing backslash inside a # comment must NEVER swallow the next line.
    self.assertEqual(
        shell_safety._expand_shell_subcommands(
            "# comment \\\ngit reset --hard"
        ),
        ["git reset --hard"],
    )
    self.assertEqual(
        shell_safety._expand_shell_subcommands(
            "# comment \\\r\ngit reset --hard"
        ),
        ["git reset --hard"],
    )

    # Mid-word # directly after a stripped continuation is literal, not a comment.
    self.assertEqual(
        shell_safety._preprocess_shell("echo ok\\\n#notcomment"),
        "echo ok#notcomment",
    )
    self.assertEqual(
        shell_safety._tokenize_subcommand(
            shell_safety._preprocess_shell("echo ok\\\n#notcomment")
        ),
        [["echo", "ok#notcomment"]],
    )

    # Word-start # after whitespace and stripped continuation starts a real comment.
    self.assertEqual(
        shell_safety._expand_shell_subcommands(
            "echo ok \\\n#comment\ngit reset --hard"
        ),
        ["echo ok", "git reset --hard"],
    )

  def test_tokenize_subcommand_direct_calls_and_unclosed_quote_fallback(self):
    """Strips continuations on direct _tokenize_subcommand calls and malformed-quote fallback (GH-279)."""
    self.assertEqual(
        shell_safety._tokenize_subcommand(
            "\\\nmake -f .workflow/state/Makefile commit APPROVED=1"
        ),
        [["make", "-f", ".workflow/state/Makefile", "commit", "APPROVED=1"]],
    )
    self.assertEqual(
        shell_safety._tokenize_subcommand("\\\r\ngit reset --hard"),
        [["git", "reset", "--hard"]],
    )
    self.assertEqual(
        shell_safety._tokenize_subcommand("echo 'a\\\nb'"),
        [["echo", "a\\\nb"]],
    )
    self.assertEqual(
        shell_safety._tokenize_subcommand('echo "foo\\\nbar"'),
        [["echo", "foobar"]],
    )
    unclosed_groups = shell_safety._tokenize_subcommand(
        'echo "unterminated && \\\ngit reset --hard'
    )
    self.assertIn(["git", "reset", "--hard"], unclosed_groups)

  def test_database_safety_across_line_continuations(self):
    """Blocks production DB commands split across LF and CRLF continuations (GH-279)."""
    for bad_cmd in (
        "ps\\\nql -h prod.db.internal myapp",
        "ps\\\r\nql -h prod.db.internal myapp",
        "echo ok && \\\npsql -h prod.db.internal myapp",
        "echo ok && \\\r\npsql -h prod.db.internal myapp",
    ):
      hook_ok, _ = shell_safety.evaluate_database_safety(bad_cmd, {})
      mcp_ok, _ = shell_safety.evaluate_mcp_database_safety(bad_cmd, {})
      self.assertFalse(hook_ok, f"Expected Hook DB deny for {bad_cmd!r}")
      self.assertFalse(mcp_ok, f"Expected MCP DB deny for {bad_cmd!r}")

    safe_cmd = "psql -h localhost \\\nmyapp_test"
    cfg = {"testDatabases": ["myapp_test"]}
    self.assertTrue(shell_safety.evaluate_database_safety(safe_cmd, cfg)[0])
    self.assertTrue(
        shell_safety.evaluate_mcp_database_safety(safe_cmd, cfg)[0]
    )


if __name__ == "__main__":
  unittest.main()
