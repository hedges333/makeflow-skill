"""State Makefile synthesis and .git/info/exclude bootstrap helpers (GH-274).

Synthesizes the deterministic ``.workflow/state/Makefile`` DAG gate recipes,
bootstraps ``.git/info/exclude`` for local workflow state directories, and
refreshes ignored workspace Makefiles without polluting non-Makeflow repos.
"""

import os
import shlex

from lib.main_sync import _run_repo_git

_DEFAULT_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS_DIR = os.path.join(_DEFAULT_REPO_ROOT, "scripts")


def ensure_git_exclude_workflow_dirs(repo_root: str) -> bool:
  """Ensures .workflow/state/ and .workflow/archive/ are in Git info/exclude.

  Args:
    repo_root: Repository or linked worktree root directory.

  Returns:
    True when inside a Git repository and exclude entries are present; False
    otherwise.
  """
  if not repo_root or not os.path.isdir(repo_root):
    return False
  proc = _run_repo_git(repo_root, "rev-parse", "--git-path", "info/exclude")
  if proc.returncode != 0:
    return False
  raw_excl = (proc.stdout or "").strip()
  if not raw_excl:
    return False
  excl_path = (
      raw_excl
      if os.path.isabs(raw_excl)
      else os.path.join(repo_root, raw_excl)
  )
  try:
    os.makedirs(os.path.dirname(excl_path), exist_ok=True)
    existing_text = ""
    if os.path.isfile(excl_path):
      with open(excl_path, "r", encoding="utf-8", errors="replace") as f:
        existing_text = f.read()
    existing_lines = {
        ln.strip() for ln in existing_text.splitlines() if ln.strip()
    }
    missing = [
        entry
        for entry in (".workflow/state/", ".workflow/archive/")
        if entry not in existing_lines
    ]
    if missing:
      prefix = (
          ""
          if (not existing_text or existing_text.endswith("\n"))
          else "\n"
      )
      with open(excl_path, "a", encoding="utf-8") as f:
        f.write(prefix + "\n".join(missing) + "\n")
    return True
  except OSError:
    return False


def refresh_state_makefile_if_ignored(repo_root: str) -> bool:
  """Writes or updates .workflow/state/Makefile only when .workflow/state/ is ignored.

  Args:
    repo_root: Candidate workspace root directory.

  Returns:
    True when ``.workflow/`` exists, ``.workflow/state/`` is Git-ignored, and
    the Makefile is up to date; False otherwise without touching disk.
  """
  if not repo_root or not os.path.isdir(os.path.join(repo_root, ".workflow")):
    return False
  ign_proc = _run_repo_git(repo_root, "check-ignore", "-q", ".workflow/state/")
  if ign_proc.returncode != 0:
    return False

  state_dir = os.path.join(repo_root, ".workflow", "state")
  mk_path = os.path.join(state_dir, "Makefile")
  expected = generate_state_makefile(repo_root)
  try:
    if os.path.isfile(mk_path):
      with open(mk_path, "r", encoding="utf-8") as f:
        if f.read() == expected:
          return True
    os.makedirs(state_dir, exist_ok=True)
    with open(mk_path, "w", encoding="utf-8") as f:
      f.write(expected)
    return True
  except OSError:
    return False


def _workflow_target_recipes(gates_script: str, quoted_root: str) -> str:
  """Builds the read-init-d gate, 14 DAG target recipes, and generic advance."""
  base = f"\t@$(PYTHON) {gates_script} --repo-root {quoted_root} advance"
  specs = (
      ("ticket-assigned", "", "--to T10"),
      ("archaeology-verified", "", "--to T15"),
      ("approved-plan", " check-plan", "--to T20"),
      ("implementation-diff", "", "--to T30"),
      ("adversarial-review", " check-approved-tree check-regression", "--to T40"),
      ("triaged-blockers", "", "--to T42"),
      ("approved-review", " check-approved-tree", "--to T45"),
      ("commit", " check-approved-tree check-commit-msg", "--to T50"),
      ("push", " check-main-sync", "--to T60"),
      ("bot-quiescence", "", "--to T70"),
      ("ready-to-land", "", "--to T75"),
      ("land", "", "--to T80"),
      ("codified-scars-consolidated", "", "--to T90"),
      ("finish", "", "--to T90 --complete"),
  )
  blocks = [
      f"read-init-d:\n\t@$(PYTHON) {gates_script} --repo-root {quoted_root} read-init-d --make-env\n"
  ]
  blocks.extend(
      f"{name}:{prereqs}\n{base} {flag} --make-env\n"
      for name, prereqs, flag in specs
  )
  blocks.append(f"advance:\n{base} --make-env\n")
  return "\n".join(blocks)


def generate_state_makefile(repo_root: str) -> str:
  """Returns the canonical content of .workflow/state/Makefile for repo_root.

  Args:
    repo_root: Workspace repository root directory.

  Returns:
    Synthesized GNU Makefile contents for workflow gates and ``ast-lint``.
  """
  abs_root = os.path.abspath(repo_root)
  gates_script = shlex.quote(
      os.path.abspath(os.path.join(_SCRIPTS_DIR, "workflow_gates.py"))
  ).replace("$", "$$")
  guard_script = shlex.quote(
      os.path.abspath(os.path.join(_SCRIPTS_DIR, "tdd_guard.py"))
  ).replace("$", "$$")
  scrub_script = shlex.quote(
      os.path.abspath(os.path.join(_SCRIPTS_DIR, "ast_scrub.py"))
  ).replace("$", "$$")
  quoted_root = shlex.quote(abs_root).replace("$", "$$")
  phony = (
      "read-init-d ticket-assigned archaeology-verified approved-plan "
      "implementation-diff adversarial-review triaged-blockers approved-review "
      "commit push bot-quiescence ready-to-land land "
      "codified-scars-consolidated finish advance "
      "check-intake release-intake check-plan check-approved-tree check-commit-msg "
      "check-post-commit check-main-sync verify-regression check-regression "
      "repo-tree promote-review park-session resume-parked "
      "witness-red verify-green verify-citation ast-lint"
  )
  recipes = _workflow_target_recipes(gates_script, quoted_root)
  return (
      "# Synthesized by workflow_gates.py init-makefile (SCAR-ARCH-06, SCAR-TEST-12)\n"
      "PYTHON ?= python3\n"
      "FROM ?=\n"
      "TO ?=\n"
      "APPROVED ?=\n"
      "BLOCKERS ?=\n"
      "ALLOW_MAIN_CONFLICT ?=\n"
      "NO_FETCH ?=\n"
      "TRANSCRIPT ?=\n"
      "TICKET ?=\n"
      "ALLOW_BLOCKED ?=\n"
      "FILE ?=\n"
      "TEST_DIR =\n"
      "TEST_CMD =\n"
      "ALLOW_HEAD_MOVE =\n"
      "CITE =\n"
      "REGRESSION_CMD ?=\n"
      "SUBPROJECT ?=\n"
      "PATH_PREFIX ?=\n"
      "TREE_LIMIT ?=\n"
      "NO_LIMIT ?=\n"
      "COUNT_ONLY ?=\n"
      "SRC_DIR ?=\n"
      "ROUND ?=\n"
      "PR ?=\n"
      "BRANCH ?=\n"
      "REASON ?=\n"
      "CRON ?=\n"
      "_SQ := '\n"
      "export TICKET ALLOW_BLOCKED FROM TO APPROVED BLOCKERS ALLOW_MAIN_CONFLICT NO_FETCH TRANSCRIPT FILE\n"
      "export TEST_DIR TEST_CMD ALLOW_HEAD_MOVE CITE\n"
      "export REGRESSION_CMD SUBPROJECT\n"
      "export PATH_PREFIX TREE_LIMIT NO_LIMIT COUNT_ONLY\n\n"
      f".PHONY: {phony}\n\n"
      f"{recipes}\n"
      "check-intake:\n"
      f"\t@$(PYTHON) {gates_script} --repo-root {quoted_root} intake --make-env\n\n"
      "release-intake:\n"
      f"\t@$(PYTHON) {gates_script} --repo-root {quoted_root} release-intake --make-env\n\n"
      "check-plan:\n"
      f"\t@$(PYTHON) {gates_script} --repo-root {quoted_root} check-plan\n\n"
      "check-approved-tree:\n"
      f"\t@$(PYTHON) {gates_script} --repo-root {quoted_root} approved-tree\n\n"
      "check-commit-msg:\n"
      f"\t@$(PYTHON) {gates_script} --repo-root {quoted_root} commit-msg --make-env\n\n"
      "check-post-commit:\n"
      f"\t@$(PYTHON) {gates_script} --repo-root {quoted_root} post-commit --make-env\n\n"
      "check-main-sync:\n"
      f"\t@$(PYTHON) {gates_script} --repo-root {quoted_root} main-sync --make-env\n\n"
      "verify-regression:\n"
      f"\t@$(PYTHON) {gates_script} --repo-root {quoted_root} verify-regression --make-env\n\n"
      "check-regression:\n"
      f"\t@$(PYTHON) {gates_script} --repo-root {quoted_root} check-regression --make-env\n\n"
      "repo-tree:\n"
      f"\t@$(PYTHON) {gates_script} --repo-root {quoted_root} repo-tree --make-env\n\n"
      "promote-review:\n"
      f'\t@$(PYTHON) {gates_script} --repo-root {quoted_root} promote-review --round "$(ROUND)"\n\n'
      "park-session:\n"
      "\t@MF_PARK_PR='$(subst $(_SQ),$(_SQ)\\$(_SQ)$(_SQ),$(value PR))' "
      "MF_PARK_BRANCH='$(subst $(_SQ),$(_SQ)\\$(_SQ)$(_SQ),$(value BRANCH))' "
      "MF_PARK_REASON='$(subst $(_SQ),$(_SQ)\\$(_SQ)$(_SQ),$(value REASON))' "
      "MF_PARK_CRON='$(subst $(_SQ),$(_SQ)\\$(_SQ)$(_SQ),$(value CRON))' "
      f"$(PYTHON) {gates_script} --repo-root {quoted_root} park-session --from-env\n\n"
      "resume-parked:\n"
      "\t@MF_PARK_PR='$(subst $(_SQ),$(_SQ)\\$(_SQ)$(_SQ),$(value PR))' "
      "MF_PARK_BRANCH='$(subst $(_SQ),$(_SQ)\\$(_SQ)$(_SQ),$(value BRANCH))' "
      f"$(PYTHON) {gates_script} --repo-root {quoted_root} resume-parked --from-env\n\n"
      "witness-red:\n"
      f"\t@$(PYTHON) {guard_script} --repo-root {quoted_root} witness-red --make-env\n\n"
      "verify-green:\n"
      f"\t@$(PYTHON) {guard_script} --repo-root {quoted_root} verify-green --make-env\n\n"
      "verify-citation:\n"
      f"\t@$(PYTHON) {guard_script} --repo-root {quoted_root} verify-citation --make-env\n\n"
      "ast-lint:\n"
      f'\t@$(PYTHON) {scrub_script} --repo-root {quoted_root}$(if $(SRC_DIR), --src-dir "$(SRC_DIR)",) --strict\n'
  )
