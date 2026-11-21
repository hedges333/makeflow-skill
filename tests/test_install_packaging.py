#!/usr/bin/env python3
"""Hermetic packaging tests for runtime allowlist staging and installation (GH-244)."""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from typing import Dict, List, Set, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_MAKEFILE = os.path.join(_REPO_ROOT, "Makefile")
_REPO_INSTALL_SH = os.path.join(_REPO_ROOT, "install.sh")

RUNTIME_ALLOWLIST: Tuple[str, ...] = (
    "plugin.json",
    "hooks.json",
    "mcp_config.json",
    "default_config.json",
    "LICENSE",
    "README.md",
    "assets",
    "agents",
    "lib",
    "mcp",
    "rules",
    "skills",
    "scripts",
)

RUNTIME_ALLOWLIST_FILES: Tuple[str, ...] = (
    "plugin.json",
    "hooks.json",
    "mcp_config.json",
    "default_config.json",
    "LICENSE",
    "README.md",
)

RUNTIME_ALLOWLIST_DIRS: Tuple[str, ...] = (
    "assets",
    "agents",
    "lib",
    "mcp",
    "rules",
    "skills",
    "scripts",
)

STAGE_EXCLUDES: Tuple[str, ...] = (
    "--exclude=__pycache__",
    "--exclude=*.py[cod]",
    "--exclude=*$py.class",
    "--exclude=.DS_Store",
)

SEEDED_RUNTIME_FILES: Tuple[str, ...] = (
    "plugin.json",
    "hooks.json",
    "mcp_config.json",
    "default_config.json",
    "LICENSE",
    "README.md",
    "assets/logo.svg",
    "agents/tdd-worker.md",
    "lib/__init__.py",
    "mcp/server.py",
    "rules/AGENTS.md",
    "skills/makeflow/SKILL.md",
    "scripts/session_start.py",
)

EXCLUDED_REPO_PATHS: Tuple[str, ...] = (
    ".workflow",
    ".workflow/config.json.example",
    ".workflow/config.json",
    ".workflow/history.json",
    ".workflow/history.md",
    ".workflow/tickets/244.json",
    ".workflow/scars/local.md",
    ".workflow/state/10_ticket.md",
    ".workflow/archive/GH-1/10_ticket.md",
    ".workflow/plan.md",
    ".workflow/commit_msg.txt",
    "tests",
    "tests/test_dummy.py",
    ".github",
    ".github/workflows/ci.yml",
    "notes",
    "notes/design.md",
    "Makefile",
    "install.sh",
    ".gitignore",
    "CHANGELOG.md",
    "CLAUDE.md",
    ".claude-plugin",
    ".claude-plugin/plugin.json",
    ".codex-plugin",
    ".codex-plugin/plugin.json",
    ".cursor",
    ".cursor/rules/makeflow.mdc",
    ".cursorrules",
    "adapters",
    "adapters/claude.md",
    ".git",
    ".git/HEAD",
    ".tmp",
    ".tmp/scratch.txt",
    ".venv",
    ".venv/bin/python",
)

EXCLUDED_BYTECODE_PATHS: Tuple[str, ...] = (
    "lib/__pycache__",
    "lib/__pycache__/mod.cpython-313.pyc",
    "mcp/__pycache__",
    "mcp/__pycache__/server.cpython-313.pyc",
    "scripts/__pycache__",
    "scripts/__pycache__/session_start.cpython-313.pyc",
    "skills/makeflow/__pycache__",
    "skills/makeflow/__pycache__/helper.cpython-313.pyc",
    "lib/stale.pyc",
    "mcp/stale.pyo",
    "scripts/stale.pyd",
    "lib/Worker$py.class",
    "assets/.DS_Store",
    "rules/.DS_Store",
)


def _write_text(path: str, content: str) -> None:
  """Writes UTF-8 text to a file path, creating parent directories.

  Args:
    path: Absolute destination file path.
    content: UTF-8 string content to write.
  """
  os.makedirs(os.path.dirname(path), exist_ok=True)
  with open(path, "w", encoding="utf-8") as handle:
    handle.write(content)


def _walk_rel_paths(root_dir: str) -> List[str]:
  """Collects sorted relative file, symlink, and directory paths under root_dir.

  Args:
    root_dir: Directory root to walk without following symlinks.

  Returns:
    Sorted list of POSIX-style relative paths inside root_dir.
  """
  rel_paths: List[str] = []
  for current_root, dirs, files in os.walk(root_dir, followlinks=False):
    dirs.sort()
    files.sort()
    for dname in dirs:
      full_dir = os.path.join(current_root, dname)
      rel_paths.append(os.path.relpath(full_dir, root_dir).replace(os.sep, "/"))
    for fname in files:
      full_file = os.path.join(current_root, fname)
      rel_paths.append(os.path.relpath(full_file, root_dir).replace(os.sep, "/"))
  return sorted(rel_paths)


def _build_stub_agy_script() -> str:
  """Builds a hermetic Python stub script simulating the agy CLI.

  Returns:
    Source code for an executable stub agy script that logs invocations and
    installs staged plugin trees into isolated HOME.
  """
  return f"""#!{sys.executable}
import json
import os
import shutil
import sys


def _snapshot(target_dir):
  top_entries = []
  all_paths = []
  if os.path.isdir(target_dir):
    top_entries = sorted(os.listdir(target_dir))
    for root, dirs, files in os.walk(target_dir, followlinks=False):
      dirs.sort()
      files.sort()
      for dname in dirs:
        all_paths.append(
            os.path.relpath(os.path.join(root, dname), target_dir).replace(os.sep, "/")
        )
      for fname in files:
        all_paths.append(
            os.path.relpath(os.path.join(root, fname), target_dir).replace(os.sep, "/")
        )
  return top_entries, sorted(all_paths)


def _append_log(entry):
  log_path = os.environ.get("AGY_CALL_LOG", "")
  if log_path:
    with open(log_path, "a", encoding="utf-8") as handle:
      handle.write(json.dumps(entry) + "\\n")


def main():
  args = sys.argv[1:]
  if args[:2] == ["plugin", "validate"] and len(args) >= 3:
    raw_target = args[2]
    abs_target = os.path.abspath(raw_target)
    real_target = os.path.realpath(abs_target)
    top_entries, all_paths = _snapshot(abs_target)
    _append_log({{
        "action": "validate",
        "args": args,
        "raw_target": raw_target,
        "abs_target": abs_target,
        "real_target": real_target,
        "top_entries": top_entries,
        "all_rel_paths": all_paths,
    }})
    exit_code = int(os.environ.get("AGY_VALIDATE_EXIT", "0"))
    if exit_code != 0:
      sys.stderr.write("Error: simulated strict validation failure\\n")
    sys.exit(exit_code)

  if args[:2] == ["plugin", "install"] and len(args) >= 3:
    raw_target = args[2]
    abs_target = os.path.abspath(raw_target)
    real_target = os.path.realpath(abs_target)
    top_entries, all_paths = _snapshot(abs_target)
    _append_log({{
        "action": "install",
        "args": args,
        "raw_target": raw_target,
        "abs_target": abs_target,
        "real_target": real_target,
        "top_entries": top_entries,
        "all_rel_paths": all_paths,
    }})
    for root, dirs, files in os.walk(abs_target, followlinks=False):
      for entry in list(dirs) + list(files):
        full = os.path.join(root, entry)
        if os.path.islink(full):
          resolved = os.path.realpath(full)
          if os.path.commonpath([real_target, resolved]) != real_target:
            sys.stderr.write(
                f"Error: symlink {{full}} points outside {{abs_target}}\\n"
            )
            sys.exit(1)
    dest = os.path.join(
        os.environ["HOME"], ".gemini", "config", "plugins", "makeflow"
    )
    if os.path.lexists(dest):
      shutil.rmtree(dest)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copytree(abs_target, dest, symlinks=True)
    sys.exit(0)

  sys.stderr.write(f"Error: unexpected agy invocation: {{args}}\\n")
  sys.exit(1)


if __name__ == "__main__":
  main()
"""


def _build_stub_git_script() -> str:
  """Builds a hermetic Python stub script simulating the git CLI.

  Returns:
    Source code for an executable stub git script that logs clone invocations
    and copies a seeded repository tree into the target clone directory.
  """
  return f"""#!{sys.executable}
import json
import os
import shutil
import sys


def _append_log(entry):
  log_path = os.environ.get("GIT_CALL_LOG", "")
  if log_path:
    with open(log_path, "a", encoding="utf-8") as handle:
      handle.write(json.dumps(entry) + "\\n")


def main():
  args = sys.argv[1:]
  if len(args) == 5 and args[:3] == ["clone", "--depth", "1"]:
    repo_url = args[3]
    target_dir = args[4]
    abs_target = os.path.abspath(target_dir)
    real_target = os.path.realpath(abs_target)
    _append_log({{
        "action": "clone",
        "args": args,
        "repo_url": repo_url,
        "raw_target": target_dir,
        "abs_target": abs_target,
        "real_target": real_target,
    }})
    seed_src = os.environ.get("GIT_SEED_SRC", "")
    if not seed_src or not os.path.isdir(seed_src):
      sys.stderr.write("Error: missing GIT_SEED_SRC for stub git\\n")
      sys.exit(1)
    shutil.copytree(seed_src, abs_target, symlinks=True, dirs_exist_ok=True)
    sys.exit(0)

  sys.stderr.write(f"Error: unexpected git invocation: {{args}}\\n")
  sys.exit(1)


if __name__ == "__main__":
  main()
"""


def _create_packaging_fixture(
    base_dir: str,
    with_agy: bool = True,
    validate_exit: int = 0,
    seed_bytecode: bool = True,
    with_git: bool = False,
) -> Dict[str, object]:
  """Creates an isolated source repository, HOME, TMPDIR, and PATH fixture.

  Args:
    base_dir: Temporary parent directory owned by the calling test case.
    with_agy: Whether to install the stub agy executable on isolated PATH.
    validate_exit: Exit code returned by stub agy on plugin validate.
    seed_bytecode: Whether to seed __pycache__ and bytecode artifacts in runtime dirs.
    with_git: Whether to install the stub git executable on isolated PATH.

  Returns:
    Dictionary containing fixture paths and hermetic environment mapping.
  """
  home_dir = os.path.join(base_dir, "home")
  tmp_dir = os.path.join(base_dir, "tmp")
  bin_dir = os.path.join(base_dir, "bin")
  ext_dir = os.path.join(base_dir, "external_runtime")
  src_dir = os.path.join(base_dir, "src_repo")
  install_dir = os.path.join(
      home_dir, ".gemini", "config", "plugins", "makeflow"
  )
  for directory in (home_dir, tmp_dir, bin_dir, ext_dir, src_dir):
    os.makedirs(directory, exist_ok=True)

  ext_python = os.path.join(ext_dir, "python3.13")
  _write_text(ext_python, "#!/bin/sh\nexit 0\n")
  os.chmod(ext_python, 0o755)

  runtime_contents = {
      "plugin.json": '{"name": "makeflow", "version": "0.0.1"}\n',
      "hooks.json": '{"makeflow-guard": {}}\n',
      "mcp_config.json": '{"mcpServers": {}}\n',
      "default_config.json": '{"models": {}}\n',
      "LICENSE": "MIT\n",
      "README.md": "# Makeflow\n",
      "assets/logo.svg": "<svg></svg>\n",
      "agents/tdd-worker.md": "---\nname: tdd-worker\n---\n",
      "lib/__init__.py": '"""Lib package."""\n',
      "mcp/server.py": '"""MCP server."""\n',
      "rules/AGENTS.md": "# Rules\n",
      "skills/makeflow/SKILL.md": "# Makeflow Skill\n",
      "scripts/session_start.py": "#!/usr/bin/env python3\n",
  }
  for rel_path, content in runtime_contents.items():
    _write_text(os.path.join(src_dir, rel_path), content)

  repo_only_contents = {
      ".workflow/config.json.example": "{}\n",
      ".workflow/config.json": '{"local": true}\n',
      ".workflow/history.json": "[]\n",
      ".workflow/history.md": "# History\n",
      ".workflow/tickets/244.json": "{}\n",
      ".workflow/scars/local.md": "# Local scars\n",
      ".workflow/state/10_ticket.md": "# Active ticket\n",
      ".workflow/archive/GH-1/10_ticket.md": "# Archived ticket\n",
      ".workflow/plan.md": "# Local plan\n",
      ".workflow/commit_msg.txt": "fix: local\n",
      "tests/test_dummy.py": "# Dummy test\n",
      "tests/__pycache__/test_dummy.cpython-313.pyc": "pyc",
      ".github/workflows/ci.yml": "name: CI\n",
      "notes/design.md": "# Design notes\n",
      "Makefile": "all:\n\t@true\n",
      "install.sh": "#!/usr/bin/env bash\n",
      ".gitignore": "__pycache__/\n",
      "CHANGELOG.md": "# Changelog\n",
      "CLAUDE.md": "# Claude\n",
      ".claude-plugin/plugin.json": '{"name": "makeflow"}\n',
      ".codex-plugin/plugin.json": '{"name": "makeflow"}\n',
      ".cursor/rules/makeflow.mdc": "# Cursor\n",
      ".cursorrules": "# Cursor rules\n",
      "adapters/claude.md": "# Adapter\n",
      ".git/HEAD": "ref: refs/heads/main\n",
      ".tmp/scratch.txt": "scratch\n",
  }
  for rel_path, content in repo_only_contents.items():
    _write_text(os.path.join(src_dir, rel_path), content)

  if seed_bytecode:
    for rel_path in EXCLUDED_BYTECODE_PATHS:
      if rel_path.endswith("__pycache__"):
        continue
      _write_text(os.path.join(src_dir, rel_path), "artifact\n")

  venv_bin = os.path.join(src_dir, ".venv", "bin")
  os.makedirs(venv_bin, exist_ok=True)
  os.symlink(ext_python, os.path.join(venv_bin, "python"))

  for tool in (
      "sh",
      "bash",
      "tar",
      "mkdir",
      "rm",
      "mktemp",
      "true",
      "make",
      "cat",
      "cp",
      "dirname",
      "basename",
      "env",
  ):
    resolved = shutil.which(tool)
    if resolved:
      os.symlink(resolved, os.path.join(bin_dir, tool))

  agy_log = os.path.join(base_dir, "agy_calls.jsonl")
  if with_agy:
    agy_path = os.path.join(bin_dir, "agy")
    _write_text(agy_path, _build_stub_agy_script())
    os.chmod(agy_path, 0o755)

  empty_dir = os.path.join(base_dir, "empty_cwd")
  os.makedirs(empty_dir, exist_ok=True)
  git_log = os.path.join(base_dir, "git_calls.jsonl")
  if with_git:
    git_path = os.path.join(bin_dir, "git")
    _write_text(git_path, _build_stub_git_script())
    os.chmod(git_path, 0o755)

  env = {
      "HOME": home_dir,
      "PATH": bin_dir,
      "TMPDIR": tmp_dir,
      "AGY_CALL_LOG": agy_log,
      "AGY_VALIDATE_EXIT": str(validate_exit),
  }
  if with_git:
    env["GIT_CALL_LOG"] = git_log
    env["GIT_SEED_SRC"] = src_dir

  return {
      "home_dir": home_dir,
      "tmp_dir": tmp_dir,
      "bin_dir": bin_dir,
      "src_dir": src_dir,
      "empty_dir": empty_dir,
      "install_dir": install_dir,
      "agy_log": agy_log,
      "git_log": git_log,
      "env": env,
  }


def _read_jsonl_calls(log_path: str) -> List[Dict[str, object]]:
  """Reads logged stub CLI invocations from a JSON lines file.

  Args:
    log_path: Path to the JSONL log written by a stub CLI executable.

  Returns:
    Ordered list of invocation dictionaries, or empty list if no log exists.
  """
  if not os.path.isfile(log_path):
    return []
  calls: List[Dict[str, object]] = []
  with open(log_path, "r", encoding="utf-8") as handle:
    for line in handle:
      stripped = line.strip()
      if stripped:
        calls.append(json.loads(stripped))
  return calls


def _read_agy_calls(log_path: str) -> List[Dict[str, object]]:
  """Reads logged stub agy invocations from a JSON lines file.

  Args:
    log_path: Path to the JSONL log written by stub agy.

  Returns:
    Ordered list of invocation dictionaries, or empty list if no log exists.
  """
  return _read_jsonl_calls(log_path)


def _extract_makefile_packaging_config(
    makefile_text: str,
) -> Tuple[List[str], List[str]]:
  """Extracts PLUGIN_RUNTIME_PATHS and PLUGIN_STAGE_EXCLUDES from Makefile.

  Args:
    makefile_text: Full UTF-8 contents of the repository Makefile.

  Returns:
    Tuple of (runtime_paths_list, excludes_list).
  """
  paths: List[str] = []
  excludes: List[str] = []
  for raw_line in makefile_text.splitlines():
    stripped = raw_line.strip()
    if stripped.startswith("PLUGIN_RUNTIME_PATHS"):
      _, _, rhs = stripped.partition(":=")
      if not rhs:
        _, _, rhs = stripped.partition("=")
      paths = rhs.strip().split()
    elif stripped.startswith("PLUGIN_STAGE_EXCLUDES"):
      _, _, rhs = stripped.partition(":=")
      if not rhs:
        _, _, rhs = stripped.partition("=")
      excludes = shlex.split(rhs.strip().replace("$$", "$"))
  return paths, excludes


def _extract_install_sh_packaging_config(
    install_sh_text: str,
) -> Tuple[List[str], List[str]]:
  """Extracts RUNTIME_ALLOWLIST and copy_plugin_tree excludes from install.sh.

  Args:
    install_sh_text: Full UTF-8 contents of the repository install.sh.

  Returns:
    Tuple of (runtime_allowlist_list, excludes_list).
  """
  allowlist: List[str] = []
  allowlist_match = re.search(
      r"RUNTIME_ALLOWLIST=\((.*?)\)", install_sh_text, re.DOTALL
  )
  if allowlist_match:
    allowlist = allowlist_match.group(1).split()

  excludes: List[str] = []
  fn_match = re.search(
      r"copy_plugin_tree\(\)\s*\{(.*?)^\}",
      install_sh_text,
      re.DOTALL | re.MULTILINE,
  )
  if fn_match:
    fn_body = fn_match.group(1)
    for token in re.findall(r"--exclude=[^\s\\]+", fn_body):
      excludes.extend(shlex.split(token))
  return allowlist, excludes


class TestInstallPackagingAllowlist(unittest.TestCase):
  """Verifies runtime allowlist staging, validation, and installation (GH-244)."""

  def _assert_clean_runtime_tree(
      self,
      top_entries: List[str],
      all_rel_paths: List[str],
  ) -> None:
    """Asserts a staged or installed tree contains only allowlisted runtime files.

    Args:
      top_entries: Top-level entry names observed in the target directory.
      all_rel_paths: All relative file/dir/symlink paths inside the target directory.
    """
    self.assertEqual(set(top_entries), set(RUNTIME_ALLOWLIST))
    rel_set: Set[str] = set(all_rel_paths)
    for expected in SEEDED_RUNTIME_FILES:
      self.assertIn(expected, rel_set, f"Missing runtime file: {expected}")
    for forbidden in EXCLUDED_REPO_PATHS:
      self.assertNotIn(
          forbidden, rel_set, f"Repo-only path leaked into package: {forbidden}"
      )
    for bytecode in EXCLUDED_BYTECODE_PATHS:
      self.assertNotIn(
          bytecode, rel_set, f"Bytecode/OS artifact leaked into package: {bytecode}"
      )
    for rel in all_rel_paths:
      parts = rel.split("/")
      self.assertNotIn("__pycache__", parts, f"__pycache__ leaked: {rel}")
      self.assertFalse(
          rel.endswith((".pyc", ".pyo", ".pyd", "$py.class", ".DS_Store")),
          f"Excluded artifact leaked: {rel}",
      )

  def _assert_staged_validate_entry(
      self,
      call: Dict[str, object],
      src_dir: str,
      tmp_dir: str,
  ) -> None:
    """Asserts an agy plugin validate call targeted a clean staged directory.

    Args:
      call: Logged stub agy invocation dictionary.
      src_dir: Source repository directory that must not be validated directly.
      tmp_dir: Isolated TMPDIR expected to host the temporary staging tree.
    """
    self.assertEqual(call["action"], "validate")
    args = call["args"]
    self.assertEqual(len(args), 4, f"Expected --strict validate args: {args}")
    self.assertEqual(args[0], "plugin")
    self.assertEqual(args[1], "validate")
    self.assertEqual(args[3], "--strict")
    self.assertNotEqual(call["raw_target"], ".")
    self.assertNotEqual(call["real_target"], os.path.realpath(src_dir))
    self.assertEqual(os.path.basename(str(call["abs_target"])), "makeflow")
    self.assertEqual(
        os.path.commonpath(
            [os.path.realpath(tmp_dir), str(call["real_target"])]
        ),
        os.path.realpath(tmp_dir),
    )
    self._assert_clean_runtime_tree(
        list(call["top_entries"]), list(call["all_rel_paths"])
    )

  def test_install_sh_with_agy_stages_allowlist_and_validates_staged_dir(self):
    """install.sh with agy stages only allowlist, validates staged tree, and installs."""
    with tempfile.TemporaryDirectory() as base_dir:
      fx = _create_packaging_fixture(base_dir, with_agy=True)
      proc = subprocess.run(
          ["bash", _REPO_INSTALL_SH],
          cwd=fx["src_dir"],
          env=fx["env"],
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(proc.returncode, 0, proc.stderr)
      calls = _read_agy_calls(str(fx["agy_log"]))
      self.assertEqual(len(calls), 2, f"Unexpected agy calls: {calls}")
      self._assert_staged_validate_entry(
          calls[0], str(fx["src_dir"]), str(fx["tmp_dir"])
      )
      self.assertEqual(calls[1]["action"], "install")
      self.assertEqual(calls[1]["abs_target"], calls[0]["abs_target"])
      self._assert_clean_runtime_tree(
          list(calls[1]["top_entries"]), list(calls[1]["all_rel_paths"])
      )
      install_dir = str(fx["install_dir"])
      self._assert_clean_runtime_tree(
          sorted(os.listdir(install_dir)), _walk_rel_paths(install_dir)
      )
      self.assertEqual(os.listdir(str(fx["tmp_dir"])), [])

  def test_install_sh_dry_run_validates_staged_dir_without_installing(self):
    """install.sh --dry-run validates staged allowlist with --strict without installing."""
    with tempfile.TemporaryDirectory() as base_dir:
      fx = _create_packaging_fixture(base_dir, with_agy=True)
      proc = subprocess.run(
          ["bash", _REPO_INSTALL_SH, "--dry-run"],
          cwd=fx["src_dir"],
          env=fx["env"],
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(proc.returncode, 0, proc.stderr)
      calls = _read_agy_calls(str(fx["agy_log"]))
      self.assertEqual(len(calls), 1, f"Expected validate only: {calls}")
      self._assert_staged_validate_entry(
          calls[0], str(fx["src_dir"]), str(fx["tmp_dir"])
      )
      self.assertFalse(os.path.lexists(str(fx["install_dir"])))
      self.assertEqual(os.listdir(str(fx["tmp_dir"])), [])

  def test_install_sh_validate_failure_aborts_before_install_and_cleans_temp_dir(
      self,
  ):
    """install.sh aborts before agy plugin install and cleans TMPDIR when validate fails."""
    with tempfile.TemporaryDirectory() as base_dir:
      fx = _create_packaging_fixture(base_dir, with_agy=True, validate_exit=1)
      proc = subprocess.run(
          ["bash", _REPO_INSTALL_SH],
          cwd=fx["src_dir"],
          env=fx["env"],
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertNotEqual(proc.returncode, 0)
      calls = _read_agy_calls(str(fx["agy_log"]))
      self.assertEqual(len(calls), 1, f"Expected validate only: {calls}")
      self._assert_staged_validate_entry(
          calls[0], str(fx["src_dir"]), str(fx["tmp_dir"])
      )
      self.assertFalse(os.path.lexists(str(fx["install_dir"])))
      self.assertEqual(os.listdir(str(fx["tmp_dir"])), [])

  def test_install_sh_fallback_without_agy_copies_only_allowlist(self):
    """install.sh without agy copies only the 13 runtime paths into isolated HOME."""
    with tempfile.TemporaryDirectory() as base_dir:
      fx = _create_packaging_fixture(base_dir, with_agy=False)
      proc = subprocess.run(
          ["bash", _REPO_INSTALL_SH],
          cwd=fx["src_dir"],
          env=fx["env"],
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(proc.returncode, 0, proc.stderr)
      install_dir = str(fx["install_dir"])
      self._assert_clean_runtime_tree(
          sorted(os.listdir(install_dir)), _walk_rel_paths(install_dir)
      )
      self.assertEqual(os.listdir(str(fx["tmp_dir"])), [])

  def test_install_sh_remote_with_agy_clones_stages_validates_and_installs(
      self,
  ):
    """Non-repo install.sh with agy clones repo, validates staged allowlist, and installs (GH-244)."""
    with tempfile.TemporaryDirectory() as base_dir:
      fx = _create_packaging_fixture(base_dir, with_agy=True, with_git=True)
      proc = subprocess.run(
          ["bash", _REPO_INSTALL_SH],
          cwd=fx["empty_dir"],
          env=fx["env"],
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(proc.returncode, 0, proc.stderr)
      git_calls = _read_jsonl_calls(str(fx["git_log"]))
      self.assertEqual(len(git_calls), 1, f"Expected single git clone: {git_calls}")
      self.assertEqual(
          git_calls[0]["args"][:4],
          [
              "clone",
              "--depth",
              "1",
              "https://github.com/hedges333/makeflow-skill.git",
          ],
      )
      clone_real = str(git_calls[0]["real_target"])
      tmp_real = os.path.realpath(str(fx["tmp_dir"]))
      self.assertEqual(os.path.commonpath([tmp_real, clone_real]), tmp_real)
      self.assertNotEqual(clone_real, tmp_real)

      agy_calls = _read_agy_calls(str(fx["agy_log"]))
      self.assertEqual(len(agy_calls), 2, f"Unexpected agy calls: {agy_calls}")
      self._assert_staged_validate_entry(
          agy_calls[0], str(fx["empty_dir"]), str(fx["tmp_dir"])
      )
      self.assertNotEqual(agy_calls[0]["real_target"], clone_real)
      self.assertNotEqual(
          os.path.commonpath([clone_real, str(agy_calls[0]["real_target"])]),
          clone_real,
      )
      self.assertNotEqual(
          agy_calls[0]["real_target"], os.path.realpath(str(fx["src_dir"]))
      )
      self.assertEqual(agy_calls[1]["action"], "install")
      self.assertEqual(agy_calls[1]["abs_target"], agy_calls[0]["abs_target"])
      self._assert_clean_runtime_tree(
          list(agy_calls[1]["top_entries"]),
          list(agy_calls[1]["all_rel_paths"]),
      )
      install_dir = str(fx["install_dir"])
      self._assert_clean_runtime_tree(
          sorted(os.listdir(install_dir)), _walk_rel_paths(install_dir)
      )
      self.assertEqual(os.listdir(str(fx["tmp_dir"])), [])
      self.assertEqual(os.listdir(str(fx["empty_dir"])), [])

  def test_install_sh_remote_with_agy_dry_run_skips_git_and_install(self):
    """Non-repo install.sh --dry-run with agy prints plan without running git or agy (GH-244)."""
    with tempfile.TemporaryDirectory() as base_dir:
      fx = _create_packaging_fixture(base_dir, with_agy=True, with_git=True)
      proc = subprocess.run(
          ["bash", _REPO_INSTALL_SH, "--dry-run"],
          cwd=fx["empty_dir"],
          env=fx["env"],
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(proc.returncode, 0, proc.stderr)
      self.assertIn(
          "Dry run: will clone https://github.com/hedges333/makeflow-skill.git, "
          "stage runtime tree, validate, and install.",
          proc.stdout,
      )
      self.assertNotIn("Installation Complete", proc.stdout)
      self.assertEqual(_read_jsonl_calls(str(fx["git_log"])), [])
      self.assertEqual(_read_agy_calls(str(fx["agy_log"])), [])
      self.assertFalse(os.path.lexists(str(fx["install_dir"])))
      self.assertEqual(os.listdir(str(fx["tmp_dir"])), [])
      self.assertEqual(os.listdir(str(fx["empty_dir"])), [])

  def test_install_sh_remote_with_agy_validate_failure_cleans_temp_dirs(self):
    """Non-repo install.sh aborts before install and cleans clone and stage dirs on validate failure (GH-244)."""
    with tempfile.TemporaryDirectory() as base_dir:
      fx = _create_packaging_fixture(
          base_dir, with_agy=True, validate_exit=1, with_git=True
      )
      proc = subprocess.run(
          ["bash", _REPO_INSTALL_SH],
          cwd=fx["empty_dir"],
          env=fx["env"],
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertNotEqual(proc.returncode, 0)
      git_calls = _read_jsonl_calls(str(fx["git_log"]))
      self.assertEqual(len(git_calls), 1, f"Expected single git clone: {git_calls}")
      agy_calls = _read_agy_calls(str(fx["agy_log"]))
      self.assertEqual(len(agy_calls), 1, f"Expected validate only: {agy_calls}")
      self._assert_staged_validate_entry(
          agy_calls[0], str(fx["empty_dir"]), str(fx["tmp_dir"])
      )
      self.assertFalse(os.path.lexists(str(fx["install_dir"])))
      self.assertEqual(os.listdir(str(fx["tmp_dir"])), [])

  def test_install_sh_remote_without_agy_clones_and_copies_only_allowlist(self):
    """Non-repo install.sh without agy clones repo, copies allowlist, and cleans TMPDIR (GH-244)."""
    with tempfile.TemporaryDirectory() as base_dir:
      fx = _create_packaging_fixture(base_dir, with_agy=False, with_git=True)
      proc = subprocess.run(
          ["bash", _REPO_INSTALL_SH],
          cwd=fx["empty_dir"],
          env=fx["env"],
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(proc.returncode, 0, proc.stderr)
      git_calls = _read_jsonl_calls(str(fx["git_log"]))
      self.assertEqual(len(git_calls), 1, f"Expected single git clone: {git_calls}")
      self.assertEqual(
          git_calls[0]["args"][:4],
          [
              "clone",
              "--depth",
              "1",
              "https://github.com/hedges333/makeflow-skill.git",
          ],
      )
      install_dir = str(fx["install_dir"])
      self._assert_clean_runtime_tree(
          sorted(os.listdir(install_dir)), _walk_rel_paths(install_dir)
      )
      self.assertEqual(os.listdir(str(fx["tmp_dir"])), [])
      self.assertEqual(os.listdir(str(fx["empty_dir"])), [])

  def test_makefile_validate_stages_allowlist_and_cleans_temp_dir(self):
    """make validate stages only allowlist paths, validates with --strict, and cleans TMPDIR."""
    with tempfile.TemporaryDirectory() as base_dir:
      fx = _create_packaging_fixture(base_dir, with_agy=True)
      proc = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              _REPO_MAKEFILE,
              "validate",
              f"MKFILE_DIR={fx['src_dir']}",
              "PYTHON=true",
          ],
          cwd=fx["src_dir"],
          env=fx["env"],
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(proc.returncode, 0, proc.stderr)
      calls = _read_agy_calls(str(fx["agy_log"]))
      self.assertEqual(len(calls), 1, f"Unexpected agy calls: {calls}")
      self._assert_staged_validate_entry(
          calls[0], str(fx["src_dir"]), str(fx["tmp_dir"])
      )
      self.assertEqual(os.listdir(str(fx["tmp_dir"])), [])

  def test_makefile_validate_failure_emits_doc16_reflex_and_cleans_temp_dir(self):
    """make validate emits DOC-16 reflex and exits 2 when staged validation fails."""
    with tempfile.TemporaryDirectory() as base_dir:
      fx = _create_packaging_fixture(base_dir, with_agy=True, validate_exit=1)
      proc = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              _REPO_MAKEFILE,
              "validate",
              f"MKFILE_DIR={fx['src_dir']}",
              f"SCRIPTS_DIR={os.path.join(_REPO_ROOT, 'scripts')}",
              f"PYTHON={sys.executable}",
          ],
          cwd=fx["src_dir"],
          env=fx["env"],
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(proc.returncode, 2, proc.stderr)
      self.assertIn("DOC-16", proc.stderr)
      self.assertIn("PLUGIN_MANIFEST_VALIDATION_FAILED", proc.stderr)
      calls = _read_agy_calls(str(fx["agy_log"]))
      self.assertEqual(len(calls), 1)
      self._assert_staged_validate_entry(
          calls[0], str(fx["src_dir"]), str(fx["tmp_dir"])
      )
      self.assertEqual(os.listdir(str(fx["tmp_dir"])), [])

  def test_makefile_install_with_agy_stages_after_test_and_excludes_pyc(self):
    """make install with agy re-stages after test, validates staged dir, and excludes pyc."""
    with tempfile.TemporaryDirectory() as base_dir:
      fx = _create_packaging_fixture(base_dir, with_agy=True)
      fake_python = os.path.join(str(fx["bin_dir"]), "fake_python")
      _write_text(
          fake_python,
          (
              "#!/bin/sh\n"
              "mkdir -p lib/__pycache__ mcp/__pycache__ scripts/__pycache__\n"
              "printf 'pyc' > lib/__pycache__/generated.cpython-313.pyc\n"
              "printf 'pyc' > mcp/__pycache__/generated.cpython-313.pyc\n"
              "printf 'pyc' > scripts/__pycache__/generated.cpython-313.pyc\n"
              "exit 0\n"
          ),
      )
      os.chmod(fake_python, 0o755)
      proc = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              _REPO_MAKEFILE,
              "install",
              f"MKFILE_DIR={fx['src_dir']}",
              f"INSTALL_DIR={fx['install_dir']}",
              f"PYTHON={fake_python}",
          ],
          cwd=fx["src_dir"],
          env=fx["env"],
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(proc.returncode, 0, proc.stderr)
      self.assertTrue(
          os.path.isfile(
              os.path.join(
                  str(fx["src_dir"]),
                  "lib",
                  "__pycache__",
                  "generated.cpython-313.pyc",
              )
          )
      )
      calls = _read_agy_calls(str(fx["agy_log"]))
      self.assertEqual(len(calls), 3, f"Expected [validate, validate, install]: {calls}")
      for validate_call in calls[:2]:
        self._assert_staged_validate_entry(
            validate_call, str(fx["src_dir"]), str(fx["tmp_dir"])
        )
      self.assertEqual(calls[2]["action"], "install")
      self.assertEqual(calls[2]["abs_target"], calls[1]["abs_target"])
      self._assert_clean_runtime_tree(
          list(calls[2]["top_entries"]), list(calls[2]["all_rel_paths"])
      )
      install_dir = str(fx["install_dir"])
      self._assert_clean_runtime_tree(
          sorted(os.listdir(install_dir)), _walk_rel_paths(install_dir)
      )
      self.assertEqual(os.listdir(str(fx["tmp_dir"])), [])

  def test_makefile_install_fallback_without_agy_excludes_repo_files_and_pyc(self):
    """make install without agy copies only allowlist and excludes bytecode and .workflow."""
    with tempfile.TemporaryDirectory() as base_dir:
      fx = _create_packaging_fixture(base_dir, with_agy=False)
      proc = subprocess.run(
          [
              "make",
              "-s",
              "-f",
              _REPO_MAKEFILE,
              "install",
              f"MKFILE_DIR={fx['src_dir']}",
              f"INSTALL_DIR={fx['install_dir']}",
              "PYTHON=true",
          ],
          cwd=fx["src_dir"],
          env=fx["env"],
          capture_output=True,
          text=True,
          check=False,
      )
      self.assertEqual(proc.returncode, 0, proc.stderr)
      install_dir = str(fx["install_dir"])
      self._assert_clean_runtime_tree(
          sorted(os.listdir(install_dir)), _walk_rel_paths(install_dir)
      )
      self.assertEqual(os.listdir(str(fx["tmp_dir"])), [])

  def test_missing_allowlist_path_fails_cleanly_without_overwriting_install(self):
    """Missing allowlist path causes clean non-zero failure without partial install."""
    cases = (
        ("install_sh_with_agy", True, "install.sh"),
        ("install_sh_no_agy", False, "install.sh"),
        ("make_install_with_agy", True, "make_install"),
        ("make_install_no_agy", False, "make_install"),
        ("make_validate_with_agy", True, "make_validate"),
    )
    for label, with_agy, mode in cases:
      with self.subTest(case=label):
        with tempfile.TemporaryDirectory() as base_dir:
          fx = _create_packaging_fixture(base_dir, with_agy=with_agy)
          sentinel_path = os.path.join(str(fx["install_dir"]), "sentinel.txt")
          _write_text(sentinel_path, "preserve-existing-install\n")
          os.unlink(os.path.join(str(fx["src_dir"]), "LICENSE"))

          if mode == "install.sh":
            cmd = ["bash", _REPO_INSTALL_SH]
          elif mode == "make_install":
            cmd = [
                "make",
                "-s",
                "-f",
                _REPO_MAKEFILE,
                "install",
                f"MKFILE_DIR={fx['src_dir']}",
                f"INSTALL_DIR={fx['install_dir']}",
                "PYTHON=true",
            ]
          else:
            cmd = [
                "make",
                "-s",
                "-f",
                _REPO_MAKEFILE,
                "validate",
                f"MKFILE_DIR={fx['src_dir']}",
                "PYTHON=true",
            ]

          proc = subprocess.run(
              cmd,
              cwd=str(fx["src_dir"]),
              env=fx["env"],
              capture_output=True,
              text=True,
              check=False,
          )
          self.assertNotEqual(
              proc.returncode,
              0,
              f"{label} unexpectedly succeeded with missing LICENSE",
          )
          self.assertEqual(_read_agy_calls(str(fx["agy_log"])), [])
          with open(sentinel_path, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "preserve-existing-install\n")
          self.assertEqual(sorted(os.listdir(str(fx["install_dir"]))), ["sentinel.txt"])
          self.assertEqual(os.listdir(str(fx["tmp_dir"])), [])

  def test_repo_manifest_and_packaging_allowlist_parity(self):
    """Live repo root contains all 13 runtime paths and Makefile matches install.sh."""
    for rel_file in RUNTIME_ALLOWLIST_FILES:
      self.assertTrue(
          os.path.isfile(os.path.join(_REPO_ROOT, rel_file)),
          f"Missing runtime file in repo root: {rel_file}",
      )
    for rel_dir in RUNTIME_ALLOWLIST_DIRS:
      self.assertTrue(
          os.path.isdir(os.path.join(_REPO_ROOT, rel_dir)),
          f"Missing runtime directory in repo root: {rel_dir}",
      )

    with open(_REPO_MAKEFILE, "r", encoding="utf-8") as handle:
      makefile_text = handle.read()
    with open(_REPO_INSTALL_SH, "r", encoding="utf-8") as handle:
      install_sh_text = handle.read()

    mk_paths, mk_excludes = _extract_makefile_packaging_config(makefile_text)
    sh_paths, sh_excludes = _extract_install_sh_packaging_config(install_sh_text)

    self.assertEqual(tuple(mk_paths), RUNTIME_ALLOWLIST)
    self.assertEqual(tuple(sh_paths), RUNTIME_ALLOWLIST)
    self.assertEqual(tuple(mk_excludes), STAGE_EXCLUDES)
    self.assertEqual(tuple(sh_excludes), STAGE_EXCLUDES)
    self.assertNotIn("-cf - .", makefile_text)
    self.assertNotIn("-cf - .", install_sh_text)


if __name__ == "__main__":
  unittest.main()
