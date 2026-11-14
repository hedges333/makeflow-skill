"""Declarative target configuration and transcript turn-boundary guard (GH-186, SCAR-PROC-102).

Enforces that pause gates (T20, T42, T45, T50, T60, T75) and pause-gated shell
commands (git commit, git push, gh pr merge) cannot be exited or executed in the
same autonomous turn that entered the pause gate without an intervening human
USER_INPUT turn.
"""

import json
import os
import time

GATE_ENTRY_FILENAME = "gate_entry.json"

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
        "next": ["T60"],
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


def extract_advance_target_calls(tool_calls):
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


def step_enters_gate(step, gate):
  """Checks if a PLANNER_RESPONSE step entered the specified pause gate."""
  if not isinstance(step, dict) or step.get("type") != "PLANNER_RESPONSE":
    return False

  tool_calls = step.get("tool_calls") or []
  adv_calls = extract_advance_target_calls(tool_calls)
  for adv in adv_calls:
    if adv["nextTarget"] == gate:
      return True

  if adv_calls:
    return False

  entry_cards = tuple(TARGETS_CONFIG.get(gate, {}).get("entryCards", ()))
  if not entry_cards:
    return False

  for tc in tool_calls:
    if not isinstance(tc, dict):
      continue
    tc_name = _unwrap_json_val(tc.get("name", ""))
    tc_args = _unwrap_json_val(tc.get("args", {}))
    if not isinstance(tc_args, dict):
      continue
    if tc_name in ("write_file", "edit_file"):
      path_val = str(
          _unwrap_json_val(
              tc_args.get("Path") or tc_args.get("TargetFile") or ""
          )
      )
      base = os.path.basename(path_val.replace("\\", "/"))
      if base in entry_cards:
        return True

  return False


def step_enters_or_presents_gate(step, gate, check_content_prompt=False):
  """Alias for step_enters_gate without prose or 99_next_action.md scraping."""
  del check_content_prompt
  return step_enters_gate(step, gate)


def detect_latest_target(steps, state_dir=None):
  """Returns the latest target reached in transcript steps or gate_entry.json."""
  latest = ""
  for st in steps or ():
    if not isinstance(st, dict):
      continue
    for adv in extract_advance_target_calls(st.get("tool_calls") or []):
      if adv["nextTarget"]:
        latest = adv["nextTarget"]
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


def _has_user_turn_since_entry(steps, gate, entry):
  """Checks whether steps contain a human approval turn after entry."""
  entry_step_idx = entry.get("entryStepIndex", -1)
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
    last_user_idx = -1
    last_entry_idx = -1
    for idx, st in enumerate(steps):
      if is_human_approval_step(st, gate):
        last_user_idx = idx
      if step_enters_gate(st, gate):
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
      if step_enters_gate(st, gate):
        return (
            False,
            f"BLOCKED: Cannot bundle pause gate {current_target} entry"
            f" and exit transition {current_target} -> {next_target} in the"
            " same autonomous turn without an intervening human USER_INPUT"
            " step (SCAR-PROC-102).",
        )
      for adv in extract_advance_target_calls(st.get("tool_calls") or []):
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
        and not _has_user_turn_since_entry(steps, gate, entry)
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

  if entry and entry.get("gate") == cmd_gate and not entry.get("humanTurnVerified"):
    if not steps or not _has_user_turn_since_entry(steps, cmd_gate, entry):
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
    if step_enters_gate(st, cmd_gate):
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
