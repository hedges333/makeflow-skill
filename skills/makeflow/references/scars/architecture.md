# Architecture and Pattern Scars

This reference manual documents universal architecture and software design pattern scars (`SCAR-ARCH-*`). These rules enforce testability, object-oriented design, decoupling, and lifecycle boundaries across all supported programming languages.

## Scars in this Category

| ID | Title | Scope |
| :--- | :--- | :--- |
| `SCAR-ARCH-05` | Testable Object-Oriented Script Template (BaseScript Pattern) | Script Lifecycles & In-Memory Testability |
| `SCAR-ARCH-06` | Just-in-Time Vector 4 Reflex Gates | Context Budget & Runtime Recovery |
| `SCAR-ARCH-07` | Anti-Speculative Defenses and Caller Grounding | Architecture & Caller Invariants |
| `SCAR-ARCH-08` | Polyglot Layer Separation and Structural Debt Triage | Layer Boundaries & Structural Triage |

---

### SCAR-ARCH-05: Testable Object-Oriented Script Template (BaseScript Pattern)

- **Wound**: Procedural CLI scripts and `main` entry points with sprawling imperative blocks cannot be unit-tested hermetically in memory. Mixing shared library modules, executable CLI scripts, and `test_*.py` suites in one `scripts/` folder causes cross-script coupling and shebang confusion. In unhandled error scenarios, scripts terminate the test runner or subagent process with `sys.exit()`, `process.exit()`, or `os.Exit()`.
- **Trap**: Writing procedural script files that pass long argument lists through helper functions or rely on global variables. Importing from peer executable scripts in `scripts/`, adding shebang headers to library modules, or keeping unit tests inside `scripts/`. Calling process-terminating exit functions directly inside business logic.
- **Permanent Reflex**: Encapsulate script and entry-point logic in cohesive, testable class objects or lifecycle runners implementing the **Template Method** pattern (`BaseScript`). Structure execution into clear lifecycle hooks: `parse_args`, `setup`, `run`, and `cleanup` with fatal exception trapping. Inject standard I/O streams (`stdout`, `stderr`) to allow in-memory stream assertion without OS stream hijacking. Separate code into three layers: shared library modules in `lib/` without shebangs, thin CLI wrappers in `scripts/`, and unit tests in `tests/`. Keep executable CLI files in `scripts/` to thin wrappers (`sys.exit(ScriptClass().execute())`, `exit(Script::run())`) that import only from `lib/`.
- **Socratic Probe**: Can this script's execution lifecycle be tested hermetically in memory without invoking subprocesses, cross-script imports, or module-level globals?

### SCAR-ARCH-06: Just-in-Time Vector 4 Reflex Gates

- **Wound**: Loading static negative rules into `SKILL.md` inflates every turn by thousands of tokens and dilutes model attention across active instructions.
- **Trap**: Writing prose prohibitions in skill prompts for conditions that deterministic `Make` targets or Python gate scripts can detect at runtime.
- **Permanent Reflex**: Synthesize `.workflow/state/Makefile` via `workflow_gates.py` and invoke `make -f .workflow/state/Makefile` gates paired with `emit_reflex.py` (`SCAR-ARCH-05`). Emit `[SCAR-ID: <CATEGORY>-<NUMBER>] <VIOLATION_CODE>` with `TRIGGER`, `REJECTED`, and `REFLEX` on `stderr` and exit with code `2`.
- **Socratic Probe**: Can a deterministic `Make` or Python gate trap this failure at runtime with exit code `2` instead of static `SKILL.md` prose?

### SCAR-ARCH-07: Anti-Speculative Defenses and Caller Grounding

- **Wound**: Speculative regex heuristics and defensive parser branches added complexity, caused false positives on real tickets, and stalled reviews over non-existent caller patterns.
- **Trap**: Designing or critiquing code around hypothetical "what if" scenarios before verifying who calls the code and whether the problem exists in practice.
- **Permanent Reflex**: Ground every implementation branch and review finding in an observed failure or a concrete caller contract. If a caller is internal and deterministic, reject speculative flag-shuffling or heuristic fallbacks. Balance "the problem could occur" against "the problem will occur": address certain failures, but defer hypothetical problems until a bug is observed or reported.
- **Socratic Probe**: Have you identified a specific, existing use case or real caller contract that makes this change necessary?

### SCAR-ARCH-08: Polyglot Layer Separation and Structural Debt Triage

- **Wound**: Copying sibling file placement into a conflated directory compounds structural debt and cross-entrypoint coupling, while unapproved mid-ticket directory reorganizations cause severe blast-radius regressions.
- **Trap**: Adding new domain logic into executable scripts or flat directories without auditing structure at Target 15 and Target 20. Reorganizing unrelated legacy directories mid-ticket without explicit Target 20 human approval.
- **Permanent Reflex**: Audit subproject layout at Target 15 against `skills/makeflow/references/polyglot/<lang>.md` and disclose module placement in `20_plan.md`. Enforce one-way dependencies (`entrypoints -> library`). If an existing layout is messy, offer a user choice at Target 20. Either keep in-ticket edits minimal with a follow-up ticket, or include scoped reorganization with explicit human approval.
- **Socratic Probe**: Does this change place reusable logic, thin entrypoints, and tests in idiomatic language layers without cross-entrypoint imports or unapproved reorganization?
