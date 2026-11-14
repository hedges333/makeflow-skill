# Testing and Verification Scars

This reference manual documents testing and verification scars (`SCAR-TEST-*`, `SCAR-AST-*`). These rules govern test environment isolation, hermetic execution, and test execution discipline.

## Scars in this Category

| ID | Title | Scope |
| :--- | :--- | :--- |
| `SCAR-TEST-05` | Isolated Test Environments | Test Database & Environment Safety |
| `SCAR-TEST-08` | Two-Tier Test Isolation | Hermetic Unit vs Integration Fixtures |
| `SCAR-TEST-09` | Fail-Fast Targeted TDD Loop | Red-Green Test Iteration (`-x -v`) |
| `SCAR-TEST-10` | Pre-Flight Structural Static Analysis Gate | Fast Structural Syntax Pre-Flight Check |
| `SCAR-TEST-11` | Shift-Left Testing Burden Balance | Up-Front Testing Feasibility & Burden Budget |
| `SCAR-TEST-12` | Cryptographically Anchored TDD Receipts | Receipt Sealing, Citation & Toolchain Immutability |
| `SCAR-TEST-22` | Traceable Test Naming and Artifact Longevity | Regression Test Naming & Tracing |
| `SCAR-TEST-24` | Coverage-Driven Fallback Branch Deletion Trap | Defensive Branch Safety & Coverage |
| `SCAR-TEST-29` | Red-Green Marginal-Line Test Law | Red-Green TDD Discipline |
| `SCAR-TEST-44` | Phase-Separated Flakiness Discrimination | Test Flakiness & Defect Isolation |
| `SCAR-AST-01` | Structural AST Anti-Shortcut Scrubbing Gate | Structural AST Anti-Shortcut Gate |

---

### SCAR-TEST-05: Isolated Test Environments

- **Wound**: Automated tests connect to a shared or production database and alter live rows.
- **Trap**: Running test suites with default environment variables instead of dedicated test database credentials.
- **Permanent Reflex**: Point all tests to `*_test` databases, in-memory stores, or mock fixtures. Never connect tests to production databases.
- **Socratic Probe**: Could these tests inadvertently connect to a live environment and alter real data? How can you guarantee that tests point exclusively to isolated test databases, in-memory stores, or mocks?

### SCAR-TEST-08: Two-Tier Test Isolation (Hermetic Unit vs Integration Fixtures)

- **Wound**: An unprivileged test runner or CI container fails session-level database DDL or network initialization in a root fixture, causing the entire test suite to crash before running any pure unit tests.
- **Trap**: Attaching database connections, socket initialization, or schema migrations to global `autouse=True` session fixtures instead of isolating them behind explicit markers.
- **Permanent Reflex**: Partition test suites into two tiers: Tier 1 hermetic unit tests with zero external daemon or socket dependencies (allowing self-contained ephemeral loopback sockets such as `127.0.0.1:0` or temporary Unix domain sockets in `tmp_path`), and Tier 2 marked integration tests that verify external dependencies during pre-flight. Never attach live database connections or unmanaged network sockets to un-isolated session fixtures.
- **Socratic Probe**: If root fixtures initialize live sockets or database DDL, will pure unit tests fail in unprivileged environments? Can you separate hermetic unit tests from external integration fixtures so unit tests always run in memory?

### SCAR-TEST-09: Fail-Fast Targeted TDD Loop (pytest -x -v)

- **Wound**: Running a large test suite during active debugging generates hundreds of cascading tracebacks, exhausting context limits and obscuring the initial defect.
- **Trap**: Running broad test suites without fail-fast flags while attempting to iterate on a single failing test.
- **Permanent Reflex**: Use fail-fast flags (`pytest -x -v` or language equivalent) during active Red-Green TDD cycles. Iterate exclusively on the targeted failing test until green before running the full regression suite.
- **Socratic Probe**: Can you develop and run targeted tests with fail-fast flags so execution stops immediately on failure? If you iterate on only the failing test until it passes, will you identify defects without drowning in cascading logs?

### SCAR-TEST-10: Pre-Flight Structural Static Analysis Gate

- **Wound**: Running a slow runtime test suite on code with syntax errors or undefined symbols wastes time and pollutes logs.
- **Trap**: Relying on full runtime test suites to detect basic structural syntax mistakes or missing imports.
- **Permanent Reflex**: Run fast structural static analysis before you execute functional tests. Use available comprehensive linting programs for languages in the project, or fall back to standard library AST parsing.
- **Socratic Probe**: If the code contains a basic syntax error or missing import, why wait for a heavy test suite to run? Would a fast AST or static linter check catch structural defects in milliseconds?

### SCAR-TEST-11: Shift-Left Testing Burden Balance in Archaeology and Planning

- **Wound**: An agent writes a shallow implementation plan that tests only documentation strings or superficial metadata. This requires human operator intervention at Target 20 or causes an adversarial critic rejection at Target 40. Alternatively, an agent over-engineers tests by pulling in heavy external compilers, slow daemons, or unneeded dependencies.
- **Trap**: Postponing test comprehensiveness and burden balance evaluation to the Target 40 adversarial review causes delays and rework loops.
- **Permanent Reflex**: Shift-left Pillar 2 evaluation into Target 15 and Target 20. In Target 15 archaeology receipts, classify the failure mechanism and evaluate test feasibility within hermetic fixtures (< 100 ms). In Target 20 plans, disclose the testing balance and prohibit surface-only testing when hermetic boundary testing is possible.
- **Socratic Probe**: Are you settling for superficial assertions when hermetic boundary tests are fast and feasible? Does your test plan strike the right balance between rigorous contract coverage and fast execution without heavy dependencies, or do you need to interview the user to determine if heavy dependencies are necessary for some tests to be comprehensive?

### SCAR-TEST-12: Cryptographically Anchored TDD Receipts and Toolchain Immutability

- **Wound**: An agent passes the Green test gate by swapping `TEST_CMD`, dropping an untracked fixture, citing uncommitted scratch files, committing mid-phase, or tampering with governance scripts and Makefiles.
- **Trap**: Unanchored test runs trust conversational compliance, `git write-tree` ignores untracked fixtures, and autonomous agents have filesystem write access to verification scripts.
- **Permanent Reflex**: Run `witness-red` to write a cryptographic seal (a tamper-detection hash) over Git `HEAD`, `test_cmd`, all test files, and core governance scripts in `30_tdd_receipt.json`. Run `verify-green` to confirm that the receipt matches, verify governance toolchain integrity, and replay the sealed command. If governance scripts change after `witness-red`, emit `GOVERNANCE_TOOLCHAIN_TAMPERED` with exit code `2` and record a collision in `forensic_log`. If `.workflow/state/Makefile` changes, emit `STATE_MAKEFILE_TAMPERED` with exit code `2` and record a collision. Run `verify-citation` to verify line citations against immutable Git blobs in witnessed `HEAD`. If a cited file is uncommitted or missing, emit `UNCOMMITTED_WITNESS_FILE` with exit code `2` and record the collision in `forensic_log`. If a citation is blank or a comment line, emit `TRIVIAL_WITNESS_LINE` with exit code `2` and record the collision in `forensic_log`. Intercept shell redirection, truncation, and deletion commands targeting governance files in `scripts/pre_tool_guard.py`.
- **Socratic Probe**: Did production code fix the bug, or did a test file, fixture, command, citation, commit, governance script, or Makefile change after `witness-red`?

### SCAR-TEST-22: Traceable Test Naming and Artifact Longevity

- **Wound**: A developer writes tests named with temporary review finding numbers. After the ticket closes, future maintainers cannot trace why the test exists or what defect it guards.
- **Trap**: Using short-lived review comment numbers in permanent test names.
- **Circuit Breaker**: If you create a test, never name it after temporary review finding numbers.
- **Permanent Reflex**: Name regression tests after permanent ticket keys and concrete failure behaviors. Include the ticket identifier in the test docstring.
- **Socratic Probe**: Does this test name describe the concrete behavior and reference a permanent ticket key?

### SCAR-TEST-24: Coverage-Driven Fallback Branch Deletion Trap

- **Wound**: An agent deletes a defensive fallback branch to achieve 100 percent branch coverage. When an unexpected input arrives in production, the process crashes.
- **Trap**: Prioritizing artificial test coverage metrics over defensive code safety.
- **Circuit Breaker**: If a coverage tool flags an unreached fallback branch, never delete the branch to satisfy the tool.
- **Permanent Reflex**: Preserve all fallback branches that guard system boundaries. If a branch is hard to reach in tests, write a failure injection fixture instead of deleting code.
- **Socratic Probe**: Did you delete this fallback branch because it is unreachable, or did you delete it to inflate coverage metrics?

### SCAR-TEST-29: Red-Green Marginal-Line Test Law

- **Wound**: An agent writes a new test that passes before any production edits occur. The test verifies nothing new and allows silent regressions.
- **Trap**: Accepting an immediate green test without witnessing it fail first on broken code.
- **Circuit Breaker**: If a new test passes before you modify production code, stop immediately.
- **Permanent Reflex**: Before you apply production edits, make sure that every new test fails on unmodified code. Apply production edits only after you witness the red failure.
- **Socratic Probe**: Did you witness this test fail red before you wrote the fix?

### SCAR-TEST-44: Phase-Separated Flakiness Discrimination

- **Wound**: A test fails during local development. The agent blames flakiness and re-runs the suite until it passes, masking a real race condition.
- **Trap**: Assuming that an intermittent failure is external noise rather than a code defect.
- **Circuit Breaker**: If a local test fails, never re-run it blindly without investigating the root cause.
- **Permanent Reflex**: Treat all local test failures as deterministic defects. If a test fails twice with the same stack trace, fix the defect before you proceed.
- **Socratic Probe**: Are you re-running this test to get lucky, or did you locate the root cause?

### SCAR-AST-01: Structural AST Anti-Shortcut Scrubbing Gate

- **Wound**: An agent takes destructive shortcuts to pass failing tests in production code. The agent silences exceptions with empty handlers, returns trivial default values, or leaks test mocks into production modules.
- **Trap**: Relying on negative prose instructions in prompts primes agents to use forbidden keywords, while runtime test suites fail to catch silent swallowing of errors.
- **Permanent Reflex**: Enforce deterministic AST structural scrubbing in `ast-lint` and pre-flight static analysis gates. Parse source files into standard library abstract syntax trees, reject silent exception handlers, trivial fallback returns, and production mock imports with Vector 4 Reflex directives and exit code 2, and require specific exception handling or explicit logging.
- **Socratic Probe**: Did production code legitimately handle or log the error, or did it swallow exceptions with a bare handler, return a trivial placeholder default, or leak test doubles into source modules?
