# MIL-STD-38784B Technical Manuals Standard Practice

- **Source Standard**: `MIL-STD-38784B`, *Department of Defense Standard Practice for General Style and Format of Technical Manuals*.
- **License Status**: Public Domain (`17 U.S.C. § 105` as a work of the United States Government).

This module encapsulates the language, style, structural, and procedural rules of `MIL-STD-38784B` (Sections 3, 4.1 through 4.8, and 5.1 through 5.10).
It omits only physical print-shop layout measurements such as paper margins, binder holes, foldout pasteboards, and microfiche frames.

---

## 1. Technical Manual Definitions and Taxonomy (`MIL-STD-38784B` Section 3)

`MIL-STD-38784B` Section 3 defines the core operational terms and normative modal verbs that govern technical manuals and runbooks.
Every author and AI agent must apply these terms with one exact meaning across all procedures.

### Table 1. Core Operational Definitions (`Section 3`)

| Term | `MIL-STD-38784B` Normative Definition | Software and Agentic Engineering Equivalent |
| :--- | :--- | :--- |
| `WARNING` | Operating or maintenance procedure, practice, or condition that could result in personal injury or loss of life if ignored. | Physical hardware hazard, electrical shock risk, or safety-critical robotics actuator hazard. |
| `CAUTION` | Operating or maintenance procedure, practice, or condition that could result in equipment damage or data destruction if ignored. | Production data loss, unrecoverable state corruption, security breach, or service outage. |
| `NOTE` | Essential operating or maintenance condition or statement that highlights supplemental factual context. | Read-only architectural context, environment path note, or runtime behavior explanation. |
| `procedural step` | Single numbered directive instruction that commands an operator to perform one action or verify one response. | Numbered CLI execution step, configuration edit, or verification command in a runbook. |
| `checkout` | Diagnostic or operational test sequence that verifies a system operates within specified boundaries. | Pre-flight health check, smoke test suite (`make test-static`), or readiness probe. |
| `alignment` | Adjustment procedure that brings mechanical, electrical, or software parameters into specified operational tolerances. | Configuration synchronization, schema migration, or clock and threshold calibration. |
| `troubleshooting` | Systematic fault-isolation process that maps observed symptoms to probable causes, diagnostic checks, and corrective actions. | Root-cause analysis (`RCA`), bisect workflow, or synthetic scar diagnostic table. |
| `maintenance level` | Authorized operational tier permitted to execute a specific inspection, repair, or configuration procedure. | Permission boundary separating user, operator, developer, and root administrator actions. |

### Table 2. Normative Modal Verb Mapping (`Section 3`)

| Modal Verb in Specification | Normative Meaning | Required Form in Operator Procedures |
| :--- | :--- | :--- |
| `shall` | Binding mandatory requirement in specification text | Replace `shall` with a direct imperative verb (`Run`, `Verify`) or `must` in user documentation. |
| `must` | Mandatory operational constraint or invariant | Use for strict preconditions and invariant rules (`The token must not expire`). |
| `should` | Recommended practice when valid alternatives exist | State the condition under which the recommendation applies. |
| `may` | Permissible optional action within authorized bounds | Use only for optional reader choices (`You may pass `--verbose` to log headers`). |
| `will` | Factual prediction of automated system response | Use only to describe deterministic system reactions (`The daemon will close the socket`). |

---

## 2. General Style, Readability, Nomenclature, and References (`MIL-STD-38784B` Section 4, `4.1`–`4.7`)

`MIL-STD-38784B` Section 4 (`4.1` through `4.7`) governs grammatical mood, readability targets, verbatim nomenclature, abbreviations, units of measure, equations, and cross-references.

### 2.1 Grammatical Mood and Readability Targets (`4.1`–`4.2`)

1. Write every procedural instruction in the second-person imperative mood (`Start the worker`, `Inspect the log`).
2. Write descriptive explanations in the third-person indicative mood and active voice (`The scheduler assigns tasks`).
3. Keep descriptive sentences at 25 words or fewer so the text meets standard technical readability comprehension targets.
4. State one main fact per descriptive sentence and avoid conversational idioms.

### 2.2 Verbatim Component Nomenclature (`4.3`)

Readers and AI agents rely on exact lexical matching to track controls, services, and variables across a manual.

1. Match the exact spelling, capitalization, and punctuation of physical panel labels, CLI flags, API keys, and UI controls verbatim.
2. Assign one exact noun name (`nomenclature`) to each component, file, or state variable throughout the document.
3. Never alternate between synonym terms (`config`, `settings`, `options`, `preferences`) for the same artifact.
4. Do not convert component or domain nouns into improvised verbs (`Send the payload to the webhook` instead of `Webhook the payload`).

### 2.3 Acronyms and Abbreviations (`4.4`)

1. Spell out every acronym or abbreviation in full at first use, followed by the abbreviation in parentheses.
2. Example of compliant first use: `The Abstract Syntax Tree (AST) scrubber parses Python source files.`
3. Ban single-use abbreviations across all manuals and specifications.
4. If a technical term appears only once in a document, write the full spelled-out term without an abbreviation.

### 2.4 Symbols, Units of Measure, Equations, and Cross-References (`4.5`–`4.7`)

1. Express all units of measure consistently with standard International System (`SI`) or computing symbols (`ms`, `MB`, `Hz`, `V`).
2. State numeric tolerances explicitly (`500 ms +/- 25 ms` or `10 MB to 50 MB`).
3. When you introduce a mathematical equation, define every variable symbol immediately below the formula.
4. Write every internal or external cross-reference with an explicit target identifier (`See Section 4.8` or `Refer to Table 3`).
5. Never use vague relative pointers (`above`, `below`, `earlier`, `later`) because text reflow breaks positional meaning.
6. Do not chain a cross-reference to a second section that merely redirects the reader to a third section.

---

## 3. Safety and Hazard Alert Hierarchy (`MIL-STD-38784B` Section 4.8)

`MIL-STD-38784B` Section 4.8 establishes a strict three-tier hierarchy for `WARNING`, `CAUTION`, and `NOTE` alerts.
This hierarchy prevents operators and agents from confusing informational notes with destructive hazards.

### Table 3. Three-Tier Hazard Alert Classification (`Section 4.8`)

| Alert Tier | Severity Priority | Hazard Scope | Mandatory Placement | Imperative Commands Permitted |
| :--- | :--- | :--- | :--- | :--- |
| `WARNING` | `1` (Highest) | Personal injury, loss of life, or physical hardware hazard | Immediately before the step that introduces the hazard | None (State hazard and consequence before the numbered step) |
| `CAUTION` | `2` (Medium) | Equipment damage, software crash, security breach, or data loss | Immediately before the step that risks system or data damage | None (State prohibited action and consequence before the step) |
| `NOTE` | `3` (Informational) | Supplemental factual context or operational explanation | Before or after the applicable paragraph or step | None (Zero commands or hidden safety rules allowed) |

### 3.1 Alert Ordering, Anatomy, and Multi-Step Scoping (`Section 4.8`)

1. If multiple alerts apply to the same step, order them strictly by severity: `WARNING` first, `CAUTION` second, and `NOTE` third.
2. Place `WARNING` and `CAUTION` blocks immediately before the procedural step to which they apply, never after the step.
3. State the prohibited action or hazard condition in the first sentence of the `WARNING` or `CAUTION` body.
4. State the concrete consequence in the second sentence (`The command drops active production tables without confirmation.`).
5. Never hide an imperative command, operational limit, or safety requirement inside a `NOTE` block.
6. The procedure must remain safe and executable even if a parser strips every `NOTE` block from the file.
7. When a hazard applies to a contiguous multi-step sequence, declare the exact step range in the alert header (`CAUTION (Steps 3 through 6)`).

Example of a compliant `CAUTION` block with prohibited action and consequence:

> [!CAUTION]
> Do not pass the `--force` flag on production database hosts during Steps 2 through 4.
> The command drops active customer tables without confirmation.

Example of a compliant `NOTE` block with zero commands:

> [!NOTE]
> The test runner creates an isolated SQLite database file inside the system temporary directory.

---

## 4. Structural Divisions, Numbering, and Paragraph Hierarchy (`MIL-STD-38784B` Section 5, `5.1`–`5.2`)

`MIL-STD-38784B` Section 5 (`5.1` and `5.2`) standardizes document divisions, subdivision nesting depth, paragraph numbering, and lead-in text.

1. Organize technical manuals into clear structural divisions: Section, Subsection, Paragraph, and Procedural Step.
2. Limit heading and paragraph subdivision nesting depth to a maximum of four levels (`1.`, `1.1`, `1.1.1`, `1.1.1.1`).
3. If content requires more than four hierarchy levels, split the topic into a separate section or table.
4. Never create an orphan single subdivision (`1.1` must always have at least a sibling `1.2`).
5. Provide a brief lead-in sentence or clause under a parent heading whenever child subdivisions or steps follow.

---

## 5. Procedural Step Anatomy, Action-Verification Pairing, and Branching (`MIL-STD-38784B` Section 5.3)

`MIL-STD-38784B` Section 5.3 defines the syntax for procedural step construction, verification checks, conditional branching, and loop limits.

1. Keep every procedural sentence at 20 words or fewer.
2. Start each procedural step with a direct imperative verb (`Run`, `Open`, `Verify`, `Disconnect`).
3. Include only one instruction per sentence unless two actions must occur at the same instant.
4. If a step depends on a precondition, place the condition before the imperative command with a comma.
5. Follow every state-changing action step with an explicit verification statement using `Verify that` (`Run the migration. Verify that the schema version equals 42.`).
6. Express conditional branching inside a procedure with the exact target step number (`If the checksum matches, proceed to step 6.`).
7. When a step instructs the operator to repeat prior actions, state the exact step range and maximum retry limit (`Repeat steps 2 through 4 at most 3 times.`).
8. Limit procedural substep nesting to two levels: primary numbered steps (`1.`, `2.`) and lettered substeps (`a.`, `b.`).

### Table 4. Procedural Step Construction Patterns (`Section 5.3`)

| Procedural Pattern | Non-Compliant Phrasing | Compliant `MIL-STD-38784B` Phrasing |
| :--- | :--- | :--- |
| Condition before command | `Run the database migration script if the schema version table is outdated.` | `If the schema version table is outdated, run the database migration script.` |
| Action-verification pairing | `Start the daemon and make sure it works.` | `Start the worker daemon. Verify that port 8080 accepts loopback connections.` |
| Conditional branching | `Skip the next few steps if the cache is warm.` | `If the cache file exists, proceed to step 5.` |
| Bounded retry loop | `Keep retrying the handshake until it connects.` | `If the handshake times out, repeat steps 2 through 3 at most 3 times.` |

---

## 6. Technical Tables and Vertical Lists (`MIL-STD-38784B` Section 5, `5.4`–`5.5`)

`MIL-STD-38784B` Sections `5.4` and `5.5` govern technical tables and vertical lists so structured data remains unambiguous.

### 6.1 Technical Table Rules (`5.4`)

1. Assign a sequential number and descriptive title to every formal table.
2. Make every column header self-contained and include explicit units of measure in the header (`Response Time (ms)`).
3. Never leave any table cell blank or empty.
4. If a cell has no value, write `None` or `Not applicable` so the reader knows the cell was not omitted by mistake.
5. Place table footnotes immediately below the bottom border of the table they qualify.

### 6.2 Vertical List Parallelism (`5.5`)

1. Introduce every vertical list with a complete lead-in clause or sentence that ends with a colon.
2. Keep all items in a vertical list grammatically parallel (all imperative sentences, all noun phrases, or all declarative sentences).
3. Use numbered lists for ordered sequences or priority rankings, and use bulleted lists for unordered sets.

---

## 7. Descriptive Text, Theory of Operation, and Troubleshooting Anatomy (`MIL-STD-38784B` Section 5, `5.6`–`5.10`)

`MIL-STD-38784B` Sections `5.6` through `5.10` govern descriptive system overviews, functional theory of operation, and diagnostic troubleshooting matrices.

### 7.1 Functional Theory of Operation (`5.6`–`5.8`)

1. Organize every theory of operation section in functional signal or data-flow order: `input -> processing -> output`.
2. Describe how subsystems interact at the top-level architecture boundary before explaining internal module details.
3. Separate descriptive theory of operation sections from imperative maintenance procedures.

### 7.2 Four-Column Troubleshooting Matrix and Synthetic Scar Mapping (`5.9`–`5.10`)

`MIL-STD-38784B` Sections `5.9` and `5.10` standardize fault isolation around four diagnostic columns: `Symptom/Malfunction`, `Probable Cause`, `Test/Inspection`, and `Corrective Action`.
In `makeflow` agentic workflows, this four-column `MIL-STD-38784B` diagnostic structure maps directly to the four fields of a Synthetic Scar (`Wound`, `Trap`, `Circuit Breaker`, and `Reflex`).

### Table 5. `MIL-STD-38784B` Diagnostic Troubleshooting to Synthetic Scar Mapping (`Sections 5.9`–`5.10`)

| `MIL-STD-38784B` Troubleshooting Column | `Makeflow` Synthetic Scar Field | Normative Diagnostic Purpose | Concrete Engineering Example |
| :--- | :--- | :--- | :--- |
| `Symptom/Malfunction` | `Wound` | Observable failure mode, exit code, or abnormal system output | `PermissionError` when promoting an artifact across service accounts. |
| `Probable Cause` | `Trap` | Underlying root cause, anti-pattern, or unverified assumption | `shutil.copy2` attempts `copystat` metadata preservation on a foreign-owned target file. |
| `Test/Inspection` | `Circuit Breaker` | Deterministic test, AST check, or probe that detects the fault | Static AST check or unit test asserting `safe_copy` rejects `shutil.copy2` across user boundaries. |
| `Corrective Action` | `Reflex` | Exact code change, procedure, or automated rule that resolves the fault | Stream content bytes with `shutil.copyfile` instead of `shutil.copy2`. |
| `None` (No fault observed) | `Not applicable` | Nominal checkout state where all diagnostic inspections pass | All pre-flight checks return exit code `0`. |
