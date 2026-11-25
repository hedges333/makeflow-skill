"""Black-box harness boundary guard for Makeflow dogfooding (GH-222, SCAR-PROC-124).

Enforces that during post-plan execution (T30 through T90), the Depth-0
supervisor treats Makeflow's own governance, hook, gate, and runtime lock
internals as a black box unless a repository source file is explicitly in scope
via `.workflow/state/20_plan.md` or the active Git diff.
"""

import io
import json
import os
import re
import subprocess
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Sequence, Set, Tuple

from lib.config import is_ticket_active
from lib.reflex import emit_reflex
from lib.state_machine import infer_current_target
from lib.turn_boundary import read_active_subagent

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAKEFLOW_REPO_ROOT_REAL = os.path.realpath(_REPO_ROOT)
MAKEFLOW_REPO_MARKERS: Tuple[str, ...] = (
    "hooks.json",
    os.path.join("skills", "makeflow", "SKILL.md"),
    os.path.join("rules", "AGENTS.md"),
)

PLUGIN_CACHE_RAW_PREFIXES: Tuple[str, ...] = (
    "~/.gemini/config/plugins",
    "~/.gemini/plugins",
    "~/.claude/plugins",
)
KNOWN_PLUGIN_CACHE_PREFIXES: Tuple[str, ...] = tuple(
    os.path.abspath(os.path.expanduser(p)) for p in PLUGIN_CACHE_RAW_PREFIXES
)

PRE_IMPLEMENTATION_TARGETS: FrozenSet[str] = frozenset({
    "T00",
    "T10",
    "T15",
    "T20",
})

POST_PLAN_TARGETS: FrozenSet[str] = frozenset({
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
})

RUNTIME_LOCK_STATE_RELPATHS: FrozenSet[str] = frozenset({
    ".workflow/state/gate_entry.json",
    ".workflow/state/active_target.json",
    ".workflow/state/active_subagent.json",
    ".workflow/state/Makefile",
})

_RUNTIME_LOCK_BASENAMES: FrozenSet[str] = frozenset(
    os.path.basename(rel) for rel in RUNTIME_LOCK_STATE_RELPATHS
)

HARNESS_INTERNAL_RELPATHS: FrozenSet[str] = frozenset({
    "hooks.json",
    "default_config.json",
    "mcp/server.py",
    "mcp_config.json",
    "scripts/tdd_guard.py",
    "scripts/pre_tool_guard.py",
    "scripts/workflow_gates.py",
    "scripts/emit_reflex.py",
    "scripts/commit_msg.py",
    "scripts/session_start.py",
    "scripts/workspace_mutex.py",
    "lib/base_script.py",
    "lib/git_index.py",
    "lib/git_safety.py",
    "lib/pre_tool_guard.py",
    "lib/regression_gate.py",
    "lib/tdd_guard.py",
    "lib/workflow_gates.py",
    "lib/main_sync.py",
    "lib/state_makefile.py",
    "lib/plugin_gates.py",
    "lib/intake_gates.py",
    "lib/commit_gates.py",
    "lib/parking_gates.py",
    "lib/workflow_gates_cli.py",
    "lib/state_machine.py",
    "lib/reflex.py",
    "lib/config.py",
    "lib/shell_safety.py",
    "lib/target90_guard.py",
    "lib/turn_boundary.py",
    "lib/harness_boundary.py",
    "lib/commit_msg.py",
    "lib/session_start.py",
    "lib/workspace_mutex.py",
    "lib/review_promotion.py",
    "lib/ticket_state.py",
    "lib/repo_tree.py",
    "lib/safe_copy.py",
    "lib/signal_trap.py",
    "lib/parking_state.py",
})

_TRAILING_BOUNDARY_PATTERN = r"(?![A-Za-z0-9_/\-]|\.[A-Za-z0-9_])"
_LEADING_REL_BOUNDARY_PATTERN = r"(?:^|(?<=[^A-Za-z0-9_./\-]))(?:\./)?"
_BASE_BRANCH_REFS: Tuple[str, ...] = (
    "main",
    "origin/main",
    "master",
    "origin/master",
)


def _plugin_cache_prefixes() -> Set[str]:
  """Returns absolute and realpath prefixes for known Makeflow plugin caches.

  Returns:
    Set of expanded and realpath-resolved plugin cache root directories.
  """
  prefixes = set(KNOWN_PLUGIN_CACHE_PREFIXES)
  for raw_pref in PLUGIN_CACHE_RAW_PREFIXES:
    prefixes.add(os.path.abspath(os.path.expanduser(raw_pref)))
  return prefixes | {os.path.realpath(p) for p in prefixes}


def is_plugin_cache_path(abs_target: str) -> bool:
  """Checks whether abs_target or its realpath lies inside a plugin cache root.

  Args:
    abs_target: Absolute target path to inspect.

  Returns:
    True if ``abs_target`` or its resolved realpath falls under a known plugin
    cache directory, otherwise False.
  """
  if not abs_target or not isinstance(abs_target, str):
    return False
  all_prefixes = _plugin_cache_prefixes()
  for cand in (abs_target, os.path.realpath(abs_target)):
    if any(
        cand == pref or cand.startswith(pref + os.sep) for pref in all_prefixes
    ):
      return True
  return False


def find_makeflow_plugin_root(abs_target: str) -> Optional[str]:
  """Returns the enclosing Makeflow plugin root directory for abs_target.

  Recognizes Makeflow's own repository root, directories containing co-located
  Makeflow repository markers, and directories containing a Makeflow
  ``plugin.json`` manifest.

  Args:
    abs_target: Target path to resolve upward.

  Returns:
    Absolute path to the enclosing Makeflow plugin root directory, or None if
    no Makeflow root encloses ``abs_target``.
  """
  if not abs_target or not isinstance(abs_target, str):
    return None
  curr = os.path.dirname(os.path.abspath(abs_target))
  while curr and curr != os.path.dirname(curr):
    if os.path.realpath(curr) == MAKEFLOW_REPO_ROOT_REAL:
      return curr
    if all(
        os.path.isfile(os.path.join(curr, m)) for m in MAKEFLOW_REPO_MARKERS
    ):
      return curr
    pj = os.path.join(curr, "plugin.json")
    if os.path.isfile(pj):
      try:
        with open(pj, "r", encoding="utf-8") as f:
          data = json.load(f)
        if isinstance(data, dict) and data.get("name") == "makeflow":
          return curr
      except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        pass
    curr = os.path.dirname(curr)
  return None


def _is_makeflow_plugin_dir_name(name: str) -> bool:
  """Checks whether a plugin cache directory segment identifies Makeflow.

  Args:
    name: First directory segment beneath a plugin cache root.

  Returns:
    True when ``name`` equals ``makeflow`` or starts with ``makeflow-`` or
    ``makeflow_`` (case-insensitive), otherwise False.
  """
  lower = name.lower()
  return (
      lower == "makeflow"
      or lower.startswith("makeflow-")
      or lower.startswith("makeflow_")
  )


def _extract_plugin_cache_relpath(cand: str) -> str:
  """Extracts the plugin-relative POSIX path from an installed cache path.

  Args:
    cand: Absolute or realpath-resolved file path inside a plugin cache root.

  Returns:
    POSIX relative path inside the Makeflow plugin directory, or empty string
    if ``cand`` does not belong to Makeflow.
  """
  plugin_root = find_makeflow_plugin_root(cand)
  if plugin_root is not None:
    return os.path.relpath(cand, plugin_root).replace(os.sep, "/")
  for pref in sorted(_plugin_cache_prefixes(), key=len, reverse=True):
    if cand.startswith(pref + os.sep):
      tail = cand[len(pref) + 1 :].replace(os.sep, "/")
      parts = tail.split("/", 1)
      if len(parts) == 2 and _is_makeflow_plugin_dir_name(parts[0]):
        return parts[1]
  return ""


def _resolve_target_candidates(
    raw_path: str, workspace_paths: Optional[Sequence[str]]
) -> List[str]:
  """Resolves deduplicated absolute and realpath candidates for a target path.

  Args:
    raw_path: Raw target path argument from a ``read_file`` tool call.
    workspace_paths: Optional workspace root paths for resolving relative paths.

  Returns:
    Deduplicated list of normalized absolute and realpath strings.
  """
  expanded = os.path.expanduser(raw_path)
  non_empty_roots = [
      ws.strip()
      for ws in (workspace_paths or ())
      if isinstance(ws, str) and ws.strip()
  ]
  if os.path.isabs(expanded):
    abs_list = [os.path.abspath(expanded)]
  elif non_empty_roots:
    abs_list = [
        os.path.abspath(os.path.join(os.path.expanduser(ws), raw_path))
        for ws in non_empty_roots
    ]
  else:
    abs_list = [os.path.abspath(expanded)]

  seen: Set[str] = set()
  candidates: List[str] = []
  for abs_path in abs_list:
    for cand in (abs_path, os.path.realpath(abs_path)):
      if cand not in seen:
        seen.add(cand)
        candidates.append(cand)
  return candidates


def _resolve_guarded_target(state_dir: str) -> Optional[str]:
  """Returns the active guarded target ID when in T30-T90, otherwise None.

  Args:
    state_dir: Resolved workflow state directory path.

  Returns:
    Canonical target ID in ``POST_PLAN_TARGETS`` when the session is active
    and post-plan, or None for pre-implementation, inactive, or completed
    sessions.
  """
  if not state_dir or not os.path.isdir(state_dir):
    return None
  na_path = os.path.join(state_dir, "99_next_action.md")
  if os.path.isfile(na_path) and not is_ticket_active(state_dir):
    return None
  target = infer_current_target(state_dir)
  if target in POST_PLAN_TARGETS:
    return target
  return None


def _is_child_subagent_caller(
    payload: Mapping[str, Any], state_dir: str
) -> bool:
  """Returns True only when the caller is a verified active child subagent.

  Requires ``active_subagent.json`` to exist with a non-empty
  ``supervisorConversationId`` distinct from the caller's non-empty
  ``conversationId``.

  Args:
    payload: PreToolUse hook payload dictionary.
    state_dir: Resolved workflow state directory path.

  Returns:
    True if the caller is a child subagent, otherwise False.
  """
  active = read_active_subagent(state_dir)
  if active is None:
    return False
  caller_cid = str(payload.get("conversationId") or "").strip()
  sup_cid = str(active.get("supervisorConversationId") or "").strip()
  return bool(caller_cid and sup_cid and caller_cid != sup_cid)


def _match_runtime_lock_relpath(
    cand: str, state_dir: str
) -> Optional[str]:
  """Matches cand against Makeflow internal runtime lock state files.

  Args:
    cand: Normalized absolute or realpath candidate string.
    state_dir: Active workflow state directory path.

  Returns:
    Matched canonical ``.workflow/state/<name>`` relative path, or None.
  """
  norm = cand.replace("\\", "/").rstrip("/")
  for rel in RUNTIME_LOCK_STATE_RELPATHS:
    if norm == rel or norm.endswith("/" + rel):
      return rel
  if state_dir:
    real_state_dir = os.path.realpath(state_dir)
    real_cand = os.path.realpath(cand)
    if os.path.dirname(real_cand) == real_state_dir:
      base = os.path.basename(real_cand)
      if base in _RUNTIME_LOCK_BASENAMES:
        return f".workflow/state/{base}"
  return None


def plan_mentions_relpath(
    plan_text: str, rel_path: str, plugin_root: Optional[str] = None
) -> bool:
  """Checks whether plan_text explicitly references rel_path at path boundaries.

  Matches backticked paths, ``./`` prefixed paths, markdown table cells, and
  ``file://`` or absolute repository paths while rejecting substring traps such
  as ``other/lib/config.py`` or ``lib/config.py.bak``.

  Args:
    plan_text: Raw UTF-8 text of ``.workflow/state/20_plan.md``.
    rel_path: POSIX repository-relative path to search for.
    plugin_root: Optional Makeflow repository root for exact absolute and
      ``file://`` URL matching.

  Returns:
    True when ``plan_text`` explicitly references ``rel_path``, else False.
  """
  if not plan_text or not rel_path:
    return False
  norm_rel = rel_path.replace("\\", "/")
  while norm_rel.startswith("./"):
    norm_rel = norm_rel[2:]
  norm_rel = norm_rel.lstrip("/")
  if not norm_rel:
    return False
  escaped_rel = re.escape(norm_rel)

  rel_pattern = re.compile(
      _LEADING_REL_BOUNDARY_PATTERN + escaped_rel + _TRAILING_BOUNDARY_PATTERN
  )
  if rel_pattern.search(plan_text):
    return True

  if plugin_root:
    roots = {
        os.path.abspath(plugin_root).replace("\\", "/").rstrip("/"),
        os.path.realpath(plugin_root).replace("\\", "/").rstrip("/"),
    }
    for root in roots:
      abs_posix = f"{root}/{norm_rel}"
      abs_pat = re.compile(
          r"(?:^|(?<=[^A-Za-z0-9_./\-]))(?:file://)?"
          + re.escape(abs_posix)
          + _TRAILING_BOUNDARY_PATTERN
      )
      if abs_pat.search(plan_text):
        return True
  else:
    file_url_pat = re.compile(
        r"file://[^\s`'\"<>|)]+/" + escaped_rel + _TRAILING_BOUNDARY_PATTERN
    )
    if file_url_pat.search(plan_text):
      return True

  return False


def _is_listed_in_plan(
    state_dir: str, rel_path: str, plugin_root: str
) -> bool:
  """Returns True when rel_path is explicitly listed in state_dir/20_plan.md.

  Args:
    state_dir: Active workflow state directory path.
    rel_path: POSIX repository-relative file path.
    plugin_root: Makeflow repository root directory path.

  Returns:
    True if ``20_plan.md`` exists and explicitly references ``rel_path``.
  """
  plan_path = os.path.join(state_dir, "20_plan.md")
  if not os.path.isfile(plan_path):
    return False
  try:
    with open(plan_path, "r", encoding="utf-8", errors="replace") as f:
      plan_text = f.read()
  except OSError:
    return False
  return plan_mentions_relpath(plan_text, rel_path, plugin_root=plugin_root)


def _run_git_name_lines(
    plugin_root: str, *git_args: str
) -> Optional[List[str]]:
  """Runs a read-only Git command with lockless options and returns output lines.

  Args:
    plugin_root: Git repository root directory.
    *git_args: Subcommand arguments passed after Git global flags.

  Returns:
    Normalized POSIX relative paths from stdout when exit code is 0, or None
    when the Git command fails or times out.
  """
  try:
    proc = subprocess.run(
        [
            "git",
            "--no-optional-locks",
            "-c",
            "core.quotepath=false",
            *git_args,
        ],
        cwd=plugin_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=2,
    )
  except (OSError, subprocess.SubprocessError):
    return None
  if proc.returncode != 0:
    return None
  return [
      line.strip().replace("\\", "/")
      for line in (proc.stdout or "").splitlines()
      if line.strip()
  ]


def _collect_git_in_scope_files(plugin_root: str) -> FrozenSet[str]:
  """Collects unstaged, staged, untracked, and base-branch diff modified files.

  Args:
    plugin_root: Makeflow Git repository root directory.

  Returns:
    Frozenset of repository-relative POSIX paths currently modified in scope.
  """
  if not plugin_root or not os.path.exists(os.path.join(plugin_root, ".git")):
    return frozenset()
  files: Set[str] = set()
  for git_args in (
      ("diff", "--name-only"),
      ("diff", "--name-only", "--cached"),
      ("ls-files", "--others", "--exclude-standard"),
  ):
    lines = _run_git_name_lines(plugin_root, *git_args)
    if lines:
      files.update(lines)
  for base_ref in _BASE_BRANCH_REFS:
    branch_lines = _run_git_name_lines(
        plugin_root, "diff", "--name-only", f"{base_ref}...HEAD"
    )
    if branch_lines is not None:
      files.update(branch_lines)
      break
  return frozenset(files)


def _format_harness_boundary_reflex(
    blocked_target: str,
    active_target: str,
    category: str,
) -> str:
  """Formats a SCAR-PROC-124 Vector 4 Reflex message for a denied read.

  Args:
    blocked_target: Display path of the blocked governance or lock file.
    active_target: Active Makeflow target ID (for example ``"T30"``).
    category: Violation category (``"runtime_lock"``, ``"plugin_cache"``, or
      ``"governance_source"``).

  Returns:
    Formatted multi-line Vector 4 Reflex string including Socratic Probe.
  """
  if category == "runtime_lock":
    trigger = (
        f"Depth-0 supervisor attempted to read internal runtime lock file"
        f" '{blocked_target}' during active target {active_target}."
    )
    rejected = (
        "Inspecting live Makeflow runtime lock state during post-plan"
        " execution breaks the black-box harness boundary (SCAR-PROC-124)."
    )
  elif category == "plugin_cache":
    trigger = (
        f"Depth-0 supervisor attempted to read installed Makeflow plugin file"
        f" '{blocked_target}' during active target {active_target}."
    )
    rejected = (
        "Inspecting installed Makeflow hook or gate source files during"
        " post-plan execution breaks the black-box harness boundary"
        " (SCAR-PROC-124)."
    )
  else:
    trigger = (
        f"Depth-0 supervisor attempted to read out-of-scope Makeflow governance"
        f" file '{blocked_target}' during active target {active_target}"
        " (absent from 20_plan.md and active Git diff)."
    )
    rejected = (
        "Reading out-of-scope Makeflow guard, hook, or gate implementation"
        " files while dogfooding instead of treating the workflow harness as"
        " a black box (SCAR-PROC-124)."
    )

  reflex = (
      "Run `make -f .workflow/state/Makefile` targets directly, follow any"
      " Vector 4 Reflex directive emitted on exit code 2, and consult"
      " skills/makeflow/SKILL.md or skills/makeflow/references/ instead of"
      " inspecting harness internals. If blocked with no documented path,"
      " stop tool calls and ask the user."
  )
  buf = io.StringIO()
  emit_reflex(
      stream=buf,
      scar_id="SCAR-PROC-124",
      violation_code="OUT_OF_SCOPE_HARNESS_INSPECTION",
      trigger=trigger,
      rejected=rejected,
      reflex=reflex,
  )
  return buf.getvalue().strip()


def evaluate_harness_read_boundary(
    payload: Mapping[str, Any],
    state_dir: str,
    workspace_paths: Optional[Sequence[str]] = None,
) -> Tuple[bool, str]:
  """Evaluates a PreToolUse read_file call against the Black-Box Harness Boundary.

  Enforces SCAR-PROC-124 during T30 through T90 for the Depth-0 supervisor:
  1. Allows pre-implementation targets (T00-T20), inactive/completed sessions,
     and verified child subagents.
  2. Unconditionally denies reads of live runtime lock files
     (``gate_entry.json``, ``active_target.json``, ``active_subagent.json``,
     ``.workflow/state/Makefile``).
  3. Denies reads of installed plugin cache files in
     ``HARNESS_INTERNAL_RELPATHS``.
  4. Inside a Makeflow repository checkout, denies reads of
     ``HARNESS_INTERNAL_RELPATHS`` unless explicitly listed in ``20_plan.md``
     or modified in the active Git diff.
  5. Leaves external consumer repositories and always-readable Makeflow docs,
     Memento state cards, tests, and agent prompts unblocked.

  Args:
    payload: Parsed PreToolUse JSON payload dictionary.
    state_dir: Resolved workflow state directory path.
    workspace_paths: Optional sequence of workspace root directories.

  Returns:
    Tuple of ``(allowed, deny_reason)`` where ``deny_reason`` is empty when
    allowed and contains a formatted ``SCAR-PROC-124`` Vector 4 Reflex when
    denied.
  """
  if not isinstance(payload, Mapping):
    return True, ""

  raw_tc = payload.get("toolCall")
  tool_call = raw_tc if isinstance(raw_tc, Mapping) else {}
  if str(tool_call.get("name") or "").strip() != "read_file":
    return True, ""

  raw_args = tool_call.get("args")
  args = raw_args if isinstance(raw_args, Mapping) else {}
  raw_path = str(
      args.get("Path") or args.get("file_path") or args.get("path") or ""
  ).strip()
  if not raw_path:
    return True, ""

  active_target = _resolve_guarded_target(state_dir)
  if active_target is None:
    return True, ""

  if _is_child_subagent_caller(payload, state_dir):
    return True, ""

  eff_workspaces = (
      workspace_paths
      if workspace_paths is not None
      else payload.get("workspacePaths")
  )
  candidates = _resolve_target_candidates(raw_path, eff_workspaces)

  # 1. Unconditional protection for live runtime lock files during T30-T90
  for cand in candidates:
    lock_rel = _match_runtime_lock_relpath(cand, state_dir)
    if lock_rel is not None:
      return (
          False,
          _format_harness_boundary_reflex(
              lock_rel, active_target, category="runtime_lock"
          ),
      )

  # 2. Installed plugin cache governance/hook source files (never in-scope)
  for cand in candidates:
    if is_plugin_cache_path(cand):
      cache_rel = _extract_plugin_cache_relpath(cand)
      if cache_rel in HARNESS_INTERNAL_RELPATHS:
        return (
            False,
            _format_harness_boundary_reflex(
                cache_rel, active_target, category="plugin_cache"
            ),
        )

  # 3. Dogfooding Makeflow checkout governance/hook files unless in-scope
  git_scope_cache: Dict[str, FrozenSet[str]] = {}
  for cand in candidates:
    if is_plugin_cache_path(cand):
      continue
    plugin_root = find_makeflow_plugin_root(cand)
    if plugin_root is None:
      continue
    rel_to_plugin = os.path.relpath(cand, plugin_root).replace(os.sep, "/")
    if rel_to_plugin not in HARNESS_INTERNAL_RELPATHS:
      continue
    if _is_listed_in_plan(state_dir, rel_to_plugin, plugin_root):
      continue
    real_root = os.path.realpath(plugin_root)
    if real_root not in git_scope_cache:
      git_scope_cache[real_root] = _collect_git_in_scope_files(plugin_root)
    if rel_to_plugin in git_scope_cache[real_root]:
      continue
    return (
        False,
        _format_harness_boundary_reflex(
            rel_to_plugin, active_target, category="governance_source"
        ),
    )

  return True, ""
