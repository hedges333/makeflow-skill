"""CLI parser builder and GNU Make environment hydration for workflow_gates (GH-274).

Constructs the 20-subcommand ``SilentArgumentParser`` for ``WorkflowGatesScript``
and hydrates unset gate options from exported GNU Make and parking environment
variables.
"""

import argparse
import os
from typing import Any, List, Mapping, Optional, Tuple

from lib.base_script import ReflexArgumentError, SilentArgumentParser
from lib.parking_gates import ParkSessionRequest, ResumeParkedRequest
from lib.plugin_gates import DEFAULT_SKILL_MAX_BYTES
from lib.repo_tree import DEFAULT_REPO_TREE_THRESHOLD


def _env_truthy(
    name: str, environ: Optional[Mapping[str, str]] = None
) -> bool:
  """Returns True when environment variable name has a non-falsey value."""
  env = os.environ if environ is None else environ
  return env.get(name, "").strip().lower() not in (
      "",
      "0",
      "false",
      "no",
  )


def _register_promote_review_subparser(subparsers: Any) -> None:
  """Registers the promote-review subparser for Target 40 review promotion."""
  p_prom = subparsers.add_parser(
      "promote-review",
      help="Validate 40_critic_review_round<N>.md and promote it to 40_critic_review.md.",
      description="Validate Six-Pillar headings and binary verdict syntax, then copy the round card via safe_copy_file.",
      epilog="Example:\n  python3 scripts/workflow_gates.py --repo-root . promote-review --round 1",
      formatter_class=argparse.RawDescriptionHelpFormatter,
  )
  p_prom.add_argument(
      "--round",
      dest="review_round",
      default="",
      help="Positive integer critic review round number (for example 1).",
  )
  p_prom.add_argument(
      "--json",
      action="store_true",
      dest="sub_json",
      help="Emit output or Vector 4 Reflex errors as JSON.",
  )


def _register_parking_subparsers(subparsers: Any) -> None:
  """Registers park-session and resume-parked subparsers for Target 70."""
  p_park = subparsers.add_parser(
      "park-session",
      help="Persist structured PARKED state at Target 70 and emit a Parking Receipt.",
      description="Save 70_parking_state.json, render 70_bot_triage.md and 99_next_action.md, and print a Parking Receipt.",
      epilog="Example:\n  python3 scripts/workflow_gates.py --repo-root . park-session --pr 47 --reason 'Awaiting peer review'",
      formatter_class=argparse.RawDescriptionHelpFormatter,
  )
  p_park.add_argument(
      "--pr", dest="park_pr", type=int, default=0, help="Pull request number."
  )
  p_park.add_argument(
      "--pr-url", dest="park_pr_url", default="", help="Pull request URL."
  )
  p_park.add_argument(
      "--branch", dest="park_branch", default="", help="PR head branch name."
  )
  p_park.add_argument(
      "--reason",
      dest="park_reason",
      default="",
      help="Concise reason for parking.",
  )
  p_park.add_argument(
      "--ci-status", dest="park_ci_status", default="", help="Latest CI status."
  )
  p_park.add_argument(
      "--review-decision",
      dest="park_review_decision",
      default="",
      help="Latest GitHub reviewDecision.",
  )
  p_park.add_argument(
      "--approvals",
      dest="park_approvals",
      type=int,
      default=None,
      help="Valid approval count.",
  )
  p_park.add_argument(
      "--required-approvals",
      dest="park_required_approvals",
      type=int,
      default=None,
      help="Effective required approval count.",
  )
  p_park.add_argument(
      "--require-codeowners",
      action="store_true",
      dest="park_require_codeowners",
      help="Indicate CODEOWNERS reviews are required.",
  )
  p_park.add_argument(
      "--pending-reviewers",
      dest="park_pending_reviewers",
      default="",
      help="Comma-separated pending reviewers.",
  )
  p_park.add_argument(
      "--changes-requested-by",
      dest="park_changes_requested_by",
      default="",
      help="Comma-separated reviewers requesting changes.",
  )
  p_park.add_argument(
      "--unresolved-threads",
      dest="park_unresolved_threads",
      type=int,
      default=None,
      help="Count of unresolved review threads.",
  )
  p_park.add_argument(
      "--cron",
      dest="park_cron",
      default="",
      help="Optional background polling cron expression.",
  )
  p_park.add_argument(
      "--from-env",
      action="store_true",
      dest="park_from_env",
      help="Hydrate PR, branch, reason, and cron from MF_PARK_* environment variables.",
  )
  p_park.add_argument(
      "--json",
      action="store_true",
      dest="sub_json",
      help="Emit output or errors as JSON.",
  )

  p_resume = subparsers.add_parser(
      "resume-parked",
      help="Re-check a parked PR once and advance to T75, return to T30, or stay parked.",
      description="Query PR status once without sleeping and reconcile 70_parking_state.json and Target 70/75 state.",
      epilog="Example:\n  python3 scripts/workflow_gates.py --repo-root . resume-parked",
      formatter_class=argparse.RawDescriptionHelpFormatter,
  )
  p_resume.add_argument(
      "--pr",
      dest="park_pr",
      type=int,
      default=0,
      help="Optional PR number override.",
  )
  p_resume.add_argument(
      "--branch",
      dest="park_branch",
      default="",
      help="Optional expected PR head branch override.",
  )
  p_resume.add_argument(
      "--from-env",
      action="store_true",
      dest="park_from_env",
      help="Hydrate PR and branch from MF_PARK_* environment variables.",
  )
  p_resume.add_argument(
      "--json",
      action="store_true",
      dest="sub_json",
      help="Emit output or errors as JSON.",
  )


def _register_repo_tree_subparser(subparsers: Any) -> None:
  """Registers the repo-tree subparser for Target 15 Git file tree indexing."""
  p_tree_idx = subparsers.add_parser(
      "repo-tree",
      help="List repository Git file tree index with SCAR-TOOL-16 volume triage.",
      description="Collect tracked and non-ignored untracked Git paths for Target 15 archaeology dispatch.",
      epilog="Example:\n  python3 scripts/workflow_gates.py --repo-root . repo-tree",
      formatter_class=argparse.RawDescriptionHelpFormatter,
  )
  p_tree_idx.add_argument(
      "--prefix",
      dest="tree_prefix",
      default="",
      help="Optional repository-relative path prefix filter.",
  )
  p_tree_idx.add_argument(
      "--approved-limit",
      dest="tree_approved_limit",
      type=int,
      default=None,
      help="Explicit user-approved maximum number of file paths to display.",
  )
  p_tree_idx.add_argument(
      "--count-only",
      action="store_true",
      dest="tree_count_only",
      help="Print file counts and top-level directory breakdown without listing file paths.",
  )
  p_tree_idx.add_argument(
      "--no-limit",
      action="store_true",
      dest="tree_no_limit",
      help="Return the full file tree index when the user explicitly approves unbounded output.",
  )
  p_tree_idx.add_argument(
      "--threshold",
      dest="tree_threshold",
      type=int,
      default=DEFAULT_REPO_TREE_THRESHOLD,
      help=f"Volume triage file threshold (default {DEFAULT_REPO_TREE_THRESHOLD}).",
  )
  p_tree_idx.add_argument(
      "--json",
      action="store_true",
      dest="sub_json",
      help="Emit output or Vector 4 Reflex errors as JSON.",
  )
  p_tree_idx.add_argument(
      "--make-env",
      action="store_true",
      dest="sub_make_env",
      help="Hydrate PATH_PREFIX, TREE_LIMIT, NO_LIMIT, and COUNT_ONLY from environment.",
  )


def _register_regression_subparsers(subparsers: Any) -> None:
  """Registers verify-regression and check-regression subparsers."""
  specs = (
      (
          "verify-regression",
          "Run the active subproject full regression suite and seal 30_regression_receipt.json.",
          "Example:\n  python3 scripts/workflow_gates.py --repo-root . verify-regression",
      ),
      (
          "verify-trial-merge",
          "Verify HEAD + origin/main in an ephemeral worktree and seal 75_trial_merge_receipt.json.",
          "Example:\n  python3 scripts/workflow_gates.py --repo-root . verify-trial-merge",
      ),
      (
          "check-regression",
          "Verify .workflow/state/30_regression_receipt.json against live tree and approved_tree.txt.",
          "Example:\n  python3 scripts/workflow_gates.py --repo-root . check-regression",
      ),
  )
  for name, desc, epilog in specs:
    p_reg = subparsers.add_parser(
        name,
        help=desc,
        description=desc,
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_reg.add_argument(
        "--regression-cmd",
        dest="regression_cmd",
        default="",
        help="Explicit full subproject regression test command.",
    )
    p_reg.add_argument(
        "--subproject",
        dest="subproject",
        default=None,
        help="Relative path to active subproject directory (default '.').",
    )
    p_reg.add_argument(
        "--timeout",
        dest="timeout",
        type=float,
        default=None,
        help="Regression command timeout in seconds.",
    )
    p_reg.add_argument(
        "--json",
        action="store_true",
        dest="sub_json",
        help="Emit output or Vector 4 Reflex errors as JSON.",
    )
    p_reg.add_argument(
        "--make-env",
        action="store_true",
        dest="sub_make_env",
        help="Hydrate REGRESSION_CMD and SUBPROJECT from environment.",
    )


def _register_read_init_subparser(subparsers: Any) -> None:
  """Registers the Target 00 read-init-d inspection/reconciliation subparser."""
  p_init_d = subparsers.add_parser(
      "read-init-d",
      help="Inspect and reconcile Target 00 session state on disk.",
      description="Reconcile active session state without mutating clean or completed workspaces.",
      epilog="Example:\n  python3 scripts/workflow_gates.py --repo-root . read-init-d",
      formatter_class=argparse.RawDescriptionHelpFormatter,
  )
  p_init_d.add_argument(
      "--from", dest="adv_from", default="", help="Optional source target ID."
  )
  p_init_d.add_argument(
      "--json", action="store_true", dest="sub_json", help="Emit errors as JSON."
  )
  p_init_d.add_argument(
      "--make-env",
      action="store_true",
      dest="sub_make_env",
      help="Hydrate FROM from environment.",
  )


def _register_advance_subparser(subparsers: Any) -> None:
  """Registers the advance subcommand parser on subparsers."""
  p_adv = subparsers.add_parser(
      "advance",
      help="Validate and execute a Makefile DAG target transition.",
      description="Execute a deterministic Makefile DAG target transition and update 99_next_action.md.",
      epilog="Example:\n  python3 scripts/workflow_gates.py --repo-root . advance --to T15",
      formatter_class=argparse.RawDescriptionHelpFormatter,
  )
  p_adv.add_argument(
      "--from", dest="adv_from", default="", help="Source target ID."
  )
  p_adv.add_argument(
      "--to", dest="adv_to", default="", help="Destination target ID."
  )
  p_adv.add_argument(
      "--approved",
      dest="adv_approved",
      nargs="?",
      const="1",
      default=None,
      help="Human pause-gate approval flag (1/true or 0/false).",
  )
  p_adv.add_argument(
      "--blockers", dest="adv_blockers", default=None, help="Critic blocker count."
  )
  p_adv.add_argument(
      "--allow-main-conflict",
      action="store_true",
      dest="allow_main_conflict",
      help="Allow advancing despite upstream main conflicts.",
  )
  p_adv.add_argument(
      "--no-fetch",
      action="store_true",
      dest="no_fetch",
      help="Inspect local remote-tracking ref without running git fetch.",
  )
  p_adv.add_argument(
      "--transcript", dest="adv_transcript", default="", help="Transcript JSONL path."
  )
  p_adv.add_argument(
      "--complete",
      action="store_true",
      dest="adv_complete",
      help="Complete Target 90 (finish) and clean intermediate state cards.",
  )
  p_adv.add_argument(
      "--json", action="store_true", dest="sub_json", help="Emit errors as JSON."
  )
  p_adv.add_argument(
      "--make-env",
      action="store_true",
      dest="sub_make_env",
      help="Hydrate FROM, TO, APPROVED, BLOCKERS, and TRANSCRIPT from environment.",
  )


def _register_check_plan_subparser(subparsers: Any) -> None:
  """Registers the check-plan subparser for Target 20 canonical plan validation."""
  p_plan = subparsers.add_parser(
      "check-plan",
      help="Validate .workflow/state/20_plan.md against the canonical 8-section spec.",
      description="Verify all 8 canonical headings, Section 3 subheadings, Section 2 Given-When-Then, and Section 7 table.",
      epilog="Example:\n  python3 scripts/workflow_gates.py --repo-root . check-plan",
      formatter_class=argparse.RawDescriptionHelpFormatter,
  )
  p_plan.add_argument(
      "--json",
      action="store_true",
      dest="sub_json",
      help="Emit output or Vector 4 Reflex errors as JSON.",
  )


def build_workflow_gates_parser(
    default_repo_root: str,
) -> SilentArgumentParser:
  """Builds the 20-subcommand SilentArgumentParser for WorkflowGatesScript.

  Args:
    default_repo_root: Default value for the ``--repo-root`` CLI flag.

  Returns:
    Configured ``SilentArgumentParser`` covering all 20 workflow gate
    subcommands.
  """
  parser = SilentArgumentParser(
      prog="workflow_gates.py",
      description="Deterministic Makefile workflow gate checks emitting Vector 4 Reflexes (SCAR-ARCH-06).",
      epilog=(
          "Examples:\n"
          "  python3 scripts/workflow_gates.py init-makefile\n"
          "  python3 scripts/workflow_gates.py intake --ticket GH-170\n"
          "  python3 scripts/workflow_gates.py approved-tree\n"
          "  python3 scripts/workflow_gates.py commit-msg --file .workflow/state/50_commit_msg.txt"
      ),
      formatter_class=argparse.RawDescriptionHelpFormatter,
  )
  parser.add_argument(
      "--repo-root",
      default=default_repo_root,
      help="Repository root directory to check.",
  )
  parser.add_argument(
      "--lock-dir",
      default=None,
      help="Optional directory for multi-workspace mutual exclusion locks.",
  )
  parser.add_argument(
      "--json",
      action="store_true",
      dest="as_json",
      help="Emit Vector 4 Reflex errors as JSON.",
  )
  parser.add_argument(
      "--make-env",
      action="store_true",
      dest="make_env",
      help="Hydrate gate arguments from exported GNU Make environment variables.",
  )

  subparsers = parser.add_subparsers(
      dest="gate",
      parser_class=SilentArgumentParser,
      help="Workflow gate subcommand to execute.",
  )

  p_init = subparsers.add_parser(
      "init-makefile",
      help="Synthesize .workflow/state/Makefile for the target workspace.",
      description="Synthesize deterministic .workflow/state/Makefile gate targets for the workspace.",
      epilog="Example:\n  python3 scripts/workflow_gates.py --repo-root . init-makefile",
      formatter_class=argparse.RawDescriptionHelpFormatter,
  )
  p_init.add_argument(
      "--json",
      action="store_true",
      dest="sub_json",
      help="Emit errors as JSON.",
  )

  p_files = subparsers.add_parser(
      "plugin-files",
      help="Verify all required Makeflow plugin manifests and core skill files exist.",
      description="Verify that required plugin manifests, hooks, MCP config, and SKILL.md exist.",
      epilog="Example:\n  python3 scripts/workflow_gates.py plugin-files",
      formatter_class=argparse.RawDescriptionHelpFormatter,
  )
  p_files.add_argument(
      "--json",
      action="store_true",
      dest="sub_json",
      help="Emit errors as JSON.",
  )

  p_budget = subparsers.add_parser(
      "skill-budget",
      help="Verify skills/makeflow/SKILL.md stays within the context byte budget.",
      description="Check that skills/makeflow/SKILL.md does not exceed the byte budget ceiling.",
      epilog="Example:\n  python3 scripts/workflow_gates.py skill-budget --max-bytes 24576",
      formatter_class=argparse.RawDescriptionHelpFormatter,
  )
  p_budget.add_argument(
      "--max-bytes",
      type=int,
      default=DEFAULT_SKILL_MAX_BYTES,
      help="Maximum allowed size in bytes for SKILL.md (default 24576).",
  )
  p_budget.add_argument(
      "--json",
      action="store_true",
      dest="sub_json",
      help="Emit errors as JSON.",
  )

  p_intake = subparsers.add_parser(
      "intake",
      help="Run Target 10 intake hygiene, git exclude, and ticket collision checks.",
      description="Verify clean git status, .git/info/exclude rules, and multi-workspace ticket lock at Target 10.",
      epilog="Example:\n  python3 scripts/workflow_gates.py intake --ticket GH-170",
      formatter_class=argparse.RawDescriptionHelpFormatter,
  )
  p_intake.add_argument(
      "--ticket",
      default=None,
      help="Ticket identifier to acquire and check (for example GH-170 or LOCAL-1).",
  )
  p_intake.add_argument(
      "--lock-dir",
      default=None,
      dest="sub_lock_dir",
      help="Directory for workspace mutual exclusion locks.",
  )
  p_intake.add_argument(
      "--allow-blocked",
      action="store_true",
      dest="allow_blocked",
      help="Allow intake even when upstream blocking issues are open.",
  )
  p_intake.add_argument(
      "--json",
      action="store_true",
      dest="sub_json",
      help="Emit errors as JSON.",
  )
  p_intake.add_argument(
      "--make-env",
      action="store_true",
      dest="sub_make_env",
      help="Hydrate TICKET and ALLOW_BLOCKED from environment.",
  )

  p_rel = subparsers.add_parser(
      "release-intake",
      help="Release active ticket lock and restore terminal title when idle.",
      description="Release the workspace mutex lock for the active ticket and restore the terminal title when idle.",
      epilog="Example:\n  python3 scripts/workflow_gates.py release-intake --ticket GH-170",
      formatter_class=argparse.RawDescriptionHelpFormatter,
  )
  p_rel.add_argument(
      "--ticket",
      default=None,
      help="Ticket identifier whose lock should be released.",
  )
  p_rel.add_argument(
      "--lock-dir",
      default=None,
      dest="sub_lock_dir",
      help="Directory for workspace mutual exclusion locks.",
  )
  p_rel.add_argument(
      "--json",
      action="store_true",
      dest="sub_json",
      help="Emit errors as JSON.",
  )
  p_rel.add_argument(
      "--make-env",
      action="store_true",
      dest="sub_make_env",
      help="Hydrate TICKET from environment.",
  )

  p_rel_lock = subparsers.add_parser(
      "release-lock",
      help="Alias for release-intake to release active ticket and workspace locks.",
      description="Alias for release-intake to release active ticket and workspace locks.",
      epilog="Example:\n  python3 scripts/workflow_gates.py release-lock --ticket GH-170",
      formatter_class=argparse.RawDescriptionHelpFormatter,
  )
  p_rel_lock.add_argument(
      "--ticket",
      default=None,
      help="Ticket identifier whose lock should be released.",
  )
  p_rel_lock.add_argument(
      "--lock-dir",
      default=None,
      dest="sub_lock_dir",
      help="Directory for workspace mutual exclusion locks.",
  )
  p_rel_lock.add_argument(
      "--json",
      action="store_true",
      dest="sub_json",
      help="Emit errors as JSON.",
  )
  p_rel_lock.add_argument(
      "--make-env",
      action="store_true",
      dest="sub_make_env",
      help="Hydrate TICKET from environment.",
  )

  p_tree = subparsers.add_parser(
      "approved-tree",
      help="Verify staged git write-tree matches .workflow/state/approved_tree.txt.",
      description="Verify that the current git index matches the locked Merkle tree hash from Target 40.",
      epilog="Example:\n  python3 scripts/workflow_gates.py approved-tree",
      formatter_class=argparse.RawDescriptionHelpFormatter,
  )
  p_tree.add_argument(
      "--json",
      action="store_true",
      dest="sub_json",
      help="Emit errors as JSON.",
  )

  p_msg = subparsers.add_parser(
      "commit-msg",
      help="Validate .workflow/state/50_commit_msg.txt against Conventional Commits 1.0.0.",
      description="Validate the prepared commit message file for Target 50 against Conventional Commits and trailer rules.",
      epilog="Example:\n  python3 scripts/workflow_gates.py commit-msg --file .workflow/state/50_commit_msg.txt",
      formatter_class=argparse.RawDescriptionHelpFormatter,
  )
  p_msg.add_argument(
      "--file",
      dest="commit_msg_file",
      default=None,
      help="Path to commit message file (defaults to .workflow/state/50_commit_msg.txt).",
  )
  p_msg.add_argument(
      "--json",
      action="store_true",
      dest="sub_json",
      help="Emit errors as JSON.",
  )
  p_msg.add_argument(
      "--make-env",
      action="store_true",
      dest="sub_make_env",
      help="Hydrate FILE from environment.",
  )

  p_post = subparsers.add_parser(
      "post-commit",
      help="Verify HEAD commit message matches 50_commit_msg.txt and HEAD^{tree} matches approved_tree.txt.",
      description="Revalidate HEAD commit message and tree lock on Target 50 exit.",
      epilog="Example:\n  python3 scripts/workflow_gates.py post-commit",
      formatter_class=argparse.RawDescriptionHelpFormatter,
  )
  p_post.add_argument(
      "--file",
      dest="commit_msg_file",
      default=None,
      help="Path to approved commit message file (defaults to .workflow/state/50_commit_msg.txt).",
  )
  p_post.add_argument(
      "--json",
      action="store_true",
      dest="sub_json",
      help="Emit output or Vector 4 Reflex errors as JSON.",
  )
  p_post.add_argument(
      "--make-env",
      action="store_true",
      dest="sub_make_env",
      help="Hydrate FILE from environment.",
  )

  p_title = subparsers.add_parser(
      "terminal-title",
      help="Update the interactive terminal title for the active ticket and directory.",
      description="Detect terminal capability and set the terminal window title for the active ticket.",
      epilog="Example:\n  python3 scripts/workflow_gates.py terminal-title --ticket GH-170",
      formatter_class=argparse.RawDescriptionHelpFormatter,
  )
  p_title.add_argument(
      "--ticket",
      default=None,
      help="Ticket ID to display in the terminal title.",
  )
  p_title.add_argument(
      "--directory",
      default=None,
      help="Directory name to display in the terminal title.",
  )
  p_title.add_argument(
      "--title",
      default=None,
      help="Explicit full terminal title string override.",
  )
  p_title.add_argument(
      "--issue-tag",
      default=None,
      help="Short issue description tag for the terminal title.",
  )
  p_title.add_argument(
      "--json",
      action="store_true",
      dest="sub_json",
      help="Emit output as JSON.",
  )

  p_sync = subparsers.add_parser(
      "main-sync",
      help="Check upstream origin/main advancement and 3-way merge-tree conflicts.",
      description="Fetch origin/main (unless --no-fetch is set) and check for upstream commits and 3-way merge conflicts.",
      epilog="Example:\n  python3 scripts/workflow_gates.py --repo-root . main-sync",
      formatter_class=argparse.RawDescriptionHelpFormatter,
  )
  p_sync.add_argument(
      "--no-fetch",
      action="store_true",
      dest="no_fetch",
      help="Inspect local remote-tracking ref without running git fetch.",
  )
  p_sync.add_argument(
      "--fail-on-behind",
      action="store_true",
      dest="fail_on_behind",
      help="Exit 2 with UPSTREAM_MAIN_BEHIND when origin/main is ahead even without conflicts.",
  )
  p_sync.add_argument(
      "--allow-conflict",
      action="store_true",
      dest="allow_conflict",
      help="Exit 0 with warning when operator explicitly overrides upstream main conflicts.",
  )
  p_sync.add_argument(
      "--json",
      action="store_true",
      dest="sub_json",
      help="Emit output or Vector 4 Reflex errors as JSON.",
  )
  p_sync.add_argument(
      "--make-env",
      action="store_true",
      dest="sub_make_env",
      help="Hydrate NO_FETCH and ALLOW_MAIN_CONFLICT from environment.",
  )

  _register_advance_subparser(subparsers)
  _register_read_init_subparser(subparsers)
  _register_regression_subparsers(subparsers)
  _register_repo_tree_subparser(subparsers)
  _register_promote_review_subparser(subparsers)
  _register_parking_subparsers(subparsers)
  _register_check_plan_subparser(subparsers)
  return parser


def hydrate_from_make_env(
    target: Any, environ: Optional[Mapping[str, str]] = None
) -> None:
  """Populates unset gate fields on target from exported GNU Make environment variables.

  Args:
    target: Mutable runner instance (such as ``WorkflowGatesScript``) holding
      parsed CLI attributes.
    environ: Optional environment mapping. Defaults to ``os.environ``.

  Returns:
    ``None`` after updating unset attributes on ``target`` in place.

  Raises:
    ReflexArgumentError: When ``TREE_LIMIT`` is set to a non-positive or
      non-integer value.
  """
  env = os.environ if environ is None else environ
  if not target.ticket and env.get("TICKET", "").strip():
    target.ticket = env["TICKET"].strip()
  if not target.allow_blocked and _env_truthy("ALLOW_BLOCKED", environ=env):
    target.allow_blocked = True
  if not target.commit_msg_file and env.get("FILE", "").strip():
    target.commit_msg_file = env["FILE"].strip()
  if not target.no_fetch and _env_truthy("NO_FETCH", environ=env):
    target.no_fetch = True
  if _env_truthy("ALLOW_MAIN_CONFLICT", environ=env):
    target.allow_main_conflict = True
    target.allow_conflict = True
  if not target.adv_from and env.get("FROM", "").strip():
    target.adv_from = env["FROM"].strip()
  if not target.adv_to and env.get("TO", "").strip():
    target.adv_to = env["TO"].strip()
  if target.adv_approved is None and env.get("APPROVED", "").strip():
    target.adv_approved = env["APPROVED"].strip()
  if target.adv_blockers is None and env.get("BLOCKERS", "").strip():
    target.adv_blockers = env["BLOCKERS"].strip()
  if not target.adv_transcript and env.get("TRANSCRIPT", "").strip():
    target.adv_transcript = env["TRANSCRIPT"].strip()
  if not target.regression_cmd and env.get("REGRESSION_CMD", "").strip():
    target.regression_cmd = env["REGRESSION_CMD"].strip()
  if not target.subproject and env.get("SUBPROJECT", "").strip():
    target.subproject = env["SUBPROJECT"].strip()
  if not target.tree_prefix and env.get("PATH_PREFIX", "").strip():
    target.tree_prefix = env["PATH_PREFIX"].strip()
  if (
      target.tree_approved_limit is None
      and env.get("TREE_LIMIT", "").strip()
  ):
    raw_limit = env["TREE_LIMIT"].strip()
    if not raw_limit.isascii() or not raw_limit.isdigit() or int(raw_limit) <= 0:
      raise ReflexArgumentError("TREE_LIMIT must be a positive integer.")
    target.tree_approved_limit = int(raw_limit)
  if not target.tree_no_limit and _env_truthy("NO_LIMIT", environ=env):
    target.tree_no_limit = True
  if not target.tree_count_only and _env_truthy("COUNT_ONLY", environ=env):
    target.tree_count_only = True


def hydrate_parking_from_env(
    *,
    pr: int = 0,
    branch: str = "",
    reason: str = "",
    cron: str = "",
    environ: Optional[Mapping[str, str]] = None,
) -> Tuple[int, str, str, str]:
  """Hydrates unset park-session and resume-parked options from MF_PARK_* env vars.

  Args:
    pr: Parsed CLI pull request number (0 when unset).
    branch: Parsed CLI head branch override.
    reason: Parsed CLI parking reason.
    cron: Parsed CLI polling cron expression.
    environ: Optional environment mapping. Defaults to ``os.environ``.

  Returns:
    Tuple ``(pr, branch, reason, cron)`` hydrated from recipe-scoped
    ``MF_PARK_*`` environment variables when CLI values are unset.

  Raises:
    ReflexArgumentError: When ``MF_PARK_PR`` is non-empty and not a
      non-negative ASCII integer.
  """
  env = os.environ if environ is None else environ
  if not pr:
    raw_pr = env.get("MF_PARK_PR", "").strip()
    if raw_pr:
      if not raw_pr.isascii() or not raw_pr.isdigit():
        raise ReflexArgumentError("PR must be a non-negative integer.")
      pr = int(raw_pr)
  if not branch and env.get("MF_PARK_BRANCH", "").strip():
    branch = env["MF_PARK_BRANCH"].strip()
  if not reason and env.get("MF_PARK_REASON", "").strip():
    reason = env["MF_PARK_REASON"].strip()
  if not cron and env.get("MF_PARK_CRON", "").strip():
    cron = env["MF_PARK_CRON"].strip()
  return pr, branch, reason, cron


def parse_workflow_gates_args(
    target: Any, raw_args: List[str], default_repo_root: str
) -> None:
  """Parses CLI arguments and populates gate fields on target.

  Args:
    target: Mutable runner instance (such as ``WorkflowGatesScript``) receiving
      parsed attributes.
    raw_args: Raw CLI argument list excluding program name.
    default_repo_root: Default value for ``--repo-root``.

  Returns:
    ``None`` after populating validated CLI and Make environment fields on
    ``target``.

  Raises:
    ReflexArgumentError: When required subcommands, positive integer flags, or
      mutually exclusive ``repo-tree`` flags fail validation.
  """
  parser = build_workflow_gates_parser(default_repo_root)
  ns = parser.parse_args(raw_args)
  if not ns.gate:
    parser.error(
        "Specify a gate subcommand: init-makefile, plugin-files, skill-budget, "
        "intake, check-plan, approved-tree, commit-msg, post-commit, main-sync, "
        "verify-regression, check-regression, verify-trial-merge, repo-tree, promote-review, "
        "park-session, resume-parked, terminal-title, release-intake, "
        "release-lock, read-init-d, or advance."
    )

  target.repo_root = os.path.abspath(ns.repo_root)
  target.lock_dir = getattr(ns, "sub_lock_dir", None) or getattr(ns, "lock_dir", None)
  target.as_json = bool(ns.as_json or getattr(ns, "sub_json", False))
  target.make_env = bool(
      getattr(ns, "make_env", False) or getattr(ns, "sub_make_env", False)
  )
  target.gate = ns.gate
  if ns.gate == "skill-budget":
    if ns.max_bytes <= 0:
      parser.error("--max-bytes must be a positive integer.")
    target.max_bytes = ns.max_bytes
  elif ns.gate in ("commit-msg", "post-commit"):
    target.commit_msg_file = ns.commit_msg_file
  elif ns.gate == "main-sync":
    target.no_fetch = bool(getattr(ns, "no_fetch", False))
    target.fail_on_behind = bool(getattr(ns, "fail_on_behind", False))
    target.allow_conflict = bool(getattr(ns, "allow_conflict", False))
  elif ns.gate in (
      "verify-regression",
      "check-regression",
      "verify-trial-merge",
  ):
    target.regression_cmd = str(getattr(ns, "regression_cmd", "") or "").strip()
    raw_sub = getattr(ns, "subproject", None)
    target.subproject = str(raw_sub).strip() if raw_sub is not None else ""
    target.regression_timeout = getattr(ns, "timeout", None)
  elif ns.gate == "repo-tree":
    if ns.tree_threshold <= 0:
      parser.error("--threshold must be a positive integer.")
    if ns.tree_approved_limit is not None and ns.tree_approved_limit <= 0:
      parser.error("--approved-limit must be a positive integer.")
    target.tree_prefix = str(getattr(ns, "tree_prefix", "") or "").strip()
    target.tree_approved_limit = ns.tree_approved_limit
    target.tree_count_only = bool(getattr(ns, "tree_count_only", False))
    target.tree_no_limit = bool(getattr(ns, "tree_no_limit", False))
    target.tree_threshold = ns.tree_threshold
  elif ns.gate == "promote-review":
    target.review_round = str(getattr(ns, "review_round", "") or "").strip()
  elif ns.gate in ("park-session", "resume-parked"):
    from_env = bool(getattr(ns, "park_from_env", False))
    pr = int(getattr(ns, "park_pr", 0) or 0)
    pr_url = str(getattr(ns, "park_pr_url", "") or "").strip()
    branch = str(getattr(ns, "park_branch", "") or "").strip()
    reason = str(getattr(ns, "park_reason", "") or "").strip()
    ci_status = str(getattr(ns, "park_ci_status", "") or "").strip()
    review_decision = str(
        getattr(ns, "park_review_decision", "") or ""
    ).strip()
    raw_approvals = getattr(ns, "park_approvals", None)
    raw_req_approvals = getattr(ns, "park_required_approvals", None)
    raw_threads = getattr(ns, "park_unresolved_threads", None)
    approvals = int(raw_approvals) if raw_approvals is not None else 0
    required_approvals = (
        int(raw_req_approvals) if raw_req_approvals is not None else 0
    )
    require_codeowners = bool(getattr(ns, "park_require_codeowners", False))
    pending_reviewers = str(
        getattr(ns, "park_pending_reviewers", "") or ""
    ).strip()
    changes_requested_by = str(
        getattr(ns, "park_changes_requested_by", "") or ""
    ).strip()
    unresolved_threads = int(raw_threads) if raw_threads is not None else 0
    cron = str(getattr(ns, "park_cron", "") or "").strip()
    has_explicit_status = any((
        bool(pr_url),
        bool(ci_status),
        bool(review_decision),
        raw_approvals is not None,
        raw_req_approvals is not None,
        require_codeowners,
        bool(pending_reviewers),
        bool(changes_requested_by),
        raw_threads is not None,
    ))
    if from_env:
      pr, branch, reason, cron = hydrate_parking_from_env(
          pr=pr, branch=branch, reason=reason, cron=cron
      )
    if ns.gate == "park-session":
      target.park_request = ParkSessionRequest(
          repo_root=target.repo_root,
          pr=pr,
          pr_url=pr_url,
          branch=branch,
          reason=reason,
          ci_status=ci_status,
          review_decision=review_decision,
          approvals=approvals,
          required_approvals=required_approvals,
          require_codeowners=require_codeowners,
          pending_reviewers=pending_reviewers,
          changes_requested_by=changes_requested_by,
          unresolved_threads=unresolved_threads,
          cron=cron,
          has_explicit_status=has_explicit_status,
      )
    else:
      target.resume_request = ResumeParkedRequest(
          repo_root=target.repo_root,
          pr=pr,
          branch=branch,
      )
  elif ns.gate == "read-init-d":
    target.adv_from = str(getattr(ns, "adv_from", "") or "").strip()
  elif ns.gate == "advance":
    target.adv_from = str(getattr(ns, "adv_from", "") or "").strip()
    target.adv_to = str(getattr(ns, "adv_to", "") or "").strip()
    target.adv_approved = getattr(ns, "adv_approved", None)
    target.adv_blockers = getattr(ns, "adv_blockers", None)
    target.allow_main_conflict = bool(getattr(ns, "allow_main_conflict", False))
    target.no_fetch = bool(getattr(ns, "no_fetch", False))
    target.adv_transcript = str(getattr(ns, "adv_transcript", "") or "").strip()
    target.adv_complete = bool(getattr(ns, "adv_complete", False))
    target.subproject = ""
  elif ns.gate in ("terminal-title", "intake", "release-intake", "release-lock"):
    target.ticket = getattr(ns, "ticket", None)
    target.directory = getattr(ns, "directory", None)
    target.title = getattr(ns, "title", None)
    target.issue_tag = getattr(ns, "issue_tag", None)
    target.allow_blocked = bool(getattr(ns, "allow_blocked", False))

  if target.make_env:
    hydrate_from_make_env(target)

  if target.gate == "repo-tree":
    if target.tree_approved_limit is not None and target.tree_no_limit:
      parser.error("--approved-limit and --no-limit cannot be used together.")
    if target.tree_count_only and (
        target.tree_approved_limit is not None or target.tree_no_limit
    ):
      parser.error(
          "--count-only cannot be combined with --approved-limit or --no-limit."
      )
