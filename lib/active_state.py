"""Canonical active workflow target state modeling and atomic persistence (GH-281).

Provides the immutable ``ActiveTargetState`` value object, the atomic
``ActiveStateStore`` repository for ``.workflow/state/active_target.json``,
status predicates, atomic UTF-8 file persistence, and shared state-card
enumeration. This is a pure leaf module with zero upward Makeflow imports.
"""

from dataclasses import dataclass
import json
import os
import re
import tempfile
from typing import Any, Dict, FrozenSet, List, Optional, Tuple, Union

ACTIVE_TARGET_FILENAME = "active_target.json"

VALID_ACTIVE_STATUSES: FrozenSet[str] = frozenset({
    "ACTIVE",
    "PARKED",
    "COMPLETE",
})

POST_LANDING_TARGETS: FrozenSet[str] = frozenset({"T80", "T90"})

INTERNAL_STATE_JSON_FILES: FrozenSet[str] = frozenset({
    ACTIVE_TARGET_FILENAME,
    "gate_entry.json",
    "active_subagent.json",
    "loaded_scars.json",
    "30_tdd_receipt.json",
    "30_regression_receipt.json",
    "70_parking_state.json",
})

_NUMBERED_STATE_JSON_RE = re.compile(r"^[1-9][0-9]_.*\.json$")


def _clean_str(value: Any) -> str:
  """Coerces a non-null scalar value to a stripped string.

  Args:
    value: Raw value from a parsed JSON mapping.

  Returns:
    Trimmed string representation, or empty string when value is None.
  """
  if value is None:
    return ""
  return str(value).strip()


def _clean_str_tuple(raw_items: Any) -> Tuple[str, ...]:
  """Normalizes a sequence of target strings into a deduplicated tuple.

  Args:
    raw_items: Candidate list or tuple of target identifier strings.

  Returns:
    Tuple of unique non-empty stripped strings preserving order.
  """
  if not isinstance(raw_items, (list, tuple)):
    return ()
  seen: List[str] = []
  for item in raw_items:
    text = _clean_str(item) if isinstance(item, str) else ""
    if text and text not in seen:
      seen.append(text)
  return tuple(seen)


def _resolve_lifecycle(
    raw_status: Any,
    raw_complete: Any,
    raw_parked: Any,
    raw_parking_reason: Any,
) -> Tuple[str, bool, bool, str]:
  """Normalizes status, complete, parked, and parking_reason from one rule set.

  Precedence order:
  1. ``COMPLETE`` (``status == "COMPLETE"`` or ``complete is True``) clears
     ``parked`` and ``parking_reason``.
  2. ``PARKED`` (``status == "PARKED"`` or ``parked is True`` when status is
     not explicitly ``"ACTIVE"``) enforces ``parked=True`` and keeps
     ``parking_reason``.
  3. ``ACTIVE`` (``status == "ACTIVE"`` or ``complete is False``) clears
     ``parked`` and ``parking_reason``.
  4. Legacy pre-GH-281 records omitting all lifecycle markers return
     ``("", False, False, "")``.

  Args:
    raw_status: Candidate lifecycle status value (case-insensitive string).
    raw_complete: Candidate completion boolean (non-bool values are ignored).
    raw_parked: Candidate parking boolean (non-bool values are ignored).
    raw_parking_reason: Candidate parking reason text.

  Returns:
    Normalized ``(status, complete, parked, parking_reason)`` tuple.
  """
  status_token = _clean_str(raw_status).upper()
  complete_bool = raw_complete if isinstance(raw_complete, bool) else None
  parked_bool = raw_parked if isinstance(raw_parked, bool) else False
  reason = _clean_str(raw_parking_reason)

  if status_token == "COMPLETE" or complete_bool is True:
    return ("COMPLETE", True, False, "")
  if status_token == "PARKED" or (status_token != "ACTIVE" and parked_bool):
    return ("PARKED", False, True, reason)
  if status_token == "ACTIVE" or complete_bool is False:
    return ("ACTIVE", False, False, "")
  return ("", False, False, "")


def stage_atomic_text(
    target_path: str,
    content: str,
    *,
    prefix: str = ".tmp_state_",
    suffix: str = ".tmp",
) -> str:
  """Stages UTF-8 text into a flushed, fsynced temporary file next to target_path.

  Cleans up the temporary file on any write, encoding, or fsync exception so
  orphan temp files never leak inside the destination directory.

  Args:
    target_path: Final destination file path whose directory hosts the temp file.
    content: Full UTF-8 text content to stage.
    prefix: Temporary filename prefix inside the destination directory.
    suffix: Temporary filename suffix inside the destination directory.

  Returns:
    Path to the flushed and fsynced temporary file ready for ``os.replace``.

  Raises:
    OSError: If directory creation, writing, or fsync fails.
    UnicodeEncodeError: If ``content`` cannot be encoded as UTF-8.
  """
  parent_dir = os.path.dirname(os.path.abspath(target_path))
  os.makedirs(parent_dir, exist_ok=True)
  tmp_path: Optional[str] = None
  succeeded = False
  try:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=parent_dir,
        prefix=prefix,
        suffix=suffix,
        delete=False,
    ) as tmp_file:
      tmp_path = tmp_file.name
      tmp_file.write(content)
      tmp_file.flush()
      os.fsync(tmp_file.fileno())
    succeeded = True
    return tmp_path
  finally:
    if not succeeded and tmp_path is not None:
      try:
        os.unlink(tmp_path)
      except OSError:
        pass


def atomic_write_text(
    target_path: str,
    content: str,
    *,
    prefix: str = ".tmp_state_",
    suffix: str = ".tmp",
) -> str:
  """Atomically writes UTF-8 text via temporary file, fsync, and os.replace.

  Guarantees readers never observe partial or truncated files and cleans up
  temporary files on any exception (SCAR-TOOL-13).

  Args:
    target_path: Destination file path to create or replace.
    content: Full UTF-8 text content to persist.
    prefix: Temporary filename prefix inside the destination directory.
    suffix: Temporary filename suffix inside the destination directory.

  Returns:
    The written ``target_path``.

  Raises:
    OSError: If directory creation, temporary write, fsync, or replacement fails.
    UnicodeEncodeError: If ``content`` cannot be encoded as UTF-8.
  """
  tmp_path = stage_atomic_text(
      target_path, content, prefix=prefix, suffix=suffix
  )
  replaced = False
  try:
    os.replace(tmp_path, target_path)
    replaced = True
    return target_path
  finally:
    if not replaced:
      try:
        os.unlink(tmp_path)
      except OSError:
        pass


@dataclass(frozen=True)
class ActiveTargetState:
  """Immutable structured state persisted in ``.workflow/state/active_target.json``.

  Preserves GH-47 camelCase keys (``previousTarget``, ``target``,
  ``targetName``, ``skippedTargets``, ``parked``, ``parkingReason``) alongside
  explicit lifecycle fields (``status``, ``complete``, ``ticketId``,
  ``branch``).

  Attributes:
    target: Canonical Makeflow target ID (for example ``"T30"`` or ``"T90"``).
    target_name: Human-readable target goal name (for example ``"finish"``).
    previous_target: Prior canonical Makeflow target ID before transition.
    status: Explicit lifecycle status (``"ACTIVE"``, ``"PARKED"``,
      ``"COMPLETE"``), or ``""`` when loaded from a legacy pre-GH-281 file
      omitting both ``status`` and ``complete``.
    complete: True when ``make finish`` has completed the ticket lifecycle.
    ticket_id: Canonical ticket identifier (for example ``"GH-281"``).
    branch: Active Git branch associated with the ticket.
    skipped_targets: Tuple of skipped canonical target IDs (such as ``"T60"``).
    parked: True when a Target 70 session is actively parked (GH-47).
    parking_reason: Concise human-readable reason for Target 70 parking.
  """

  target: str = ""
  target_name: str = ""
  previous_target: str = ""
  status: str = ""
  complete: bool = False
  ticket_id: str = ""
  branch: str = ""
  skipped_targets: Tuple[str, ...] = ()
  parked: bool = False
  parking_reason: str = ""

  @classmethod
  def from_dict(cls, data: Dict[str, Any]) -> "ActiveTargetState":
    """Constructs a normalized ``ActiveTargetState`` from a JSON dictionary.

    Args:
      data: Parsed dictionary loaded from ``active_target.json``.

    Returns:
      Normalized immutable ``ActiveTargetState`` instance.

    Raises:
      TypeError: If ``data`` is not a dictionary.
    """
    if not isinstance(data, dict):
      raise TypeError("ActiveTargetState.from_dict expects a dict.")

    status, is_complete, is_parked, parking_reason = _resolve_lifecycle(
        data.get("status"),
        data.get("complete"),
        data.get("parked"),
        data.get("parkingReason"),
    )

    return cls(
        target=_clean_str(data.get("target")),
        target_name=_clean_str(data.get("targetName")),
        previous_target=_clean_str(data.get("previousTarget")),
        status=status,
        complete=is_complete,
        ticket_id=_clean_str(data.get("ticketId")),
        branch=_clean_str(data.get("branch")),
        skipped_targets=_clean_str_tuple(data.get("skippedTargets")),
        parked=is_parked,
        parking_reason=parking_reason,
    )

  def _resolved_lifecycle(self) -> Tuple[str, bool, bool, str]:
    """Resolves normalized ``(status, complete, parked, parking_reason)``."""
    return _resolve_lifecycle(
        self.status,
        self.complete,
        self.parked,
        self.parking_reason,
    )

  def has_explicit_status(self) -> bool:
    """Returns True when ``status`` holds a canonical explicit status token.

    Returns:
      True for ``"ACTIVE"``, ``"PARKED"``, or ``"COMPLETE"``; False for legacy
      pre-GH-281 files lacking explicit lifecycle metadata.
    """
    return self.status in VALID_ACTIVE_STATUSES

  def is_complete(self) -> bool:
    """Returns True when the ticket lifecycle is completed (``finish`` ran).

    Returns:
      True when ``complete`` is True or ``status == "COMPLETE"``.
    """
    return self._resolved_lifecycle()[1]

  def is_active(self) -> bool:
    """Returns True when an in-progress (active or parked) ticket is running.

    Returns:
      True when the state is not complete and targets a post-T00 workflow step.
    """
    if self.is_complete():
      return False
    return bool(self.target) and self.target != "T00"

  def is_target_90(self) -> bool:
    """Returns True when the workflow is actively in Target 90 before finish.

    Returns:
      True when ``target == "T90"`` and ``is_complete()`` is False.
    """
    return self.target == "T90" and not self.is_complete()

  def is_post_landing(self) -> bool:
    """Returns True when the workflow is in T80 (land), T90, or COMPLETE state.

    Returns:
      True for ``T80``, ``T90``, or completed lifecycles so post-landing
      commits bypass Target 50 feature commit validation.
    """
    return self.target in POST_LANDING_TARGETS or self.is_complete()

  def to_dict(self) -> Dict[str, Any]:
    """Serializes this state into canonical camelCase JSON fields.

    Returns:
      Dictionary preserving GH-47 camelCase keys and explicit lifecycle status.
    """
    (
        effective_status,
        effective_complete,
        effective_parked,
        effective_reason,
    ) = self._resolved_lifecycle()

    payload: Dict[str, Any] = {
        "branch": self.branch,
        "complete": effective_complete,
        "previousTarget": self.previous_target,
        "skippedTargets": list(self.skipped_targets),
        "status": effective_status or "ACTIVE",
        "target": self.target,
        "targetName": self.target_name,
        "ticketId": self.ticket_id,
    }
    if effective_parked:
      payload["parked"] = True
      if effective_reason:
        payload["parkingReason"] = effective_reason
    return payload


class ActiveStateStore:
  """Atomic repository for ``.workflow/state/active_target.json``.

  Attributes:
    state_dir: Path to the workflow state directory.
  """

  def __init__(self, state_dir: str) -> None:
    """Initializes the store for a workflow state directory.

    Args:
      state_dir: Path to ``.workflow/state``.
    """
    self.state_dir: str = state_dir

  @classmethod
  def from_repo_root(cls, repo_root: str) -> "ActiveStateStore":
    """Constructs a store anchored at ``<repo_root>/.workflow/state``.

    Args:
      repo_root: Git repository root directory path.

    Returns:
      ``ActiveStateStore`` pointing to the repository state directory.
    """
    return cls(os.path.join(os.path.abspath(repo_root), ".workflow", "state"))

  @property
  def path(self) -> str:
    """Returns the full path to ``active_target.json`` inside ``state_dir``."""
    return os.path.join(self.state_dir, ACTIVE_TARGET_FILENAME)

  def exists(self) -> bool:
    """Returns True when ``active_target.json`` exists as a regular file."""
    return bool(self.state_dir) and os.path.isfile(self.path)

  def load_raw(self) -> Optional[Dict[str, Any]]:
    """Loads the raw JSON object dictionary from ``active_target.json``.

    Returns:
      Parsed dictionary when valid, or None on missing, unreadable, non-UTF8,
      non-object, or malformed JSON.
    """
    if not self.exists():
      return None
    try:
      with open(self.path, "r", encoding="utf-8") as f:
        data = json.load(f)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
      return None
    return data if isinstance(data, dict) else None

  def load(self) -> Optional[ActiveTargetState]:
    """Loads and normalizes ``ActiveTargetState`` from ``active_target.json``.

    Returns:
      ``ActiveTargetState`` when present and valid, otherwise None.
    """
    data = self.load_raw()
    if data is None:
      return None
    return ActiveTargetState.from_dict(data)

  def save(self, state: Union[ActiveTargetState, Dict[str, Any]]) -> str:
    """Atomically persists pretty-printed ``active_target.json`` to disk.

    Args:
      state: ``ActiveTargetState`` instance or raw dictionary to normalize.

    Returns:
      Path to the written ``active_target.json`` file.

    Raises:
      TypeError: If ``state`` is neither ``ActiveTargetState`` nor ``dict``.
      OSError: If atomic persistence fails.
    """
    if isinstance(state, ActiveTargetState):
      record = state
    elif isinstance(state, dict):
      record = ActiveTargetState.from_dict(state)
    else:
      raise TypeError(
          "ActiveStateStore.save expects ActiveTargetState or dict."
      )
    serialized = json.dumps(record.to_dict(), indent=2, sort_keys=True) + "\n"
    return atomic_write_text(
        self.path,
        serialized,
        prefix=".tmp_active_target_",
        suffix=".json",
    )

  def clear(self) -> bool:
    """Removes ``active_target.json`` when present.

    Returns:
      True when removed, or False when absent.
    """
    if not self.exists():
      return False
    try:
      os.unlink(self.path)
      return True
    except FileNotFoundError:
      return False


def is_state_card_filename(filename: str) -> bool:
  """Checks whether a filename is a Memento state card (Markdown or report JSON).

  Includes all ``*.md`` cards and numbered ``<NN>_*.json`` report/ticket cards
  while excluding runtime lock, receipt, and parking state JSON files.

  Args:
    filename: Base filename inside ``.workflow/state``.

  Returns:
    True when ``filename`` represents a user-facing or report state card.
  """
  if not filename or not isinstance(filename, str):
    return False
  if filename.endswith(".md"):
    return True
  if filename in INTERNAL_STATE_JSON_FILES:
    return False
  return bool(_NUMBERED_STATE_JSON_RE.match(filename))


def list_state_card_files(state_dir: str) -> List[str]:
  """Lists sorted Memento state card filenames present inside ``state_dir``.

  Args:
    state_dir: Path to the ``.workflow/state`` directory.

  Returns:
    Sorted list of regular state card filenames, or empty list when absent or
    unreadable.
  """
  if not state_dir or not os.path.isdir(state_dir):
    return []
  try:
    entries = os.listdir(state_dir)
  except OSError:
    return []
  return sorted(
      name
      for name in entries
      if is_state_card_filename(name)
      and os.path.isfile(os.path.join(state_dir, name))
  )
