# BDD discovery, example mapping, and executable scenarios

Behavior-Driven Development (BDD, defining software behavior through concrete examples before coding) aligns human intent with automated verification.
This guide distills open-source BDD practices into Simple English rules for Makeflow Target 15 discovery, Target 20 planning, and Target 30 testing.

## 1. Three Amigos discovery and Example Mapping

Before you write code, clarify uncertain requirements using Three Amigos (product, engineering, and testing perspectives) and Example Mapping (a structured rule-and-example card method):

1. State the core user story or ticket goal in one short sentence.
2. List the concrete business rules that govern the change.
3. Write concrete examples under each rule, including happy paths, boundary inputs, and failure modes.
4. Capture unresolved questions and clarify them with the operator using `ask_question` in Target 15 before drafting `.workflow/state/20_plan.md`.
5. Slice oversized stories into smaller independent deliverables when too many rules or open questions appear.

## 2. Outcome-focused Given-When-Then syntax

Write acceptance criteria using Given-When-Then (a three-part scenario format):

- **Given**: Describe the initial system state or precondition.
- **When**: Describe one concrete action, command, or input event.
- **Then**: Describe the observable outcome, state change, or error response.

Follow four scenario quality rules:

1. Focus on observable behavior and outcomes instead of internal UI clicks or private helper names.
2. Keep one clear action per scenario so every failure pinpoints one defect.
3. Use compact data tables when multiple input examples exercise the same business rule.
4. Avoid vague words such as properly, correctly, or appropriately.

## 3. Complexity scaling and `planning.bddMode`

Apply proportional BDD ceremony so simple bug fixes stay fast while complex features receive structured discovery.
Configure `planning.bddMode` in `.workflow/config.json`, `<agent_plugin_data>/config.json`, or `MAKEFILE_WORKFLOW_PLANNING_BDD_MODE`:

| `planning.bddMode` value | Planning and testing behavior |
| :--- | :--- |
| `""` (unset default) | Ask once in Target 15 and offer to save the choice to `user-global`, `project-local`, or `both`. |
| `adaptive` | Bugfix tickets use one concise Given-When-Then scenario with zero extra questions. Feature and ambiguous tickets run bounded `ask_question` discovery in Target 15 and full scenarios in Section 2 of `20_plan.md`. |
| `full_gherkin` | Run structured discovery in Target 15 and author executable `.feature` files in Target 30 when supported. Record executed scenarios in Target 90 resolution summaries. |
| `planning_only` | Keep Given-When-Then acceptance criteria in Section 2 of `20_plan.md` without creating separate `.feature` files. |
| `ask` | Prompt for the active BDD tier on each ticket during Target 15 before drafting `20_plan.md`. |

## 4. Polyglot `.feature` execution under `full_gherkin`

When `planning.bddMode` is `full_gherkin` and the subproject supports a BDD runner, map scenarios to native `.feature` files or Markdown BDD specs:

- **Python**: `pytest-bdd` or `behave` (or stdlib `unittest` behavioral suites when third-party dependencies are disallowed).
- **TypeScript / JavaScript**: `@cucumber/cucumber` or `playwright-bdd`.
- **Ruby**: `cucumber`.
- **Java / Kotlin**: `cucumber-jvm`.
- **C# / .NET**: `Reqnroll` (`SpecFlow` successor).
- **Go**: `godog`.
- **Rust**: `cucumber-rs`.
- **Dart / Flutter**: `bdd_widget_test` or `gherkin`.
- **Markdown-native**: Executable Markdown scenario specifications when teams prefer plain Markdown over Gherkin files.

## 5. Common Gherkin anti-patterns to avoid

Reject four common BDD traps during Target 20 planning and Target 40 review:

1. **Imperative script coupling**: Listing low-level button IDs or SQL statements instead of user-visible outcomes.
2. **Compound multi-action scenarios**: Chaining unrelated `When` and `Then` blocks inside one bloated scenario.
3. **Hidden preconditions**: Omitting required fixture or state setup from the `Given` clause.
4. **Pause-gate interrogation**: Calling `ask_question` after entering Target 20 instead of completing discovery in Target 15 (`SCAR-PROC-161`).

## 6. Source attribution and license table

This reference synthesizes public engineering concepts from eight open-source BDD repositories:

| Source repository | License | Distilled concepts |
| :--- | :--- | :--- |
| `cucumber/docs` | MIT | Three Amigos discovery workshops, Example Mapping, and clear Gherkin authoring. |
| `RSginer/spec-kit-bdd` | MIT | Translating specifications into Gherkin scenarios, step stubs, and requirement coverage tables. |
| `H2Sxxa/spec-coding-skills` | MIT | Refining ambiguous requests into BDD specifications with acceptance criteria and edge cases. |
| `AsiaOstrich/universal-dev-standards` (`bdd-assistant`) | CC BY 4.0 / MIT | Language-agnostic Markdown BDD workflow guidance across polyglot stacks. |
| `angelo-v/opencode-playground` (`bdd-gherkin`) | MIT | Outcome-focused Given-When-Then rules, scenario data tables, and Gherkin anti-patterns. |
| `openpracticelibrary/openpracticelibrary` | Apache-2.0 | Example Mapping practice framing, BDD discovery, and vertical story slicing. |
| `bifravst/bdd-markdown` | BSD-3-Clause | Executable BDD planning and scenario organization in plain Markdown. |
| `reqnroll/Sample-ReqOverflow` | MIT | Feature-level planning structure and domain-oriented scenario grouping. |
