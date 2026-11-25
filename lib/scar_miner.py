"""Target 90 Rapid-Regret Miner engine for multi-tier scars and upstreaming (GH-117).

Provides validated scar modeling, 68-code ontology-to-filename mapping,
collision-free tier-scoped Markdown persistence and parsing, scoped JSON
configuration updates, human-gated GitHub issue upstreaming, and a BaseScript
CLI controller with explicit subcommands (SCAR-API-0001, SCAR-ARCH-05).
"""

from dataclasses import dataclass
import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from lib.agent_paths import AgentPathRegistry
from lib.base_script import BaseScript, ScriptArgumentParser
from lib.scar_candidate import ScarCandidateValidator, validate_socratic_probe
from lib.scar_ontology import DOMAIN_CATEGORY_ONTOLOGY, normalize_domain_category

UPSTREAM_REPO = "hedges333/makeflow-skill"

TIER_ID_PREFIXES: Dict[str, str] = {
    "project": "LOCAL",
    "local": "LOCAL",
    "user": "USER",
    "custom": "CUSTOM",
}

_SPOKE_GROUP_CODES: Dict[str, Tuple[str, ...]] = {
    "architecture.md": (
        "ARCH",
        "DIR",
        "API",
        "DEP",
        "PUB",
        "REFACTOR",
        "INTEROP",
        "MIXIN",
        "INV",
        "TYPE",
        "REGEX",
        "SYNTAX",
        "TMPL",
        "ASYNC",
        "CONC",
        "STATE",
        "BLOC",
        "RIV",
        "ERR",
        "FAIL",
        "PERF",
        "MATH",
        "GEOM",
    ),
    "data.md": (
        "DATA",
        "SCHEMA",
        "DB",
        "STORAGE",
        "WIRE",
        "NET",
    ),
    "ui.md": (
        "UI",
        "FLUTTER",
        "NAV",
        "GENUI",
        "A2UI",
        "A11Y",
        "I18N",
        "WEB",
        "SSR",
    ),
    "testing.md": (
        "TEST",
        "VERIF",
        "AST",
    ),
    "security.md": (
        "SEC",
        "AUTH",
    ),
    "system.md": (
        "SYS",
        "ENV",
        "TIME",
        "BUILD",
        "OBS",
        "OTEL",
        "DIAG",
    ),
    "git_and_shell.md": (
        "GIT",
        "SH",
        "SCRIPT",
        "CI",
        "HOOK",
    ),
    "tooling.md": (
        "TOOL",
        "HCI",
        "DOC",
        "SKILL",
    ),
    "process.md": (
        "PROC",
        "METRIC",
        "GOV",
        "REVIEW",
        "DIFF",
        "COLLAB",
        "META",
        "CRAFT",
        "PEDAGOGY",
    ),
}

CATEGORY_SPOKE_FILES: Dict[str, str] = {
    code: filename
    for filename, codes in _SPOKE_GROUP_CODES.items()
    for code in codes
}

_SAFE_FILENAME_RE = re.compile(r"^[a-z0-9_]+\.md$")
_TRAVERSAL_RE = re.compile(r"[/\\]|\.\.|\x00")
_SCAR_HEADING_RE = re.compile(
    r"^###\s+(SCAR-[A-Z0-9]+(?:-[A-Z0-9]+)*):\s*(.+?)\s*$"
)
_FIELD_BULLET_RE = re.compile(
    r"^(?:-\s*)?\*\*(Category|Applies When|Wound|Trap|Circuit Breaker|Permanent Reflex|Reflex|Socratic Probe)\*\*:\s*(.*)$",
    re.IGNORECASE,
)
_LEADING_SCAR_ID_RE = re.compile(r"^\s*SCAR-(?:[A-Z0-9]+-)+\d+\s*[:\-–—]*\s*")
_TIER_SCOPED_SCAR_ID_RE = re.compile(
    r"\bSCAR-(?:LOCAL|USER|CUSTOM)-(?:[A-Z0-9]+-)*\d+\b:?\s*"
)
_FENCE_OPEN_RE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
_GITHUB_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_GH_SUBPROCESS_TIMEOUT_SECONDS = 30
_FILENAME_DEFAULT_DOMAIN: Dict[str, str] = {
    "architecture": "ARCH",
    "data": "DATA",
    "data_and_database": "DATA",
    "ui": "UI",
    "testing": "TEST",
    "security": "SEC",
    "security_and_auth": "SEC",
    "system": "SYS",
    "system_and_os": "SYS",
    "git_and_shell": "GIT",
    "tooling": "TOOL",
    "tooling_and_hci": "TOOL",
    "process": "PROC",
    "local": "ARCH",
}


def category_to_filename(category: str) -> str:
  """Map a domain category code or prose alias to a safe categorized Markdown filename.

  Args:
    category: Canonical domain code (e.g. ``DB``, ``PROC``) or prose alias
      (e.g. ``Database``, ``Testing``).

  Returns:
    Safe basename ending in ``.md`` (for example ``data.md`` or ``process.md``).

  Raises:
    ValueError: If ``category`` is empty or contains path traversal characters.
  """
  if not isinstance(category, str) or not category.strip():
    raise ValueError("Category must be a non-empty string.")
  cleaned = category.strip()
  if _TRAVERSAL_RE.search(cleaned):
    raise ValueError(
        f"Category '{category}' contains forbidden path traversal characters."
    )
  domain = normalize_domain_category(cleaned)
  filename = CATEGORY_SPOKE_FILES.get(domain, "architecture.md")
  if not _SAFE_FILENAME_RE.match(filename):
    raise ValueError(f"Resolved unsafe scar filename '{filename}'.")
  return filename


def _validate_directory_path(path: Any, label: str) -> str:
  """Validate that a directory path is a non-empty string without null bytes.

  Args:
    path: Candidate directory path value.
    label: Parameter label used in diagnostic error messages.

  Returns:
    Trimmed directory path string.

  Raises:
    ValueError: If ``path`` is not a non-empty string or contains null bytes.
  """
  if not isinstance(path, str) or not path.strip():
    raise ValueError(f"{label} must be a non-empty string path.")
  if "\x00" in path:
    raise ValueError(f"{label} must not contain embedded null bytes.")
  return path.strip()


def _update_fence_state(
    line: str,
    in_fence: bool,
    fence_char: str,
    fence_len: int,
) -> Tuple[bool, str, int]:
  """Advance Markdown fenced code block state across backtick and tilde fences."""
  stripped = line.strip()
  if in_fence:
    if (
        stripped
        and len(stripped) >= fence_len
        and set(stripped) == {fence_char}
    ):
      return False, "", 0
    return True, fence_char, fence_len
  match = _FENCE_OPEN_RE.match(line)
  if match:
    delimiter = match.group(1)
    info_string = match.group(2)
    ch = delimiter[0]
    if ch == "`" and "`" in info_string:
      return False, "", 0
    return True, ch, len(delimiter)
  return False, "", 0


def _strip_edge_blank_lines(text: str) -> str:
  """Normalize line endings and strip leading/trailing blank lines without altering indentation."""
  raw_lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
  while raw_lines and not raw_lines[0].strip():
    raw_lines.pop(0)
  while raw_lines and not raw_lines[-1].strip():
    raw_lines.pop()
  if len(raw_lines) == 1:
    return raw_lines[0].strip()
  return "\n".join(raw_lines)


def _format_markdown_field(label: str, value: str) -> str:
  """Format one scar Markdown bullet preserving multi-line indentation and fenced blocks."""
  cleaned = _strip_edge_blank_lines(value)
  first_line = cleaned.split("\n", 1)[0]
  if _FENCE_OPEN_RE.match(first_line):
    return f"- **{label}**:\n{cleaned}"
  return f"- **{label}**: {cleaned}"


@dataclass(frozen=True)
class MinedScarRecord:
  """Immutable validated synthetic scar candidate captured at Target 90.

  Attributes:
    title: Short descriptive scar title.
    category: Canonical domain category code or prose alias.
    wound: Failure mode and observable operational impact.
    trap: False assumption or shortcut that caused the failure.
    reflex: Mandatory permanent engineering rule preventing recurrence.
    socratic_probe: One to three Simple English questions ending in ``?``.
    circuit_breaker: Optional explicit halt trigger before the permanent reflex.
    applies_when: Optional trigger scope summary.
  """

  title: str
  category: str
  wound: str
  trap: str
  reflex: str
  socratic_probe: str
  circuit_breaker: str = ""
  applies_when: str = ""


def validate_mined_scar(record: MinedScarRecord) -> List[str]:
  """Validate a MinedScarRecord against anatomy, ontology, and Simple English rules.

  Delegates prose and probe validation to ``ScarCandidateValidator`` and
  ``validate_socratic_probe()`` in ``lib/scar_candidate.py``.

  Args:
    record: Candidate scar record to validate.

  Returns:
    List of diagnostic error strings (empty when valid).
  """
  errors: List[str] = []
  if not isinstance(record.title, str) or not record.title.strip():
    errors.append("Scar field 'title' must be a non-empty string.")
  elif "\n" in record.title or "\r" in record.title:
    errors.append("Scar field 'title' must be a single line.")

  if record.applies_when:
    if not isinstance(record.applies_when, str):
      errors.append("Scar field 'applies_when' must be a string.")
    elif "\n" in record.applies_when or "\r" in record.applies_when:
      errors.append("Scar field 'applies_when' must be a single line.")

  try:
    category_to_filename(record.category)
  except ValueError as exc:
    errors.append(str(exc))

  candidate = {
      "id": "SCAR-DRAFT",
      "raw_wound": record.wound,
      "raw_trap": record.trap,
      "raw_circuit_breaker": record.circuit_breaker,
      "raw_reflex": record.reflex,
      "wound": record.wound,
      "trap": record.trap,
      "circuit_breaker": record.circuit_breaker,
      "reflex": record.reflex,
      "socratic_probe": record.socratic_probe,
  }
  validator = ScarCandidateValidator()
  for err in validator.validate(candidate):
    if err not in errors:
      errors.append(err)
  for err in validate_socratic_probe(record.socratic_probe, scar_id="SCAR-DRAFT"):
    if err not in errors:
      errors.append(err)
  return errors


def _atomic_write_text(file_path: str, content: str) -> None:
  """Write UTF-8 text atomically within the destination file's parent directory."""
  parent_dir = os.path.dirname(os.path.abspath(file_path))
  os.makedirs(parent_dir, exist_ok=True)
  tmp_fd, tmp_path = tempfile.mkstemp(
      dir=parent_dir, prefix=".tmp_makeflow_", suffix=".tmp"
  )
  try:
    with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
      fh.write(content)
    os.replace(tmp_path, file_path)
  except Exception:
    if os.path.exists(tmp_path):
      try:
        os.unlink(tmp_path)
      except OSError:
        pass
    raise


class MarkdownScarStore:
  """Persists and parses categorized Markdown scar files without binary DB side effects."""

  def _normalize_tier(self, tier: str) -> Tuple[str, str]:
    """Normalize storage tier name to ``(canonical_tier, id_scope_prefix)``."""
    cleaned = str(tier or "project").strip().lower()
    if cleaned not in TIER_ID_PREFIXES:
      valid = ", ".join(sorted(TIER_ID_PREFIXES.keys()))
      raise ValueError(f"Unsupported scar tier '{tier}'. Expected one of: {valid}.")
    canonical = "project" if cleaned == "local" else cleaned
    return canonical, TIER_ID_PREFIXES[cleaned]

  def _resolve_safe_file_path(self, target_dir: str, filename: str) -> str:
    """Resolve and verify that ``filename`` stays strictly inside ``target_dir``."""
    abs_dir = os.path.abspath(os.path.expanduser(target_dir))
    real_dir = os.path.realpath(abs_dir)
    candidate_path = os.path.realpath(os.path.join(real_dir, filename))
    if os.path.commonpath([real_dir, candidate_path]) != real_dir:
      raise ValueError(f"Resolved scar path '{candidate_path}' escapes '{real_dir}'.")
    return os.path.join(abs_dir, filename)

  def _collect_existing_ids(self, target_dir: str) -> set:
    """Scan existing ``*.md`` files in ``target_dir`` for allocated scar IDs."""
    existing: set = set()
    if not target_dir or not os.path.isdir(target_dir):
      return existing
    for entry in sorted(os.listdir(target_dir)):
      if not entry.endswith(".md"):
        continue
      full_path = os.path.join(target_dir, entry)
      if not os.path.isfile(full_path):
        continue
      try:
        in_fence = False
        fence_char = ""
        fence_len = 0
        with open(full_path, "r", encoding="utf-8") as fh:
          for raw_line in fh:
            line = raw_line.rstrip("\r\n")
            if not in_fence:
              match = _SCAR_HEADING_RE.match(line.strip())
              if match:
                existing.add(match.group(1))
                continue
            in_fence, fence_char, fence_len = _update_fence_state(
                line, in_fence, fence_char, fence_len
            )
      except (OSError, UnicodeDecodeError):
        continue
    return existing

  def allocate_scar_id(
      self,
      target_dir: str,
      category: str,
      tier: str = "project",
  ) -> str:
    """Allocate a collision-free tier-scoped 4-digit scar ID.

    Args:
      target_dir: Directory containing Markdown scar files.
      category: Domain category code or prose alias.
      tier: Storage tier (``project``, ``user``, or ``custom``).

    Returns:
      Unique scar identifier such as ``SCAR-LOCAL-DB-0001``.
    """
    _, scope_prefix = self._normalize_tier(tier)
    domain = normalize_domain_category(category)
    prefix = f"SCAR-{scope_prefix}-{domain}"
    existing = self._collect_existing_ids(target_dir)
    seq = 1
    while True:
      candidate = f"{prefix}-{seq:04d}"
      if candidate not in existing:
        return candidate
      seq += 1

  def format_markdown_entry(
      self,
      scar_id: str,
      record: MinedScarRecord,
  ) -> str:
    """Format a validated scar record in canonical four-part plus optional Circuit Breaker Markdown.

    Args:
      scar_id: Allocated tier-scoped scar identifier.
      record: Validated ``MinedScarRecord`` instance.

    Returns:
      Canonical Markdown section ending with a trailing newline.
    """
    domain = normalize_domain_category(record.category)
    lines = [
        f"### {scar_id}: {record.title.strip()}",
        "",
        f"- **Category**: `{domain}`",
    ]
    if record.applies_when and record.applies_when.strip():
      lines.append(f"- **Applies When**: {record.applies_when.strip()}")
    lines.append(_format_markdown_field("Wound", record.wound))
    lines.append(_format_markdown_field("Trap", record.trap))
    if record.circuit_breaker and record.circuit_breaker.strip():
      lines.append(_format_markdown_field("Circuit Breaker", record.circuit_breaker))
    lines.append(_format_markdown_field("Permanent Reflex", record.reflex))
    lines.append(_format_markdown_field("Socratic Probe", record.socratic_probe))
    return "\n".join(lines) + "\n"

  def save_scar(
      self,
      target_dir: str,
      record: MinedScarRecord,
      tier: str = "project",
  ) -> Dict[str, Any]:
    """Validate and atomically append a scar to its categorized Markdown file.

    Args:
      target_dir: Destination directory for categorized ``*.md`` files.
      record: Candidate scar record to validate and persist.
      tier: Storage tier (``project``, ``user``, or ``custom``).

    Returns:
      Structured receipt with ``scarId``, ``category``, ``filePath``, ``tier``,
      and ``targetDir``.

    Raises:
      ValueError: If validation fails, ``target_dir`` is invalid or an existing
        non-directory file, or path resolution escapes ``target_dir``.
    """
    clean_target_dir = _validate_directory_path(target_dir, "target_dir")
    errors = validate_mined_scar(record)
    if errors:
      raise ValueError("Invalid scar candidate: " + "; ".join(errors))

    canonical_tier, _ = self._normalize_tier(tier)
    domain = normalize_domain_category(record.category)
    filename = category_to_filename(record.category)
    abs_dir = os.path.abspath(os.path.expanduser(clean_target_dir))
    if (os.path.exists(abs_dir) or os.path.islink(abs_dir)) and not os.path.isdir(
        abs_dir
    ):
      raise ValueError(
          f"Target scar directory '{abs_dir}' exists and is not a directory."
      )
    os.makedirs(abs_dir, exist_ok=True)
    file_path = self._resolve_safe_file_path(abs_dir, filename)
    scar_id = self.allocate_scar_id(abs_dir, domain, tier=canonical_tier)
    entry = self.format_markdown_entry(scar_id, record)

    if os.path.isfile(file_path):
      with open(file_path, "r", encoding="utf-8") as fh:
        existing_content = fh.read()
      separator = "\n\n" if existing_content.strip() else ""
      updated = existing_content.rstrip() + separator + entry
    else:
      heading_title = os.path.splitext(filename)[0].replace("_", " ").title()
      updated = f"# Synthetic Scars: {heading_title}\n\n{entry}"

    _atomic_write_text(file_path, updated)
    return {
        "scarId": scar_id,
        "category": domain,
        "filePath": file_path,
        "tier": canonical_tier,
        "targetDir": abs_dir,
    }

  def _infer_category_from_id_or_file(
      self,
      scar_id: str,
      explicit_category: str,
      file_path: str,
      title: str,
      wound: str,
      trap: str,
      reflex: str,
  ) -> str:
    """Resolve canonical domain category from explicit tag, tier scar ID, or filename."""
    cleaned_explicit = explicit_category.strip().strip("`")
    if cleaned_explicit:
      return normalize_domain_category(
          cleaned_explicit,
          scar_id=scar_id,
          title=title,
          wound=wound,
          trap=trap,
          reflex=reflex,
      )
    tier_match = re.match(
        r"^SCAR-(?:LOCAL|USER|CUSTOM)-([A-Z0-9]+)-\d+$",
        scar_id.strip().upper(),
    )
    if tier_match and tier_match.group(1) in DOMAIN_CATEGORY_ONTOLOGY:
      return tier_match.group(1)
    stem = os.path.splitext(os.path.basename(file_path))[0].lower()
    fallback = _FILENAME_DEFAULT_DOMAIN.get(stem, "")
    return normalize_domain_category(
        fallback,
        scar_id=scar_id,
        title=title,
        wound=wound,
        trap=trap,
        reflex=reflex,
    )

  def parse_markdown_file(
      self,
      file_path: str,
      source_tier: str = "local",
  ) -> List[Dict[str, Any]]:
    """Parse structured synthetic scar dictionaries from one Markdown file.

    Args:
      file_path: Path to a categorized or local Markdown scar file.
      source_tier: Source tier label assigned to parsed rows (e.g. ``local``,
        ``user``, or ``custom``).

    Returns:
      List of structured scar dictionaries ready for SQLite FTS5 indexing.
    """
    if not file_path or not os.path.isfile(file_path):
      return []
    try:
      with open(file_path, "r", encoding="utf-8") as fh:
        lines = fh.readlines()
    except (OSError, UnicodeDecodeError):
      return []

    records: List[Dict[str, Any]] = []
    current_id: Optional[str] = None
    current_title = ""
    fields: Dict[str, List[str]] = {}
    active_field: Optional[str] = None

    def _join_field(key: str) -> str:
      raw_lines = list(fields.get(key, []))
      while raw_lines and not raw_lines[0].strip():
        raw_lines.pop(0)
      while raw_lines and not raw_lines[-1].strip():
        raw_lines.pop()
      return "\n".join(raw_lines)

    def _flush() -> None:
      if not current_id:
        return
      wound = _join_field("wound")
      trap = _join_field("trap")
      cb = _join_field("circuit_breaker")
      reflex = _join_field("reflex")
      probe = _join_field("socratic_probe")
      applies_when = _join_field("applies_when").strip()
      explicit_cat = _join_field("category").strip()
      if not (wound and trap and reflex and probe):
        return
      domain = self._infer_category_from_id_or_file(
          scar_id=current_id,
          explicit_category=explicit_cat,
          file_path=file_path,
          title=current_title,
          wound=wound,
          trap=trap,
          reflex=reflex,
      )
      records.append(
          {
              "id": current_id,
              "title": current_title,
              "category": domain,
              "category_slug": domain.lower(),
              "applies_when": applies_when,
              "wound": wound,
              "trap": trap,
              "circuit_breaker": cb,
              "reflex": reflex,
              "socratic_probe": probe,
              "source": source_tier,
              "file_path": os.path.abspath(file_path),
          }
      )

    field_name_map = {
        "category": "category",
        "applies when": "applies_when",
        "wound": "wound",
        "trap": "trap",
        "circuit breaker": "circuit_breaker",
        "permanent reflex": "reflex",
        "reflex": "reflex",
        "socratic probe": "socratic_probe",
    }

    in_fence = False
    fence_char = ""
    fence_len = 0

    for raw_line in lines:
      line = raw_line.rstrip("\r\n")
      if in_fence:
        if active_field:
          fields[active_field].append(line)
        in_fence, fence_char, fence_len = _update_fence_state(
            line, in_fence, fence_char, fence_len
        )
        continue

      stripped = line.strip()
      heading_match = _SCAR_HEADING_RE.match(stripped)
      if heading_match:
        _flush()
        current_id = heading_match.group(1).strip()
        current_title = heading_match.group(2).strip()
        fields = {}
        active_field = None
        continue
      if not current_id:
        in_fence, fence_char, fence_len = _update_fence_state(
            line, in_fence, fence_char, fence_len
        )
        continue
      if stripped.startswith("## ") or stripped == "---":
        _flush()
        current_id = None
        current_title = ""
        fields = {}
        active_field = None
        continue
      bullet_match = _FIELD_BULLET_RE.match(stripped)
      if bullet_match:
        canonical_key = field_name_map[bullet_match.group(1).lower()]
        active_field = canonical_key
        fields.setdefault(canonical_key, [])
        first_text = bullet_match.group(2).strip()
        if first_text:
          fields[canonical_key].append(first_text)
          in_fence, fence_char, fence_len = _update_fence_state(
              first_text, in_fence, fence_char, fence_len
          )
      elif active_field:
        fields[active_field].append(line if stripped else "")
        in_fence, fence_char, fence_len = _update_fence_state(
            line, in_fence, fence_char, fence_len
        )

    _flush()
    return records

  def parse_directory(
      self,
      directory: str,
      source_tier: str = "local",
  ) -> List[Dict[str, Any]]:
    """Parse all ``*.md`` scar files inside one directory in lexical filename order.

    Args:
      directory: Path to a directory containing Markdown scar files.
      source_tier: Source tier label assigned to parsed rows.

    Returns:
      Aggregated list of parsed scar dictionaries.
    """
    if not directory or not os.path.isdir(directory):
      return []
    parsed: List[Dict[str, Any]] = []
    for entry in sorted(os.listdir(directory)):
      if not entry.endswith(".md"):
        continue
      full_path = os.path.join(directory, entry)
      if os.path.isfile(full_path):
        parsed.extend(
            self.parse_markdown_file(full_path, source_tier=source_tier)
        )
    return parsed


class ScarConfigStore:
  """Persists custom scar directories into user-global and project-local JSON configs."""

  def __init__(self, registry: Optional[AgentPathRegistry] = None) -> None:
    """Initialize ScarConfigStore with an AgentPathRegistry collaborator.

    Args:
      registry: Optional ``AgentPathRegistry`` instance.
    """
    self.registry = registry or AgentPathRegistry()

  def _append_directory_to_file(self, config_path: str, directory: str) -> str:
    """Read, update ``scars.directories`` idempotently, and atomically write one JSON config."""
    abs_cfg = os.path.abspath(os.path.expanduser(config_path))
    data: Dict[str, Any] = {}
    if os.path.isfile(abs_cfg):
      with open(abs_cfg, "r", encoding="utf-8") as fh:
        raw_content = fh.read().strip()
      if raw_content:
        loaded = json.loads(raw_content)
        if not isinstance(loaded, dict):
          raise ValueError(
              f"Configuration file '{abs_cfg}' must contain a JSON object."
          )
        data = loaded

    scars_section = data.get("scars")
    if not isinstance(scars_section, dict):
      scars_section = {}
      data["scars"] = scars_section

    existing_dirs = scars_section.get("directories")
    if not isinstance(existing_dirs, list):
      existing_dirs = []
    cleaned_existing = [
        item.strip()
        for item in existing_dirs
        if isinstance(item, str) and item.strip()
    ]
    seen_norm = {
        os.path.normpath(os.path.expanduser(item)) for item in cleaned_existing
    }
    target_norm = os.path.normpath(os.path.expanduser(directory))
    if target_norm not in seen_norm:
      cleaned_existing.append(directory)
    scars_section["directories"] = cleaned_existing

    _atomic_write_text(abs_cfg, json.dumps(data, indent=2) + "\n")
    return abs_cfg

  def add_scar_directory(
      self,
      directory: str,
      scope: str,
      workspace_dir: Optional[str] = None,
      agent: Optional[str] = None,
  ) -> Dict[str, Any]:
    """Persist a custom scar directory into ``user-global``, ``project-local``, or ``both``.

    Args:
      directory: Custom scar directory path to persist.
      scope: Target scope (``user-global``, ``project-local``, or ``both``).
      workspace_dir: Project root path (required for ``project-local`` and ``both``).
      agent: Optional agent name override for user-global config path lookup.

    Returns:
      Dictionary with ``directory``, ``scope``, and ``updatedFiles``.

    Raises:
      ValueError: If ``directory``, ``scope``, or required ``workspace_dir`` is invalid.
    """
    clean_dir = _validate_directory_path(directory, "directory")
    clean_scope = str(scope or "").strip().lower()
    if clean_scope not in ("user-global", "project-local", "both"):
      raise ValueError(
          f"Invalid scope '{scope}'. Expected 'user-global', 'project-local', or 'both'."
      )

    targets: List[str] = []
    if clean_scope in ("user-global", "both"):
      profile = self.registry.get_profile(agent)
      targets.append(profile.global_config_path)
    if clean_scope in ("project-local", "both"):
      clean_ws = _validate_directory_path(workspace_dir, "workspace_dir")
      ws_abs = os.path.abspath(os.path.expanduser(clean_ws))
      targets.append(os.path.join(ws_abs, ".workflow", "config.json"))

    updated = [
        self._append_directory_to_file(cfg_path, clean_dir)
        for cfg_path in targets
    ]
    return {
        "directory": clean_dir,
        "scope": clean_scope,
        "updatedFiles": updated,
    }


class UpstreamIssuePublisher:
  """Verifies GitHub CLI access and submits human-approved community scar issues."""

  def __init__(self, repo: str = UPSTREAM_REPO) -> None:
    """Initialize UpstreamIssuePublisher targeting a GitHub repository.

    Args:
      repo: Target ``owner/repo`` slug (defaults to ``hedges333/makeflow-skill``).

    Raises:
      ValueError: If ``repo`` is not a valid ``owner/repo`` slug.
    """
    if not isinstance(repo, str) or not repo.strip():
      raise ValueError("Upstream repository must be a non-empty 'owner/repo' slug.")
    cleaned_repo = repo.strip()
    if cleaned_repo.startswith("-") or not _GITHUB_REPO_RE.match(cleaned_repo):
      raise ValueError(
          f"Invalid GitHub repository slug '{repo}'. Expected 'owner/repo'."
      )
    self.repo = cleaned_repo

  def _clean_title(self, title: str) -> str:
    """Strip any leading scar ID prefix and tier-scoped scar IDs from a title.

    Args:
      title: Raw candidate scar title.

    Returns:
      Non-empty cleaned title text without assigned scar IDs.

    Raises:
      ValueError: If removing scar IDs leaves an empty title.
    """
    cleaned = title or ""
    while True:
      updated = _LEADING_SCAR_ID_RE.sub("", cleaned)
      if updated == cleaned:
        break
      cleaned = updated
    cleaned = _TIER_SCOPED_SCAR_ID_RE.sub("", cleaned).strip()
    if not cleaned:
      raise ValueError(
          "Scar title must contain descriptive text beyond an assigned scar ID."
      )
    return cleaned

  def _strip_local_ids(self, text: str) -> str:
    """Strip tier-scoped local/user/custom scar IDs while preserving Tier 1 references."""
    return _TIER_SCOPED_SCAR_ID_RE.sub("", text or "").strip()

  def format_issue_payload(self, record: MinedScarRecord) -> Dict[str, Any]:
    """Build an integer-ID-free GitHub issue title and Markdown body for a scar.

    Args:
      record: Validated ``MinedScarRecord`` instance.

    Returns:
      Dictionary with ``repo``, ``title``, ``labels``, and ``body``.

    Raises:
      ValueError: If the scar title has no descriptive text beyond a scar ID.
    """
    domain = normalize_domain_category(record.category)
    clean_title = self._clean_title(record.title)
    title = f"[SCAR] [{domain}] {clean_title}"

    body_lines = [
        "## Proposed Synthetic Scar",
        "",
        f"- **Category**: `{domain}`",
        f"- **Title**: {clean_title}",
        "",
        f"- **Wound**: {self._strip_local_ids(record.wound)}",
        f"- **Trap**: {self._strip_local_ids(record.trap)}",
    ]
    if record.circuit_breaker and record.circuit_breaker.strip():
      body_lines.append(
          f"- **Circuit Breaker**: {self._strip_local_ids(record.circuit_breaker)}"
      )
    body_lines.append(
        f"- **Permanent Reflex**: {self._strip_local_ids(record.reflex)}"
    )
    body_lines.append(
        f"- **Socratic Probe**: {self._strip_local_ids(record.socratic_probe)}"
    )
    body_lines.extend(
        [
            "",
            "> Canonical scar ID assignment is deferred to maintainers on merge.",
        ]
    )
    return {
        "repo": self.repo,
        "title": title,
        "labels": ["enhancement"],
        "body": "\n".join(body_lines) + "\n",
    }

  def check_access(self) -> Dict[str, Any]:
    """Verify that ``gh`` CLI is installed, authenticated, and can view the upstream repo.

    Returns:
      Status dictionary with ``ready``, ``ghInstalled``, ``authenticated``,
      ``repoAccessible``, ``repo``, and ``reason``.
    """
    if not shutil.which("gh"):
      return {
          "ready": False,
          "ghInstalled": False,
          "authenticated": False,
          "repoAccessible": False,
          "repo": self.repo,
          "reason": "GitHub CLI (gh) is not installed on PATH.",
      }

    try:
      auth_res = subprocess.run(
          ["gh", "auth", "status"],
          capture_output=True,
          text=True,
          check=False,
          timeout=_GH_SUBPROCESS_TIMEOUT_SECONDS,
      )
    except (subprocess.TimeoutExpired, OSError) as exc:
      return {
          "ready": False,
          "ghInstalled": True,
          "authenticated": False,
          "repoAccessible": False,
          "repo": self.repo,
          "reason": f"GitHub CLI auth check failed: {exc}",
      }
    if auth_res.returncode != 0:
      detail = (auth_res.stderr or auth_res.stdout or "not authenticated").strip()
      return {
          "ready": False,
          "ghInstalled": True,
          "authenticated": False,
          "repoAccessible": False,
          "repo": self.repo,
          "reason": f"GitHub CLI is not authenticated: {detail}",
      }

    try:
      repo_res = subprocess.run(
          ["gh", "repo", "view", self.repo, "--json", "nameWithOwner"],
          capture_output=True,
          text=True,
          check=False,
          timeout=_GH_SUBPROCESS_TIMEOUT_SECONDS,
      )
    except (subprocess.TimeoutExpired, OSError) as exc:
      return {
          "ready": False,
          "ghInstalled": True,
          "authenticated": True,
          "repoAccessible": False,
          "repo": self.repo,
          "reason": f"Cannot access upstream repository '{self.repo}': {exc}",
      }
    if repo_res.returncode != 0:
      detail = (repo_res.stderr or repo_res.stdout or "repo inaccessible").strip()
      return {
          "ready": False,
          "ghInstalled": True,
          "authenticated": True,
          "repoAccessible": False,
          "repo": self.repo,
          "reason": f"Cannot access upstream repository '{self.repo}': {detail}",
      }

    return {
        "ready": True,
        "ghInstalled": True,
        "authenticated": True,
        "repoAccessible": True,
        "repo": self.repo,
        "reason": "OK",
    }

  def submit_issue(
      self,
      record: MinedScarRecord,
      approved: bool = False,
  ) -> Dict[str, Any]:
    """Submit a human-approved synthetic scar proposal via ``gh issue create --body-file``.

    Args:
      record: Validated ``MinedScarRecord`` instance.
      approved: Explicit human approval flag (required by SCAR-PROC-108).

    Returns:
      Submission receipt containing ``submitted``, ``repo``, ``issueUrl``,
      ``title``, and ``body``.

    Raises:
      PermissionError: If ``approved`` is False.
      ValueError: If ``record`` fails scar validation.
      RuntimeError: If ``gh`` preflight or ``gh issue create`` fails.
    """
    if not approved:
      raise PermissionError(
          "Explicit human approval (--approved) is required before submitting "
          "an upstream GitHub issue (SCAR-PROC-108)."
      )

    errors = validate_mined_scar(record)
    if errors:
      raise ValueError("Invalid scar candidate: " + "; ".join(errors))

    access = self.check_access()
    if not access["ready"]:
      raise RuntimeError(access["reason"])

    payload = self.format_issue_payload(record)
    with tempfile.TemporaryDirectory() as tmp_dir:
      body_path = os.path.join(tmp_dir, "issue_body.md")
      with open(body_path, "w", encoding="utf-8") as fh:
        fh.write(payload["body"])

      cmd = [
          "gh",
          "issue",
          "create",
          "--repo",
          self.repo,
          "--title",
          payload["title"],
          "--body-file",
          body_path,
      ]
      for label in payload["labels"]:
        cmd.extend(["--label", label])

      try:
        create_res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=_GH_SUBPROCESS_TIMEOUT_SECONDS,
        )
      except (subprocess.TimeoutExpired, OSError) as exc:
        raise RuntimeError(f"gh issue create failed: {exc}") from exc
      if create_res.returncode != 0:
        detail = (create_res.stderr or create_res.stdout or "unknown error").strip()
        raise RuntimeError(f"gh issue create failed: {detail}")

    return {
        "submitted": True,
        "repo": self.repo,
        "issueUrl": (create_res.stdout or "").strip(),
        "title": payload["title"],
        "body": payload["body"],
    }


def _add_scar_record_args(parser: ScriptArgumentParser) -> None:
  """Register shared scar field CLI flags on save and upstream-submit subparsers."""
  parser.add_argument(
      "--json-file",
      default=None,
      help="Optional path to a JSON file containing scar fields.",
  )
  parser.add_argument("--title", default="", help="Short descriptive scar title.")
  parser.add_argument("--category", default="", help="Domain category code or alias.")
  parser.add_argument("--wound", default="", help="Failure wound description.")
  parser.add_argument("--trap", default="", help="Root-cause false assumption.")
  parser.add_argument(
      "--circuit-breaker",
      default="",
      help="Optional explicit halt trigger before the permanent reflex.",
  )
  parser.add_argument("--reflex", default="", help="Permanent prevention reflex.")
  parser.add_argument(
      "--socratic-probe",
      default="",
      help="One to three Simple English Socratic probe questions.",
  )
  parser.add_argument(
      "--applies-when",
      default="",
      help="Optional trigger scope summary.",
  )


class ScarMinerScript(BaseScript):
  """BaseScript CLI entrypoint for Target 90 Rapid-Regret Miner workflows."""

  def __init__(self) -> None:
    """Initialize ScarMinerScript with empty parsed argument namespace."""
    super().__init__()
    self.parsed_args: Any = None

  def _build_parser(self) -> ScriptArgumentParser:
    """Construct the subcommand CLI parser without boolean mode flags (SCAR-API-0001)."""
    parser = ScriptArgumentParser(
        prog="scar_miner.py",
        description="Target 90 Rapid-Regret Miner CLI for multi-tier scars and upstreaming.",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    paths_p = subparsers.add_parser(
        "paths",
        help="Resolve project, user-wide agent, and legacy Makeflow paths.",
    )
    paths_p.add_argument("--workspace", default=None, help="Project root directory.")
    paths_p.add_argument("--agent", default=None, help="Agent name override.")
    paths_p.add_argument("--home", default=None, help="Home directory override.")

    save_p = subparsers.add_parser(
        "save",
        help="Validate and save a scar to a categorized Markdown file.",
    )
    save_p.add_argument(
        "--tier",
        required=True,
        choices=("project", "user", "custom"),
        help="Target storage tier.",
    )
    save_p.add_argument("--workspace", default=None, help="Project root directory.")
    save_p.add_argument("--custom-dir", default=None, help="Custom directory for custom tier.")
    save_p.add_argument("--agent", default=None, help="Agent name override.")
    save_p.add_argument("--home", default=None, help="Home directory override.")
    _add_scar_record_args(save_p)

    cfg_p = subparsers.add_parser(
        "save-config",
        help="Persist a custom scar directory into user-global and/or project-local config.",
    )
    cfg_p.add_argument("--dir", required=True, dest="directory", help="Custom scar directory path.")
    cfg_p.add_argument(
        "--scope",
        required=True,
        choices=("user-global", "project-local", "both"),
        help="Configuration scope to update.",
    )
    cfg_p.add_argument("--workspace", default=None, help="Project root directory.")
    cfg_p.add_argument("--agent", default=None, help="Agent name override.")
    cfg_p.add_argument("--home", default=None, help="Home directory override.")

    check_p = subparsers.add_parser(
        "upstream-check",
        help="Check gh CLI installation, authentication, and upstream repository access.",
    )
    check_p.add_argument(
        "--repo",
        default=UPSTREAM_REPO,
        help="Upstream GitHub owner/repo slug.",
    )

    submit_p = subparsers.add_parser(
        "upstream-submit",
        help="Submit a human-approved synthetic scar issue to the upstream repository.",
    )
    submit_p.add_argument(
        "--repo",
        default=UPSTREAM_REPO,
        help="Upstream GitHub owner/repo slug.",
    )
    submit_p.add_argument(
        "--approved",
        action="store_true",
        default=False,
        help="Explicit human approval confirmation required by SCAR-PROC-108.",
    )
    _add_scar_record_args(submit_p)

    return parser

  def parse_args(self, args: Optional[List[str]] = None) -> None:
    """Parse CLI subcommand arguments into ``self.parsed_args``."""
    super().parse_args(args)
    parser = self._build_parser()
    self.parsed_args = parser.parse_args(self.raw_args)

  def _load_record_from_args(self) -> MinedScarRecord:
    """Build a MinedScarRecord from ``--json-file`` or explicit CLI field flags."""
    ns = self.parsed_args
    payload: Dict[str, Any] = {}
    if ns.json_file:
      with open(ns.json_file, "r", encoding="utf-8") as fh:
        loaded = json.load(fh)
      if not isinstance(loaded, dict):
        raise ValueError("JSON scar input file must contain a JSON object.")
      payload = loaded

    def _field(cli_val: str, *json_keys: str) -> str:
      if cli_val:
        return cli_val
      for key in json_keys:
        raw = payload.get(key)
        if isinstance(raw, str) and raw.strip():
          return raw.strip()
      return ""

    return MinedScarRecord(
        title=_field(ns.title, "title"),
        category=_field(ns.category, "category"),
        wound=_field(ns.wound, "wound"),
        trap=_field(ns.trap, "trap"),
        circuit_breaker=_field(
            ns.circuit_breaker, "circuit_breaker", "circuitBreaker"
        ),
        reflex=_field(ns.reflex, "reflex", "permanent_reflex", "permanentReflex"),
        socratic_probe=_field(
            ns.socratic_probe, "socratic_probe", "socraticProbe"
        ),
        applies_when=_field(ns.applies_when, "applies_when", "appliesWhen"),
    )

  def _emit_json(self, data: Dict[str, Any]) -> None:
    """Write formatted JSON output to ``self.stdout``."""
    self.stdout.write(json.dumps(data, indent=2) + "\n")

  def _run_paths(self) -> int:
    ns = self.parsed_args
    registry = AgentPathRegistry(home_dir=ns.home)
    self._emit_json(registry.to_dict(agent=ns.agent, workspace_dir=ns.workspace))
    return 0

  def _run_save(self) -> int:
    ns = self.parsed_args
    registry = AgentPathRegistry(home_dir=ns.home)
    if ns.tier == "project":
      ws = ns.workspace or os.getcwd()
      target_dir = os.path.join(
          os.path.abspath(os.path.expanduser(ws)), ".workflow", "scars"
      )
    elif ns.tier == "user":
      target_dir = registry.get_profile(ns.agent).scars_dir
    else:
      if not ns.custom_dir or not ns.custom_dir.strip():
        raise ValueError("--custom-dir is required when --tier is 'custom'.")
      target_dir = ns.custom_dir.strip()

    record = self._load_record_from_args()
    receipt = MarkdownScarStore().save_scar(target_dir, record, tier=ns.tier)
    self._emit_json(receipt)
    return 0

  def _run_save_config(self) -> int:
    ns = self.parsed_args
    registry = AgentPathRegistry(home_dir=ns.home)
    ws = ns.workspace
    if ns.scope in ("project-local", "both") and not ws:
      ws = os.getcwd()
    receipt = ScarConfigStore(registry=registry).add_scar_directory(
        ns.directory,
        scope=ns.scope,
        workspace_dir=ws,
        agent=ns.agent,
    )
    self._emit_json(receipt)
    return 0

  def _run_upstream_check(self) -> int:
    ns = self.parsed_args
    status = UpstreamIssuePublisher(repo=ns.repo).check_access()
    self._emit_json(status)
    return 0 if status["ready"] else 1

  def _run_upstream_submit(self) -> int:
    ns = self.parsed_args
    record = self._load_record_from_args()
    receipt = UpstreamIssuePublisher(repo=ns.repo).submit_issue(
        record, approved=bool(ns.approved)
    )
    self._emit_json(receipt)
    return 0

  def run(self) -> int:
    """Dispatch the selected subcommand and return its integer exit code."""
    handlers = {
        "paths": self._run_paths,
        "save": self._run_save,
        "save-config": self._run_save_config,
        "upstream-check": self._run_upstream_check,
        "upstream-submit": self._run_upstream_submit,
    }
    handler = handlers[self.parsed_args.subcommand]
    return handler()
