"""Vector 4 Reflex emitter library module (SCAR-ARCH-05, SCAR-ARCH-06).

Emits actionable runtime recovery directives on stderr (text or JSON) and returns
exit code 2 so deterministic Make and Python gates guide agent self-correction
without inflating static SKILL.md context.
"""

import argparse
import glob
import json
import os
import re
import sys
from typing import Dict, List, Optional, TextIO, Tuple

from lib.base_script import BaseScript, ReflexArgumentError, SilentArgumentParser

REFLEX_EXIT_CODE = 2
_HEAVY_BORDER = "=" * 80
_LIGHT_BORDER = "-" * 80
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CORE_SCAR_PROBE_CACHE: Dict[str, str] = {}


def normalize_scar_id(raw_scar_id: str) -> Tuple[str, str]:
  """Normalizes 'SCAR-PROC-99' or 'PROC-99' to ('SCAR-PROC-99', 'PROC-99')."""
  cleaned = (raw_scar_id or "").strip()
  while cleaned.upper().startswith("SCAR-"):
    cleaned = cleaned[5:].strip()
  if not cleaned:
    raise ReflexArgumentError("scar_id must not be empty")
  return f"SCAR-{cleaned}", cleaned


def resolve_core_scar_probe(
    scar_id: str, repo_root: Optional[str] = None
) -> str:
  """Resolves the Socratic Probe for a core scar from skills/makeflow/references/scars/*.md."""
  try:
    full_id, _ = normalize_scar_id(scar_id)
  except ReflexArgumentError:
    return ""

  root = os.path.abspath(repo_root or _REPO_ROOT)
  cache_key = f"{root}:{full_id}"
  if cache_key in _CORE_SCAR_PROBE_CACHE:
    return _CORE_SCAR_PROBE_CACHE[cache_key]

  scars_dir = os.path.join(root, "skills", "makeflow", "references", "scars")
  header_pat = re.compile(
      rf"^###\s+{re.escape(full_id)}\b", re.IGNORECASE | re.MULTILINE
  )
  probe_pat = re.compile(
      r"^-\s+\*\*Socratic Probe\*\*:\s*(.+)$", re.MULTILINE
  )

  for md_path in sorted(glob.glob(os.path.join(scars_dir, "*.md"))):
    try:
      with open(md_path, "r", encoding="utf-8") as f:
        text = f.read()
    except OSError:
      continue
    m_hdr = header_pat.search(text)
    if not m_hdr:
      continue
    rest = text[m_hdr.end() :]
    next_hdr = re.search(r"^###\s+SCAR-", rest, re.MULTILINE)
    section = rest[: next_hdr.start()] if next_hdr else rest
    m_probe = probe_pat.search(section)
    if m_probe:
      probe_val = m_probe.group(1).strip()
      _CORE_SCAR_PROBE_CACHE[cache_key] = probe_val
      return probe_val

  _CORE_SCAR_PROBE_CACHE[cache_key] = ""
  return ""


def emit_reflex(
    stderr: Optional[TextIO] = None,
    scar_id: str = "",
    violation_code: str = "",
    trigger: str = "",
    rejected: str = "",
    reflex: str = "",
    as_json: bool = False,
    *,
    code: Optional[str] = None,
    socratic_probe: Optional[str] = None,
    output_json: Optional[bool] = None,
    stream: Optional[TextIO] = None,
) -> int:
  """Writes a formatted Vector 4 Reflex block or JSON payload to stderr."""
  target_stream = stream if stream is not None else (stderr or sys.stderr)
  use_json = output_json if output_json is not None else as_json
  full_id, short_id = normalize_scar_id(scar_id)
  v_code = (code if code is not None else violation_code).strip()
  trig = trigger.strip()
  trap = rejected.strip()
  action = reflex.strip()
  resolved_probe = (
      socratic_probe.strip()
      if socratic_probe and socratic_probe.strip()
      else resolve_core_scar_probe(full_id)
  )

  if use_json:
    payload = {
        "scar_id": full_id,
        "short_id": short_id,
        "violation_code": v_code,
        "trigger": trig,
        "rejected": trap,
        "reflex": action,
        "socratic_probe": resolved_probe,
        "exit_code": REFLEX_EXIT_CODE,
    }
    target_stream.write(json.dumps(payload) + "\n")
  else:
    probe_line = (
        f"SOCRATIC PROBE:  {resolved_probe}\n" if resolved_probe else ""
    )
    block = (
        f"{_HEAVY_BORDER}\n"
        f"[SCAR-ID: {short_id}] {v_code}\n"
        f"{_LIGHT_BORDER}\n"
        f"TRIGGER (THIS):  {trig}\n"
        f"REJECTED (TRAP): {trap}\n"
        f"REFLEX (ACTION): {action}\n"
        f"{probe_line}"
        f"{_HEAVY_BORDER}\n"
    )
    target_stream.write(block)
  return REFLEX_EXIT_CODE


class EmitReflexScript(BaseScript):
  """CLI script runner for emitting Vector 4 Reflex recovery directives."""

  def __init__(self) -> None:
    super().__init__()
    self.scar_id: str = ""
    self.violation_code: str = ""
    self.trigger: str = ""
    self.rejected: str = ""
    self.reflex: str = ""
    self.as_json: bool = False

  def parse_args(self, args: Optional[List[str]] = None) -> None:
    super().parse_args(args)
    parser = SilentArgumentParser(
        prog="emit_reflex.py",
        description="Emit a structured Vector 4 Reflex recovery directive on stderr and exit 2 (SCAR-ARCH-06).",
        epilog=(
            "Examples:\n"
            "  python3 scripts/emit_reflex.py --scar-id GIT-25 --code UNCLEAN_STATE \\\n"
            "    --trigger 'Dirty tree.' --rejected 'Committing dirty tree.' --reflex 'Clean tree.'\n"
            "  python3 scripts/emit_reflex.py GIT-25 UNCLEAN_STATE 'Dirty tree.' 'Committing dirty tree.' 'Clean tree.'"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--scar-id",
        dest="opt_scar_id",
        help="Synthetic scar identifier (for example SCAR-GIT-25 or GIT-25).",
    )
    parser.add_argument(
        "--code",
        dest="opt_code",
        help="Machine-readable violation code (for example UNCLEAN_INTAKE_STATE).",
    )
    parser.add_argument(
        "--trigger",
        dest="opt_trigger",
        help="Description of the condition that triggered the violation.",
    )
    parser.add_argument(
        "--rejected",
        dest="opt_rejected",
        help="Description of the rejected action or trap.",
    )
    parser.add_argument(
        "--reflex",
        dest="opt_reflex",
        help="Actionable recovery instruction for the agent.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit the Vector 4 Reflex payload as single-line JSON on stderr.",
    )
    parser.add_argument(
        "positional",
        nargs="*",
        help="Optional 5 positional arguments: <scar_id> <code> <trigger> <rejected> <reflex>.",
    )

    ns = parser.parse_args(self.raw_args)
    named = (
        ns.opt_scar_id,
        ns.opt_code,
        ns.opt_trigger,
        ns.opt_rejected,
        ns.opt_reflex,
    )
    any_named = any(val is not None for val in named)
    all_named = all(val is not None for val in named)

    if ns.positional and any_named:
      parser.error(
          "Do not mix positional arguments with --scar-id/--code/--trigger/--rejected/--reflex flags."
      )
    elif len(ns.positional) == 5 and not any_named:
      raw_scar, raw_code, raw_trigger, raw_rejected, raw_reflex = ns.positional
    elif not ns.positional and all_named:
      raw_scar, raw_code, raw_trigger, raw_rejected, raw_reflex = named
    else:
      parser.error(
          "Provide either --scar-id, --code, --trigger, --rejected, and --reflex "
          "or 5 positional arguments: <scar_id> <code> <trigger> <rejected> <reflex>."
      )

    for label, val in (
        ("scar_id", raw_scar),
        ("code", raw_code),
        ("trigger", raw_trigger),
        ("rejected", raw_rejected),
        ("reflex", raw_reflex),
    ):
      if not val or not val.strip():
        parser.error(f"Required argument '{label}' must not be empty.")

    normalize_scar_id(raw_scar)
    self.scar_id = raw_scar
    self.violation_code = raw_code
    self.trigger = raw_trigger
    self.rejected = raw_rejected
    self.reflex = raw_reflex
    self.as_json = bool(ns.as_json)

  def run(self) -> int:
    return emit_reflex(
        stderr=self.stderr,
        scar_id=self.scar_id,
        violation_code=self.violation_code,
        trigger=self.trigger,
        rejected=self.rejected,
        reflex=self.reflex,
        as_json=self.as_json,
    )
