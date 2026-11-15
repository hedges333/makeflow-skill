"""Canonical synthetic scar domain ontology, ID assignment, and target shifting.

Separates Makeflow scar identity (`id`), domain category normalization,
4-digit canonical scar IDs, and shift-left integer target bindings (`mf_targets`)
from upstream registry schemas (`ss_id` and `ss_targets`).
"""

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

DOMAIN_CATEGORY_ONTOLOGY: Dict[str, str] = {
    # Architecture, Layout, and Reuse
    "ARCH": "Module boundaries, coupling, and composition",
    "DIR": "Directory layout and file hierarchy",
    "API": "Public interfaces and behavioral contracts",
    "DEP": "Package dependencies and manifest constraints",
    "PUB": "Package publishing and public release hygiene",
    "REFACTOR": "Structural code transformations and cleanup",
    "INTEROP": "Foreign function and host platform bridges",
    "MIXIN": "Trait, mixin, and inheritance composition",
    "INV": "Cross-module architectural and execution invariants",
    # Types, Syntax, and Execution
    "TYPE": "Static type safety and generic variance",
    "AST": "Syntax trees, patterns, and code generation",
    "REGEX": "Regular expression safety and backtracking",
    "SYNTAX": "Language grammar and lexical token traps",
    "TMPL": "Template syntax, placeholders, and escaping",
    "ASYNC": "Futures, promises, timers, and event loops",
    "CONC": "Threads, isolates, locks, and races",
    "STATE": "Reactive state holders and disposal lifecycles",
    "BLOC": "BLoC and Cubit event-state lifecycles",
    "RIV": "Riverpod providers, notifiers, and ref lifecycles",
    "ERR": "Exceptions, recovery, and failure handling",
    "FAIL": "Fail-fast propagation and silent-failure traps",
    "PERF": "Latency, memory pressure, and algorithmic cost",
    "MATH": "Numerical stability, arithmetic, and formulas",
    "GEOM": "Coordinate systems, layout geometry, and bounds",
    # Data, Wire, and Persistence
    "DATA": "Domain models, cloning, and immutability",
    "SCHEMA": "Validation schemas and structural migrations",
    "DB": "Relational queries, indexes, and transactions",
    "STORAGE": "Filesystems, temporary paths, and local caches",
    "WIRE": "Serialization formats and wire protocols",
    "NET": "Sockets, HTTP requests, and transport retries",
    # User Interface and Content
    "UI": "Widgets, layout, and rendering pipelines",
    "FLUTTER": "Flutter widget trees, render objects, and bindings",
    "NAV": "Routing, navigation stacks, and deep links",
    "GENUI": "Generative and agent-driven UI surfaces",
    "A2UI": "Agent-to-UI protocol catalogs and surfaces",
    "HCI": "Prompts, ergonomics, and operator interaction",
    "A11Y": "Accessibility semantics and focus handling",
    "I18N": "Localization, collation, and text encoding",
    "DOC": "Manuals, docstrings, and technical prose",
    "SKILL": "Skill manuals, references, and progressive disclosure",
    "WEB": "DOM APIs, browser contexts, and web standards",
    "SSR": "Server-side rendering and hydration boundaries",
    # Verification, Security, and Operations
    "TEST": "Hermetic tests, mocks, and fast assertions",
    "VERIF": "Verification evidence and check completeness",
    "SEC": "Untrusted input, secrets, and sandboxing",
    "AUTH": "Identity, credentials, and access permissions",
    "SYS": "Processes, POSIX signals, and OS paths",
    "ENV": "Environment variables, runtimes, and toolchains",
    "TIME": "Clocks, timestamps, timezones, and durations",
    "SH": "Shell quoting and command script execution",
    "SCRIPT": "CLI entrypoint wrappers and automation scripts",
    "BUILD": "Compilers, bundlers, targets, and Makefiles",
    "CI": "Remote pipelines, bots, and status triage",
    "OBS": "Observability, metrics, and runtime inspection",
    "OTEL": "OpenTelemetry spans, traces, and semantic conventions",
    "DIAG": "Diagnostic codes, stack traces, and error reports",
    "GIT": "Commits, branches, and Merkle tree locks",
    "HOOK": "Pre-commit and pre-tool lifecycle hooks",
    "TOOL": "CLI wrappers and MCP tool invocation",
    # Workflow and Governance
    "PROC": "Workflow gates and execution discipline",
    "METRIC": "Process telemetry, counts, and audit thresholds",
    "GOV": "Immutable guards and policy enforcement",
    "REVIEW": "Adversarial critique and pre-merge inspection",
    "DIFF": "Patch review and consumer impact analysis",
    "COLLAB": "Issue triage, reviews, and public communication",
    "META": "Agent reasoning hygiene and reflection",
    "CRAFT": "Engineering craftsmanship and code simplicity",
    "PEDAGOGY": "Socratic probes and active engineering recall",
}

_LANGUAGE_CATEGORY_TAGS = frozenset({
    "LANG",
    "LANGUAGES",
    "POLYGLOT",
    "DART",
    "PYTHON",
    "RUST",
    "GO",
    "TYPESCRIPT",
    "JAVASCRIPT",
    "BASH",
    "SQL",
    "RUBY",
    "JAVA",
    "KOTLIN",
    "SWIFT",
    "CSHARP",
    "CPP",
    "C",
    "PHP",
    "PERL",
    "R",
    "LUA",
    "ELIXIR",
    "HASKELL",
    "SCALA",
    "ZIG",
    "OCAML",
    "CLOJURE",
    "ERLANG",
    "JULIA",
})

_PROSE_CATEGORY_ALIASES: Dict[str, str] = {
    "ARCHITECTURE": "ARCH",
    "TESTING": "TEST",
    "PROCESS": "PROC",
    "SECURITY": "SEC",
    "DATABASE": "DB",
    "SYSTEM": "SYS",
    "ENVIRONMENT": "ENV",
    "DOCUMENTATION": "DOC",
    "TOOLING": "TOOL",
}

UPSTREAM_CATEGORY_MAP: Dict[str, str] = {
    **{code: code for code in DOMAIN_CATEGORY_ONTOLOGY},
    "LANG": "ARCH",
    "LANGUAGES": "ARCH",
    "POLYGLOT": "ARCH",
    **_PROSE_CATEGORY_ALIASES,
}


def _build_category_synonym_map() -> Dict[str, Tuple[str, ...]]:
  """Build reverse lookup from canonical domain code to prose aliases only."""
  reverse: Dict[str, List[str]] = {code: [code] for code in DOMAIN_CATEGORY_ONTOLOGY}
  for prose_alias, canonical in _PROSE_CATEGORY_ALIASES.items():
    if prose_alias not in reverse[canonical]:
      reverse[canonical].append(prose_alias)
  return {k: tuple(v) for k, v in reverse.items()}


CATEGORY_SYNONYM_MAP: Dict[str, Tuple[str, ...]] = _build_category_synonym_map()

TARGET_INT_TO_PHASE_LABEL: Dict[int, str] = {
    10: "Target 10: intake",
    15: "Target 15: archaeology",
    20: "Target 20: plan",
    30: "Target 30: tdd",
    40: "Target 40: review",
    42: "Target 42: blocker-triage",
    45: "Target 45: review-approval",
    50: "Target 50: commit",
    60: "Target 60: push",
    70: "Target 70: ci-triage",
    75: "Target 75: landing",
    80: "Target 80: merge",
    90: "Target 90: finish",
}

_PLAN_PROMOTE_DOMAINS = frozenset({
    "ARCH",
    "DIR",
    "API",
    "DEP",
    "PUB",
    "REFACTOR",
    "INTEROP",
    "MIXIN",
    "INV",
    "DATA",
    "SCHEMA",
    "DB",
    "STORAGE",
    "WIRE",
    "SEC",
    "AUTH",
    "DOC",
    "SKILL",
    "DIFF",
})

_EXEC_PROMOTE_DOMAINS = frozenset({
    "STATE",
    "BLOC",
    "RIV",
    "UI",
    "FLUTTER",
    "NAV",
    "GENUI",
    "A2UI",
    "WEB",
    "SSR",
    "HCI",
    "A11Y",
    "I18N",
    "TEST",
    "VERIF",
    "ASYNC",
    "CONC",
    "AST",
    "REGEX",
    "SYNTAX",
    "TMPL",
    "TYPE",
    "ERR",
    "FAIL",
    "PERF",
    "MATH",
    "GEOM",
    "NET",
    "SYS",
    "ENV",
    "TIME",
    "SH",
    "SCRIPT",
    "BUILD",
    "OBS",
    "OTEL",
    "DIAG",
    "HOOK",
})

_DESIGN_UI_TEST_BUILD_DOMAINS = frozenset({
    "STATE",
    "BLOC",
    "RIV",
    "UI",
    "FLUTTER",
    "NAV",
    "GENUI",
    "A2UI",
    "WEB",
    "SSR",
    "HCI",
    "A11Y",
    "I18N",
    "TEST",
    "VERIF",
    "BUILD",
})

_HELPER_LANGUAGES = frozenset({"bash", "sql"})

_RE_CANONICAL_TARGET = re.compile(
    r"\b(?:target|t)\s*[-]?\s*(10|15|20|30|35|40|42|45|50|60|70|75|80|90)\b",
    re.IGNORECASE,
)
_RE_LETTERED_TARGET = re.compile(
    r"\b(?:target\s*)?(4[abc]|7[ab])\b",
    re.IGNORECASE,
)
_RE_STEP_NUMBER = re.compile(r"\bsteps?\s*(1[0-5]|[1-9])\b", re.IGNORECASE)
_RE_UPSTREAM_SINGLE_TARGET = re.compile(
    r"\btarget\s*([0-9])\b", re.IGNORECASE
)
_RE_HAS_EXPLICIT_DESIGNATOR = re.compile(
    r"\b(?:target|t)\s*[-]?\s*\d+[a-z]?\b|\bsteps?\s*\d+\b", re.IGNORECASE
)
_RE_SCAR_PREFIX_CATEGORY = re.compile(r"^SCAR-([A-Z0-9]+)-\d+$")
_RE_TRAILING_DIGITS = re.compile(r"-(\d+)$")


def _infer_domain_from_text(text: str) -> str:
  """Infer a canonical domain category for bare language tags or empty/unknown labels."""
  if re.search(r"\b(?:ast|syntax\s+tree|parser|lexer|scrubber|linter)\b", text):
    return "AST"
  if re.search(
      r"\b(?:async|await|future|promise|timers?|fakeasync|microtask|stream|event\s+loop|isolate)\b",
      text,
  ):
    return "ASYNC"
  if re.search(
      r"\b(?:type|generics?|variance|covariant|annotations?|typedef)\b", text
  ):
    return "TYPE"
  if re.search(
      r"\b(?:pubspec|cargo\.lock|package\.json|requirements\.txt|dependenc(?:y|ies)|semver|pinning)\b",
      text,
  ):
    return "DEP"
  if re.search(r"\b(?:tests?|fixture|mock|assert|unittest|pytest)\b", text):
    return "TEST"
  return "ARCH"


def normalize_domain_category(
    raw_category: Optional[str],
    scar_id: str = "",
    title: str = "",
    applies_when: str = "",
    wound: str = "",
    trap: str = "",
    reflex: str = "",
) -> str:
  """Normalize an upstream or human scar category label into DOMAIN_CATEGORY_ONTOLOGY.

  Preserves all 68 canonical domain codes 1-to-1 and infers a domain only for
  bare language tags (`LANG`, `POLYGLOT`, language names) or empty/unknown
  categories.

  Args:
    raw_category: Raw category string or slug from the source row.
    scar_id: Original scar identifier for contextual hints.
    title: Scar title.
    applies_when: Scope description text.
    wound: Scar wound text.
    trap: Scar trap text.
    reflex: Scar reflex text.

  Returns:
    Canonical uppercase category code in DOMAIN_CATEGORY_ONTOLOGY.
  """
  key = str(raw_category or "").strip().upper().replace("-", "_")
  if key in DOMAIN_CATEGORY_ONTOLOGY:
    return key

  text = " ".join(
      p for p in (scar_id, title, applies_when, wound, trap, reflex) if p
  ).lower()

  if key in _LANGUAGE_CATEGORY_TAGS:
    return _infer_domain_from_text(text)

  if key in UPSTREAM_CATEGORY_MAP:
    return UPSTREAM_CATEGORY_MAP[key]

  id_match = _RE_SCAR_PREFIX_CATEGORY.match(str(scar_id or "").strip().upper())
  if id_match and id_match.group(1) in DOMAIN_CATEGORY_ONTOLOGY:
    return id_match.group(1)

  return _infer_domain_from_text(text)


def resolve_category_filter_tokens(category: str) -> Tuple[str, ...]:
  """Return canonical category and prose alias tokens without cross-domain bleed."""
  key = str(category or "").strip().upper().replace("-", "_")
  if not key:
    return ()
  if key in CATEGORY_SYNONYM_MAP:
    return CATEGORY_SYNONYM_MAP[key]
  if key in ("LANG", "LANGUAGES", "POLYGLOT"):
    return ("LANG", "LANGUAGES", "POLYGLOT")
  mapped = _PROSE_CATEGORY_ALIASES.get(key)
  if mapped:
    tokens = list(CATEGORY_SYNONYM_MAP.get(mapped, (mapped,)))
    if key not in tokens:
      tokens.append(key)
    return tuple(tokens)
  return (key,)


def select_primary_language(
    languages: Optional[Iterable[str]],
    domain: str = "",
) -> Optional[str]:
  """Select the single primary language for ID naming, or None if universal/multi.

  When more than one non-universal language is detected, incidental helper
  tags (`bash`, `sql`) are ignored if one application language remains.

  Args:
    languages: Extracted language identifiers.
    domain: Optional canonical domain code (unused placeholder for uniform signature).

  Returns:
    Lowercase language name for single-language scars, or None.
  """
  _ = domain
  seen: Set[str] = set()
  cleaned: List[str] = []
  for lang in languages or ():
    norm = str(lang or "").strip().lower()
    if norm and norm != "universal" and norm not in seen:
      seen.add(norm)
      cleaned.append(norm)

  if not cleaned:
    return None
  if len(cleaned) == 1:
    return cleaned[0]

  dominant = [lang for lang in cleaned if lang not in _HELPER_LANGUAGES]
  if len(dominant) == 1:
    return dominant[0]
  return None


def compute_canonical_scar_id(
    raw_id: str,
    domain: str,
    languages: Optional[Iterable[str]],
    reserved_ids: Set[str],
) -> str:
  """Compute a collision-free canonical 4-digit scar ID and reserve it.

  Single-language scars use ``SCAR-LANG-{LANG}-{DOMAIN}-{NNNN}``. Universal and
  multi-language scars use ``SCAR-{DOMAIN}-{NNNN}``.

  Args:
    raw_id: Original scar identifier used to preserve numeric suffix when free.
    domain: Canonical uppercase domain code in DOMAIN_CATEGORY_ONTOLOGY.
    languages: Extracted language list for the scar.
    reserved_ids: Mutable set of already allocated canonical IDs in this namespace.

  Returns:
    Allocated canonical scar ID added to ``reserved_ids``.
  """
  primary_lang = select_primary_language(languages, domain=domain)
  if primary_lang:
    prefix = f"SCAR-LANG-{primary_lang.upper()}-{domain}"
  else:
    prefix = f"SCAR-{domain}"

  match = _RE_TRAILING_DIGITS.search(str(raw_id or "").strip())
  if match:
    preferred_idx = int(match.group(1))
    if 1 <= preferred_idx <= 9999:
      preferred_id = f"{prefix}-{preferred_idx:04d}"
      if preferred_id not in reserved_ids:
        reserved_ids.add(preferred_id)
        return preferred_id

  next_idx = 1
  while True:
    candidate = f"{prefix}-{next_idx:04d}"
    if candidate not in reserved_ids:
      reserved_ids.add(candidate)
      return candidate
    next_idx += 1


def parse_upstream_targets(raw_phases: Any) -> List[int]:
  """Parse upstream workflow phases or target numbers into sorted Makeflow integers."""
  if raw_phases is None:
    return []
  if isinstance(raw_phases, str):
    stripped = raw_phases.strip()
    if stripped.startswith("["):
      try:
        items = json.loads(stripped)
      except ValueError:
        items = [stripped]
    else:
      items = [stripped] if stripped else []
  elif isinstance(raw_phases, (list, tuple, set)):
    items = [str(item) for item in raw_phases if str(item or "").strip()]
  else:
    items = [str(raw_phases)]

  step_map = {
      1: 10,
      2: 10,
      3: 10,
      4: 20,
      5: 30,
      6: 30,
      7: 30,
      8: 50,
      9: 40,
      10: 45,
      11: 60,
      12: 70,
      13: 70,
      14: 75,
      15: 90,
  }
  single_target_map = {
      "0": 10,
      "1": 10,
      "2": 20,
      "3": 30,
      "4": 40,
      "5": 50,
      "6": 60,
      "7": 70,
      "8": 80,
      "9": 90,
  }
  letter_map = {
      "4a": 40,
      "4b": 42,
      "4c": 45,
      "7a": 70,
      "7b": 75,
  }

  found: Set[int] = set()
  for raw_item in items:
    text = str(raw_item or "").strip()
    if not text:
      continue
    lowered = text.lower()
    if re.fullmatch(r"\d{1,2}", lowered):
      if lowered in ("10", "15", "20", "30", "40", "42", "45", "50", "60", "70", "75", "80", "90"):
        found.add(int(lowered))
        continue
      if lowered == "35":
        found.add(30)
        continue
      if lowered in single_target_map:
        found.add(single_target_map[lowered])
        continue
    if lowered in letter_map:
      found.add(letter_map[lowered])
      continue

    has_explicit_designator = bool(_RE_HAS_EXPLICIT_DESIGNATOR.search(text))
    for m in _RE_CANONICAL_TARGET.finditer(text):
      val = int(m.group(1))
      found.add(30 if val == 35 else val)
    for m in _RE_LETTERED_TARGET.finditer(text):
      found.add(letter_map[m.group(1).lower()])
    for m in _RE_STEP_NUMBER.finditer(text):
      found.add(step_map[int(m.group(1))])
    for m in _RE_UPSTREAM_SINGLE_TARGET.finditer(text):
      found.add(single_target_map[m.group(1)])

    if has_explicit_designator:
      continue

    # Named phase keywords only when no explicit Target/Step designator is present
    if re.search(r"\barchaeology\b", lowered):
      found.add(15)
    if re.search(r"\bintake\b", lowered):
      found.add(10)
    if re.search(r"\bblocker(?:-triage)?\b", lowered):
      found.add(42)
    if re.search(r"\breview[- ]approval\b", lowered):
      found.add(45)
    elif re.search(r"\breview\b", lowered):
      found.add(40)
    if re.search(r"\blanding\b", lowered):
      found.add(75)
    if re.search(r"\bci[- ]triage\b", lowered):
      found.add(70)
    if re.search(r"\bplan\b", lowered):
      found.add(20)
    if re.search(r"\btdd\b", lowered):
      found.add(30)
    if re.search(r"\bcommit\b", lowered):
      found.add(50)
    if re.search(r"\bpush\b", lowered):
      found.add(60)
    if re.search(r"\bmerge\b", lowered):
      found.add(80)
    if re.search(r"\bfinish\b", lowered):
      found.add(90)

  return sorted(found)


def shift_left_targets(targets: Iterable[int], domain: str) -> List[int]:
  """Promote upstream integer targets earlier (shift left) without ever adding 40.

  Args:
    targets: Parsed Makeflow integer targets.
    domain: Canonical domain category code.

  Returns:
    Sorted list of integer targets including promoted earlier stages.
  """
  result: Set[int] = set(int(t) for t in targets)
  norm_domain = str(domain or "").strip().upper()

  if norm_domain in _PLAN_PROMOTE_DOMAINS and (30 in result or 40 in result):
    result.update((15, 20, 30))
  elif norm_domain in _EXEC_PROMOTE_DOMAINS and (40 in result and 30 not in result):
    result.add(30)
    if norm_domain in _DESIGN_UI_TEST_BUILD_DOMAINS:
      result.add(20)

  return sorted(result)


def compute_makeflow_targets(
    raw_phases: Any,
    domain: str,
    fallback_phases: Any = None,
) -> Tuple[List[int], List[str]]:
  """Compute Makeflow integer targets and canonical workflow phase labels.

  Args:
    raw_phases: Preserved upstream phase list or JSON string (`ss_targets`).
    domain: Normalized canonical domain code.
    fallback_phases: Fallback targets used only if ``raw_phases`` yields none.

  Returns:
    Tuple of ``(sorted_int_targets, canonical_phase_labels)``.
  """
  parsed = parse_upstream_targets(raw_phases)
  if not parsed and fallback_phases is not None:
    parsed = parse_upstream_targets(fallback_phases)
  shifted = shift_left_targets(parsed, domain=domain)
  labels = [
      TARGET_INT_TO_PHASE_LABEL.get(t, f"Target {t}") for t in shifted
  ]
  return shifted, labels
