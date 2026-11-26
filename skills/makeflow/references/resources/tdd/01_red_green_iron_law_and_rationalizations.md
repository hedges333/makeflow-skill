# Red-Green iron law, test structure, and rationalization counters

- **Source Standards**: Adapted from `obra/superpowers` (test-driven-development skill), `AsiaOstrich/universal-dev-standards` (testing standards), and `tweag/agentic-coding-handbook` (TDD guidance).
- **License and Attribution**: `obra/superpowers` (MIT License, Copyright Jesse Vincent), `AsiaOstrich/universal-dev-standards` (CC BY 4.0 / MIT, Universal Development Standards Contributors), and `tweag/agentic-coding-handbook` (MIT License, Copyright Modus Labs). Distilled and adapted for Makeflow agents.

This module defines the test-first iron law, meaningful Red verification, test anatomy, and counters to common agent shortcuts.

## 1. The test-first iron law

Write no production code without a failing test first (`SCAR-TEST-48`).
If you write production code before a failing test, delete that production code and start fresh from the failing test.
Never keep premature production code as a reference while writing tests afterward.

Tests written after implementation pass immediately and prove nothing about failure detection.
A test that never fails can hide a broken assertion, a wrong mock, or an unexecuted code path.

## 2. Meaningful Red verification

Not every non-zero exit code proves a valid Red phase (`SCAR-TEST-48`).
Before you run `witness-red`, inspect the test runner failure output carefully:

1. Confirm that the test fails on missing or wrong behavior rather than a syntax error.
2. Confirm that import errors occur only when the planned new symbol does not exist yet.
3. Confirm that the failure message matches the exact assertion you intend to satisfy.
4. If the test passes before production edits, fix the test because it tests existing behavior.
5. Run `make -f .workflow/state/Makefile witness-red` only after you confirm a genuine behavioral failure.

### Negative falsification check

Every new contract or validator test requires a negative falsification check.
Feed a known-bad or mutated input to confirm that the assertion fails when the contract breaks.
Without a negative case, a misconfigured matcher can pass silently on empty or malformed data.

## 3. FIRST test principles and Arrange-Act-Assert anatomy

Good unit tests follow the FIRST criteria from `AsiaOstrich/universal-dev-standards`:

- **Fast**: Run pure unit tests in memory in milliseconds so inner loops stay tight (`SCAR-TEST-08`, `SCAR-TEST-09`).
- **Independent**: Give each test its own fixtures and ephemeral temporary directory so execution order never matters (`SCAR-TEST-45`).
- **Repeatable**: Eliminate live clocks, network calls, and random seeds without fixed injection so runs give identical results.
- **Self-Validating**: Express pass or fail through explicit assertions rather than manual log inspection.
- **Timely**: Write each test right before the minimal production slice that satisfies it.

### Arrange-Act-Assert (AAA) structure

Structure every test body into three clear visual sections:

1. **Arrange**: Build minimal input data, fixtures, or boundary doubles for one specific scenario.
2. **Act**: Invoke the single public seam or command under test.
3. **Assert**: Check observable outputs, state transitions, or boundary calls with explicit literal values.

Keep each test focused on one behavior with a behavioral domain name (`SCAR-TEST-22`).
If a test name contains `and`, split it into two focused tests.

## 4. Tests as executable prompts

In agentic workflows, tests act as unambiguous specifications that anchor the model attention window.
Clear test inputs, edge cases, and expected error codes constrain the Green implementation better than prose descriptions.
When a bug is reported, write a minimal reproduction test before inspecting deep call stacks.
The failing test locks the exact defect and prevents speculative fixes in unrelated modules.

## 5. Agent rationalization counters

Coding agents frequently invent plausible excuses to bypass TDD under time or context pressure.
Treat every excuse in this table as an immediate stop signal (`SCAR-TEST-48`):

| Agent rationalization | Reality and mandatory counter |
| :--- | :--- |
| "This change is too simple to test." | Trivial edits break edge cases and regressions. Run 1 focused slice with zero padding. |
| "I will write the tests right after I verify the fix." | Tests written after code pass immediately and never prove they catch the defect. |
| "I already ran a manual CLI check and saw it work." | Manual checks leave zero repeatable regression guard for future edits or CI runs. |
| "Deleting my early draft wastes work." | Sunk cost bias hides untested paths. Delete premature production code and write Red first. |
| "I will keep the code as a reference while writing tests." | Keeping code turns TDD into test-after adaptation. Delete it and drive design from tests. |
| "Existing tests are hard to set up, so I will skip this case." | Hard test setup signals tight coupling. Introduce a clean seam or boundary fixture first. |
| "I will weaken this assertion so the suite turns green." | Weakening assertions hides production regressions and triggers `EACCES_TEST_TAMPERING`. |
| "The failure is a syntax error, so exit code is non-zero." | Syntax and typo crashes are false Red witnesses. Fix the test so the assertion fails cleanly. |
