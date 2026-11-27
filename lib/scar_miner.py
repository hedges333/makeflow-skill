"""Target 90 Rapid-Regret Miner engine for ordered SQLite scar tiers and upstreaming (GH-117, GH-304).

Provides validated scar modeling, collision-free tier-scoped SQLite persistence
and explicit ``--id`` overrides composing directly with ``lib/scars_db.py``,
scoped JSON configuration updates (``scars.databases``), human-gated GitHub
issue upstreaming, and a BaseScript CLI controller with explicit subcommands
(SCAR-API-0001, SCAR-ARCH-05).
"""

from dataclasses import dataclass
import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from lib.agent_paths import AgentPathRegistry, PathContext
from lib.base_script import BaseScript, ScriptArgumentParser
from lib.config import (
    DEFAULT_SCAR_DATABASES,
    expand_user_path,
    insert_scar_databases_before_bundled,
)
from lib.scar_candidate import ScarCandidateValidator, validate_socratic_probe
from lib.scar_locations import resolve_scar_locations
from lib.scar_ontology import normalize_domain_category
from lib.scars_db import next_tier_scar_id, upsert_scar

UPSTREAM_REPO = "hedges333/makeflow-skill"

TIER_ID_PREFIXES: Dict[str, str] = {
    "project": "LOCAL",
    "local": "LOCAL",
    "user": "USER",
    "custom": "CUSTOM",
}

_TIER_SOURCE_LABELS: Dict[str, str] = {
    "project": "local",
    "local": "local",
    "user": "user",
    "custom": "custom",
}

_TRAVERSAL_RE = re.compile(r"[/\\]|\.\.|\x00")
_SCAR_ID_RE = re.compile(r"^SCAR-(?:[A-Z0-9]+-)+\d+$")
_LEADING_SCAR_ID_RE = re.compile(r"^\s*SCAR-(?:[A-Z0-9]+-)+\d+\s*[:\-–—]*\s*")
_TIER_SCOPED_SCAR_ID_RE = re.compile(
    r"\bSCAR-(?:LOCAL|USER|CUSTOM)-(?:[A-Z0-9]+-)*\d+\b:?\s*"
)
_SQLITE_CANONICAL_SCAR_ID_RE = re.compile(
    r"\bSCAR-(?:LANG-[A-Z0-9]+-)?[A-Z0-9]+-\d{4,}\b:?\s*"
)
_GITHUB_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_GH_SUBPROCESS_TIMEOUT_SECONDS = 30


def _validate_path_string(path: Any, label: str) -> str:
  """Validate that a filesystem path is a non-empty string without null bytes.

  Args:
    path: Candidate filesystem path value.
    label: Parameter label used in diagnostic error messages.

  Returns:
    Trimmed filesystem path string.

  Raises:
    ValueError: If ``path`` is not a non-empty string or contains null bytes.
  """
  if not isinstance(path, str) or not path.strip():
    raise ValueError(f"{label} must be a non-empty string path.")
  if "\x00" in path:
    raise ValueError(f"{label} must not contain embedded null bytes.")
  return path.strip()


def validate_scar_id(scar_id: Any) -> str:
  """Validate and normalize an explicit scar identifier for override/upsert.

  Accepts canonical patterns such as ``SCAR-ARCH-0001``,
  ``SCAR-LOCAL-DATA-0001``, ``SCAR-LANG-PYTHON-ARCH-0005``, and ``SCAR-TEST-45``.

  Args:
    scar_id: Candidate scar identifier string.

  Returns:
    Normalized uppercase scar identifier string.

  Raises:
    ValueError: If ``scar_id`` is empty, multi-line, or malformed.
  """
  if not isinstance(scar_id, str) or not scar_id.strip():
    raise ValueError("Scar ID must be a non-empty string.")
  if "\n" in scar_id or "\r" in scar_id or "\x00" in scar_id:
    raise ValueError("Scar ID must be a single line without control characters.")
  normalized = scar_id.strip().upper()
  if not _SCAR_ID_RE.match(normalized):
    raise ValueError(
        f"Invalid scar ID '{scar_id}'. Expected format like 'SCAR-ARCH-0001' "
        "or 'SCAR-LOCAL-DATA-0001'."
    )
  return normalized


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

  if not isinstance(record.category, str) or not record.category.strip():
    errors.append("Category must be a non-empty string.")
  elif _TRAVERSAL_RE.search(record.category.strip()):
    errors.append(
        f"Category '{record.category}' contains forbidden path traversal characters."
    )
  else:
    normalize_domain_category(record.category.strip())

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


class SqliteScarStore:
  """Persists and upserts validated mined scars into tiered SQLite registries."""

  def _normalize_tier(self, tier: str) -> Tuple[str, str]:
    """Normalize storage tier name to ``(canonical_tier, source_label)``."""
    cleaned = str(tier or "project").strip().lower()
    if cleaned not in TIER_ID_PREFIXES:
      valid = ", ".join(sorted(TIER_ID_PREFIXES.keys()))
      raise ValueError(
          f"Unsupported scar tier '{tier}'. Expected one of: {valid}."
      )
    canonical = "project" if cleaned == "local" else cleaned
    return canonical, _TIER_SOURCE_LABELS[cleaned]

  def save_scar(
      self,
      db_path: str,
      record: MinedScarRecord,
      tier: str = "project",
      scar_id: Optional[str] = None,
  ) -> Dict[str, Any]:
    """Validate and upsert a mined scar into the target SQLite scars database.

    Args:
      db_path: Destination SQLite database file path.
      record: Candidate ``MinedScarRecord`` to validate and persist.
      tier: Storage tier (``project``, ``user``, or ``custom``).
      scar_id: Optional explicit scar ID for overriding/upserting an existing ID.

    Returns:
      Structured receipt with ``scarId``, ``category``, ``dbPath``, ``tier``,
      ``source``, and ``updated``.

    Raises:
      ValueError: If validation fails, ``scar_id`` is malformed, or ``db_path``
        is invalid or points to an existing directory.
    """
    clean_db_path = _validate_path_string(db_path, "db_path")
    abs_db_path = os.path.abspath(os.path.expanduser(clean_db_path))
    if os.path.isdir(abs_db_path):
      raise ValueError(
          f"Target scar database path '{abs_db_path}' exists and is a directory."
      )

    canonical_tier, source_label = self._normalize_tier(tier)
    validated_id = validate_scar_id(scar_id) if scar_id is not None else None

    errors = validate_mined_scar(record)
    if errors:
      raise ValueError("Invalid scar candidate: " + "; ".join(errors))

    domain = normalize_domain_category(record.category.strip())
    final_id = validated_id or next_tier_scar_id(
        abs_db_path, domain, tier=canonical_tier
    )
    upsert_receipt = upsert_scar(
        abs_db_path,
        {
            "id": final_id,
            "title": record.title.strip(),
            "category": domain,
            "category_slug": domain.lower(),
            "visibility": source_label,
            "applies_when": record.applies_when.strip(),
            "graduation_status": source_label,
            "wound": record.wound.strip(),
            "trap": record.trap.strip(),
            "circuit_breaker": record.circuit_breaker.strip(),
            "reflex": record.reflex.strip(),
            "socratic_probe": record.socratic_probe.strip(),
            "source": source_label,
        },
    )
    return {
        "scarId": final_id,
        "category": domain,
        "dbPath": upsert_receipt["db_path"],
        "tier": canonical_tier,
        "source": source_label,
        "updated": bool(upsert_receipt["updated"]),
    }


class ScarConfigStore:
  """Persists SQLite scar database paths into ``scars.databases`` in JSON configs."""

  def __init__(self, registry: Optional[AgentPathRegistry] = None) -> None:
    """Initialize ScarConfigStore with an AgentPathRegistry collaborator.

    Args:
      registry: Optional ``AgentPathRegistry`` instance.
    """
    self.registry = registry or AgentPathRegistry()

  def _append_database_to_file(
      self,
      config_path: str,
      db_path: str,
      workspace_dir: Optional[str] = None,
      agent: Optional[str] = None,
  ) -> str:
    """Read, update ``scars.databases`` idempotently, and atomically write one JSON config."""
    abs_cfg = os.path.abspath(
        expand_user_path(config_path, home_dir=self.registry.home_dir)
    )
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

    existing_dbs = scars_section.get("databases")
    if not isinstance(existing_dbs, list):
      existing_dbs = list(DEFAULT_SCAR_DATABASES)

    scars_section["databases"] = insert_scar_databases_before_bundled(
        existing_dbs,
        [db_path],
        context=PathContext(
            workspace_dir=workspace_dir,
            agent=agent,
            home_dir=self.registry.home_dir,
        ),
    )

    _atomic_write_text(abs_cfg, json.dumps(data, indent=2) + "\n")
    return abs_cfg

  def add_scar_database(
      self,
      db_path: str,
      scope: str,
      workspace_dir: Optional[str] = None,
      agent: Optional[str] = None,
  ) -> Dict[str, Any]:
    """Persist a custom SQLite scar database into ``user-global``, ``project-local``, or ``both``.

    Inserts ``db_path`` into ``scars.databases`` immediately before the bundled
    registry entry (seeding defaults if ``scars.databases`` was absent) or
    appends it when the bundled entry was explicitly omitted.

    Args:
      db_path: Custom SQLite scar database path to persist.
      scope: Target scope (``user-global``, ``project-local``, or ``both``).
      workspace_dir: Project root path (required for ``project-local`` and ``both``).
      agent: Optional agent name override for user-global config path lookup.

    Returns:
      Dictionary with ``database``, ``scope``, and ``updatedFiles``.

    Raises:
      ValueError: If ``db_path``, ``scope``, or required ``workspace_dir`` is invalid.
    """
    clean_db = _validate_path_string(db_path, "db_path")
    clean_scope = str(scope or "").strip().lower()
    if clean_scope not in ("user-global", "project-local", "both"):
      raise ValueError(
          f"Invalid scope '{scope}'. Expected 'user-global', 'project-local', or 'both'."
      )

    targets: List[str] = []
    ws_abs: Optional[str] = None
    if clean_scope in ("user-global", "both"):
      profile = self.registry.get_profile(agent)
      targets.append(profile.global_config_path)
    if clean_scope in ("project-local", "both"):
      clean_ws = _validate_path_string(workspace_dir, "workspace_dir")
      ws_abs = os.path.abspath(
          expand_user_path(clean_ws, home_dir=self.registry.home_dir)
      )
      targets.append(os.path.join(ws_abs, ".workflow", "config.json"))

    updated = [
        self._append_database_to_file(
            cfg_path, clean_db, workspace_dir=ws_abs, agent=agent
        )
        for cfg_path in targets
    ]
    return {
        "database": clean_db,
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
    """Strip any leading scar ID prefix, tier-scoped IDs, and canonical 4-digit IDs from a title.

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
    cleaned = _TIER_SCOPED_SCAR_ID_RE.sub("", cleaned)
    cleaned = _SQLITE_CANONICAL_SCAR_ID_RE.sub("", cleaned).strip()
    if not cleaned:
      raise ValueError(
          "Scar title must contain descriptive text beyond an assigned scar ID."
      )
    return cleaned

  def _strip_assigned_ids(self, text: str) -> str:
    """Strip tier-scoped and 4-digit canonical SQLite scar IDs while keeping short Tier 1 refs."""
    cleaned = _TIER_SCOPED_SCAR_ID_RE.sub("", text or "")
    cleaned = _SQLITE_CANONICAL_SCAR_ID_RE.sub("", cleaned)
    return cleaned.strip()

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
        f"- **Wound**: {self._strip_assigned_ids(record.wound)}",
        f"- **Trap**: {self._strip_assigned_ids(record.trap)}",
    ]
    if record.circuit_breaker and record.circuit_breaker.strip():
      body_lines.append(
          f"- **Circuit Breaker**: {self._strip_assigned_ids(record.circuit_breaker)}"
      )
    body_lines.append(
        f"- **Permanent Reflex**: {self._strip_assigned_ids(record.reflex)}"
    )
    body_lines.append(
        f"- **Socratic Probe**: {self._strip_assigned_ids(record.socratic_probe)}"
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
        description="Target 90 Rapid-Regret Miner CLI for tiered SQLite scars and upstreaming.",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    paths_p = subparsers.add_parser(
        "paths",
        help="Resolve project, user-wide agent, bundled, and configured SQLite scar DB paths.",
    )
    paths_p.add_argument("--workspace", default=None, help="Project root directory.")
    paths_p.add_argument("--agent", default=None, help="Agent name override.")
    paths_p.add_argument("--home", default=None, help="Home directory override.")
    paths_p.add_argument(
        "--plugin-root",
        default=None,
        help="Optional Makeflow plugin root override.",
    )

    save_p = subparsers.add_parser(
        "save",
        help="Validate and upsert a scar into a tiered SQLite scars database.",
    )
    save_p.add_argument(
        "--tier",
        required=True,
        choices=("project", "user", "custom"),
        help="Target storage tier.",
    )
    save_p.add_argument("--workspace", default=None, help="Project root directory.")
    save_p.add_argument(
        "--custom-db",
        default=None,
        help="Custom SQLite database path for custom tier.",
    )
    save_p.add_argument(
        "--id",
        dest="scar_id",
        default=None,
        help="Optional explicit scar ID for overriding or upserting an existing ID.",
    )
    save_p.add_argument("--agent", default=None, help="Agent name override.")
    save_p.add_argument("--home", default=None, help="Home directory override.")
    _add_scar_record_args(save_p)

    cfg_p = subparsers.add_parser(
        "save-config",
        help="Persist a custom SQLite scar database into scars.databases in user-global and/or project-local config.",
    )
    cfg_p.add_argument(
        "--db",
        required=True,
        dest="database",
        help="Custom SQLite scar database path.",
    )
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
    ws_abs = (
        os.path.abspath(expand_user_path(ns.workspace, home_dir=ns.home))
        if ns.workspace
        else None
    )
    locations = resolve_scar_locations(
        context=PathContext(
            workspace_dir=ws_abs,
            agent=ns.agent,
            home_dir=ns.home,
            plugin_root=ns.plugin_root,
        )
    )
    payload = registry.to_dict(agent=ns.agent, workspace_dir=ws_abs)
    payload.update(locations.to_dict())
    self._emit_json(payload)
    return 0

  def _run_save(self) -> int:
    ns = self.parsed_args
    if ns.tier == "project":
      ws = ns.workspace or os.getcwd()
      locations = resolve_scar_locations(
          context=PathContext(
              workspace_dir=ws,
              agent=ns.agent,
              home_dir=ns.home,
          )
      )
      assert locations.project_db is not None
      target_db = locations.project_db
    elif ns.tier == "user":
      locations = resolve_scar_locations(
          context=PathContext(agent=ns.agent, home_dir=ns.home)
      )
      target_db = locations.user_db
    else:
      if not ns.custom_db or not ns.custom_db.strip():
        raise ValueError("--custom-db is required when --tier is 'custom'.")
      target_db = expand_user_path(ns.custom_db.strip(), home_dir=ns.home)

    record = self._load_record_from_args()
    receipt = SqliteScarStore().save_scar(
        target_db, record, tier=ns.tier, scar_id=ns.scar_id
    )
    self._emit_json(receipt)
    return 0

  def _run_save_config(self) -> int:
    ns = self.parsed_args
    registry = AgentPathRegistry(home_dir=ns.home)
    ws = ns.workspace
    if ns.scope in ("project-local", "both") and not ws:
      ws = os.getcwd()
    receipt = ScarConfigStore(registry=registry).add_scar_database(
        ns.database,
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
