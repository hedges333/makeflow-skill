# Public seams, behavioral contracts, and mocking boundaries

- **Source Standards**: Adapted from `mattpocock/skills` (`tdd` skill), `fheikens/stdd` (Specification and Test-Driven Development), and `AsiaOstrich/universal-dev-standards`.
- **License and Attribution**: `mattpocock/skills` (MIT License, Copyright Matt Pocock), `fheikens/stdd` (CC BY 4.0, Author Frank Heikens), and `AsiaOstrich/universal-dev-standards` (CC BY 4.0 / MIT, Universal Development Standards Contributors). Distilled and adapted for Makeflow agents.

This module defines how to choose public test seams, preserve permanent behavioral contracts, prevent tautological assertions, and limit mocking to system boundaries.

## 1. Test behavior through public seams

A seam is a stable public interface where callers invoke a module and observe its outcomes.
Good seams include public functions, repository methods, HTTP handlers, CLI `BaseScript.execute()` calls, and domain service entrypoints.

1. Declare planned public seams in `.workflow/state/20_plan.md` before Target 30 begins.
2. Call only public interfaces in unit tests rather than private helper methods.
3. Assert observable return values, persisted outputs, emitted events, or exit codes.
4. Avoid asserting internal call order, private attributes, or intermediate helper names.

### Refactoring resilience test

Ask one diagnostic question about every new test.
If you rename private helpers or rewrite the internal algorithm while preserving behavior, does the test stay green?
If a safe internal refactor breaks the test, the test is coupled to implementation details (`SCAR-TEST-47`).

## 2. Specifications as permanent contracts

Adapted from `fheikens/stdd`, treat behavioral tests as permanent contracts and production implementations as disposable code.
An implementation can be rewritten, optimized, or replaced when requirements scale.
The behavioral test suite remains the durable proof that the system honors every user contract.

1. Express each test in domain language rather than low-level storage or framework terms.
2. Separate business contract assertions from test fixture plumbing helpers.
3. When two tests share identical setup noise, extract a small factory helper during post-green Refactor.
4. Keep assertion expectations visible inside each test body so failures read clearly at a glance.

## 3. Independent expectations and anti-tautology

A tautological test computes its expected value with the same formula or helper that production code uses (`SCAR-TEST-47`).
If the formula has a bug, both production code and test compute the same wrong answer and pass silently.

```python
# Bad: tautological mirror of production formula
expected = price * (1 - discount_rate)
self.assertEqual(apply_discount(price, discount_rate), expected)

# Good: independent literal expectation derived from business rules
self.assertEqual(apply_discount(100, 0.15), 85.0)
```

Follow three rules to prevent tautological tests:

1. Write expected values as independent literals or hand-verified fixtures derived from ticket requirements.
2. Never import private production constants or helper formulas to construct expected test outputs.
3. Pair every happy-path assertion with at least one boundary or invalid-input assertion.

## 4. Boundary-only mocking

Mock only at external system boundaries that are slow, non-deterministic, or out of process (`SCAR-TEST-47`).
Valid mock boundaries include network APIs, payment gateways, email providers, system clocks, and process spawns.

Never mock internal domain collaborators, value objects, parsers, or pure data transformations.
Mocking internal classes freezes the class graph, hides real integration bugs, and creates counterfeit tests that pass when real collaborators fail.

| Boundary type | Recommended test double strategy | Reason |
| :--- | :--- | :--- |
| Pure domain functions and helpers | Use real production objects | Zero latency and real integration proof |
| Local filesystem and Git repos | Use `tempfile.TemporaryDirectory()` (`SCAR-TEST-45`) | Real OS semantics with automatic cleanup |
| Relational databases | Use SQLite `:memory:` or isolated `*_test` DB | Exercises real SQL queries safely (`SCAR-TEST-08`) |
| Network APIs and clocks | Inject interface fake or boundary mock | Deterministic, fast, and zero live traffic |
| Subprocess or daemon protocols | Use loopback `127.0.0.1:0` or controlled fake | Hermetic lifecycle with zero external services |

### Dependency injection for testability

If a module constructs external clients directly inside its body, pass the dependency into the constructor or function parameter instead.
Constructor injection lets tests supply a narrow boundary double without monkeypatching internal module globals.
When you must verify a boundary double, prefer stateful in-memory fakes over brittle call-count mocks.
