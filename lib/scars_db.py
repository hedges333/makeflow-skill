"""Sync and query synthetic scars using SQLite and FTS5 (GH-118, SCAR-ARCH-05).

Fetches public synthetic scars from syntheticscars.org, caches HTTP ETag to avoid
duplicate downloads, stores scars in SQLite with full-text search (FTS5), and provides
fast on-demand JIT scar queries for MCP tools and CLI runners.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import sqlite3
import sys
from typing import Any, Dict, List, NoReturn, Optional, Sequence
import urllib.error
import urllib.request

from lib.base_script import BaseScript, SilentArgumentParser

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_REGISTRY_URL = "https://syntheticscars.org/registry.json"


def resolve_scars_db_path(
    db_path: Optional[str] = None,
    agent: Optional[str] = None,
    workspace_dir: Optional[str] = None,
    package_root: Optional[str] = None,
) -> str:
  """Resolves the SQLite scars database path according to priority cascade."""
  if db_path:
    return os.path.abspath(os.path.expanduser(db_path))

  env_path = os.getenv("MAKEFLOW_SCARS_DB")
  if env_path:
    return os.path.abspath(os.path.expanduser(env_path))

  pkg_root = package_root or _REPO_ROOT
  bundled_path = os.path.join(
      pkg_root, "skills", "makeflow", "data", "scars_registry.db"
  )
  if os.path.exists(bundled_path):
    return os.path.abspath(bundled_path)

  if workspace_dir:
    ws_db = os.path.join(workspace_dir, ".workflow", "cache", "scars_registry.db")
    if os.path.exists(ws_db):
      return os.path.abspath(ws_db)

  home = os.path.expanduser("~")
  agent_key = (agent or "").strip().lower()

  agent_paths = {
      "gemini": os.path.join(
          home, ".gemini", "antigravity-cli", "plugin_data", "makeflow", "scars_registry.db"
      ),
      "claude": os.path.join(
          home, ".claude", "plugin_data", "makeflow", "scars_registry.db"
      ),
      "cursor": os.path.join(
          home, ".cursor", "plugin_data", "makeflow", "scars_registry.db"
      ),
      "copilot": os.path.join(
          home, ".copilot", "plugin_data", "makeflow", "scars_registry.db"
      ),
      "chatgpt": os.path.join(
          home, ".chatgpt", "plugin_data", "makeflow", "scars_registry.db"
      ),
  }

  if agent_key in agent_paths:
    return agent_paths[agent_key]

  xdg_cache = os.getenv("XDG_CACHE_HOME")
  if xdg_cache:
    xdg_db = os.path.join(xdg_cache, "makeflow", "scars_registry.db")
    if os.path.exists(xdg_db):
      return os.path.abspath(xdg_db)

  user_default = os.path.join(home, ".cache", "makeflow", "scars_registry.db")
  if os.path.exists(user_default):
    return user_default

  return os.path.abspath(bundled_path)


def _sanitize_fts_query(query: str) -> str:
  """Sanitizes user search query for safe FTS5 MATCH evaluation."""
  tokens = re.findall(r"[a-zA-Z0-9_\-]+", query)
  if not tokens:
    return ""
  quoted = []
  for t in tokens:
    cleaned = t.replace('"', "")
    if cleaned:
      quoted.append(f'"{cleaned}"*')
  return " ".join(quoted)


def _parse_json_list(raw_val: Any) -> List[str]:
  """Parses a JSON array string or list into a clean list of non-empty strings."""
  if not raw_val:
    return []
  if isinstance(raw_val, list):
    return [str(x).strip() for x in raw_val if str(x).strip()]
  if isinstance(raw_val, str):
    try:
      parsed = json.loads(raw_val)
      if isinstance(parsed, list):
        return [str(x).strip() for x in parsed if str(x).strip()]
    except (json.JSONDecodeError, TypeError, ValueError):
      return [p.strip() for p in raw_val.split(",") if p.strip()]
  return []


_CANONICAL_PHASE_ORDER = (
    "Target 10: intake",
    "Target 15: archaeology",
    "Target 20: plan",
    "Target 30: tdd",
    "Target 40: review",
    "Target 42: blocker-triage",
    "Target 45: review-approval",
    "Target 50: commit",
    "Target 60: push",
    "Target 70: ci-triage",
    "Target 75: landing",
    "Target 80: merge",
    "Target 90: finish",
)

_PHASE_PATTERNS = (
    (
        "Target 10: intake",
        re.compile(
            r"\b(?:target\s*10|t10|target\s*1\b|step\s*[123]\b|intake|branch)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Target 15: archaeology",
        re.compile(
            r"\b(?:target\s*15|t15|archaeolog\w*|familiarize|inoculate|worktree\s+verification|historical\s+precedent)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Target 20: plan",
        re.compile(
            r"\b(?:target\s*20|t20|target\s*2\b|step\s*4\b|plan|approved-plan|architecture\s+design|schema\s+architecture)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Target 30: tdd",
        re.compile(
            r"\b(?:target\s*30|target\s*35|t30|t35|target\s*3\b|step\s*[567]\b|tdd|tdd-red|tdd-green|implementation|implementation-diff)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Target 40: review",
        re.compile(
            r"\b(?:target\s*40|t40|target\s*4a?\b|step\s*9\b|4a\b|adversarial|critic|critic-review|six\s+pillars|pillar\s+[1-6])\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Target 42: blocker-triage",
        re.compile(
            r"\b(?:target\s*42|t42|target\s*4b\b|blocker-triage|triaged-blockers|blocker\s+triage)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Target 45: review-approval",
        re.compile(
            r"\b(?:target\s*45|t45|step\s*10\b|review-approval|approved-review|peer\s+review)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Target 50: commit",
        re.compile(
            r"\b(?:target\s*50|t50|target\s*5\b|step\s*8\b|commit|pre-commit|local\s+static\s+analysis|local\s+verification)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Target 60: push",
        re.compile(
            r"\b(?:target\s*60|t60|target\s*6\b|step\s*11\b|push|pre-push)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Target 70: ci-triage",
        re.compile(
            r"\b(?:target\s*70|t70|target\s*7a?(?:\.2)?\b|7a(?:\.2)?\b|step\s*1[23]\b|ci-triage|bot\s+triage|triage-verified|ready-to-land|remote\s+ci)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Target 75: landing",
        re.compile(
            r"\b(?:target\s*75|t75|target\s*7b\b|7b\b|step\s*14\b|landing)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Target 80: merge",
        re.compile(
            r"\b(?:target\s*80|t80|target\s*8\b|merge|merged)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Target 90: finish",
        re.compile(
            r"\b(?:target\s*90|t90|target\s*9\b|step\s*15\b|finish|retrospective|history\s+ledger)\b",
            re.IGNORECASE,
        ),
    ),
)

_SYNONYM_RULES = (
    (re.compile(r"\bre-?entran\w*\b", re.IGNORECASE), ("reentrancy",)),
    (
        re.compile(
            r"\b(?:race\s+conditions?|data\s+races?|concurrent\s+mutations?)\b",
            re.IGNORECASE,
        ),
        ("race condition", "concurrency"),
    ),
    (
        re.compile(r"\b(?:toctou|time-of-check)\b", re.IGNORECASE),
        ("toctou", "race condition"),
    ),
    (
        re.compile(
            r"\b(?:deadlocks?|circular\s+waits?|lock\s+ordering|livelocks?)\b",
            re.IGNORECASE,
        ),
        ("deadlock",),
    ),
    (
        re.compile(
            r"\b(?:memory\s+leaks?|resource\s+leaks?|unclosed\s+streams?|undisposed|subscription\s+leaks?|controller\s+disposal|malloc\s+leak)\b",
            re.IGNORECASE,
        ),
        ("memory leak",),
    ),
    (
        re.compile(
            r"\b(?:semver|breaking\s+changes?|signature\s+mutations?|downgrade\s+breakage|positional-to-named)\b",
            re.IGNORECASE,
        ),
        ("semver breakage",),
    ),
    (re.compile(r"\bidempoten\w*\b", re.IGNORECASE), ("idempotency",)),
    (re.compile(r"\bbackpressure\b", re.IGNORECASE), ("backpressure",)),
    (
        re.compile(r"\b(?:stack\s+overflow|infinite\s+recursion)\b", re.IGNORECASE),
        ("stack overflow",),
    ),
    (
        re.compile(r"\b(?:zombie\s+process\w*|reap\s+zombies?)\b", re.IGNORECASE),
        ("zombie process",),
    ),
    (
        re.compile(r"\b(?:flaky\s+tests?|flakiness)\b", re.IGNORECASE),
        ("flaky test",),
    ),
    (
        re.compile(r"\b(?:hermetic\b|in-memory\s+test\s+isolation)\b", re.IGNORECASE),
        ("hermetic isolation",),
    ),
    (
        re.compile(r"\b(?:template\s+method|basescript)\b", re.IGNORECASE),
        ("template method", "basescript"),
    ),
    (re.compile(r"\bcompleter\b", re.IGNORECASE), ("completer",)),
    (re.compile(r"\bmerkle\s+tree\b", re.IGNORECASE), ("merkle tree",)),
    (
        re.compile(r"\b(?:mutex|mutual\s+exclusion)\b", re.IGNORECASE),
        ("mutex",),
    ),
    (
        re.compile(r"\b(?:hydration\s+mismatch|ssr\s+hydration)\b", re.IGNORECASE),
        ("hydration",),
    ),
)

_LANGUAGE_RULES = (
    (
        "dart",
        re.compile(
            r"(?:\bdart\b|\bflutter\b|\briverpod\b|\bbloc\b|\bcubit\b|\bpubspec\b|\bCompleter\b|\bWidgetTester\b|\bStatefulWidget\b|\bStatelessWidget\b|\bBuildContext\b|\bfakeAsync\b|\bAsyncNotifier\b|\bStateNotifier\b|\bChangeNotifier\b|\bValueNotifier\b|\.dart\b|```dart\b|SCAR-(?:LANG-DART|FLUTTER|BLOC|RIV|PUB))",
            re.IGNORECASE,
        ),
    ),
    (
        "python",
        re.compile(
            r"(?:\bpython3?\b|\bpytest\b|\bunittest\b|\bfastapi\b|\bpydantic\b|\bshutil\.|\bsqlite3\.|\bargparse\b|\bpy_compile\b|\bpyproject\.toml\b|\.py\b|```(?:python|py)\b|SCAR-(?:LANG-PYTHON|PY-))",
            re.IGNORECASE,
        ),
    ),
    (
        "typescript",
        re.compile(
            r"(?:\btypescript\b|\btsconfig\b|\btsc\b|\.tsx?\b|```(?:typescript|tsx?)\b|SCAR-LANG-TYPESCRIPT)",
            re.IGNORECASE,
        ),
    ),
    (
        "javascript",
        re.compile(
            r"(?:\bjavascript\b|\bnode\.?js\b|\.jsx?\b|```(?:javascript|jsx?)\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "rust",
        re.compile(
            r"(?:\brust\b|\bcargo\b|\bclippy\b|\btokio\b|\bborrow\s+checker\b|#\[cfg|\.rs\b|```(?:rust|rs)\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "go",
        re.compile(
            r"(?:\bgolang\b|\bgoroutines?\b|\bgo\s+(?:test|mod|services?|crates?|modules?|routines?)\b|\bgo\.mod\b|_test\.go\b|\bin\s+go\b|```(?:go|golang)\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "c",
        re.compile(
            r"(?:\bc\s+(?:malloc|modules?|code|compiler|library)\b|\bin\s+c\b|\bmalloc\b|\bcalloc\b|\bvalgrind\b|\.[ch]\b|```c\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "cpp",
        re.compile(
            r"(?:\bc\+\+|\bcpp\b|\bstd::|\bcmake\b|```(?:cpp|c\+\+)\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "bash",
        re.compile(
            r"(?:\bbash\b|\bshell\s+scripts?\b|\bzsh\b|\bchmod\s+\+x\b|#!/bin/|\bsh\s+-c\b|```(?:bash|sh|shell|zsh)\b|SCAR-SH-)",
            re.IGNORECASE,
        ),
    ),
    (
        "sql",
        re.compile(
            r"(?:\bsqlite3?\b|\bpostgresql\b|\bpostgres\b|\bmysql\b|\bmariadb\b|\bfts5\b|\bCREATE\s+TABLE\b|\bALTER\s+TABLE\b|```sql\b|SCAR-DB-)",
            re.IGNORECASE,
        ),
    ),
    (
        "java",
        re.compile(
            r"(?:\bjava\b|\bjvm\b|\bNullPointerException\b|\bmaven\b|\bgradle\b|```java\b)",
            re.IGNORECASE,
        ),
    ),
    ("kotlin", re.compile(r"(?:\bkotlin\b|```kotlin\b)", re.IGNORECASE)),
    (
        "swift",
        re.compile(r"(?:\bswift\b|\bswiftui\b|```swift\b)", re.IGNORECASE),
    ),
    (
        "csharp",
        re.compile(
            r"(?:\bc#\b|\bcsharp\b|\bdotnet\b|```(?:csharp|cs)\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "ruby",
        re.compile(r"(?:\bruby\b|\brspec\b|\brails\b|```ruby\b)", re.IGNORECASE),
    ),
    (
        "perl",
        re.compile(
            r"(?:\bperl\b|\bcpan\b|```perl\b|SCAR-(?:MF-)?LANG-PERL)",
            re.IGNORECASE,
        ),
    ),
    (
        "php",
        re.compile(r"(?:\bphp\b|\bphpunit\b|```php\b)", re.IGNORECASE),
    ),
    (
        "elixir",
        re.compile(r"(?:\belixir\b|\bphoenix\b|```elixir\b)", re.IGNORECASE),
    ),
    (
        "gdscript",
        re.compile(r"(?:\bgdscript\b|\bgodot\b)", re.IGNORECASE),
    ),
    ("nim", re.compile(r"(?:\bnim\b|\bnimble\b)", re.IGNORECASE)),
    ("haxe", re.compile(r"(?:\bhaxe\b)", re.IGNORECASE)),
    ("odin", re.compile(r"(?:\bodin\b)", re.IGNORECASE)),
    ("dlang", re.compile(r"(?:\bdlang\b)", re.IGNORECASE)),
)

_FRAMEWORK_RULES = (
    (
        "flutter",
        re.compile(
            r"(?:\bflutter\b|\bWidgetTester\b|\bStatefulWidget\b|\bStatelessWidget\b|\bBuildContext\b|\bCustomPaint\b|\bRenderObject\b|\bMaterialApp\b|\bScaffold\b|\bChangeNotifier\b|\bValueNotifier\b|\bAnimationController\b|\bGoRouter\b|SCAR-FLUTTER)",
            re.IGNORECASE,
        ),
    ),
    (
        "bloc",
        re.compile(
            r"(?:\bbloc\b|\bcubit\b|\bBlocProvider\b|\bBlocBuilder\b|\bBlocListener\b|\bReplayBloc\b|\bReplayCubit\b|\bHydratedBloc\b|\bblocTest\b|SCAR-BLOC)",
            re.IGNORECASE,
        ),
    ),
    (
        "riverpod",
        re.compile(
            r"(?:\briverpod\b|\bProviderContainer\b|\bStateNotifier\b|\bAsyncValue\b|\bAsyncNotifier\b|\bConsumerWidget\b|\bWidgetRef\b|SCAR-RIV)",
            re.IGNORECASE,
        ),
    ),
    (
        "react",
        re.compile(
            r"(?:\breact\b|\bnext\.?js\b|\buseEffect\b|\buseState\b|\buseMemo\b|\buseCallback\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "sqlite",
        re.compile(
            r"(?:\bsqlite3?\b|\bfts5\b|\bsqlite_master\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "fastapi",
        re.compile(
            r"(?:\bfastapi\b|\bstarlette\b|\buvicorn\b|\bpydantic\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "pytest",
        re.compile(r"(?:\bpytest\b|\bconftest\.py\b)", re.IGNORECASE),
    ),
    (
        "unittest",
        re.compile(r"(?:\bunittest\b|\bTestCase\b)", re.IGNORECASE),
    ),
    (
        "docker",
        re.compile(
            r"(?:\bdocker\b|\bdockerfile\b|\bdocker-compose\b|\bcontainers?\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "git",
        re.compile(
            r"(?:\bgit\b|\bworktree\b|\brebase\b|\bmerkle\b|\bwrite-tree\b|SCAR-GIT)",
            re.IGNORECASE,
        ),
    ),
    (
        "github-actions",
        re.compile(
            r"(?:\bgithub\s+actions\b|\bgh\s+pr\b|\bgh\s+issue\b|SCAR-CI)",
            re.IGNORECASE,
        ),
    ),
    (
        "mcp",
        re.compile(
            r"(?:\bmcp\b|\bjson-rpc\b|\bworkflow_query_scars\b|\bworkflow_advance_target\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "postgres",
        re.compile(
            r"(?:\bpostgresql\b|\bpostgres\b|\bpsql\b|\bpg_dump\b)",
            re.IGNORECASE,
        ),
    ),
    ("redux", re.compile(r"\bredux\b", re.IGNORECASE)),
    ("swiftui", re.compile(r"\bswiftui\b", re.IGNORECASE)),
    ("a2ui", re.compile(r"(?:\ba2ui\b|\bgenui\b|SCAR-A2UI|SCAR-GENUI)", re.IGNORECASE)),
    (
        "opentelemetry",
        re.compile(r"(?:\bopentelemetry\b|\botel\b|SCAR-OTEL)", re.IGNORECASE),
    ),
)

_EXCEPTION_CLASS_RE = re.compile(
    r"\b([A-Z][a-zA-Z0-9]*(?:Error|Exception|Fault|Failure|Warning))\b"
)
_POSIX_OR_ERRNO_RE = re.compile(
    r"\b(SIG(?:TERM|INT|KILL|HUP|CHLD|PIPE|ALRM|SEGV|ABRT|USR1|USR2)|E(?:ADDRINUSE|ACCES|PERM|NOENT|EXIST|AGAIN|INTR|TIMEDOUT|CONNREFUSED|CONNRESET|PIPE|NOSPC|NOMEM|INVAL|BUSY|XDEV))\b"
)
_UPPER_DIAG_CODE_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,}(?:_[A-Z0-9]{2,})+)\b")
_NON_DIAG_UPPER = {
    "REPO_ROOT",
    "DEFAULT_REGISTRY_URL",
    "SAMPLE_SCARS",
    "TEST_DIR",
    "TEST_CMD",
    "PRIMARY_KEY",
    "CREATE_TABLE",
    "INSERT_INTO",
    "INLINE_CODE",
    "HOSTING_MODE",
    "MESSAGE_PRIORITY_HIGH",
}
_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "when",
    "where",
    "without",
    "using",
    "over",
    "under",
    "between",
    "before",
    "after",
    "during",
    "while",
    "not",
    "are",
    "was",
    "were",
    "has",
    "have",
    "had",
    "can",
    "may",
    "will",
    "must",
    "should",
    "all",
    "any",
    "each",
    "every",
}


def _normalize_workflow_phases(
    raw_phases: List[str],
    category: str = "",
    applies_when: str = "",
    title: str = "",
    default_fallback: bool = True,
) -> List[str]:
  """Normalizes workflow phase strings to canonical Target 10..90 labels."""
  matched = set()
  joined = " | ".join(str(p) for p in raw_phases if str(p).strip())

  for canonical, pattern in _PHASE_PATTERNS:
    if pattern.search(joined):
      matched.add(canonical)

  # Check bare shorthand numbers like "2, 3, 4a"
  if re.search(r"(?:^|[\s,\[\"]+)(?:2)(?:[\s,\]\"]+|$)", joined):
    matched.add("Target 20: plan")
  if re.search(r"(?:^|[\s,\[\"]+)(?:3)(?:[\s,\]\"]+|$)", joined):
    matched.add("Target 30: tdd")

  if not matched:
    context = f"{category} {applies_when} {title}"
    for canonical, pattern in _PHASE_PATTERNS:
      if pattern.search(context):
        matched.add(canonical)

  if not matched and default_fallback:
    cat_upper = (category or "").strip().upper()
    if cat_upper in {"ARCH", "API", "DATA", "DEP", "STORAGE", "WIRE", "SCHEMA"}:
      matched.update(
          ("Target 20: plan", "Target 30: tdd", "Target 40: review")
      )
    elif cat_upper in {"REVIEW", "GOV", "PEDAGOGY", "META"}:
      matched.add("Target 40: review")
    elif cat_upper in {"GIT"}:
      matched.update(("Target 50: commit", "Target 60: push"))
    elif cat_upper in {"CI"}:
      matched.add("Target 70: ci-triage")
    else:
      matched.update(("Target 30: tdd", "Target 40: review"))

  return [p for p in _CANONICAL_PHASE_ORDER if p in matched]


def extract_scar_metadata(scar_row: Dict[str, Any]) -> Dict[str, List[str]]:
  """Deterministically extracts keywords, languages, frameworks, diagnostic_codes, and workflow_phases."""
  scar_id = str(scar_row.get("id") or "").strip()
  orig_id = str(
      scar_row.get("original_id") or scar_row.get("originalId") or ""
  ).strip()
  title = str(scar_row.get("title") or "").strip()
  category = str(scar_row.get("category") or "").strip()
  cat_slug = str(
      scar_row.get("category_slug") or scar_row.get("categorySlug") or ""
  ).strip()
  applies_when = str(
      scar_row.get("applies_when") or scar_row.get("appliesWhen") or ""
  ).strip()

  raw_phases_val = (
      scar_row.get("workflow_phases")
      if "workflow_phases" in scar_row
      else scar_row.get("workflowPhases")
  )
  raw_phases_list = _parse_json_list(raw_phases_val)

  text_fields = (
      scar_id,
      orig_id,
      title,
      category,
      cat_slug,
      applies_when,
      str(scar_row.get("raw_wound") or ""),
      str(scar_row.get("raw_trap") or ""),
      str(scar_row.get("raw_circuit_breaker") or ""),
      str(scar_row.get("raw_reflex") or ""),
      str(scar_row.get("wound") or ""),
      str(scar_row.get("trap") or ""),
      str(
          scar_row.get("circuit_breaker")
          or scar_row.get("circuitBreaker")
          or ""
      ),
      str(scar_row.get("reflex") or ""),
      str(
          scar_row.get("socratic_probe")
          or scar_row.get("socraticProbe")
          or ""
      ),
  )
  combined_text = "\n".join(t for t in text_fields if t)

  # 1. Extract keywords & semantic synonyms
  keywords: List[str] = []
  seen_kw = set()

  def _add_kw(val: str) -> None:
    cleaned = val.strip()
    low = cleaned.lower()
    if cleaned and low not in seen_kw:
      seen_kw.add(low)
      keywords.append(cleaned)

  for pattern, synonyms in _SYNONYM_RULES:
    if pattern.search(combined_text):
      for syn in synonyms:
        _add_kw(syn)

  # Extract inline backticked identifiers (outside fenced code blocks)
  no_fenced = re.sub(r"```[\s\S]*?```", " ", combined_text)
  for inline_match in re.finditer(r"`([^`\n]{1,60})`", no_fenced):
    span = inline_match.group(1).strip()
    for ident in re.findall(r"[a-zA-Z_][a-zA-Z0-9_.\-]{1,40}", span):
      cleaned_ident = ident.strip(".-_")
      if len(cleaned_ident) >= 2 and cleaned_ident.lower() not in _STOPWORDS:
        _add_kw(cleaned_ident)

  if scar_id:
    _add_kw(scar_id)
  if orig_id:
    _add_kw(orig_id)
  if cat_slug:
    _add_kw(cat_slug.lower())
  elif category:
    _add_kw(category.lower())

  for word in re.findall(r"[a-zA-Z][a-zA-Z0-9_\-]{2,}", f"{title} {applies_when}"):
    if word.lower() not in _STOPWORDS:
      _add_kw(word.lower())
      if len(keywords) >= 25:
        break

  if not keywords:
    _add_kw("synthetic-scar")

  # 2. Extract languages
  languages: List[str] = []
  for lang_name, lang_re in _LANGUAGE_RULES:
    if lang_re.search(combined_text):
      languages.append(lang_name)
  if not languages:
    languages.append("universal")

  # 3. Extract frameworks
  frameworks: List[str] = []
  for fw_name, fw_re in _FRAMEWORK_RULES:
    if fw_re.search(combined_text):
      frameworks.append(fw_name)

  # 4. Extract diagnostic codes
  diag_codes: List[str] = []
  seen_diag = set()

  def _add_diag(code: str) -> None:
    if code and code not in seen_diag:
      seen_diag.add(code)
      diag_codes.append(code)

  for m in _POSIX_OR_ERRNO_RE.finditer(combined_text):
    _add_diag(m.group(1))
  for m in _EXCEPTION_CLASS_RE.finditer(combined_text):
    token = m.group(1)
    if token not in {"Error", "Exception", "Warning", "Failure", "Fault"}:
      _add_diag(token)
  for m in _UPPER_DIAG_CODE_RE.finditer(combined_text):
    token = m.group(1)
    if token not in _NON_DIAG_UPPER and not token.startswith("SCAR_"):
      _add_diag(token)

  # 5. Normalize workflow phases
  workflow_phases = _normalize_workflow_phases(
      raw_phases_list,
      category=category or cat_slug,
      applies_when=applies_when,
      title=title,
  )

  return {
      "keywords": keywords,
      "languages": languages,
      "frameworks": frameworks,
      "diagnostic_codes": diag_codes,
      "workflow_phases": workflow_phases,
  }


def query_scars(
    db_path: str,
    query: str = "",
    category: Optional[str] = None,
    language: Optional[str] = None,
    limit: int = 3,
    source: Optional[str] = None,
    framework: Optional[str] = None,
    diagnostic_code: Optional[str] = None,
    workflow_phase: Optional[str] = None,
) -> List[Dict[str, Any]]:
  """Queries SQLite scars database using FTS5, fallback keyword search, or metadata filters."""
  if not db_path or not os.path.isfile(db_path):
    return []

  conn = None
  try:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='scars'"
    )
    if not cur.fetchone():
      return []

    cur.execute("PRAGMA table_info(scars)")
    cols = {r[1] for r in cur.fetchall()}
    has_source = "source" in cols
    has_orig_id = "original_id" in cols
    has_raw_hash = "raw_hash" in cols
    has_keywords = "keywords" in cols
    has_languages = "languages" in cols
    has_frameworks = "frameworks" in cols
    has_diag_codes = "diagnostic_codes" in cols

    wound_expr = (
        "COALESCE(s.wound, s.raw_wound)" if "raw_wound" in cols else "s.wound"
    )
    trap_expr = (
        "COALESCE(s.trap, s.raw_trap)" if "raw_trap" in cols else "s.trap"
    )
    cb_expr = (
        "COALESCE(s.circuit_breaker, s.raw_circuit_breaker)"
        if "raw_circuit_breaker" in cols
        else "s.circuit_breaker"
    )
    reflex_expr = (
        "COALESCE(s.reflex, s.raw_reflex)"
        if "raw_reflex" in cols
        else "s.reflex"
    )
    probe_expr = (
        "COALESCE(s.socratic_probe, '')"
        if "socratic_probe" in cols
        else "''"
    )
    source_col = "s.source" if has_source else "'upstream' AS source"
    orig_id_col = "s.original_id" if has_orig_id else "NULL AS original_id"
    raw_hash_col = "s.raw_hash" if has_raw_hash else "NULL AS raw_hash"
    keywords_col = "s.keywords" if has_keywords else "'[]' AS keywords"
    languages_col = "s.languages" if has_languages else "'[]' AS languages"
    frameworks_col = "s.frameworks" if has_frameworks else "'[]' AS frameworks"
    diag_codes_col = (
        "s.diagnostic_codes" if has_diag_codes else "'[]' AS diagnostic_codes"
    )

    select_fields = (
        "s.id, s.title, s.category, s.category_slug, s.visibility,"
        f" s.workflow_phases, s.applies_when, s.graduation_status, {wound_expr},"
        f" {trap_expr}, {cb_expr}, {reflex_expr}, {probe_expr},"
        f" {source_col}, {orig_id_col}, {raw_hash_col},"
        f" {keywords_col}, {languages_col}, {frameworks_col}, {diag_codes_col} "
    )

    def _append_filters(base_sql: str, params_list: List[Any]) -> Optional[str]:
      sql_out = base_sql
      if source and source.strip():
        if has_source:
          sql_out += "AND LOWER(s.source) = LOWER(?) "
          params_list.append(source.strip())
        elif source.strip().lower() != "upstream":
          return None

      if category and category.strip():
        sql_out += "AND (LOWER(s.category) = LOWER(?) OR LOWER(s.category_slug) = LOWER(?)) "
        params_list.extend([category.strip(), category.strip()])

      if language and language.strip():
        lang_clean = language.strip().lower()
        lang_substr = f"%{lang_clean}%"
        if has_languages:
          lang_json_tok = f'%"{lang_clean}"%'
          sql_out += (
              "AND (LOWER(COALESCE(s.languages, '')) LIKE ? "
              "OR ((s.languages IS NULL OR s.languages = '' OR s.languages = '[]') "
              "AND (LOWER(s.applies_when) LIKE ? OR LOWER(s.title) LIKE ? "
              f"OR LOWER({reflex_expr}) LIKE ?))) "
          )
          params_list.extend(
              [lang_json_tok, lang_substr, lang_substr, lang_substr]
          )
        else:
          sql_out += (
              "AND (LOWER(s.applies_when) LIKE ? OR LOWER(s.title) LIKE ? "
              f"OR LOWER({reflex_expr}) LIKE ?) "
          )
          params_list.extend([lang_substr, lang_substr, lang_substr])

      if framework and framework.strip():
        fw_clean = framework.strip().lower()
        fw_substr = f"%{fw_clean}%"
        if has_frameworks:
          fw_json_tok = f'%"{fw_clean}"%'
          sql_out += (
              "AND (LOWER(COALESCE(s.frameworks, '')) LIKE ? "
              "OR ((s.frameworks IS NULL OR s.frameworks = '' OR s.frameworks = '[]') "
              "AND (LOWER(s.applies_when) LIKE ? OR LOWER(s.title) LIKE ? "
              f"OR LOWER({wound_expr}) LIKE ? OR LOWER({reflex_expr}) LIKE ?))) "
          )
          params_list.extend(
              [fw_json_tok, fw_substr, fw_substr, fw_substr, fw_substr]
          )
        else:
          sql_out += (
              "AND (LOWER(s.applies_when) LIKE ? OR LOWER(s.title) LIKE ? "
              f"OR LOWER({wound_expr}) LIKE ? OR LOWER({reflex_expr}) LIKE ?) "
          )
          params_list.extend([fw_substr, fw_substr, fw_substr, fw_substr])

      if diagnostic_code and diagnostic_code.strip():
        diag_clean = diagnostic_code.strip().lower()
        diag_substr = f"%{diag_clean}%"
        if has_diag_codes:
          diag_json_tok = f'%"{diag_clean}"%'
          sql_out += (
              "AND (LOWER(COALESCE(s.diagnostic_codes, '')) LIKE ? "
              "OR ((s.diagnostic_codes IS NULL OR s.diagnostic_codes = '' OR s.diagnostic_codes = '[]') "
              f"AND (LOWER(s.title) LIKE ? OR LOWER({wound_expr}) LIKE ? "
              f"OR LOWER({trap_expr}) LIKE ? OR LOWER({reflex_expr}) LIKE ?))) "
          )
          params_list.extend(
              [diag_json_tok, diag_substr, diag_substr, diag_substr, diag_substr]
          )
        else:
          sql_out += (
              f"AND (LOWER(s.title) LIKE ? OR LOWER({wound_expr}) LIKE ? "
              f"OR LOWER({trap_expr}) LIKE ? OR LOWER({reflex_expr}) LIKE ?) "
          )
          params_list.extend(
              [diag_substr, diag_substr, diag_substr, diag_substr]
          )

      if workflow_phase and workflow_phase.strip():
        wp_clean = workflow_phase.strip().lower()
        wp_substr = f"%{wp_clean}%"
        norm_wp = _normalize_workflow_phases(
            [workflow_phase.strip()],
            default_fallback=False,
        )
        if norm_wp:
          norm_substr = f"%{norm_wp[0].lower()}%"
          sql_out += (
              "AND (LOWER(COALESCE(s.workflow_phases, '')) LIKE ? "
              "OR LOWER(COALESCE(s.workflow_phases, '')) LIKE ?) "
          )
          params_list.extend([wp_substr, norm_substr])
        else:
          sql_out += "AND LOWER(COALESCE(s.workflow_phases, '')) LIKE ? "
          params_list.append(wp_substr)

      return sql_out

    has_fts = False
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='scars_fts'"
    )
    if cur.fetchone():
      has_fts = True

    results = []
    query_str = query or ""

    if has_fts and query_str.strip():
      sanitized = _sanitize_fts_query(query_str)
      if sanitized:
        sql = (
            f"SELECT {select_fields}"
            "FROM scars s "
            "JOIN scars_fts fts ON s.id = fts.id "
            "WHERE scars_fts MATCH ? "
        )
        params: List[Any] = [sanitized]
        filtered_sql = _append_filters(sql, params)
        if filtered_sql is None:
          return []
        filtered_sql += "ORDER BY rank LIMIT ?"
        params.append(max(1, limit))

        try:
          cur.execute(filtered_sql, params)
          rows = cur.fetchall()
          for r in rows:
            results.append(_format_scar_row(r))
          if results:
            return results
        except sqlite3.OperationalError:
          # FTS error or syntax fallback
          pass

    # Fallback search using standard LIKE queries or filter-only search
    query_tokens = [
        tok.strip().lower() for tok in query_str.split() if tok.strip()
    ]
    has_any_filter = any(
        bool(f and str(f).strip())
        for f in (
            category,
            language,
            source,
            framework,
            diagnostic_code,
            workflow_phase,
        )
    )
    if not query_tokens and not has_any_filter:
      return []

    params = []
    if query_tokens:
      sql = f"SELECT {select_fields}FROM scars s WHERE ("
      where_clauses = []
      for tok in query_tokens:
        pattern = f"%{tok}%"
        clause = (
            f"LOWER(s.title) LIKE ? OR LOWER({wound_expr}) LIKE ? OR LOWER({trap_expr}) LIKE ?"
            f" OR LOWER({reflex_expr}) LIKE ? OR LOWER({probe_expr}) LIKE ?"
            " OR LOWER(s.applies_when) LIKE ? OR LOWER(s.id) LIKE ?"
        )
        tok_params = [
            pattern,
            pattern,
            pattern,
            pattern,
            pattern,
            pattern,
            pattern,
        ]
        if has_keywords:
          clause += " OR LOWER(COALESCE(s.keywords, '')) LIKE ?"
          tok_params.append(pattern)
        if has_languages:
          clause += " OR LOWER(COALESCE(s.languages, '')) LIKE ?"
          tok_params.append(pattern)
        if has_frameworks:
          clause += " OR LOWER(COALESCE(s.frameworks, '')) LIKE ?"
          tok_params.append(pattern)
        if has_diag_codes:
          clause += " OR LOWER(COALESCE(s.diagnostic_codes, '')) LIKE ?"
          tok_params.append(pattern)
        where_clauses.append(f"({clause})")
        params.extend(tok_params)
      sql += " AND ".join(where_clauses) + ") "
    else:
      sql = f"SELECT {select_fields}FROM scars s WHERE 1=1 "

    filtered_sql = _append_filters(sql, params)
    if filtered_sql is None:
      return []

    filtered_sql += "ORDER BY s.id ASC LIMIT ?"
    params.append(max(1, limit))

    cur.execute(filtered_sql, params)
    rows = cur.fetchall()
    for r in rows:
      results.append(_format_scar_row(r))

    return results
  except (sqlite3.Error, OSError, ValueError):
    return []
  finally:
    if conn:
      conn.close()


def _format_scar_row(row: Any) -> Dict[str, Any]:
  """Formats a SQLite row tuple into a structured scar dictionary."""
  phases = _parse_json_list(row[5] or "[]")

  socratic_probe = row[12] if len(row) > 12 and row[12] is not None else ""
  source = row[13] if len(row) > 13 and row[13] is not None else "upstream"
  orig_id = row[14] if len(row) > 14 and row[14] is not None else None
  raw_hash = row[15] if len(row) > 15 and row[15] is not None else None
  keywords = _parse_json_list(row[16]) if len(row) > 16 else []
  languages = _parse_json_list(row[17]) if len(row) > 17 else []
  frameworks = _parse_json_list(row[18]) if len(row) > 18 else []
  diag_codes = _parse_json_list(row[19]) if len(row) > 19 else []

  return {
      "id": row[0],
      "title": row[1],
      "category": row[2],
      "categorySlug": row[3],
      "visibility": row[4],
      "workflowPhases": phases,
      "workflow_phases": phases,
      "appliesWhen": row[6],
      "graduationStatus": row[7],
      "wound": row[8],
      "trap": row[9],
      "circuitBreaker": row[10],
      "reflex": row[11],
      "socraticProbe": socratic_probe,
      "socratic_probe": socratic_probe,
      "source": source,
      "originalId": orig_id,
      "original_id": orig_id,
      "rawHash": raw_hash,
      "raw_hash": raw_hash,
      "keywords": keywords,
      "languages": languages,
      "frameworks": frameworks,
      "diagnosticCodes": diag_codes,
      "diagnostic_codes": diag_codes,
  }


def extract_protected_spans(text: str) -> Dict[str, List[str]]:
  """Extracts fenced code blocks, display LaTeX, and inline LaTeX from text.

  Ignores dollar signs inside fenced code blocks and inline backtick code spans.
  """
  content = text or ""
  fenced_code: List[str] = []
  display_math: List[str] = []
  inline_math: List[str] = []

  def _mask_fenced(match: re.Match[str]) -> str:
    fenced_code.append(match.group(0))
    return f"\x00FENCED_{len(fenced_code) - 1}\x00"

  masked = re.sub(r"```[\s\S]*?```", _mask_fenced, content)
  masked = re.sub(r"`[^`\n]+`", "\x00INLINE_CODE\x00", masked)

  def _mask_display(match: re.Match[str]) -> str:
    display_math.append(match.group(0))
    return f"\x00DISPLAY_{len(display_math) - 1}\x00"

  masked = re.sub(r"\$\$[\s\S]+?\$\$", _mask_display, masked)

  def _mask_inline(match: re.Match[str]) -> str:
    inline_math.append(match.group(0))
    return f"\x00INLINE_{len(inline_math) - 1}\x00"

  re.sub(r"(?<!\$)\$(?!\$)([^\$\n]+?)(?<!\$)\$(?!\$)", _mask_inline, masked)

  return {
      "fenced_code": fenced_code,
      "display_math": display_math,
      "inline_math": inline_math,
  }


def _strip_protected_and_inline_code(text: str) -> str:
  """Masks fenced code, inline code, display math, and inline math for prose checks."""
  masked = re.sub(r"```[\s\S]*?```", " ", text or "")
  masked = re.sub(r"`[^`\n]+`", " ", masked)
  masked = re.sub(r"\$\$[\s\S]+?\$\$", " ", masked)
  masked = re.sub(r"(?<!\$)\$(?!\$)([^\$\n]+?)(?<!\$)\$(?!\$)", " ", masked)
  return masked


def verify_scar_transformation(
    raw_row: Dict[str, Any], transformed_row: Dict[str, Any]
) -> List[str]:
  """Verifies that a Simple English scar transformation preserves protected spans and rules."""
  errors: List[str] = []

  for req_field in ("wound", "trap", "reflex"):
    val = transformed_row.get(req_field)
    if not isinstance(val, str) or not val.strip():
      errors.append(f"Field '{req_field}' must be a non-empty string.")

  raw_cb = str(raw_row.get("raw_circuit_breaker") or "")
  trans_cb = transformed_row.get("circuit_breaker")
  if raw_cb.strip():
    if not isinstance(trans_cb, str) or not trans_cb.strip():
      errors.append(
          "Field 'circuit_breaker' must be non-empty when 'raw_circuit_breaker' is non-empty."
      )
  elif trans_cb is None:
    errors.append(
        "Field 'circuit_breaker' must be '' (not None) when 'raw_circuit_breaker' is empty."
    )

  field_pairs = (
      ("raw_wound", "wound"),
      ("raw_trap", "trap"),
      ("raw_circuit_breaker", "circuit_breaker"),
      ("raw_reflex", "reflex"),
  )
  for raw_key, trans_key in field_pairs:
    raw_text = str(raw_row.get(raw_key) or "")
    trans_text = str(transformed_row.get(trans_key) or "")
    raw_spans = extract_protected_spans(raw_text)
    trans_spans = extract_protected_spans(trans_text)
    for span_kind in ("fenced_code", "display_math", "inline_math"):
      if raw_spans[span_kind] != trans_spans[span_kind]:
        errors.append(
            f"Field '{trans_key}' {span_kind} mismatch: expected {raw_spans[span_kind]}, got {trans_spans[span_kind]}."
        )

  for prose_field in (
      "wound",
      "trap",
      "circuit_breaker",
      "reflex",
      "socratic_probe",
  ):
    val_str = str(transformed_row.get(prose_field) or "")
    prose_only = _strip_protected_and_inline_code(val_str)
    if ";" in prose_only:
      errors.append(
          f"Field '{prose_field}' contains forbidden semicolon ';' in prose."
      )
    if "\u2014" in prose_only:
      errors.append(
          f"Field '{prose_field}' contains forbidden em-dash '—' in prose."
      )

  probe_str = str(transformed_row.get("socratic_probe") or "").strip()
  if probe_str and not probe_str.endswith("?"):
    errors.append("Field 'socratic_probe' must end with '?'.")

  return errors


class ScriptArgumentError(Exception):
  """Raised when CLI argument parsing encounters an error."""

  def __init__(self, message: str, exit_code: int = 2) -> None:
    super().__init__(message)
    self.exit_code = exit_code


class ScriptArgumentParser(SilentArgumentParser):
  """Custom ArgumentParser that raises HelpExit on status 0 and ScriptArgumentError on errors."""

  def __init__(self, *args: Any, **kwargs: Any) -> None:
    if args and not isinstance(args[0], str):
      self.script = args[0]
      args = args[1:]
    else:
      self.script = kwargs.pop("script", None)
    super().__init__(*args, **kwargs)

  def error(self, message: str) -> NoReturn:
    self._printed_messages.clear()
    raise ScriptArgumentError(message, exit_code=2)

  def exit(self, status: int = 0, message: Optional[str] = None) -> NoReturn:
    if status == 0:
      super().exit(status, message)
    buffered = "".join(self._printed_messages)
    self._printed_messages.clear()
    raise ScriptArgumentError(
        message or buffered or f"Argument parser exited with status {status}",
        exit_code=2,
    )


def _enrich_scars_cursor(
    cur: sqlite3.Cursor,
    scar_ids: Optional[Sequence[str]] = None,
) -> int:
  """Populates metadata columns in scars table for all or specified scar IDs."""
  if scar_ids is not None:
    if not scar_ids:
      return 0
    placeholders = ", ".join(["?"] * len(scar_ids))
    cur.execute(
        f"""
        SELECT id, title, category, category_slug, workflow_phases, applies_when,
               raw_wound, raw_trap, raw_circuit_breaker, raw_reflex,
               wound, trap, circuit_breaker, reflex, socratic_probe, original_id
        FROM scars
        WHERE id IN ({placeholders})
        """,
        tuple(scar_ids),
    )
  else:
    cur.execute(
        """
        SELECT id, title, category, category_slug, workflow_phases, applies_when,
               raw_wound, raw_trap, raw_circuit_breaker, raw_reflex,
               wound, trap, circuit_breaker, reflex, socratic_probe, original_id
        FROM scars
        """
    )
  rows = cur.fetchall()
  enriched_count = 0
  for r in rows:
    scar_row = {
        "id": r[0],
        "title": r[1],
        "category": r[2],
        "category_slug": r[3],
        "workflow_phases": r[4],
        "applies_when": r[5],
        "raw_wound": r[6],
        "raw_trap": r[7],
        "raw_circuit_breaker": r[8],
        "raw_reflex": r[9],
        "wound": r[10],
        "trap": r[11],
        "circuit_breaker": r[12],
        "reflex": r[13],
        "socratic_probe": r[14],
        "original_id": r[15],
    }
    meta = extract_scar_metadata(scar_row)
    cur.execute(
        """
        UPDATE scars
        SET keywords = ?,
            languages = ?,
            frameworks = ?,
            diagnostic_codes = ?,
            workflow_phases = ?
        WHERE id = ?
        """,
        (
            json.dumps(meta["keywords"]),
            json.dumps(meta["languages"]),
            json.dumps(meta["frameworks"]),
            json.dumps(meta["diagnostic_codes"]),
            json.dumps(meta["workflow_phases"]),
            r[0],
        ),
    )
    enriched_count += 1
  return enriched_count


def enrich_scars_database(db_path: str) -> Dict[str, Any]:
  """Enriches metadata columns and rebuilds FTS5 index for all scars in db_path."""
  resolved_path = resolve_scars_db_path(db_path=db_path)
  if not os.path.isfile(resolved_path):
    return {
        "db_path": resolved_path,
        "enriched": 0,
        "enriched_scars": 0,
        "total_scars": 0,
    }

  sync_helper = SyncSyntheticScarsScript()
  sync_helper.db_path = resolved_path
  try:
    sync_helper._init_db_schema()
    conn = sync_helper.conn
    cur = conn.cursor()
    cur.execute("BEGIN TRANSACTION")
    enriched_count = _enrich_scars_cursor(cur)
    if sync_helper.has_fts5:
      sync_helper._populate_fts(cur)
    conn.commit()
    file_size = (
        os.path.getsize(resolved_path) if os.path.isfile(resolved_path) else 0
    )
    cur.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('db_size_bytes', ?)",
        (str(file_size),),
    )
    conn.commit()
    return {
        "db_path": resolved_path,
        "enriched": enriched_count,
        "enriched_scars": enriched_count,
        "total_scars": enriched_count,
    }
  finally:
    sync_helper.cleanup(0)


class EnrichScarsIndexScript(BaseScript):
  """Template Method script enriching multi-dimensional scar metadata and FTS5 index."""

  def __init__(self) -> None:
    super().__init__()
    self.db_path: str = ""
    self.agent: Optional[str] = None
    self.status_only: bool = False

  def parse_args(self, args: Optional[List[str]] = None) -> None:
    super().parse_args(args)
    parser = ScriptArgumentParser(
        prog="enrich_scars_index.py",
        description=(
            "Enrich multi-dimensional metadata and rebuild FTS5 index for"
            " synthetic scars database."
        ),
        epilog=(
            "Examples:\n"
            "  python3 scripts/enrich_scars_index.py --db-path"
            " skills/makeflow/data/scars_registry.db\n"
            "  python3 scripts/enrich_scars_index.py --status"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Path to SQLite scars database.",
    )
    parser.add_argument(
        "--agent",
        default=None,
        help="Agent target flavor (gemini, claude, cursor, copilot, chatgpt).",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        dest="status_only",
        help="Print current enrichment status and exit.",
    )
    parsed = parser.parse_args(self.raw_args)
    self.agent = parsed.agent
    self.status_only = parsed.status_only
    self.db_path = resolve_scars_db_path(
        db_path=parsed.db_path,
        agent=self.agent,
    )

  def run(self) -> int:
    if not os.path.isfile(self.db_path):
      self.stderr.write(f"Error: Scars database not found at '{self.db_path}'.\n")
      return 1

    if self.status_only:
      conn = sqlite3.connect(self.db_path)
      try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='scars'"
        )
        if not cur.fetchone():
          self.stdout.write(
              f"Scars database at '{self.db_path}' is uninitialized.\n"
          )
          return 0
        cur.execute("SELECT COUNT(*) FROM scars")
        total = int(cur.fetchone()[0])
        cur.execute("PRAGMA table_info(scars)")
        cols = {r[1] for r in cur.fetchall()}
        enriched = 0
        if "keywords" in cols:
          cur.execute(
              "SELECT COUNT(*) FROM scars WHERE keywords IS NOT NULL AND"
              " keywords != '' AND keywords != '[]'"
          )
          enriched = int(cur.fetchone()[0])
        self.stdout.write(
            f"Database: {self.db_path}\n"
            f"Total scars: {total}\n"
            f"Enriched scars: {enriched}\n"
        )
        return 0
      finally:
        conn.close()

    summary = enrich_scars_database(self.db_path)
    self.stdout.write(
        f"Enriched {summary['enriched']} scar(s) in '{summary['db_path']}'.\n"
    )
    return 0


class SyncSyntheticScarsScript(BaseScript):
  """Template Method script synchronizing and querying synthetic scars."""

  def __init__(self) -> None:
    super().__init__()
    self.db_path: str = ""
    self.url: str = DEFAULT_REGISTRY_URL
    self.force: bool = False
    self.status_only: bool = False
    self.agent: Optional[str] = None
    self.query: Optional[str] = None
    self.category: Optional[str] = None
    self.language: Optional[str] = None
    self.source: Optional[str] = None
    self.framework: Optional[str] = None
    self.diagnostic_code: Optional[str] = None
    self.workflow_phase: Optional[str] = None
    self.limit: int = 3
    self.export_pending: Optional[int] = None
    self.apply_transformed: Optional[str] = None
    self.import_simple_english: Optional[str] = None
    self.verify_transformed: bool = False
    self.enrich: bool = False
    self.has_fts5: bool = False
    self.conn: Optional[sqlite3.Connection] = None

  def parse_args(self, args: Optional[List[str]] = None) -> None:
    super().parse_args(args)
    parser = ScriptArgumentParser(
        prog="sync_synthetic_scars.py",
        description="Synchronize and query synthetic scars registry.",
        epilog=(
            "Examples:\n"
            "  python3 scripts/sync_synthetic_scars.py --status\n"
            "  python3 scripts/sync_synthetic_scars.py --query 'BaseScript' --limit 3"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Path to SQLite scars database.",
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_REGISTRY_URL,
        help="URL of the synthetic scars registry JSON.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force download and rebuild database even if ETag matches.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        dest="status_only",
        help="Print current database status and exit.",
    )
    parser.add_argument(
        "--agent",
        default=None,
        help="Agent target flavor (gemini, claude, cursor, copilot, chatgpt).",
    )
    parser.add_argument(
        "--query",
        default=None,
        help="Query string for search testing.",
    )
    parser.add_argument(
        "--category",
        default=None,
        help="Category filter for query.",
    )
    parser.add_argument(
        "--language",
        default=None,
        help="Language filter for query.",
    )
    parser.add_argument(
        "--source",
        default=None,
        help="Source filter for query (makeflow, upstream).",
    )
    parser.add_argument(
        "--framework",
        default=None,
        help="Framework filter for query (for example flutter, riverpod, pytest).",
    )
    parser.add_argument(
        "--diagnostic-code",
        dest="diagnostic_code",
        default=None,
        help="Diagnostic or error code filter for query (for example StateError, SIGTERM).",
    )
    parser.add_argument(
        "--workflow-phase",
        dest="workflow_phase",
        default=None,
        help="Workflow phase filter for query (for example 'Target 30: tdd', 'T30').",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="Maximum results returned by query (default 3).",
    )
    parser.add_argument(
        "--export-pending",
        nargs="?",
        const=1,
        type=int,
        default=None,
        help="Export pending untransformed scars as JSON (default 1 scar at a time).",
    )
    parser.add_argument(
        "--apply-transformed",
        default=None,
        help="Path to JSON file containing transformed Simple English scar fields.",
    )
    parser.add_argument(
        "--import-simple-english",
        "--import",
        dest="import_simple_english",
        default=None,
        help=(
            "Path to JSON file containing id, wound, trap, circuit_breaker"
            " (optional), and reflex fields to import."
        ),
    )
    parser.add_argument(
        "--verify-transformed",
        action="store_true",
        help="Verify all transformed Simple English scars in the database.",
    )
    parser.add_argument(
        "--enrich",
        action="store_true",
        help="Enrich multi-dimensional metadata and rebuild FTS5 index.",
    )

    parsed = parser.parse_args(self.raw_args)
    self.url = parsed.url
    self.force = parsed.force
    self.status_only = parsed.status_only
    self.agent = parsed.agent
    self.query = parsed.query
    self.category = parsed.category
    self.language = parsed.language
    self.source = parsed.source
    self.framework = parsed.framework
    self.diagnostic_code = parsed.diagnostic_code
    self.workflow_phase = parsed.workflow_phase
    self.limit = parsed.limit
    self.export_pending = parsed.export_pending
    self.apply_transformed = parsed.apply_transformed
    self.import_simple_english = parsed.import_simple_english
    self.verify_transformed = parsed.verify_transformed
    self.enrich = parsed.enrich
    self.db_path = resolve_scars_db_path(
        db_path=parsed.db_path,
        agent=self.agent,
    )

  def setup(self) -> None:
    """Ensures destination directory exists and prepares schema."""
    if self.db_path:
      os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)

  def _populate_fts(self, cur: sqlite3.Cursor) -> None:
    """Populates scars_fts using COALESCE over in-use, raw, and metadata columns."""
    cur.execute("DELETE FROM scars_fts")
    cur.execute(
        """
        INSERT INTO scars_fts (
            id, title, category, applies_when,
            wound, trap, reflex, socratic_probe,
            keywords, languages, frameworks, diagnostic_codes, workflow_phases,
            source, original_id
        )
        SELECT
            id,
            title,
            category,
            applies_when,
            COALESCE(wound, raw_wound, ''),
            COALESCE(trap, raw_trap, ''),
            COALESCE(reflex, raw_reflex, ''),
            COALESCE(socratic_probe, ''),
            COALESCE(keywords, '[]'),
            COALESCE(languages, '[]'),
            COALESCE(frameworks, '[]'),
            COALESCE(diagnostic_codes, '[]'),
            COALESCE(workflow_phases, '[]'),
            source,
            COALESCE(original_id, '')
        FROM scars
        """
    )
    cur.execute("INSERT INTO scars_fts(scars_fts) VALUES('optimize')")

  def _init_db_schema(self) -> None:
    """Initializes tables and probes FTS5 virtual table support."""
    if not self.conn:
      self.conn = sqlite3.connect(self.db_path)

    cur = self.conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS scars (
            id TEXT PRIMARY KEY,
            title TEXT,
            category TEXT,
            category_slug TEXT,
            visibility TEXT,
            workflow_phases TEXT,
            applies_when TEXT,
            graduation_status TEXT,
            raw_wound TEXT,
            raw_trap TEXT,
            raw_circuit_breaker TEXT,
            raw_reflex TEXT,
            raw_hash TEXT,
            wound TEXT,
            trap TEXT,
            circuit_breaker TEXT,
            reflex TEXT,
            socratic_probe TEXT,
            source TEXT DEFAULT 'upstream',
            original_id TEXT,
            keywords TEXT,
            languages TEXT,
            frameworks TEXT,
            diagnostic_codes TEXT
        )
    """)

    # Migrate columns if scars table already existed without new columns
    cur.execute("PRAGMA table_info(scars)")
    existing_cols = {r[1] for r in cur.fetchall()}
    for col_name, col_def in (
        ("raw_wound", "TEXT"),
        ("raw_trap", "TEXT"),
        ("raw_circuit_breaker", "TEXT"),
        ("raw_reflex", "TEXT"),
        ("raw_hash", "TEXT"),
        ("wound", "TEXT"),
        ("trap", "TEXT"),
        ("circuit_breaker", "TEXT"),
        ("reflex", "TEXT"),
        ("socratic_probe", "TEXT"),
        ("source", "TEXT DEFAULT 'upstream'"),
        ("original_id", "TEXT"),
        ("keywords", "TEXT"),
        ("languages", "TEXT"),
        ("frameworks", "TEXT"),
        ("diagnostic_codes", "TEXT"),
    ):
      if col_name not in existing_cols:
        cur.execute(f"ALTER TABLE scars ADD COLUMN {col_name} {col_def}")

    cur.execute(
        """
        UPDATE scars
        SET raw_wound = COALESCE(raw_wound, wound),
            raw_trap = COALESCE(raw_trap, trap),
            raw_circuit_breaker = COALESCE(raw_circuit_breaker, circuit_breaker),
            raw_reflex = COALESCE(raw_reflex, reflex)
        WHERE raw_wound IS NULL
        """
    )

    self.has_fts5 = False
    try:
      cur.execute(
          "SELECT sql FROM sqlite_master WHERE type='table' AND name='scars_fts'"
      )
      fts_row = cur.fetchone()
      if fts_row:
        fts_sql = str(fts_row[0] or "").lower()
        cur.execute("PRAGMA table_info(scars_fts)")
        fts_cols = {r[1] for r in cur.fetchall()}
        required_fts_cols = {
            "id",
            "title",
            "category",
            "applies_when",
            "wound",
            "trap",
            "reflex",
            "socratic_probe",
            "keywords",
            "languages",
            "frameworks",
            "diagnostic_codes",
            "workflow_phases",
            "source",
            "original_id",
        }
        if (
            not required_fts_cols.issubset(fts_cols)
            or "id unindexed" in fts_sql
            or "original_id unindexed" in fts_sql
        ):
          cur.execute("DROP TABLE scars_fts")

      cur.execute("""
          CREATE VIRTUAL TABLE IF NOT EXISTS scars_fts USING fts5(
              id,
              title,
              category,
              applies_when,
              wound,
              trap,
              reflex,
              socratic_probe,
              keywords,
              languages,
              frameworks,
              diagnostic_codes,
              workflow_phases,
              source UNINDEXED,
              original_id
          )
      """)
      cur.execute("SELECT COUNT(*) FROM scars_fts")
      fts_count = cur.fetchone()[0]
      cur.execute("SELECT COUNT(*) FROM scars")
      total_count = cur.fetchone()[0]
      if fts_count == 0 and total_count > 0:
        self._populate_fts(cur)
      self.has_fts5 = True
    except sqlite3.OperationalError:
      self.has_fts5 = False

    self.conn.commit()

  def _count_pending_scars(self, cur: sqlite3.Cursor) -> int:
    """Returns count of scars with any NULL in-use field."""
    cur.execute("PRAGMA table_info(scars)")
    cols = {r[1] for r in cur.fetchall()}
    if "socratic_probe" not in cols:
      return 0
    cur.execute(
        """
        SELECT COUNT(*) FROM scars
        WHERE wound IS NULL
           OR trap IS NULL
           OR circuit_breaker IS NULL
           OR reflex IS NULL
        """
    )
    row = cur.fetchone()
    return int(row[0]) if row else 0

  def _emit_pending_notice(self, cur: sqlite3.Cursor) -> None:
    """Writes pending Simple English transformation notice when NULL fields exist."""
    pending = self._count_pending_scars(cur)
    if pending <= 0:
      return
    self.stdout.write(
        f"Pending Simple English transformations: {pending} scar(s).\n"
    )

  def _report_status(self) -> int:
    """Reports status of the SQLite database."""
    if not os.path.isfile(self.db_path):
      self.stdout.write(f"Scars database not found at '{self.db_path}'.\n")
      return 0

    conn = None
    try:
      file_size = os.path.getsize(self.db_path)
      conn = sqlite3.connect(self.db_path)
      cur = conn.cursor()
      cur.execute(
          "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
      )
      if not cur.fetchone():
        self.stdout.write(f"Scars database at '{self.db_path}' is uninitialized.\n")
        return 0

      cur.execute("SELECT key, value FROM meta")
      meta = dict(cur.fetchall())
      total = meta.get("total_scars", "0")
      etag = meta.get("etag", "none")
      last_synced = meta.get("last_synced_at", "never")

      self.stdout.write(
          f"Database: {self.db_path}\n"
          f"Size: {file_size} bytes\n"
          f"Total scars: {total} scars indexed\n"
          f"ETag: {etag}\n"
          f"Last synced: {last_synced}\n"
      )
      self._emit_pending_notice(cur)
      return 0
    finally:
      if conn:
        conn.close()

  def _run_export_pending(self) -> int:
    """Exports pending scars with NULL in-use fields as JSON."""
    self._init_db_schema()
    cur = self.conn.cursor()
    limit_val = max(1, int(self.export_pending or 1))
    cur.execute(
        """
        SELECT id, title, category, applies_when,
               raw_wound, raw_trap, raw_circuit_breaker, raw_reflex, raw_hash,
               wound, trap, circuit_breaker, reflex, socratic_probe
        FROM scars
        WHERE wound IS NULL
           OR trap IS NULL
           OR circuit_breaker IS NULL
           OR reflex IS NULL
        ORDER BY id ASC
        LIMIT ?
        """,
        (limit_val,),
    )
    rows = cur.fetchall()
    exported: List[Dict[str, Any]] = []
    for r in rows:
      row_dict = {
          "id": r[0],
          "title": r[1],
          "category": r[2],
          "applies_when": r[3],
          "raw_wound": r[4] or "",
          "raw_trap": r[5] or "",
          "raw_circuit_breaker": r[6] or "",
          "raw_reflex": r[7] or "",
          "raw_hash": r[8] or "",
          "wound": r[9],
          "trap": r[10],
          "circuit_breaker": r[11],
          "reflex": r[12],
          "socratic_probe": r[13],
      }
      row_dict["null_fields"] = [
          col
          for col in (
              "wound",
              "trap",
              "circuit_breaker",
              "reflex",
              "socratic_probe",
          )
          if row_dict[col] is None
      ]
      exported.append(row_dict)

    self.stdout.write(json.dumps(exported, indent=2) + "\n")
    return 0

  def _run_apply_transformed(self) -> int:
    """Applies transformed Simple English values into NULL fields and verifies invariants."""
    if not self.apply_transformed or not os.path.isfile(self.apply_transformed):
      self.stderr.write(
          f"Error: Transformed JSON file not found: '{self.apply_transformed}'.\n"
      )
      return 1

    try:
      with open(self.apply_transformed, "r", encoding="utf-8") as f:
        payload = json.load(f)
    except (OSError, ValueError) as exc:
      self.stderr.write(f"Error: Failed to read transformed JSON: {exc}\n")
      return 1

    items = payload if isinstance(payload, list) else [payload]
    self._init_db_schema()
    cur = self.conn.cursor()

    try:
      cur.execute("BEGIN TRANSACTION")
      updated_ids: List[str] = []
      for item in items:
        if not isinstance(item, dict) or not item.get("id"):
          self.conn.rollback()
          self.stderr.write("Error: Transformed item is missing 'id'.\n")
          return 1

        scar_id = str(item["id"])
        cur.execute(
            """
            SELECT raw_wound, raw_trap, raw_circuit_breaker, raw_reflex,
                   wound, trap, circuit_breaker, reflex, socratic_probe
            FROM scars
            WHERE id = ?
            """,
            (scar_id,),
        )
        row = cur.fetchone()
        if not row:
          self.conn.rollback()
          self.stderr.write(f"Error: Scar '{scar_id}' not found in database.\n")
          return 1

        raw_row = {
            "raw_wound": row[0] if row[0] is not None else (row[4] or ""),
            "raw_trap": row[1] if row[1] is not None else (row[5] or ""),
            "raw_circuit_breaker": (
                row[2] if row[2] is not None else (row[6] or "")
            ),
            "raw_reflex": row[3] if row[3] is not None else (row[7] or ""),
        }
        existing_in_use = {
            "wound": row[4],
            "trap": row[5],
            "circuit_breaker": row[6],
            "reflex": row[7],
            "socratic_probe": row[8],
        }
        merged: Dict[str, Any] = {}
        for col in (
            "wound",
            "trap",
            "circuit_breaker",
            "reflex",
            "socratic_probe",
        ):
          if existing_in_use[col] is not None:
            merged[col] = existing_in_use[col]
          else:
            val = item.get(col)
            if col == "socratic_probe" and (val is None or not str(val).strip()):
              merged[col] = None
            else:
              merged[col] = val

        errors = verify_scar_transformation(raw_row, merged)
        if errors:
          self.conn.rollback()
          for err in errors:
            self.stderr.write(f"Error [{scar_id}]: {err}\n")
          return 1

        cur.execute(
            """
            UPDATE scars
            SET wound = ?, trap = ?, circuit_breaker = ?, reflex = ?, socratic_probe = ?
            WHERE id = ?
            """,
            (
                merged["wound"],
                merged["trap"],
                merged["circuit_breaker"],
                merged["reflex"],
                merged["socratic_probe"],
                scar_id,
            ),
        )
        updated_ids.append(scar_id)

      _enrich_scars_cursor(cur, scar_ids=updated_ids)
      if self.has_fts5:
        self._populate_fts(cur)

      self.conn.commit()
      file_size = (
          os.path.getsize(self.db_path) if os.path.isfile(self.db_path) else 0
      )
      cur.execute(
          "INSERT OR REPLACE INTO meta (key, value) VALUES ('db_size_bytes', ?)",
          (str(file_size),),
      )
      self.conn.commit()
    except sqlite3.Error as exc:
      self.conn.rollback()
      self.stderr.write(f"Error: Database failure during apply: {exc}\n")
      return 1

    self.stdout.write(f"Applied {len(items)} transformed scar(s).\n")
    return 0

  def _run_import_simple_english(self) -> int:
    """Imports Simple English wound, trap, circuit_breaker, and reflex without modifying socratic_probe."""
    if not self.import_simple_english or not os.path.isfile(
        self.import_simple_english
    ):
      self.stderr.write(
          "Error: Simple English JSON file not found:"
          f" '{self.import_simple_english}'.\n"
      )
      return 1

    try:
      with open(self.import_simple_english, "r", encoding="utf-8") as f:
        payload = json.load(f)
    except (OSError, ValueError) as exc:
      self.stderr.write(f"Error: Failed to read Simple English JSON: {exc}\n")
      return 1

    items = payload if isinstance(payload, list) else [payload]
    if not items or not all(isinstance(x, dict) for x in items):
      self.stderr.write(
          "Error: Simple English JSON must be an object or non-empty list of"
          " objects.\n"
      )
      return 1

    self._init_db_schema()
    cur = self.conn.cursor()

    try:
      cur.execute("BEGIN TRANSACTION")
      updated_ids: List[str] = []
      for item in items:
        scar_id = str(item.get("id") or "").strip()
        if not scar_id:
          self.conn.rollback()
          self.stderr.write("Error: Simple English item is missing 'id'.\n")
          return 1

        cur.execute(
            """
            SELECT raw_wound, raw_trap, raw_circuit_breaker, raw_reflex,
                   wound, trap, circuit_breaker, reflex
            FROM scars
            WHERE id = ?
            """,
            (scar_id,),
        )
        row = cur.fetchone()
        if not row:
          self.conn.rollback()
          self.stderr.write(f"Error: Scar '{scar_id}' not found in database.\n")
          return 1

        raw_row = {
            "raw_wound": row[0] if row[0] is not None else (row[4] or ""),
            "raw_trap": row[1] if row[1] is not None else (row[5] or ""),
            "raw_circuit_breaker": (
                row[2] if row[2] is not None else (row[6] or "")
            ),
            "raw_reflex": row[3] if row[3] is not None else (row[7] or ""),
        }
        cb_val = item.get("circuit_breaker")
        if cb_val is None:
          cb_val = ""

        candidate = {
            "wound": item.get("wound"),
            "trap": item.get("trap"),
            "circuit_breaker": cb_val,
            "reflex": item.get("reflex"),
        }

        errors = verify_scar_transformation(raw_row, candidate)
        if errors:
          self.conn.rollback()
          for err in errors:
            self.stderr.write(f"Error [{scar_id}]: {err}\n")
          return 1

        cur.execute(
            """
            UPDATE scars
            SET wound = ?, trap = ?, circuit_breaker = ?, reflex = ?
            WHERE id = ?
            """,
            (
                candidate["wound"],
                candidate["trap"],
                candidate["circuit_breaker"],
                candidate["reflex"],
                scar_id,
            ),
        )
        updated_ids.append(scar_id)

      _enrich_scars_cursor(cur, scar_ids=updated_ids)
      if self.has_fts5:
        self._populate_fts(cur)

      self.conn.commit()
      file_size = (
          os.path.getsize(self.db_path) if os.path.isfile(self.db_path) else 0
      )
      cur.execute(
          "INSERT OR REPLACE INTO meta (key, value) VALUES ('db_size_bytes', ?)",
          (str(file_size),),
      )
      self.conn.commit()
    except sqlite3.Error as exc:
      self.conn.rollback()
      self.stderr.write(f"Error: Database failure during import: {exc}\n")
      return 1

    self.stdout.write(f"Imported {len(items)} Simple English scar(s).\n")
    return 0

  def _run_verify_transformed(self) -> int:
    """Verifies all rows that have populated transformed fields."""
    self._init_db_schema()
    cur = self.conn.cursor()
    cur.execute(
        """
        SELECT id, raw_wound, raw_trap, raw_circuit_breaker, raw_reflex,
               wound, trap, circuit_breaker, reflex, socratic_probe
        FROM scars
        WHERE wound IS NOT NULL
           OR trap IS NOT NULL
           OR reflex IS NOT NULL
           OR socratic_probe IS NOT NULL
        """
    )
    rows = cur.fetchall()
    all_errors: List[str] = []
    for r in rows:
      scar_id = r[0]
      raw_row = {
          "raw_wound": r[1] if r[1] is not None else "",
          "raw_trap": r[2] if r[2] is not None else "",
          "raw_circuit_breaker": r[3] if r[3] is not None else "",
          "raw_reflex": r[4] if r[4] is not None else "",
      }
      trans_row = {
          "wound": r[5],
          "trap": r[6],
          "circuit_breaker": r[7],
          "reflex": r[8],
          "socratic_probe": r[9],
      }
      errs = verify_scar_transformation(raw_row, trans_row)
      for err in errs:
        all_errors.append(f"[{scar_id}] {err}")

    if all_errors:
      for err in all_errors:
        self.stderr.write(f"Verification Error {err}\n")
      return 1

    self.stdout.write(f"Verified {len(rows)} transformed scar(s) with 0 errors.\n")
    return 0

  def run(self) -> int:
    """Executes synchronization, enrichment, or query workflow."""
    if self.status_only:
      return self._report_status()

    if self.export_pending is not None:
      return self._run_export_pending()

    if self.apply_transformed is not None:
      return self._run_apply_transformed()

    if self.import_simple_english is not None:
      return self._run_import_simple_english()

    if self.verify_transformed:
      return self._run_verify_transformed()

    if self.enrich:
      summary = enrich_scars_database(self.db_path)
      self.stdout.write(
          f"Enriched {summary['enriched']} scar(s) in '{summary['db_path']}'.\n"
      )
      return 0

    has_filter = any(
        val is not None
        for val in (
            self.category,
            self.language,
            self.source,
            self.framework,
            self.diagnostic_code,
            self.workflow_phase,
        )
    )
    if self.query is not None or has_filter:
      matches = query_scars(
          db_path=self.db_path,
          query=self.query or "",
          category=self.category,
          language=self.language,
          limit=self.limit,
          source=self.source,
          framework=self.framework,
          diagnostic_code=self.diagnostic_code,
          workflow_phase=self.workflow_phase,
      )
      self.stdout.write(json.dumps(matches, indent=2) + "\n")
      return 0

    return self._sync_scars()

  def _sync_scars(self) -> int:
    """Fetches scars JSON, checks ETag, and updates SQLite tables."""
    self._init_db_schema()

    current_etag = None
    if not self.force and os.path.isfile(self.db_path):
      cur = self.conn.cursor()
      cur.execute("SELECT value FROM meta WHERE key = 'etag'")
      row = cur.fetchone()
      if row:
        current_etag = row[0]

    req = urllib.request.Request(
        self.url,
        headers={"User-Agent": "makeflow-sync/1.0", "Accept": "application/json"},
    )
    if current_etag and not self.force:
      req.add_header("If-None-Match", current_etag)

    try:
      with urllib.request.urlopen(req, timeout=30) as resp:
        status_code = getattr(resp, "status", None)
        if status_code is None and hasattr(resp, "getcode"):
          status_code = resp.getcode()

        if status_code == 304:
          self.stdout.write(
              f"Scars database is up to date (HTTP 304 Not Modified, ETag: {current_etag}).\n"
          )
          self._emit_pending_notice(self.conn.cursor())
          return 0

        new_etag = resp.headers.get("ETag") if hasattr(resp, "headers") else None
        if not new_etag and hasattr(resp, "info"):
          info_hdrs = resp.info()
          if info_hdrs:
            new_etag = info_hdrs.get("ETag")

        raw_body = resp.read().decode("utf-8")
        data = json.loads(raw_body)
    except urllib.error.HTTPError as exc:
      if exc.code == 304:
        self.stdout.write(
            f"Scars database is up to date (HTTP 304 Not Modified, ETag: {current_etag}).\n"
        )
        self._emit_pending_notice(self.conn.cursor())
        return 0
      self.stderr.write(f"Error: HTTP error during sync: {exc}\n")
      return 1
    except Exception as exc:  # pylint: disable=broad-except
      self.stderr.write(f"Error: Failed to fetch or parse registry JSON: {exc}\n")
      return 1

    scars_list = data.get("scars", []) if isinstance(data, dict) else data
    if not isinstance(scars_list, list):
      self.stderr.write("Error: Registry response does not contain a valid scar list.\n")
      return 1

    # Atomic transaction
    cur = self.conn.cursor()
    cur.execute("BEGIN TRANSACTION")
    try:
      # Query existing scars
      cur.execute(
          """
          SELECT id, title,
                 COALESCE(wound, raw_wound),
                 COALESCE(trap, raw_trap),
                 COALESCE(reflex, raw_reflex),
                 raw_hash, source, original_id
          FROM scars
          """
      )
      existing_rows = {
          r[0]: {
              "id": r[0],
              "title": r[1],
              "wound": r[2],
              "trap": r[3],
              "reflex": r[4],
              "raw_hash": r[5],
              "source": r[6] or "upstream",
              "original_id": r[7],
          }
          for r in cur.fetchall()
      }

      incoming_upstream_ids = set()
      modified = False

      # First pass: collision reconciliation with Makeflow scars
      for item in scars_list:
        scar_id = item.get("id")
        if not scar_id:
          continue
        incoming_upstream_ids.add(scar_id)

        raw_hash = hashlib.md5(
            json.dumps(item, sort_keys=True).encode("utf-8")
        ).hexdigest()

        if scar_id in existing_rows and existing_rows[scar_id]["source"] == "makeflow":
          mf_row = existing_rows[scar_id]
          content_matches = False
          if mf_row.get("raw_hash") and mf_row["raw_hash"] == raw_hash:
            content_matches = True
          elif (
              mf_row.get("title") == item.get("title")
              and mf_row.get("wound") == item.get("wound")
              and mf_row.get("trap") == item.get("trap")
              and mf_row.get("reflex") == item.get("reflex")
          ):
            content_matches = True

          if content_matches:
            # Keep upstream scar, remove Makeflow duplicate
            cur.execute(
                "DELETE FROM scars WHERE id = ? AND source = 'makeflow'",
                (scar_id,),
            )
            del existing_rows[scar_id]
            modified = True
          else:
            # Rename Makeflow scar safely without primary key collision
            suffix = scar_id[5:] if scar_id.startswith("SCAR-") else scar_id
            base_mf_id = f"SCAR-MF-{suffix}"
            new_mf_id = base_mf_id
            if new_mf_id in existing_rows:
              existing_mf = existing_rows[new_mf_id]
              if (
                  existing_mf.get("title") == mf_row.get("title")
                  and existing_mf.get("wound") == mf_row.get("wound")
              ):
                cur.execute(
                    "DELETE FROM scars WHERE id = ? AND source = 'makeflow'",
                    (scar_id,),
                )
                del existing_rows[scar_id]
                modified = True
                continue
              counter = 2
              while f"{base_mf_id}-{counter}" in existing_rows:
                counter += 1
              new_mf_id = f"{base_mf_id}-{counter}"

            cur.execute(
                "UPDATE scars SET id = ?, original_id = ? WHERE id = ? AND source = 'makeflow'",
                (new_mf_id, scar_id, scar_id),
            )
            mf_row["id"] = new_mf_id
            mf_row["original_id"] = scar_id
            existing_rows[new_mf_id] = mf_row
            del existing_rows[scar_id]
            modified = True

      # Second pass: delete upstream rows that are no longer in upstream list
      for eid, erow in list(existing_rows.items()):
        if erow.get("source") == "upstream" and eid not in incoming_upstream_ids:
          cur.execute("DELETE FROM scars WHERE id = ? AND source = 'upstream'", (eid,))
          del existing_rows[eid]
          modified = True

      # Third pass: delta sync incoming upstream scars
      inserted_or_updated = 0
      for item in scars_list:
        scar_id = item.get("id")
        if not scar_id:
          continue

        raw_hash = hashlib.md5(
            json.dumps(item, sort_keys=True).encode("utf-8")
        ).hexdigest()

        existing_row = existing_rows.get(scar_id)
        if (
            existing_row
            and existing_row.get("source") == "upstream"
            and existing_row.get("raw_hash") == raw_hash
        ):
          # Skip table rewrite for unchanged row (preserves Simple English in-use fields)
          continue

        title = item.get("title", "")
        category = item.get("category", "")
        cat_slug = item.get("categorySlug") or item.get("category_slug", "")
        visibility = item.get("visibility", "public")

        phases_raw = item.get("workflowPhases") or item.get("workflow_phases", [])
        if isinstance(phases_raw, list):
          phases_json = json.dumps(phases_raw)
        else:
          phases_json = json.dumps([str(phases_raw)])

        applies_when = item.get("appliesWhen") or item.get("applies_when", "")
        grad_status = item.get("graduationStatus") or item.get("graduation_status", "")
        raw_wound = str(item.get("wound", "") or "")
        raw_trap = str(item.get("trap", "") or "")
        raw_breaker = str(
            item.get("circuitBreaker") or item.get("circuit_breaker", "") or ""
        )
        raw_reflex = str(item.get("reflex", "") or "")
        in_use_breaker: Optional[str] = None if raw_breaker.strip() else ""

        meta = extract_scar_metadata(
            {
                "id": scar_id,
                "title": title,
                "category": category,
                "category_slug": cat_slug,
                "workflow_phases": phases_json,
                "applies_when": applies_when,
                "raw_wound": raw_wound,
                "raw_trap": raw_trap,
                "raw_circuit_breaker": raw_breaker,
                "raw_reflex": raw_reflex,
            }
        )

        cur.execute(
            """
            INSERT OR REPLACE INTO scars (
                id, title, category, category_slug, visibility,
                workflow_phases, applies_when, graduation_status,
                raw_wound, raw_trap, raw_circuit_breaker, raw_reflex, raw_hash,
                wound, trap, circuit_breaker, reflex, socratic_probe,
                source, original_id,
                keywords, languages, frameworks, diagnostic_codes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, NULL, NULL, 'upstream', NULL, ?, ?, ?, ?)
        """,
            (
                scar_id,
                title,
                category,
                cat_slug,
                visibility,
                json.dumps(meta["workflow_phases"]),
                applies_when,
                grad_status,
                raw_wound,
                raw_trap,
                raw_breaker,
                raw_reflex,
                raw_hash,
                in_use_breaker,
                json.dumps(meta["keywords"]),
                json.dumps(meta["languages"]),
                json.dumps(meta["frameworks"]),
                json.dumps(meta["diagnostic_codes"]),
            ),
        )
        existing_rows[scar_id] = {
            "id": scar_id,
            "title": title,
            "wound": raw_wound,
            "trap": raw_trap,
            "reflex": raw_reflex,
            "raw_hash": raw_hash,
            "source": "upstream",
            "original_id": None,
        }
        modified = True
        inserted_or_updated += 1

      # Enrich any makeflow rows that lack metadata
      cur.execute(
          "SELECT id FROM scars WHERE keywords IS NULL OR keywords = ''"
      )
      un_enriched_ids = [r[0] for r in cur.fetchall()]
      if un_enriched_ids:
        _enrich_scars_cursor(cur, scar_ids=un_enriched_ids)
        modified = True

      # Check if scars_fts needs refresh (if modified or scars_fts empty while scars not empty)
      if self.has_fts5:
        cur.execute("SELECT COUNT(*) FROM scars_fts")
        fts_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM scars")
        total_count = cur.fetchone()[0]
        if modified or (fts_count == 0 and total_count > 0):
          self._populate_fts(cur)
      else:
        cur.execute("SELECT COUNT(*) FROM scars")
        total_count = cur.fetchone()[0]

      now_iso = datetime.now(timezone.utc).isoformat()
      cur.execute(
          "INSERT OR REPLACE INTO meta (key, value) VALUES ('etag', ?)",
          (new_etag or "",),
      )
      cur.execute(
          "INSERT OR REPLACE INTO meta (key, value) VALUES ('last_synced_at', ?)",
          (now_iso,),
      )
      cur.execute(
          "INSERT OR REPLACE INTO meta (key, value) VALUES ('total_scars', ?)",
          (str(total_count),),
      )

      self.conn.commit()
      if modified:
        self.conn.execute("VACUUM")
      file_size = os.path.getsize(self.db_path) if os.path.isfile(self.db_path) else 0
      cur.execute(
          "INSERT OR REPLACE INTO meta (key, value) VALUES ('db_size_bytes', ?)",
          (str(file_size),),
      )
      self.conn.commit()
    except Exception as exc:  # pylint: disable=broad-except
      self.conn.rollback()
      self.stderr.write(f"Error: Failed to insert scars into database: {exc}\n")
      return 1

    self.stdout.write(
        f"Successfully synchronized {len(scars_list)} synthetic scars ({inserted_or_updated} updated) to '{self.db_path}'.\n"
    )
    self._emit_pending_notice(cur)
    return 0

  def cleanup(self, exit_code: int) -> None:
    """Closes SQLite connection."""
    if self.conn:
      try:
        self.conn.close()
      except sqlite3.Error:
        pass
      self.conn = None
