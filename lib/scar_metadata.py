"""Deterministic metadata extraction for synthetic scars (GH-145, GH-209).

Extracts keywords, programming languages, frameworks, diagnostic codes,
canonical workflow phases, and shift-left Makeflow integer targets from
synthetic scar records without LLM dependencies.
"""

import json
import re
from typing import Any, Dict, List, Optional

from lib.scar_ontology import (
    TARGET_INT_TO_PHASE_LABEL,
    compute_makeflow_targets,
    normalize_domain_category,
    parse_upstream_targets,
)

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
        re.compile(
            r"\b(?:hydration\s+mismatch|ssr\s+hydration)\b", re.IGNORECASE
        ),
        ("hydration",),
    ),
    (
        re.compile(
            r"\b(?:StreamController|controller\s+disposal|state\s+ownership|dispose\(\))\b",
            re.IGNORECASE,
        ),
        ("focusnode", "texteditingcontroller", "animationcontroller"),
    ),
)

_LANGUAGE_RULES = (
    (
        "dart",
        re.compile(
            r"(?:\bdart\b|\bflutter\b|\briverpod\b|\bbloc\b|\bcubit\b|\bBlocSignal\b|\bCubitSignal\b|\bStreamController\b|\bpubspec\b|\bpana\b|\bCompleter\b|\bWidgetTester\b|\bStatefulWidget\b|\bStatelessWidget\b|\bBuildContext\b|\bfakeAsync\b|\bAsyncNotifier\b|\bStateNotifier\b|\bChangeNotifier\b|\bValueNotifier\b|\.dart\b|```dart\b|SCAR-(?:LANG-DART|FLUTTER|BLOC|RIV))",
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
            r"(?:\bsql\b|\bfts5\b|\bPRAGMA\b|\bCREATE\s+TABLE\b|\bALTER\s+TABLE\b|```sql\b)",
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
    ("a2ui", re.compile(r"(?:\ba2ui\b|SCAR-A2UI)", re.IGNORECASE)),
    ("genui", re.compile(r"(?:\bgenui\b|SCAR-GENUI)", re.IGNORECASE)),
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


def parse_json_list(raw_val: Any) -> List[str]:
  """Parse a JSON array string or Python list into clean non-empty strings.

  Args:
    raw_val: JSON string, list, or scalar value to normalize.

  Returns:
    List of stripped non-empty string items.
  """
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


def _default_targets_for_domain(domain: str) -> List[int]:
  """Return default Makeflow integer targets when raw phases are empty.

  Args:
    domain: Canonical domain category code (for example ``ARCH`` or ``GIT``).

  Returns:
    Ordered list of default integer Makeflow target numbers.
  """
  norm_domain = str(domain or "").strip().upper()
  if norm_domain in {
      "ARCH",
      "DIR",
      "API",
      "DEP",
      "REFACTOR",
      "INTEROP",
      "MIXIN",
      "DATA",
      "SCHEMA",
      "DB",
      "STORAGE",
      "WIRE",
      "SEC",
      "AUTH",
      "DOC",
      "DIFF",
  }:
    return [15, 20, 30]
  if norm_domain in {"REVIEW", "GOV", "PEDAGOGY", "META", "COLLAB"}:
    return [40]
  if norm_domain == "GIT":
    return [50, 60]
  if norm_domain == "CI":
    return [70]
  return [20, 30]


def _normalize_workflow_phases(
    raw_phases: List[str],
    category: str = "",
    applies_when: str = "",
    title: str = "",
    default_fallback: bool = True,
    domain: Optional[str] = None,
) -> List[str]:
  """Normalize workflow phase strings to canonical Target 10..90 labels.

  Args:
    raw_phases: Raw phase strings or shorthand target tokens.
    category: Optional scar category used when inferring defaults.
    applies_when: Optional applies-when prose used for fallback matching.
    title: Optional scar title used for fallback matching.
    default_fallback: When True, infer domain defaults if raw_phases is empty.
    domain: Optional pre-normalized canonical domain code.

  Returns:
    List of canonical ``Target NN: name`` phase labels.
  """
  resolved_domain = domain or normalize_domain_category(
      category, title=title, applies_when=applies_when
  )
  parsed_ints = parse_upstream_targets(raw_phases)

  if not default_fallback:
    return [
        TARGET_INT_TO_PHASE_LABEL[t]
        for t in parsed_ints
        if t in TARGET_INT_TO_PHASE_LABEL
    ]

  fallback_ints: Optional[List[int]] = None
  if not parsed_ints:
    context_matched = set()
    context = f"{category} {applies_when} {title}"
    for canonical, pattern in _PHASE_PATTERNS:
      if pattern.search(context):
        context_matched.add(canonical)
    if context_matched:
      fallback_ints = parse_upstream_targets(list(context_matched))
    else:
      fallback_ints = _default_targets_for_domain(resolved_domain)

  _, labels = compute_makeflow_targets(
      raw_phases if parsed_ints else fallback_ints,
      domain=resolved_domain,
      fallback_phases=fallback_ints,
  )
  return labels


def extract_scar_metadata(scar_row: Dict[str, Any]) -> Dict[str, Any]:
  """Extract structured search metadata from a synthetic scar dictionary.

  Args:
    scar_row: Mapping containing scar identifiers, prose fields, and phases.

  Returns:
    Dictionary with ``keywords``, ``languages``, ``frameworks``,
    ``diagnostic_codes``, ``workflow_phases``, ``mf_targets``, and
    ``domain_category``.
  """
  scar_id = str(scar_row.get("id") or "").strip()
  ss_id = str(scar_row.get("ss_id") or scar_row.get("ssId") or "").strip()
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

  ss_targets_val = (
      scar_row.get("ss_targets")
      if "ss_targets" in scar_row
      else scar_row.get("ssTargets")
  )
  raw_phases_val = (
      scar_row.get("workflow_phases")
      if "workflow_phases" in scar_row
      else scar_row.get("workflowPhases")
  )
  raw_phases_list = (
      parse_json_list(ss_targets_val)
      if ss_targets_val is not None
      else parse_json_list(raw_phases_val)
  )

  raw_wound_val = str(
      scar_row.get("raw_wound") or scar_row.get("wound") or ""
  )
  raw_trap_val = str(
      scar_row.get("raw_trap") or scar_row.get("trap") or ""
  )
  raw_reflex_val = str(
      scar_row.get("raw_reflex") or scar_row.get("reflex") or ""
  )

  text_fields = (
      scar_id,
      ss_id,
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

  no_fenced = re.sub(r"```[\s\S]*?```", " ", combined_text)
  for inline_match in re.finditer(r"`([^`\n]{1,60})`", no_fenced):
    span = inline_match.group(1).strip()
    for ident in re.findall(r"[a-zA-Z_][a-zA-Z0-9_.\-]{1,40}", span):
      cleaned_ident = ident.strip(".-_")
      if len(cleaned_ident) >= 2 and cleaned_ident.lower() not in _STOPWORDS:
        _add_kw(cleaned_ident)

  if scar_id:
    _add_kw(scar_id)
  if ss_id:
    _add_kw(ss_id)
  if orig_id:
    _add_kw(orig_id)
  if cat_slug:
    _add_kw(cat_slug.lower())
  elif category:
    _add_kw(category.lower())

  for word in re.findall(
      r"[a-zA-Z][a-zA-Z0-9_\-]{2,}", f"{title} {applies_when}"
  ):
    if word.lower() not in _STOPWORDS:
      _add_kw(word.lower())
      if len(keywords) >= 25:
        break

  if not keywords:
    _add_kw("synthetic-scar")

  languages: List[str] = []
  for lang_name, lang_re in _LANGUAGE_RULES:
    if lang_re.search(combined_text):
      languages.append(lang_name)
  if not languages:
    languages.append("universal")

  frameworks: List[str] = []
  for fw_name, fw_re in _FRAMEWORK_RULES:
    if fw_re.search(combined_text):
      frameworks.append(fw_name)

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

  domain = normalize_domain_category(
      category or cat_slug,
      scar_id=ss_id or scar_id,
      title=title,
      applies_when=applies_when,
      wound=raw_wound_val,
      trap=raw_trap_val,
      reflex=raw_reflex_val,
  )
  parsed_ints = parse_upstream_targets(raw_phases_list)
  if parsed_ints:
    mf_targets, workflow_phases = compute_makeflow_targets(
        raw_phases_list, domain=domain
    )
  else:
    fallback_ints = _default_targets_for_domain(domain)
    mf_targets, workflow_phases = compute_makeflow_targets(
        fallback_ints, domain=domain, fallback_phases=fallback_ints
    )

  return {
      "keywords": keywords,
      "languages": languages,
      "frameworks": frameworks,
      "diagnostic_codes": diag_codes,
      "workflow_phases": workflow_phases,
      "mf_targets": mf_targets,
      "domain_category": domain,
  }
