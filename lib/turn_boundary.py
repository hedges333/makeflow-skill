"""Declarative target configuration, turn-boundary guard, and subagent halt airlock (GH-186, GH-220).

Enforces:
1. Pause gates (T20, T42, T45, T50, T60, T75) and pause-gated shell commands
   (git commit, git push, gh pr merge) require an intervening human USER_INPUT
   turn (SCAR-PROC-102).
2. Deterministic supervisory halt airlock (SCAR-PROC-95) blocking concurrent
   Depth-0 supervisor tool calls while Makeflow phase subagents execute.
"""

import io
import json
import os
import time

from lib.active_state import ActiveStateStore
from lib.reflex import emit_reflex
from lib.shell_safety import (
    ENV_ASSIGN_RE,
    _expand_shell_subcommands,
    _extract_redirection_targets,
    _extract_shell_substitutions,
    _parse_git_subcommand,
    _preprocess_shell,
    _split_git_args,
    _tokenize_subcommand,
    _unwrap_command_tokens,
)

GATE_ENTRY_FILENAME = "gate_entry.json"
ACTIVE_SUBAGENT_FILENAME = "active_subagent.json"
MAKEFLOW_PHASE_WORKERS = frozenset({
    "archaeology-worker",
    "tdd-worker",
    "adversarial-critic",
    "triage-worker",
})
SUPERVISOR_INSPECTION_TOOLS = frozenset({
    "read_file",
    "manage_subagents",
    "ask_question",
    "call_mcp_tool",
    "run_command",
})

_MAKE_GOAL_TO_TARGET = {
    "ticket-assigned": "T10",
    "archaeology-verified": "T15",
    "approved-plan": "T20",
    "implementation-diff": "T30",
    "adversarial-review": "T40",
    "triaged-blockers": "T42",
    "approved-review": "T45",
    "commit": "T50",
    "push": "T60",
    "bot-quiescence": "T70",
    "ready-to-land": "T75",
    "land": "T80",
    "codified-scars-consolidated": "T90",
    "finish": "T90",
}
_TARGET_TO_MAKE_GOAL = {
    tid: goal
    for goal, tid in _MAKE_GOAL_TO_TARGET.items()
    if goal != "finish"
}
_GOAL_OR_INIT_TO_TARGET = {"read-init-d": "T00", **_MAKE_GOAL_TO_TARGET}

_SHELL_TOOL_NAMES = frozenset({"run_command", "Bash", "bash", "shell"})

DEFAULT_TARGETS_CONFIG = {
    "T00": {
        "next": [
            "T10",
            "T15",
            "T20",
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
        ],
        "recoveryNext": [],
        "pauseGate": False,
        "evidenceKey": None,
        "entryCards": [],
        "gatedCommands": [],
    },
    "T10": {
        "next": ["T15"],
        "recoveryNext": [],
        "pauseGate": False,
        "evidenceKey": None,
        "entryCards": [],
        "gatedCommands": [],
    },
    "T15": {
        "next": ["T20"],
        "recoveryNext": [],
        "pauseGate": False,
        "evidenceKey": None,
        "entryCards": [],
        "gatedCommands": [],
    },
    "T20": {
        "next": ["T30"],
        "recoveryNext": ["T15"],
        "pauseGate": True,
        "evidenceKey": "humanPlanApproved",
        "entryCards": ["20_plan.md"],
        "gatedCommands": [],
    },
    "T30": {
        "next": ["T40"],
        "recoveryNext": ["T20", "T15"],
        "pauseGate": False,
        "evidenceKey": None,
        "entryCards": [],
        "gatedCommands": [],
    },
    "T40": {
        "next": ["T42", "T45"],
        "recoveryNext": [],
        "pauseGate": False,
        "evidenceKey": None,
        "entryCards": [],
        "gatedCommands": [],
    },
    "T42": {
        "next": ["T30"],
        "recoveryNext": ["T20", "T15"],
        "pauseGate": True,
        "evidenceKey": "humanBlockerTriageApproved",
        "entryCards": ["40_critic_review.md"],
        "gatedCommands": [],
    },
    "T45": {
        "next": ["T50"],
        "recoveryNext": ["T30"],
        "pauseGate": True,
        "evidenceKey": "humanReviewApproved",
        "entryCards": ["40_critic_review.md"],
        "gatedCommands": [],
    },
    "T50": {
        "next": ["T60", "T70"],
        "recoveryNext": ["T30"],
        "pauseGate": True,
        "evidenceKey": "humanCommitApproved",
        "entryCards": ["50_commit_msg.txt"],
        "gatedCommands": ["git commit"],
    },
    "T60": {
        "next": ["T70"],
        "recoveryNext": ["T40", "T30"],
        "pauseGate": True,
        "evidenceKey": "humanPushApproved",
        "entryCards": [],
        "gatedCommands": ["git push"],
    },
    "T70": {
        "next": ["T75"],
        "recoveryNext": ["T30"],
        "pauseGate": False,
        "evidenceKey": None,
        "entryCards": [],
        "gatedCommands": [],
    },
    "T75": {
        "next": ["T80"],
        "recoveryNext": ["T30", "T40", "T70"],
        "pauseGate": True,
        "evidenceKey": "humanLandingApproved",
        "entryCards": ["70_bot_triage.md"],
        "gatedCommands": ["gh pr merge"],
    },
    "T80": {
        "next": ["T90"],
        "recoveryNext": ["T30", "T40", "T60", "T70"],
        "pauseGate": False,
        "evidenceKey": None,
        "entryCards": [],
        "gatedCommands": [],
    },
    "T90": {
        "next": [],
        "recoveryNext": [],
        "pauseGate": False,
        "evidenceKey": None,
        "entryCards": [],
        "gatedCommands": [],
    },
}


def load_targets_config():
  """Loads declarative target config from default_config.json with fallback."""
  cfg_path = os.path.join(
      os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
      "default_config.json",
  )
  if os.path.isfile(cfg_path):
    try:
      with open(cfg_path, "r", encoding="utf-8") as f:
        data = json.load(f)
      if isinstance(data, dict) and isinstance(data.get("targets"), dict):
        return data["targets"]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
      pass
  return DEFAULT_TARGETS_CONFIG


TARGETS_CONFIG = load_targets_config()

PAUSE_GATE_TARGETS = frozenset(
    tid for tid, meta in TARGETS_CONFIG.items() if meta.get("pauseGate")
)

PAUSE_GATE_ENTRIES = frozenset(
    (src_t, dst_t)
    for src_t, meta in TARGETS_CONFIG.items()
    for dst_t in list(meta.get("next", ())) + list(meta.get("recoveryNext", ()))
    if dst_t in PAUSE_GATE_TARGETS
)

PAUSE_GATE_EXITS = {
    (tid, next_t): meta["evidenceKey"]
    for tid, meta in TARGETS_CONFIG.items()
    if meta.get("pauseGate") and meta.get("evidenceKey")
    for next_t in meta.get("next", ())
}

GATE_EVIDENCE_KEYS = dict(PAUSE_GATE_EXITS)

VALID_TRANSITIONS = {
    tid: set(meta.get("next", ())) | set(meta.get("recoveryNext", ()))
    for tid, meta in TARGETS_CONFIG.items()
}

_PAUSE_GATE_EXIT_DEST_SOURCES = {}
for _src_t, _dst_t in PAUSE_GATE_EXITS:
  _PAUSE_GATE_EXIT_DEST_SOURCES.setdefault(_dst_t, set()).add(_src_t)

RECOVERY_TRANSITIONS = frozenset(
    (src_t, dst_t)
    for src_t, meta in TARGETS_CONFIG.items()
    for dst_t in meta.get("recoveryNext", ())
)

EXPLICIT_FROM_RECOVERY_TRANSITIONS = frozenset(
    (src_t, dst_t)
    for (src_t, dst_t) in RECOVERY_TRANSITIONS
    if dst_t in _PAUSE_GATE_EXIT_DEST_SOURCES
)

_PAUSE_GATED_COMMAND_TUPLES = frozenset(
    tuple(cmd_spec.split())
    for meta in TARGETS_CONFIG.values()
    if meta.get("pauseGate")
    for cmd_spec in meta.get("gatedCommands", ())
    if isinstance(cmd_spec, str) and cmd_spec.strip()
)


def _unwrap_json_val(val):
  """Unwraps JSON-encoded string values found in compact transcript.jsonl."""
  if isinstance(val, str):
    stripped = val.strip()
    if stripped and stripped[0] in ('"', "{", "["):
      try:
        return json.loads(stripped)
      except (json.JSONDecodeError, ValueError, TypeError):
        return val
  return val


def load_transcript_steps(transcript_path):
  """Loads JSONL steps from transcript_path safely."""
  if not transcript_path or not isinstance(transcript_path, str):
    return []
  if not os.path.isfile(transcript_path):
    return []
  steps = []
  try:
    with open(transcript_path, "r", encoding="utf-8") as f:
      for line in f:
        line_s = line.strip()
        if not line_s:
          continue
        try:
          obj = json.loads(line_s)
          if isinstance(obj, dict):
            steps.append(obj)
        except (json.JSONDecodeError, ValueError, TypeError):
          continue
  except (OSError, UnicodeDecodeError):
    return []
  return steps


def _infer_shell_current_target(
    resolved_state_dir, prior_target="", next_target=""
):
  """Infers currentTarget for a shell advance call when FROM is omitted."""
  if prior_target:
    return prior_target
  if not resolved_state_dir:
    return "T00"

  entry = read_gate_entry(resolved_state_dir)
  if entry and next_target and entry.get("gate") == next_target:
    from_t = str(entry.get("fromTarget") or "").strip()
    if from_t:
      return from_t

  try:
    from lib.state_machine import infer_current_target

    inferred = infer_current_target(resolved_state_dir, next_target=next_target)
    if inferred and inferred != "T00":
      return inferred
  except ImportError:
    infer_current_target = None

  if entry and entry.get("gate") and entry.get("gate") != next_target:
    return str(entry["gate"]).strip()

  if infer_current_target is not None:
    return infer_current_target(resolved_state_dir, next_target=next_target)
  return "T00"


def _resolve_shell_state_dirs(makefile_arg, dir_arg, default_state_dir):
  """Resolves (workspace_dir, state_dir) from make -f / -C flags."""
  raw_mf = (makefile_arg or "").replace("\\", "/")
  raw_dir = (dir_arg or "").replace("\\", "/").rstrip("/")
  mf_norm = os.path.normpath(raw_mf).replace("\\", "/") if raw_mf else ""
  dir_norm = os.path.normpath(raw_dir).replace("\\", "/") if raw_dir else ""
  is_state_mk = (
      mf_norm == ".workflow/state/Makefile"
      or mf_norm.endswith("/.workflow/state/Makefile")
      or (
          mf_norm in ("", ".", "Makefile")
          and (
              dir_norm == ".workflow/state"
              or dir_norm.endswith("/.workflow/state")
          )
      )
  )
  if not is_state_mk:
    return None, None

  if mf_norm == ".workflow/state/Makefile" or mf_norm.endswith(
      "/.workflow/state/Makefile"
  ):
    if os.path.isabs(mf_norm):
      st_dir = os.path.dirname(mf_norm)
      return os.path.dirname(os.path.dirname(st_dir)), st_dir
    if dir_norm:
      st_dir = os.path.normpath(os.path.join(dir_norm, os.path.dirname(mf_norm)))
      return os.path.dirname(os.path.dirname(st_dir)), st_dir
  elif (
      dir_norm == ".workflow/state" or dir_norm.endswith("/.workflow/state")
  ) and os.path.isabs(dir_norm):
    return os.path.dirname(os.path.dirname(dir_norm)), dir_norm

  ws_dir = (
      os.path.dirname(os.path.dirname(os.path.abspath(default_state_dir)))
      if default_state_dir
      else ""
  )
  return ws_dir, default_state_dir


def _parse_make_tokens(rem):
  """Extracts (makefile_arg, dir_arg, make_vars, goals) from make tokens."""
  makefile_arg = ""
  dir_arg = ""
  make_vars = {}
  goals = []
  idx = 0
  while idx < len(rem):
    tok = rem[idx]
    if tok in ("-f", "--file", "--makefile") and idx + 1 < len(rem):
      makefile_arg = rem[idx + 1]
      idx += 2
      continue
    if tok.startswith("-f") and len(tok) > 2:
      makefile_arg = tok[2:]
    elif tok.startswith(("--file=", "--makefile=")):
      makefile_arg = tok.split("=", 1)[1]
    elif tok in ("-C", "--directory") and idx + 1 < len(rem):
      dir_arg = rem[idx + 1]
      idx += 2
      continue
    elif tok.startswith("-C") and len(tok) > 2:
      dir_arg = tok[2:]
    elif tok.startswith("--directory="):
      dir_arg = tok.split("=", 1)[1]
    elif tok in ("-I", "--include-dir", "-W", "--what-if", "-o", "--old-file"):
      idx += 2
      continue
    elif not tok.startswith("-") and "=" in tok:
      key, val = tok.split("=", 1)
      make_vars[key.strip()] = val.strip()
    elif not tok.startswith("-"):
      goals.append(tok.strip())
    idx += 1
  return makefile_arg, dir_arg, make_vars, goals


def _build_shell_adv_record(
    curr_t, next_t, approved_raw, complete, dirs, from_omitted=False
):
  """Builds a normalized advance-call dict from parsed shell parameters.

  Args:
    curr_t: Resolved source target ID (explicit or inferred).
    next_t: Canonical destination target ID.
    approved_raw: Raw ``APPROVED`` / ``--approved`` value from the command.
    complete: True when the command invokes ``finish`` completion.
    dirs: Tuple ``(workspace_dir, state_dir)``.
    from_omitted: True when the shell command omitted explicit ``FROM``/``--from``.

  Returns:
    Normalized advance-call dictionary preserving structural exit metadata.
  """
  ws_dir, st_dir = dirs
  ev = {}
  if complete:
    ev["complete"] = True
  is_approved = str(approved_raw or "").strip().lower() in (
      "1",
      "true",
      "yes",
  )
  gate_key = PAUSE_GATE_EXITS.get((curr_t, next_t))
  if gate_key and is_approved:
    ev[gate_key] = True
  return {
      "currentTarget": curr_t,
      "nextTarget": next_t,
      "evidence": ev,
      "workspaceDir": ws_dir or "",
      "stateDir": st_dir or "",
      "fromOmitted": bool(from_omitted),
      "approved": is_approved,
  }


def _consume_workflow_gates_entry(exe, args):
  """Strips python/module wrapper tokens if invocation targets workflow_gates."""
  base = os.path.basename(exe).lower()
  if base.startswith("python"):
    is_mod = False
    while args and args[0].startswith("-"):
      flag = args.pop(0)
      if flag == "-m":
        is_mod = True
        break
      if flag.startswith("-m") and len(flag) > 2:
        args.insert(0, flag[2:])
        is_mod = True
        break
    if not args:
      return False
    target = args.pop(0).replace("\\", "/")
    if is_mod:
      return target in (
          "scripts.workflow_gates",
          "lib.workflow_gates",
          "workflow_gates",
      )
    return target.endswith("workflow_gates.py")
  return base == "workflow_gates.py"


def _parse_workflow_gates_cli_advance(exe, rem, cwd_dir, default_state_dir, ctx):
  """Extracts an advance record from direct workflow_gates.py CLI tokens."""
  args = list(rem)
  if not _consume_workflow_gates_entry(exe, args) or "advance" not in args:
    return None
  prior, prefix_env = ctx if isinstance(ctx, tuple) else (str(ctx or ""), {})

  repo_root = cwd_dir or ""
  raw_from, raw_to, approved = "", "", ""
  complete, use_make_env = False, False
  idx = 0
  while idx < len(args):
    tok = args[idx]
    if tok == "--repo-root" and idx + 1 < len(args):
      repo_root, idx = args[idx + 1], idx + 2
      continue
    if tok.startswith("--repo-root="):
      repo_root = tok.split("=", 1)[1]
    elif tok == "--from" and idx + 1 < len(args):
      raw_from, idx = args[idx + 1], idx + 2
      continue
    elif tok.startswith("--from="):
      raw_from = tok.split("=", 1)[1]
    elif tok == "--to" and idx + 1 < len(args):
      raw_to, idx = args[idx + 1], idx + 2
      continue
    elif tok.startswith("--to="):
      raw_to = tok.split("=", 1)[1]
    elif tok == "--approved":
      if idx + 1 < len(args) and not args[idx + 1].startswith("-"):
        approved, idx = args[idx + 1], idx + 2
      else:
        approved, idx = "1", idx + 1
      continue
    elif tok.startswith("--approved="):
      approved = tok.split("=", 1)[1]
    elif tok == "--complete":
      complete = True
    elif tok == "--make-env":
      use_make_env = True
    idx += 1

  if use_make_env and prefix_env:
    raw_from = raw_from or prefix_env.get("FROM", "")
    raw_to = raw_to or prefix_env.get("TO", "")
    approved = approved or prefix_env.get("APPROVED", "")

  eff_root = "" if repo_root in ("", ".") else repo_root
  st_dir = (
      os.path.join(eff_root, ".workflow", "state")
      if eff_root
      else default_state_dir
  )
  next_t = _GOAL_OR_INIT_TO_TARGET.get(raw_to, raw_to)
  if raw_to == "finish":
    complete = True
  if not next_t:
    return None
  curr_t = (
      _GOAL_OR_INIT_TO_TARGET.get(raw_from, raw_from)
      if raw_from
      else _infer_shell_current_target(st_dir, prior, next_target=next_t)
  )
  return _build_shell_adv_record(
      curr_t,
      next_t,
      approved,
      complete,
      (eff_root, st_dir),
      from_omitted=not bool(raw_from),
  )


def _unwrap_tokens_preserving_env(tokens):
  """Unwraps command tokens while capturing leading KEY=VALUE assignments."""
  exe, rem = _unwrap_command_tokens(tokens)
  if not exe or exe == "SHELL_C":
    return exe, rem, {}
  prefix_end = max(0, len(tokens) - len(rem) - 1)
  env_vars = {}
  idx = 0
  while idx < prefix_end:
    tok = tokens[idx]
    if tok in ("-i", "--ignore-environment"):
      env_vars.clear()
    elif tok in ("-u", "--unset") and idx + 1 < prefix_end:
      env_vars.pop(tokens[idx + 1], None)
      idx += 2
      continue
    elif tok in (
        "-C",
        "--chdir",
        "-g",
        "-D",
        "-h",
        "-p",
        "-r",
        "-t",
        "-T",
        "-U",
    ):
      idx += 2
      continue
    elif ENV_ASSIGN_RE.match(tok):
      key, val = tok.split("=", 1)
      env_vars[key.strip()] = val.strip()
    idx += 1
  return exe, rem, env_vars


def _extract_shell_advance_calls(cmd_str, default_state_dir=""):
  """Extracts Makeflow target advance calls from a shell command string."""
  if not cmd_str or not isinstance(cmd_str, str):
    return []
  extracted = []
  cwd_override = ""
  prior_target = ""

  for subcmd in _expand_shell_subcommands(cmd_str):
    for tokens in _tokenize_subcommand(subcmd):
      exe, rem, prefix_env = _unwrap_tokens_preserving_env(tokens)
      if not exe:
        continue
      base_exe = os.path.basename(exe).lower()
      if base_exe == "cd" and rem:
        cwd_override = rem[0]
        continue

      if base_exe in ("make", "gmake"):
        mf_arg, dir_arg, make_arg_vars, goals = _parse_make_tokens(rem)
        mvars = {**prefix_env, **make_arg_vars}
        ws_dir, st_dir = _resolve_shell_state_dirs(
            mf_arg, dir_arg or cwd_override, default_state_dir
        )
        if st_dir is None and ws_dir is None:
          continue
        for goal in goals:
          complete = goal == "finish"
          if goal in _MAKE_GOAL_TO_TARGET:
            next_t = _MAKE_GOAL_TO_TARGET[goal]
          elif goal == "advance":
            raw_to = mvars.get("TO", "")
            complete = complete or (raw_to == "finish")
            next_t = _GOAL_OR_INIT_TO_TARGET.get(raw_to, raw_to)
          else:
            continue
          if not next_t:
            continue
          raw_from = mvars.get("FROM", "")
          curr_t = (
              _GOAL_OR_INIT_TO_TARGET.get(raw_from, raw_from)
              if raw_from
              else _infer_shell_current_target(
                  st_dir, prior_target, next_target=next_t
              )
          )
          rec = _build_shell_adv_record(
              curr_t,
              next_t,
              mvars.get("APPROVED"),
              complete,
              (ws_dir, st_dir),
              from_omitted=not bool(raw_from),
          )
          extracted.append(rec)
          prior_target = next_t
        continue

      cli_rec = _parse_workflow_gates_cli_advance(
          exe, rem, cwd_override, default_state_dir, (prior_target, prefix_env)
      )
      if cli_rec is not None:
        extracted.append(cli_rec)
        prior_target = cli_rec["nextTarget"]

  return extracted


def extract_advance_target_calls(tool_calls, default_state_dir=""):
  """Extracts normalized workflow_advance_target calls from a list of tool_calls."""
  if not isinstance(tool_calls, list):
    return []
  results = []
  for tc in tool_calls:
    if not isinstance(tc, dict):
      continue
    name = _unwrap_json_val(tc.get("name", ""))
    raw_args = _unwrap_json_val(tc.get("args", {}))
    if not isinstance(raw_args, dict):
      continue

    if name in _SHELL_TOOL_NAMES:
      cmd_line = _unwrap_json_val(
          raw_args.get("CommandLine")
          or raw_args.get("command")
          or raw_args.get("cmd")
          or ""
      )
      results.extend(
          _extract_shell_advance_calls(str(cmd_line), default_state_dir)
      )
      continue

    inner_args = None
    if name == "call_mcp_tool":
      tool_name = _unwrap_json_val(raw_args.get("ToolName", ""))
      if tool_name == "workflow_advance_target":
        cand = _unwrap_json_val(raw_args.get("Arguments", {}))
        if isinstance(cand, dict):
          inner_args = cand
    elif name in (
        "workflow_advance_target",
        "mcp_makeflow_makeflow_workflow_advance_target",
    ):
      inner_args = raw_args

    if isinstance(inner_args, dict):
      curr_t = _unwrap_json_val(inner_args.get("currentTarget", ""))
      next_t = _unwrap_json_val(inner_args.get("nextTarget", ""))
      ev = _unwrap_json_val(inner_args.get("evidence", {}))
      ws_dir = _unwrap_json_val(inner_args.get("workspaceDir", ""))
      st_dir = _unwrap_json_val(inner_args.get("stateDir", ""))
      results.append({
          "currentTarget": str(curr_t).strip() if curr_t else "",
          "nextTarget": str(next_t).strip() if next_t else "",
          "evidence": ev if isinstance(ev, dict) else {},
          "workspaceDir": str(ws_dir).strip() if ws_dir else "",
          "stateDir": str(st_dir).strip() if st_dir else "",
      })
  return results


def _pause_gate_exit_source(adv):
  """Returns canonical pause-gate source ID when ``adv`` is a pause-gate exit.

  Detects pause-gate exits deterministically across both ``PreToolUse`` and
  later transcript replays even when ``FROM`` was omitted and on-disk
  ``active_target.json`` has subsequently moved, completed, or been removed:

  1. Explicit or currently inferred pair ``(curr_t, next_t) in PAUSE_GATE_EXITS``.
  2. Structural ``FROM``-omitted call whose destination is in
     ``_PAUSE_GATE_EXIT_DEST_SOURCES`` (``T30``, ``T50``, ``T60``, ``T70``,
     or ``T80``). Because overloaded recovery loop-backs into those
     destinations require explicit ``FROM=<source>``, any ``FROM``-omitted
     call targeting them classifies unconditionally as a forward pause-gate
     exit regardless of ``APPROVED=1`` or mutable disk state.

  Args:
    adv: Normalized advance-call dictionary.

  Returns:
    Source pause-gate ID string (or ``"A/B"`` when multiple pause gates share
    the destination and ``FROM`` was omitted), or ``""`` when ``adv`` is not a
    pause-gate exit.
  """
  curr_t = adv.get("currentTarget", "")
  next_t = adv.get("nextTarget", "")
  if (curr_t, next_t) in PAUSE_GATE_EXITS:
    return curr_t
  if adv.get("fromOmitted") and next_t in _PAUSE_GATE_EXIT_DEST_SOURCES:
    return "/".join(sorted(_PAUSE_GATE_EXIT_DEST_SOURCES[next_t]))
  return ""


def is_from_omitted_recovery_transition(curr_t, next_t, from_omitted):
  """Returns True when an overloaded recovery loop-back omitted explicit FROM=.

  Args:
    curr_t: Resolved source target ID (for example ``"T70"`` or ``"T80"``).
    next_t: Canonical destination target ID (for example ``"T30"``).
    from_omitted: True when the shell or CLI command omitted ``FROM=`` or
      ``--from``.

  Returns:
    True when ``from_omitted`` is True and ``(curr_t, next_t)`` belongs to
    ``EXPLICIT_FROM_RECOVERY_TRANSITIONS``.
  """
  return bool(from_omitted) and (
      curr_t,
      next_t,
  ) in EXPLICIT_FROM_RECOVERY_TRANSITIONS


def format_missing_from_recovery_message(curr_t, next_t):
  """Formats the refusal message requiring explicit FROM=<current> on recovery.

  Args:
    curr_t: Resolved source target ID (for example ``"T70"`` or ``"T80"``).
    next_t: Canonical destination target ID (for example ``"T30"``).

  Returns:
    Deterministic refusal message naming the exact ``make`` command with
    ``FROM=<curr_t>``.
  """
  goal = _TARGET_TO_MAKE_GOAL.get(next_t, next_t)
  return (
      f"BLOCKED: Recovery transition {curr_t} -> {next_t} requires explicit"
      f" FROM={curr_t} because {next_t} is also a pause-gate exit target."
      f" Run: make -f .workflow/state/Makefile {goal} FROM={curr_t}"
  )


def classify_bundled_pause_gate_violation(adv_calls):
  """Classifies bundled pause-gate chaining violations within one tool call.

  Detects two distinct violations when a single tool call bundles multiple
  target advance transitions:

  1. ``"entry_and_downstream"``: An earlier transition enters a pause gate
     (``nextTarget in PAUSE_GATE_TARGETS``) and a later transition attempts
     any downstream advance in the same command.
  2. ``"exit_and_chained"``: A pause-gate exit transition (explicit,
     disk-inferred, or structurally identified ``FROM``-omitted exit) is
     bundled with any other target advance in the same command.

  Args:
    adv_calls: Sequence of normalized advance-call dicts extracted from one
      tool call.

  Returns:
    Tuple ``(violation_kind, curr_t, next_t)`` where ``violation_kind`` is
    ``"entry_and_downstream"`` or ``"exit_and_chained"``, or ``None`` when
    ``adv_calls`` has no bundled pause-gate violation.
  """
  if not adv_calls or len(adv_calls) <= 1:
    return None
  entered_gates = set()
  for adv in adv_calls:
    if not isinstance(adv, dict):
      continue
    curr_t = adv.get("currentTarget", "")
    next_t = adv.get("nextTarget", "")
    if entered_gates:
      return ("entry_and_downstream", curr_t, next_t)
    exit_src = _pause_gate_exit_source(adv)
    if exit_src:
      return ("exit_and_chained", exit_src, next_t)
    if next_t in PAUSE_GATE_TARGETS:
      entered_gates.add(next_t)
  return None


def is_bundled_pause_gate_advance_call(adv_calls):
  """Returns True when one tool call's advance calls form a self-denied bundle.

  Args:
    adv_calls: Sequence of normalized advance-call dicts extracted from one
      tool call.

  Returns:
    True when ``classify_bundled_pause_gate_violation(adv_calls)`` identifies
    a bundled pause-gate violation that ``PreToolUse`` denies before execution.
  """
  return classify_bundled_pause_gate_violation(adv_calls) is not None


def _extract_shell_tool_command(tc):
  """Extracts the unwrapped shell command string from a shell tool-call dict.

  Args:
    tc: Transcript tool-call dictionary.

  Returns:
    Command string when ``tc`` invokes a shell tool with dict arguments, or
    ``""`` otherwise.
  """
  if not isinstance(tc, dict):
    return ""
  tc_name = _unwrap_json_val(tc.get("name", ""))
  if tc_name not in _SHELL_TOOL_NAMES:
    return ""
  tc_args = _unwrap_json_val(tc.get("args", {}))
  if not isinstance(tc_args, dict):
    return ""
  cmd_line = _unwrap_json_val(
      tc_args.get("CommandLine")
      or tc_args.get("command")
      or tc_args.get("cmd")
      or ""
  )
  return str(cmd_line) if cmd_line else ""


def _shell_command_contains_gated_git_command(cmd_str):
  """Returns True when a shell command invokes a pause-gated Git or gh command.

  Matches pause-gate ``gatedCommands`` declared in ``TARGETS_CONFIG`` (``git
  commit``, ``git push``, and ``gh pr merge``) across chained subcommands,
  shell wrappers, and global flags using ``lib.shell_safety`` tokenizers so
  transcript replay matches ``PreToolUse`` gated-Git bundling denial semantics.

  Args:
    cmd_str: Raw shell command string.

  Returns:
    True if any expanded subcommand invokes a pause-gated Git or ``gh``
    command.
  """
  if not cmd_str or not isinstance(cmd_str, str):
    return False
  for subcmd in _expand_shell_subcommands(cmd_str):
    for tokens in _tokenize_subcommand(subcmd):
      exe, rem = _unwrap_command_tokens(tokens)
      if not exe or exe == "SHELL_C":
        continue
      if exe == "gh":
        _, _, _, gh_pos, _ = _split_git_args(
            rem,
            short_value_chars="R",
            long_value_flags=("--repo", "--hostname"),
        )
        if (
            len(gh_pos) >= 2
            and ("gh", gh_pos[0], gh_pos[1]) in _PAUSE_GATED_COMMAND_TUPLES
        ):
          return True
      elif exe == "git":
        subcmd_name, _, _ = _parse_git_subcommand(rem)
        if subcmd_name and ("git", subcmd_name) in _PAUSE_GATED_COMMAND_TUPLES:
          return True
  return False


def is_denied_gated_git_pause_bundle(cmd_str, adv_calls):
  """Returns True when a shell call bundles a gated Git command with pause-gate entry.

  Mirrors the unconditional ``PreToolUse`` denial for shell commands that pair
  a pause-gated Git or ``gh`` command (``git commit``, ``git push``, or
  ``gh pr merge``) with an advance entering any pause gate in
  ``PAUSE_GATE_TARGETS``.

  Args:
    cmd_str: Raw shell command string from the tool call.
    adv_calls: Sequence of normalized advance-call dicts extracted from the
      same tool call.

  Returns:
    True when ``adv_calls`` enters a pause gate and ``cmd_str`` invokes a
    pause-gated Git or ``gh`` command.
  """
  if not cmd_str or not adv_calls:
    return False
  enters_pause_gate = any(
      isinstance(adv, dict) and adv.get("nextTarget") in PAUSE_GATE_TARGETS
      for adv in adv_calls
  )
  if not enters_pause_gate:
    return False
  return _shell_command_contains_gated_git_command(cmd_str)


def _is_self_denied_advance_tool_call(tc, adv_calls):
  """Returns True when a single tool call is deterministically denied by PreToolUse.

  Args:
    tc: Transcript tool-call dictionary.
    adv_calls: Normalized advance-call dicts already extracted from ``tc``.

  Returns:
    True when ``tc`` triggers either bundled pause-gate target chaining or
    gated-Git plus pause-gate entry bundling.
  """
  if not adv_calls:
    return False
  if is_bundled_pause_gate_advance_call(adv_calls):
    return True
  cmd_str = _extract_shell_tool_command(tc)
  return is_denied_gated_git_pause_bundle(cmd_str, adv_calls)


def _extract_effective_advance_calls(tool_calls, default_state_dir=""):
  """Extracts advance calls while excluding self-denied bundled tool calls.

  A single tool call that triggers ``classify_bundled_pause_gate_violation``
  or ``is_denied_gated_git_pause_bundle`` is deterministically denied by
  ``PreToolUse`` before execution and never mutates workflow state. Skipping
  each such tool call prevents denied-call same-turn lockouts and target/gate
  spoofing while retaining separate executed tool calls in the same turn.

  Args:
    tool_calls: List of transcript tool_call dictionaries from one step.
    default_state_dir: Optional fallback ``.workflow/state`` directory path.

  Returns:
    List of normalized advance-call dicts from non-bundled tool calls.
  """
  if not isinstance(tool_calls, list):
    return []
  effective = []
  for tc in tool_calls:
    if not isinstance(tc, dict):
      continue
    adv_calls = extract_advance_target_calls(
        [tc], default_state_dir=default_state_dir
    )
    if not adv_calls or _is_self_denied_advance_tool_call(tc, adv_calls):
      continue
    effective.extend(adv_calls)
  return effective


def is_human_approval_step(step, gate=None):
  """Returns True if step represents an explicit human turn or artifact approval."""
  if not isinstance(step, dict):
    return False
  step_type = step.get("type", "")
  if step_type == "USER_INPUT" or step.get("source") == "USER_EXPLICIT":
    return True
  if gate == "T20" and step_type == "SYSTEM_MESSAGE":
    content = str(step.get("content", ""))
    if "[Approved]" in content or (
        "plan.md" in content and "Approved" in content
    ):
      return True
  return False


def _matches_entry_card_basename(raw_path, card_names):
  """Returns True when raw_path points to a filename in card_names.

  Args:
    raw_path: Candidate path string from CLI flags or redirections.
    card_names: Collection of entry-card basenames for the target gate.

  Returns:
    True if the normalized basename matches an entry in ``card_names``.
  """
  if not raw_path or not isinstance(raw_path, str):
    return False
  clean = raw_path.strip().strip("'\"").replace("\\", "/")
  return bool(clean) and os.path.basename(clean) in card_names


def _consume_commit_msg_entry(exe, args):
  """Strips python/module wrapper tokens if invocation targets commit_msg.

  Args:
    exe: Unwrapped executable name or path.
    args: Mutable argument token list modified in place when matched.

  Returns:
    True if the invocation targets ``commit_msg.py`` or its module forms.
  """
  base = os.path.basename(exe).lower()
  if base.startswith("python"):
    is_mod = False
    while args and args[0].startswith("-"):
      flag = args.pop(0)
      if flag == "-m":
        is_mod = True
        break
      if flag.startswith("-m") and len(flag) > 2:
        args.insert(0, flag[2:])
        is_mod = True
        break
    if not args:
      return False
    target = args.pop(0).replace("\\", "/")
    if is_mod:
      return target in ("scripts.commit_msg", "lib.commit_msg", "commit_msg")
    return target.endswith("commit_msg.py")
  return base == "commit_msg.py"


def _shell_command_writes_entry_card(cmd, entry_cards):
  """Returns True if a shell command writes any pause-gate entry card.

  Detects ``commit_msg.py format --out <path>`` (and ``--out=<path>``),
  explicit shell output redirections (``>``, ``>>``, ``>|``, ``&>``), and
  ``tee`` targeting an ``entry_cards`` basename while ignoring read-only
  commands such as ``git commit -F``, ``commit_msg.py validate``, and ``cat``.

  Args:
    cmd: Raw shell command string from a tool call.
    entry_cards: Iterable of entry-card basenames for the pause gate.

  Returns:
    True if ``cmd`` writes to a file matching ``entry_cards``.
  """
  if not cmd or not isinstance(cmd, str) or not entry_cards:
    return False
  card_names = set(entry_cards)

  preprocessed = _preprocess_shell(cmd)
  for candidate in [preprocessed] + _extract_shell_substitutions(preprocessed):
    for target in _extract_redirection_targets(candidate):
      if _matches_entry_card_basename(target, card_names):
        return True

  for subcmd in _expand_shell_subcommands(cmd):
    for tokens in _tokenize_subcommand(subcmd):
      exe, rem = _unwrap_command_tokens(tokens)
      if not exe or exe == "SHELL_C":
        continue
      base_exe = os.path.basename(exe).lower()

      if base_exe == "tee":
        for arg in rem:
          if arg.startswith("-"):
            continue
          if _matches_entry_card_basename(arg, card_names):
            return True
        continue

      cli_args = list(rem)
      if _consume_commit_msg_entry(exe, cli_args):
        idx = 0
        while idx < len(cli_args):
          tok = cli_args[idx]
          if tok == "--repo-root" and idx + 1 < len(cli_args):
            idx += 2
            continue
          if tok.startswith("--repo-root=") or tok == "--json":
            idx += 1
            continue
          break
        if idx < len(cli_args) and cli_args[idx] == "format":
          j = idx + 1
          while j < len(cli_args):
            tok = cli_args[j]
            if tok == "--out" and j + 1 < len(cli_args):
              if _matches_entry_card_basename(cli_args[j + 1], card_names):
                return True
              j += 2
              continue
            if tok.startswith("--out="):
              if _matches_entry_card_basename(
                  tok.split("=", 1)[1], card_names
              ):
                return True
            j += 1

  return False


def _tool_call_writes_entry_card(tc, entry_cards):
  """Returns True when a non-advance tool call writes any file in entry_cards.

  Args:
    tc: Transcript tool-call dictionary.
    entry_cards: Collection of entry-card basenames for a pause gate.

  Returns:
    True when ``tc`` writes an entry card via ``write_file``, ``edit_file``,
    or a shell command writer.
  """
  if not entry_cards or not isinstance(tc, dict):
    return False
  tc_name = _unwrap_json_val(tc.get("name", ""))
  tc_args = _unwrap_json_val(tc.get("args", {}))
  if not isinstance(tc_args, dict):
    return False
  if tc_name in ("write_file", "edit_file"):
    path_val = str(
        _unwrap_json_val(
            tc_args.get("Path") or tc_args.get("TargetFile") or ""
        )
    )
    base = os.path.basename(path_val.replace("\\", "/"))
    return base in entry_cards
  if tc_name in _SHELL_TOOL_NAMES:
    cmd_line = _extract_shell_tool_command(tc)
    return _shell_command_writes_entry_card(cmd_line, entry_cards)
  return False


def step_enters_gate(step, gate, default_state_dir=""):
  """Checks if a PLANNER_RESPONSE step entered the specified pause gate.

  Inspects each element of ``step["tool_calls"]`` independently so a
  self-denied bundled call or an advance to another target never masks a
  sibling parallel tool call that enters ``gate`` or writes one of its
  ``entryCards`` (SCAR-PROC-102). Preserves optional ``default_state_dir=""``
  for backward compatibility (SCAR-API-0002).

  Args:
    step: Transcript step dictionary.
    gate: Canonical pause-gate target ID (for example ``T20`` or ``T50``).
    default_state_dir: Optional fallback ``.workflow/state`` directory path
      for resolving ``FROM``-omitted shell advance calls.

  Returns:
    True if any non-denied tool call in the step advances into ``gate`` or
    writes one of its ``entryCards``.
  """
  if not isinstance(step, dict) or step.get("type") != "PLANNER_RESPONSE":
    return False

  tool_calls = step.get("tool_calls") or []
  entry_cards = tuple(TARGETS_CONFIG.get(gate, {}).get("entryCards", ()))

  for tc in tool_calls:
    if not isinstance(tc, dict):
      continue
    adv_calls = extract_advance_target_calls(
        [tc], default_state_dir=default_state_dir
    )
    if adv_calls:
      if _is_self_denied_advance_tool_call(tc, adv_calls):
        continue
      if any(adv["nextTarget"] == gate for adv in adv_calls):
        return True
      continue
    if _tool_call_writes_entry_card(tc, entry_cards):
      return True

  return False


def step_enters_or_presents_gate(
    step, gate, check_content_prompt=False, default_state_dir=""
):
  """Alias for step_enters_gate without prose or 99_next_action.md scraping.

  Args:
    step: Transcript step dictionary.
    gate: Canonical pause-gate target ID.
    check_content_prompt: Unused legacy parameter retained for API compatibility.
    default_state_dir: Optional fallback ``.workflow/state`` directory path.

  Returns:
    True if ``step_enters_gate`` detects entry into ``gate``.
  """
  del check_content_prompt
  return step_enters_gate(step, gate, default_state_dir=default_state_dir)


def detect_latest_target(steps, state_dir=None):
  """Returns the latest active target reached in transcript steps, active_target.json, or gate_entry.json.

  Ignores ``COMPLETE`` records persisted in ``active_target.json`` by
  ``make finish`` so completed workspaces never trigger ``T80``/``T90``
  post-landing pause-gate exemptions (SCAR-PROC-102).

  Args:
    steps: Iterable of parsed transcript step dictionaries.
    state_dir: Optional path to ``.workflow/state``.

  Returns:
    Canonical active target ID string (for example ``"T50"`` or ``"T90"``),
    or ``""`` when no active target is found.
  """
  latest = ""
  for st in steps or ():
    if not isinstance(st, dict):
      continue
    for adv in _extract_effective_advance_calls(
        st.get("tool_calls") or [], default_state_dir=state_dir or ""
    ):
      if adv["nextTarget"]:
        latest = adv["nextTarget"]
  if not latest and state_dir and isinstance(state_dir, str):
    active_state = ActiveStateStore(state_dir).load()
    if (
        active_state is not None
        and active_state.target
        and not active_state.is_complete()
    ):
      latest = active_state.target
  if not latest and state_dir:
    entry = read_gate_entry(state_dir)
    if entry and entry.get("gate"):
      latest = str(entry["gate"])
  return latest


def read_gate_entry(state_dir):
  """Reads .workflow/state/gate_entry.json if it exists."""
  if not state_dir or not isinstance(state_dir, str):
    return None
  path = os.path.join(state_dir, GATE_ENTRY_FILENAME)
  if not os.path.isfile(path):
    return None
  try:
    with open(path, "r", encoding="utf-8") as f:
      data = json.load(f)
    return data if isinstance(data, dict) else None
  except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
    return None


def record_gate_entry(state_dir, from_target, gate_target, transcript_path=""):
  """Records entry into a pause gate in .workflow/state/gate_entry.json."""
  if (
      not state_dir
      or not isinstance(state_dir, str)
      or not os.path.isdir(state_dir)
      or gate_target not in PAUSE_GATE_TARGETS
  ):
    return

  existing = read_gate_entry(state_dir)
  if (
      not transcript_path
      and existing
      and existing.get("gate") == gate_target
      and existing.get("transcriptPath")
  ):
    payload = {
        "gate": gate_target,
        "fromTarget": from_target or existing.get("fromTarget", ""),
        "enteredAt": existing.get("enteredAt", time.time()),
        "entryStepIndex": existing.get("entryStepIndex", -1),
        "userInputCountAtEntry": existing.get("userInputCountAtEntry", 0),
        "transcriptPath": existing["transcriptPath"],
        "humanTurnVerified": bool(existing.get("humanTurnVerified", False)),
    }
  else:
    steps = load_transcript_steps(transcript_path) if transcript_path else []
    last_step_idx = -1
    user_count = 0
    for idx, st in enumerate(steps):
      s_idx = st.get("step_index")
      last_step_idx = s_idx if isinstance(s_idx, int) else idx
      if is_human_approval_step(st, gate_target):
        user_count += 1

    payload = {
        "gate": gate_target,
        "fromTarget": from_target,
        "enteredAt": time.time(),
        "entryStepIndex": last_step_idx,
        "userInputCountAtEntry": user_count,
        "transcriptPath": transcript_path or "",
        "humanTurnVerified": False,
    }

  path = os.path.join(state_dir, GATE_ENTRY_FILENAME)
  try:
    with open(path, "w", encoding="utf-8") as f:
      json.dump(payload, f, indent=2, sort_keys=True)
  except OSError:
    pass


def mark_gate_human_verified(state_dir, gate_target, transcript_path=""):
  """Marks a pause gate entry as verified by an intervening human turn."""
  entry = read_gate_entry(state_dir)
  if not entry or entry.get("gate") != gate_target:
    return
  entry["humanTurnVerified"] = True
  if transcript_path:
    entry["transcriptPath"] = transcript_path
  path = os.path.join(state_dir, GATE_ENTRY_FILENAME)
  try:
    with open(path, "w", encoding="utf-8") as f:
      json.dump(entry, f, indent=2, sort_keys=True)
  except OSError:
    pass


def clear_gate_entry(state_dir, gate_target=None):
  """Removes .workflow/state/gate_entry.json when exiting a pause gate."""
  if not state_dir or not isinstance(state_dir, str):
    return
  entry = read_gate_entry(state_dir)
  if entry is None:
    return
  if gate_target is not None and entry.get("gate") != gate_target:
    return
  path = os.path.join(state_dir, GATE_ENTRY_FILENAME)
  try:
    os.unlink(path)
  except OSError:
    pass


def _has_user_turn_since_entry(steps, gate, entry, default_state_dir=""):
  """Checks whether steps contain a human approval turn after entry.

  Args:
    steps: List of parsed transcript step dictionaries.
    gate: Canonical pause-gate target ID (for example ``T20`` or ``T75``).
    entry: Parsed ``gate_entry.json`` dictionary.
    default_state_dir: Optional fallback ``.workflow/state`` directory path
      forwarded to ``step_enters_gate`` when ``entryStepIndex`` is absent.

  Returns:
    True when an explicit human approval step occurred after entering ``gate``.
  """
  entry_step_idx = entry.get("entryStepIndex", -1)
  if not isinstance(entry_step_idx, int) or entry_step_idx < 0:
    last_entry_idx = -1
    last_user_idx = -1
    for idx, st in enumerate(steps):
      if is_human_approval_step(st, gate):
        last_user_idx = idx
      if step_enters_gate(st, gate, default_state_dir=default_state_dir):
        last_entry_idx = idx
    return last_entry_idx != -1 and last_user_idx > last_entry_idx

  entry_user_count = entry.get("userInputCountAtEntry", 0)
  user_count = 0
  has_after_idx = False
  for idx, st in enumerate(steps):
    if is_human_approval_step(st, gate):
      user_count += 1
      s_idx = st.get("step_index")
      eff_idx = s_idx if isinstance(s_idx, int) else idx
      if eff_idx > entry_step_idx:
        has_after_idx = True
  return has_after_idx and user_count > entry_user_count


def evaluate_advance_target_turn_boundary(
    current_target, next_target, transcript_path, state_dir=None
):
  """Verifies that a pause-gate exit occurs after an intervening human turn."""
  if (current_target, next_target) not in PAUSE_GATE_EXITS:
    return True, ""

  gate = current_target
  entry = read_gate_entry(state_dir) if state_dir else None
  eff_transcript = transcript_path or (
      entry.get("transcriptPath", "") if entry else ""
  )
  steps = load_transcript_steps(eff_transcript)
  if steps:
    eff_state_dir = state_dir or ""
    last_user_idx = -1
    last_entry_idx = -1
    for idx, st in enumerate(steps):
      if is_human_approval_step(st, gate):
        last_user_idx = idx
      if step_enters_gate(st, gate, default_state_dir=eff_state_dir):
        last_entry_idx = idx

    if last_user_idx == -1:
      return (
          False,
          f"BLOCKED: Transition {current_target} -> {next_target} denied because"
          " no human USER_INPUT turn exists in the transcript"
          " (SCAR-PROC-102). Stop tool calls and wait for explicit human"
          " approval.",
      )

    if last_entry_idx != -1 and last_user_idx <= last_entry_idx:
      return (
          False,
          f"BLOCKED: Cannot exit pause gate {current_target} -> {next_target}"
          " in the same autonomous turn that entered"
          f" {current_target} without an intervening human USER_INPUT step"
          " (SCAR-PROC-102). Stop tool calls and wait for human approval.",
      )

    for st in steps[last_user_idx + 1 :]:
      if step_enters_gate(st, gate, default_state_dir=eff_state_dir):
        return (
            False,
            f"BLOCKED: Cannot bundle pause gate {current_target} entry"
            f" and exit transition {current_target} -> {next_target} in the"
            " same autonomous turn without an intervening human USER_INPUT"
            " step (SCAR-PROC-102).",
        )
      for adv in _extract_effective_advance_calls(
          st.get("tool_calls") or [], default_state_dir=eff_state_dir
      ):
        if (adv["currentTarget"], adv["nextTarget"]) != (
            current_target,
            next_target,
        ):
          return (
              False,
              f"BLOCKED: Cannot chain {adv['currentTarget']} ->"
              f" {adv['nextTarget']} and pause gate exit {current_target} ->"
              f" {next_target} in a single autonomous turn without an"
              " intervening human USER_INPUT step (SCAR-PROC-102).",
          )

    if (
        entry
        and entry.get("gate") == gate
        and not entry.get("humanTurnVerified")
        and not _has_user_turn_since_entry(
            steps, gate, entry, default_state_dir=eff_state_dir
        )
    ):
      return (
          False,
          f"BLOCKED: Cannot exit pause gate {current_target} -> {next_target}"
          " without a new human USER_INPUT turn after gate entry"
          " (SCAR-PROC-102).",
      )

    return True, ""

  if entry and entry.get("gate") == gate and not entry.get("humanTurnVerified"):
    return (
        False,
        f"BLOCKED: Cannot exit pause gate {current_target} -> {next_target} in"
        " the same turn it was entered without an intervening human approval"
        " turn (SCAR-PROC-102).",
    )

  return True, ""


def evaluate_command_turn_boundary(cmd_gate, transcript_path, state_dir=None):
  """Verifies that a pause-gated command is not run in the same turn as gate entry."""
  if cmd_gate not in PAUSE_GATE_TARGETS:
    return True, ""

  entry = read_gate_entry(state_dir) if state_dir else None
  eff_transcript = transcript_path or (
      entry.get("transcriptPath", "") if entry else ""
  )
  steps = load_transcript_steps(eff_transcript)

  latest_target = detect_latest_target(steps, state_dir=state_dir)
  if latest_target in ("T80", "T90") and cmd_gate in ("T50", "T60"):
    return True, ""

  eff_state_dir = state_dir or ""
  if entry and entry.get("gate") == cmd_gate and not entry.get("humanTurnVerified"):
    if not steps or not _has_user_turn_since_entry(
        steps, cmd_gate, entry, default_state_dir=eff_state_dir
    ):
      return (
          False,
          f"BLOCKED: Cannot execute pause-gated command for {cmd_gate} in the"
          f" same autonomous turn that entered {cmd_gate} without an"
          " intervening human USER_INPUT step (SCAR-PROC-102). Stop tool calls"
          " and wait for human approval.",
      )

  if not steps:
    return True, ""

  last_user_idx = -1
  last_entry_idx = -1
  for idx, st in enumerate(steps):
    if is_human_approval_step(st, cmd_gate):
      last_user_idx = idx
    if step_enters_gate(st, cmd_gate, default_state_dir=eff_state_dir):
      last_entry_idx = idx

  if last_user_idx == -1:
    return (
        False,
        f"BLOCKED: Pause-gated command at {cmd_gate} denied because no human"
        " USER_INPUT turn exists in the transcript (SCAR-PROC-102). Stop tool"
        " calls and wait for explicit human approval.",
    )

  if last_entry_idx != -1 and last_user_idx <= last_entry_idx:
    return (
        False,
        f"BLOCKED: Cannot execute pause-gated command for {cmd_gate} in the"
        f" same autonomous turn that entered {cmd_gate} without an intervening"
        " human USER_INPUT step (SCAR-PROC-102). Stop tool calls and wait for"
        " human approval.",
    )

  return True, ""


def _active_pause_gate_for_state_dir(state_dir):
  """Returns active pause gate ID from active_target.json or fallback gate_entry.json.

  Follows the canonical workflow-state precedence order used by
  ``infer_current_target`` and ``detect_latest_target``: consults
  ``active_target.json`` first, and only falls back to ``gate_entry.json``
  when ``active_target.json`` is missing, unreadable, or lacks a valid
  canonical ``"target"`` string.

  Args:
    state_dir: Path to ``.workflow/state`` directory, or None.

  Returns:
    Canonical pause-gate ID (for example ``"T20"``) when sitting at a pause
    gate, or empty string otherwise.
  """
  if not state_dir or not isinstance(state_dir, str) or not os.path.isdir(state_dir):
    return ""
  active = None
  try:
    from lib.state_machine import read_active_target

    active = read_active_target(state_dir)
  except ImportError:
    active = None
  if isinstance(active, dict):
    raw_target = active.get("target")
    active_id = raw_target.strip() if isinstance(raw_target, str) else ""
    if active_id and active_id in TARGETS_CONFIG:
      return active_id if active_id in PAUSE_GATE_TARGETS else ""
  entry = read_gate_entry(state_dir)
  if entry:
    gate_id = str(entry.get("gate") or "").strip()
    if gate_id in PAUSE_GATE_TARGETS:
      return gate_id
  return ""


def _pause_gate_entered_in_turn(turn_steps, default_state_dir=""):
  """Returns the pause gate ID entered during the current autonomous turn, if any.

  Scans ``turn_steps`` and each step's tool calls chronologically so writing a
  pause-gate entry card (such as ``20_plan.md``) or advancing into a pause gate
  records that gate, while a subsequent recovery advance to a non-pause target
  (such as ``T20 -> T15``) clears it. Excludes self-denied bundled tool calls
  while extracting advance calls once per tool call.

  Args:
    turn_steps: Transcript steps emitted since the latest human USER_INPUT step.
    default_state_dir: Optional fallback ``.workflow/state`` directory path.

  Returns:
    Canonical pause-gate ID entered in this turn, or empty string.
  """
  entered = ""
  for st in turn_steps:
    if not isinstance(st, dict) or st.get("type") != "PLANNER_RESPONSE":
      continue
    for tc in st.get("tool_calls") or []:
      if not isinstance(tc, dict):
        continue
      raw_adv_calls = extract_advance_target_calls(
          [tc], default_state_dir=default_state_dir
      )
      if raw_adv_calls:
        if _is_self_denied_advance_tool_call(tc, raw_adv_calls):
          continue
        for adv in raw_adv_calls:
          nxt = adv.get("nextTarget") or ""
          if nxt in PAUSE_GATE_TARGETS:
            entered = nxt
          elif nxt:
            entered = ""
        continue
      for gate_id in sorted(PAUSE_GATE_TARGETS):
        entry_cards = tuple(
            TARGETS_CONFIG.get(gate_id, {}).get("entryCards", ())
        )
        if _tool_call_writes_entry_card(tc, entry_cards):
          entered = gate_id
          break
  return entered


def _format_ask_question_pause_gate_reflex(violation_code, trigger):
  """Formats a SCAR-PROC-161 Vector 4 Reflex denying ask_question at a pause gate.

  Args:
    violation_code: Machine-readable reflex violation code.
    trigger: Concrete trigger message naming the active or entered pause gate.

  Returns:
    Formatted Vector 4 Reflex string.
  """
  buf = io.StringIO()
  emit_reflex(
      stream=buf,
      scar_id="SCAR-PROC-161",
      violation_code=violation_code,
      trigger=trigger,
      rejected=(
          "Calling ask_question at a human pause gate captures CLI/UI focus,"
          " blocks artifact inspection, and risks misinterpreting answers as"
          " gate approval (SCAR-PROC-161)."
      ),
      reflex=(
          "Do not call ask_question at pause gates (T20, T42, T45, T50, T60,"
          " T75). Present the proposal with file:// links in plain chat and"
          " stop tool calls (`tool_calls: []`). Complete pre-plan discovery"
          " questions in Target 15 (or recover T20 -> T15) before writing"
          " .workflow/state/20_plan.md."
      ),
  )
  return buf.getvalue().strip()


def evaluate_ask_question_turn_boundary(transcript_path, state_dir=None):
  """Verifies ask_question is not called at pause gates and has chat context.

  Enforces SCAR-PROC-161 (prohibiting ``ask_question`` at pause gates
  ``T20``, ``T42``, ``T45``, ``T50``, ``T60``, ``T75`` or after writing a
  pause-gate entry card in the same turn) and SCAR-HCI-08 (requiring non-empty
  assistant content since the latest ``USER_INPUT`` step). Preserves optional
  ``state_dir=None`` default for backward compatibility (SCAR-API-0002).

  Args:
    transcript_path: Path to the session JSONL transcript file.
    state_dir: Optional path to ``.workflow/state`` directory.

  Returns:
    Tuple ``(allowed, deny_reason)`` where ``deny_reason`` is empty when
    allowed and contains a formatted Vector 4 Reflex when denied.
  """
  active_gate = _active_pause_gate_for_state_dir(state_dir)
  if active_gate:
    return (
        False,
        _format_ask_question_pause_gate_reflex(
            "ASK_QUESTION_AT_PAUSE_GATE",
            f"ask_question was invoked while workflow state is at pause gate {active_gate}.",
        ),
    )

  if not transcript_path or not isinstance(transcript_path, str):
    return True, ""
  if not os.path.isfile(transcript_path):
    return True, ""

  steps = load_transcript_steps(transcript_path)
  if not steps:
    return True, ""

  last_user_idx = -1
  for idx, st in enumerate(steps):
    if not isinstance(st, dict):
      continue
    if st.get("type") == "USER_INPUT" or st.get("source") == "USER_EXPLICIT":
      last_user_idx = idx

  turn_steps = steps[last_user_idx + 1 :] if last_user_idx != -1 else steps
  entered_gate = _pause_gate_entered_in_turn(
      turn_steps, default_state_dir=state_dir or ""
  )
  if entered_gate:
    return (
        False,
        _format_ask_question_pause_gate_reflex(
            "ASK_QUESTION_BUNDLED_WITH_PAUSE_GATE",
            f"ask_question was invoked in the same turn after entering pause gate"
            f" {entered_gate} or writing its entry card.",
        ),
    )

  for st in turn_steps:
    if not isinstance(st, dict):
      continue
    if st.get("type") == "PLANNER_RESPONSE" or st.get("source") == "MODEL":
      raw_content = _unwrap_json_val(st.get("content"))
      if isinstance(raw_content, str) and raw_content.strip():
        return True, ""

  buf = io.StringIO()
  emit_reflex(
      stream=buf,
      scar_id="SCAR-HCI-08",
      violation_code="ASK_QUESTION_WITHOUT_CHAT_CONTEXT",
      trigger=(
          "ask_question was invoked in PreToolUse with no assistant markdown"
          " content emitted since the latest USER_INPUT step."
      ),
      rejected=(
          "Opening a blocking ask_question selector before presenting the"
          " full context and file:// links in chat."
      ),
      reflex=(
          "Include the full markdown explanation and clickable file:// links"
          " in the content field of the same PLANNER_RESPONSE step that"
          " calls ask_question, or print the details in chat first."
      ),
  )
  return False, buf.getvalue().strip()


def verify_mcp_gate_exit(
    state_dir, current_target, next_target, transcript_path=""
):
  """Server-side verification for workflow_advance_target pause-gate exits."""
  if (current_target, next_target) not in PAUSE_GATE_EXITS:
    return True, ""

  return evaluate_advance_target_turn_boundary(
      current_target,
      next_target,
      transcript_path,
      state_dir=state_dir,
  )


def _step_effective_index(step, fallback_idx):
  """Returns validated integer step_index from step, rejecting bool values.

  Args:
    step: Transcript step dictionary.
    fallback_idx: Zero-based list index fallback when step_index is absent.

  Returns:
    Integer step index for ordering comparisons.
  """
  s_idx = step.get("step_index") if isinstance(step, dict) else None
  if isinstance(s_idx, int) and not isinstance(s_idx, bool):
    return s_idx
  return fallback_idx


def read_active_subagent(state_dir):
  """Reads .workflow/state/active_subagent.json if present and valid.

  Args:
    state_dir: Path to .workflow/state directory.

  Returns:
    Parsed active subagent lock dictionary, or None when absent or invalid.
  """
  if not state_dir or not isinstance(state_dir, str):
    return None
  path = os.path.join(state_dir, ACTIVE_SUBAGENT_FILENAME)
  if not os.path.isfile(path):
    return None
  try:
    with open(path, "r", encoding="utf-8") as f:
      data = json.load(f)
    return data if isinstance(data, dict) else None
  except (
      OSError,
      UnicodeDecodeError,
      json.JSONDecodeError,
      ValueError,
      TypeError,
  ):
    return None


def record_active_subagent(
    state_dir, supervisor_cid, worker_type, transcript_path=""
):
  """Records Makeflow phase subagent dispatch in .workflow/state/active_subagent.json.

  Args:
    state_dir: Path to existing .workflow/state directory.
    supervisor_cid: Supervisor conversationId string.
    worker_type: Dispatched subagent TypeName string.
    transcript_path: Optional supervisor transcript.jsonl path.
  """
  if (
      not state_dir
      or not isinstance(state_dir, str)
      or not os.path.isdir(state_dir)
      or worker_type not in MAKEFLOW_PHASE_WORKERS
  ):
    return

  steps = load_transcript_steps(transcript_path) if transcript_path else []
  last_step_idx = -1
  for idx, st in enumerate(steps):
    last_step_idx = _step_effective_index(st, idx)

  payload = {
      "supervisorConversationId": str(supervisor_cid or "").strip(),
      "typeName": str(worker_type).strip(),
      "dispatchedStepIdx": last_step_idx,
      "dispatchedAt": time.time(),
      "transcriptPath": str(transcript_path or ""),
  }
  path = os.path.join(state_dir, ACTIVE_SUBAGENT_FILENAME)
  try:
    with open(path, "w", encoding="utf-8") as f:
      json.dump(payload, f, indent=2, sort_keys=True)
      f.write("\n")
  except OSError:
    pass


def clear_active_subagent(state_dir):
  """Unlinks .workflow/state/active_subagent.json when present.

  Args:
    state_dir: Path to .workflow/state directory.
  """
  if not state_dir or not isinstance(state_dir, str):
    return
  path = os.path.join(state_dir, ACTIVE_SUBAGENT_FILENAME)
  if os.path.isfile(path):
    try:
      os.unlink(path)
    except OSError:
      pass


def is_subagent_wakeup_step(step):
  """Returns True when step represents a reactive subagent completion wakeup.

  Excludes system "Reminder:" nudges so reminder injections do not prematurely
  unlock an active phase subagent airlock.

  Args:
    step: Transcript step dictionary.

  Returns:
    True when step is a non-reminder SYSTEM_MESSAGE or SYSTEM message delivery.
  """
  if not isinstance(step, dict):
    return False
  step_type = str(step.get("type") or "").strip()
  source = str(step.get("source") or "").strip()
  raw_content = _unwrap_json_val(step.get("content"))
  content = (
      raw_content.strip()
      if isinstance(raw_content, str)
      else str(raw_content or "").strip()
  )
  if content.startswith("Reminder:"):
    return False
  if step_type == "SYSTEM_MESSAGE":
    return True
  if source == "SYSTEM" and (
      "[Message]" in content or "<SYSTEM_MESSAGE>" in content
  ):
    return True
  return False


def is_human_user_input_step(step):
  """Returns True when step represents an explicit human operator USER_INPUT turn.

  Args:
    step: Transcript step dictionary.

  Returns:
    True when step is USER_EXPLICIT or non-SYSTEM USER_INPUT.
  """
  if not isinstance(step, dict):
    return False
  source = str(step.get("source") or "").strip()
  if source == "USER_EXPLICIT":
    return True
  step_type = str(step.get("type") or "").strip()
  return step_type == "USER_INPUT" and source != "SYSTEM"


def _maybe_record_phase_dispatch(
    tool_name, args, state_dir, caller_cid, transcript_path
):
  """Records active_subagent.json when invoke_subagent starts a phase worker."""
  if tool_name != "invoke_subagent":
    return
  worker_type = str(
      _unwrap_json_val(args.get("TypeName") or args.get("typeName") or "")
  ).strip()
  if worker_type in MAKEFLOW_PHASE_WORKERS:
    record_active_subagent(
        state_dir, caller_cid, worker_type, transcript_path=transcript_path
    )


def evaluate_subagent_airlock(payload, state_dir):
  """Enforces SCAR-PROC-95 deterministic supervisory halt while phase subagents run.

  Args:
    payload: PreToolUse hook payload dictionary.
    state_dir: Resolved .workflow/state directory path.

  Returns:
    Tuple of (allowed, reason_string).
  """
  if not isinstance(payload, dict):
    return True, ""

  raw_tc = payload.get("toolCall")
  tool_call = raw_tc if isinstance(raw_tc, dict) else {}
  tool_name = str(_unwrap_json_val(tool_call.get("name") or "")).strip()
  raw_args = _unwrap_json_val(tool_call.get("args") or {})
  args = raw_args if isinstance(raw_args, dict) else {}

  active = read_active_subagent(state_dir)
  if active is None:
    _maybe_record_phase_dispatch(
        tool_name,
        args,
        state_dir,
        str(payload.get("conversationId") or "").strip(),
        str(payload.get("transcriptPath") or "").strip(),
    )
    return True, ""

  caller_cid = str(payload.get("conversationId") or "").strip()
  sup_cid = str(active.get("supervisorConversationId") or "").strip()
  if caller_cid and caller_cid != sup_cid:
    return True, ""

  transcript_path = str(payload.get("transcriptPath") or "").strip()
  eff_transcript = transcript_path or str(
      active.get("transcriptPath") or ""
  ).strip()
  raw_disp = active.get("dispatchedStepIdx", -1)
  dispatched_idx = (
      raw_disp
      if (isinstance(raw_disp, int) and not isinstance(raw_disp, bool))
      else -1
  )

  has_wakeup = False
  has_human_override = False
  for idx, st in enumerate(load_transcript_steps(eff_transcript)):
    if not isinstance(st, dict):
      continue
    if _step_effective_index(st, idx) <= dispatched_idx:
      continue
    if is_subagent_wakeup_step(st):
      has_wakeup = True
      break
    if is_human_user_input_step(st):
      has_human_override = True

  if has_wakeup:
    clear_active_subagent(state_dir)
    _maybe_record_phase_dispatch(
        tool_name, args, state_dir, caller_cid, eff_transcript
    )
    return True, ""

  if has_human_override and tool_name in SUPERVISOR_INSPECTION_TOOLS:
    if tool_name == "manage_subagents":
      action = str(
          _unwrap_json_val(args.get("Action") or args.get("action") or "")
      ).strip().lower()
      if action in ("kill", "kill_all"):
        clear_active_subagent(state_dir)
    return True, ""

  active_worker = str(active.get("typeName") or "subagent").strip()
  buf = io.StringIO()
  emit_reflex(
      stream=buf,
      scar_id="SCAR-PROC-95",
      violation_code="CONCURRENT_SUPERVISOR_TOOL_CALL",
      trigger=(
          f"Supervisor invoked '{tool_name}' while Makeflow phase subagent"
          f" '{active_worker}' is still active"
          f" (dispatchedStepIdx={dispatched_idx}, no completion"
          " <SYSTEM_MESSAGE> wakeup recorded)."
      ),
      rejected=(
          "Bundling or running concurrent Depth-0 supervisor tool calls"
          " alongside an active phase subagent, duplicating worker scans"
          " and inflating supervisor context (SCAR-PROC-95)."
      ),
      reflex=(
          "Halt supervisor tool execution immediately (`tool_calls: []`) and"
          " wait for the reactive <SYSTEM_MESSAGE> wakeup from"
          f" '{active_worker}' before calling further tools."
      ),
  )
  return False, buf.getvalue().strip()
