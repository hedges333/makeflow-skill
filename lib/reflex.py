"""Vector 4 Reflex emitter library module (SCAR-ARCH-05, SCAR-ARCH-06).

Emits actionable runtime recovery directives on stderr (text or JSON) and returns
exit code 2 so deterministic Make and Python gates guide agent self-correction
without inflating static SKILL.md context.
"""

import json
from typing import List, Optional, TextIO, Tuple

from lib.base_script import BaseScript, ReflexArgumentError, SilentArgumentParser

REFLEX_EXIT_CODE = 2
_HEAVY_BORDER = "=" * 80
_LIGHT_BORDER = "-" * 80


def normalize_scar_id(raw_scar_id: str) -> Tuple[str, str]:
  """Normalizes 'SCAR-PROC-99' or 'PROC-99' to ('SCAR-PROC-99', 'PROC-99')."""
  cleaned = (raw_scar_id or "").strip()
  while cleaned.upper().startswith("SCAR-"):
    cleaned = cleaned[5:].strip()
  if not cleaned:
    raise ReflexArgumentError("scar_id must not be empty")
  return f"SCAR-{cleaned}", cleaned


def emit_reflex(
    stderr: TextIO,
    scar_id: str,
    violation_code: str,
    trigger: str,
    rejected: str,
    reflex: str,
    as_json: bool = False,
) -> int:
  """Writes a formatted Vector 4 Reflex block or JSON payload to stderr."""
  full_id, short_id = normalize_scar_id(scar_id)
  code = violation_code.strip()
  trig = trigger.strip()
  trap = rejected.strip()
  action = reflex.strip()

  if as_json:
    payload = {
        "scar_id": full_id,
        "short_id": short_id,
        "violation_code": code,
        "trigger": trig,
        "rejected": trap,
        "reflex": action,
        "exit_code": REFLEX_EXIT_CODE,
    }
    stderr.write(json.dumps(payload) + "\n")
  else:
    block = (
        f"{_HEAVY_BORDER}\n"
        f"[SCAR-ID: {short_id}] {code}\n"
        f"{_LIGHT_BORDER}\n"
        f"TRIGGER (THIS):  {trig}\n"
        f"REJECTED (TRAP): {trap}\n"
        f"REFLEX (ACTION): {action}\n"
        f"{_HEAVY_BORDER}\n"
    )
    stderr.write(block)
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
    parser = SilentArgumentParser(prog="emit_reflex.py", add_help=False)
    parser.add_argument("--scar-id", dest="opt_scar_id")
    parser.add_argument("--code", dest="opt_code")
    parser.add_argument("--trigger", dest="opt_trigger")
    parser.add_argument("--rejected", dest="opt_rejected")
    parser.add_argument("--reflex", dest="opt_reflex")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("positional", nargs="*")

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
