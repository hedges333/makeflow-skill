"""Structured JSON ticket metadata modeling and persistence (GH-240).

Provides the immutable TicketMetadata value object and TicketStateStore
repository for .workflow/state/10_ticket.json, eliminating lossy Markdown
round-trips and brittle prose regex scraping across Makeflow consumers.
"""

from dataclasses import dataclass
import json
import os
from typing import Any, Dict, List, Optional, Tuple, Union

TICKET_JSON_FILENAME = "10_ticket.json"


def _clean_str(value: Any) -> str:
  """Coerces a non-null scalar field to a stripped string.

  Args:
    value: Raw field value from a parsed JSON mapping.

  Returns:
    Trimmed string representation, or empty string when value is None.
  """
  if value is None:
    return ""
  return str(value).strip()


def _normalize_labels(raw_labels: Any) -> Tuple[str, ...]:
  """Normalizes label collections containing strings or GitHub label dicts.

  Args:
    raw_labels: Raw labels value (list/tuple of strings or {'name': ...} dicts).

  Returns:
    Tuple of non-empty stripped label names.
  """
  if not isinstance(raw_labels, (list, tuple)):
    return ()
  normalized: List[str] = []
  for item in raw_labels:
    if isinstance(item, str):
      name = item.strip()
    elif isinstance(item, dict):
      name = _clean_str(item.get("name"))
    else:
      name = ""
    if name:
      normalized.append(name)
  return tuple(normalized)


def _extract_ticket_id(data: Dict[str, Any]) -> str:
  """Resolves canonical ticket_id from ticket_id, local id, or GitHub number.

  Args:
    data: Parsed JSON ticket dictionary.

  Returns:
    Normalized ticket identifier string, or empty string when absent.
  """
  explicit = _clean_str(data.get("ticket_id"))
  if explicit:
    return explicit
  local_id = _clean_str(data.get("id"))
  if local_id:
    return local_id
  raw_number = data.get("number")
  if isinstance(raw_number, int) and not isinstance(raw_number, bool):
    return f"GH-{raw_number}" if raw_number > 0 else ""
  if isinstance(raw_number, str):
    stripped = raw_number.strip()
    if stripped.isdigit() and int(stripped) > 0:
      return f"GH-{int(stripped)}"
  return ""


@dataclass(frozen=True)
class TicketMetadata:
  """Immutable structured metadata for an active Makeflow ticket.

  Attributes:
    ticket_id: Canonical ticket identifier (for example 'GH-240' or 'LOCAL-1').
    title: Human-readable ticket title from the tracker.
    url: Optional tracker URL for remote issues.
    branch: Optional Git branch associated with the ticket.
    labels: Tuple of normalized label names.
    source: Optional origin tag (for example 'github', 'local', or 'fallback').
  """

  ticket_id: str = ""
  title: str = ""
  url: str = ""
  branch: str = ""
  labels: Tuple[str, ...] = ()
  source: str = ""

  @classmethod
  def from_dict(cls, data: Dict[str, Any]) -> "TicketMetadata":
    """Constructs a normalized TicketMetadata instance from a dictionary.

    Supports canonical 10_ticket.json keys, LocalTicketStore ('id') records,
    and raw `gh issue view --json` payloads ('number' and dict 'labels').

    Args:
      data: Mapping of raw ticket metadata fields.

    Returns:
      Normalized immutable TicketMetadata instance.

    Raises:
      TypeError: If data is not a dictionary.
    """
    if not isinstance(data, dict):
      raise TypeError("TicketMetadata.from_dict expects a dict.")
    return cls(
        ticket_id=_extract_ticket_id(data),
        title=_clean_str(data.get("title")),
        url=_clean_str(data.get("url")),
        branch=_clean_str(data.get("branch")),
        labels=_normalize_labels(data.get("labels")),
        source=_clean_str(data.get("source")),
    )

  def to_dict(self) -> Dict[str, Any]:
    """Serializes this metadata record into a JSON-compatible dictionary.

    Returns:
      Dictionary with canonical string keys and labels serialized as a list.
    """
    return {
        "ticket_id": self.ticket_id,
        "title": self.title,
        "url": self.url,
        "branch": self.branch,
        "labels": list(self.labels),
        "source": self.source,
    }

  def has_distinct_title(self) -> bool:
    """Checks whether title is non-empty and differs from ticket_id.

    Returns:
      True when stripped title is non-empty and not equal to ticket_id
      case-insensitively.
    """
    clean_title = self.title.strip()
    if not clean_title:
      return False
    return clean_title.lower() != self.ticket_id.strip().lower()


class TicketStateStore:
  """Repository for loading and persisting .workflow/state/10_ticket.json.

  Attributes:
    state_dir: Absolute or relative path to the workflow state directory.
  """

  def __init__(self, state_dir: str) -> None:
    """Initializes the store for a specific workflow state directory.

    Args:
      state_dir: Path to .workflow/state directory.
    """
    self.state_dir: str = state_dir

  @classmethod
  def from_repo_root(cls, repo_root: str) -> "TicketStateStore":
    """Constructs a store anchored at <repo_root>/.workflow/state.

    Args:
      repo_root: Git repository root directory path.

    Returns:
      TicketStateStore pointing to the repository's .workflow/state directory.
    """
    return cls(os.path.join(os.path.abspath(repo_root), ".workflow", "state"))

  @property
  def path(self) -> str:
    """Returns the full path to 10_ticket.json inside state_dir."""
    return os.path.join(self.state_dir, TICKET_JSON_FILENAME)

  def exists(self) -> bool:
    """Returns True when 10_ticket.json exists as a regular file."""
    return bool(self.state_dir) and os.path.isfile(self.path)

  def load(self) -> Optional[TicketMetadata]:
    """Loads and normalizes TicketMetadata from 10_ticket.json.

    Returns:
      TicketMetadata when the file exists and contains a valid JSON object,
      otherwise None on missing, unreadable, non-UTF8, or malformed JSON.
    """
    if not self.exists():
      return None
    try:
      with open(self.path, "r", encoding="utf-8") as f:
        data = json.load(f)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
      return None
    if not isinstance(data, dict):
      return None
    return TicketMetadata.from_dict(data)

  def save(self, metadata: Union[TicketMetadata, Dict[str, Any]]) -> str:
    """Persists normalized TicketMetadata deterministically to 10_ticket.json.

    Args:
      metadata: TicketMetadata instance or raw dictionary to normalize and save.

    Returns:
      Path to the written 10_ticket.json file.

    Raises:
      TypeError: If metadata is neither TicketMetadata nor dict.
    """
    if isinstance(metadata, TicketMetadata):
      record = metadata
    elif isinstance(metadata, dict):
      record = TicketMetadata.from_dict(metadata)
    else:
      raise TypeError(
          "TicketStateStore.save expects TicketMetadata or dict."
      )
    os.makedirs(self.state_dir, exist_ok=True)
    with open(self.path, "w", encoding="utf-8") as f:
      json.dump(record.to_dict(), f, indent=2)
      f.write("\n")
    return self.path
