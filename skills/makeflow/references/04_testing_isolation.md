# 04. Testing Isolation and Safe Environments

This manual defines the rules for hermetic tests (isolated tests with zero live dependencies) and production data safety.

## 1. Zero Production Data Rules

Automated tests must never connect to live systems:

1. Never point automated tests to live production databases or read production credentials.
2. Connect database tests to dedicated `*_test` databases, local Docker test containers, or in-memory engines such as SQLite `:memory:` and H2.
3. Wrap database test cases in transactions that roll back on exit when the framework supports it, for example Elixir `Ecto.Adapters.SQL.Sandbox`, `pytest`, or `rspec`.
4. Write test files inside unique OS ephemeral temporary directories (`SCAR-TEST-45`). Use `tempfile.TemporaryDirectory()`, `t.TempDir()`, `tempfile::TempDir`, or `mktemp -d` with `trap` instead of fixed `/tmp/<slug>` paths.
5. Mock or stub all external HTTP APIs, payment gateways, authentication providers, and email services.

## 2. Pre-Test Environment Checks

Run four safety steps before you execute any test suite:

1. Make sure that the test environment variable is active, such as `NODE_ENV=test`, `RAILS_ENV=test`, or `APP_ENV=testing`.
2. Read `.workflow/config.json` (`database` section) or `MAKEFILE_WORKFLOW_*` environment variables for declared `prodDatabases`, `testDatabases`, and `prodHosts`.
3. Inspect the database connection string. If it names a cloud host, staging server, `prod` database, or configured `prodDatabases` entry, stop at once.
4. If live versus test database names remain unclear, ask the user before running tests. Never fall back to a shared or production database.

## 3. Two-Tier Test Isolation Architecture (`SCAR-TEST-08`)

To prevent unprivileged runner crashes, partition tests into two distinct tiers:

1. Tier 1 (Hermetic Unit Tests): Run pure unit tests in memory with zero external socket or daemon dependencies. Keep execution fast under least-privilege environments.
2. Tier 2 (Integration Tests): Mark integration tests with explicit framework markers (such as `@pytest.mark.integration` or `@tag :integration`). Verify external dependencies during pre-flight before executing integration tests.
3. Root Fixture Safety: Never attach database DDL migrations or network socket connections to global autouse session fixtures. Unhandled fixture failures abort the entire test run before executing pure unit tests.
4. Ephemeral Loopback Sockets: Testing daemon processes or network protocols may require actual socket communication. Tier 1 hermetic tests may create and connect to self-contained ephemeral loopback sockets (for example binding to `127.0.0.1:0` or temporary Unix domain sockets in `tmp_path`). Because the test process controls the lifecycle and unbinds on completion, these tests remain fully hermetic with zero external daemon dependencies.

## 4. Ephemeral Temporary Fixtures and Behavioral Test Naming (`SCAR-TEST-45`, `SCAR-TEST-22`)

Follow three fixture and naming rules across all test suites:

1. Ephemeral Temp Directories (`SCAR-TEST-45`): Allocate unique OS temporary directories with automatic cleanup. Use `tempfile.TemporaryDirectory()`, `t.TempDir()`, `tempfile::TempDir`, or `mktemp -d` with `trap`.
2. Forbidden Cleanup Patterns (`SCAR-TEST-45`): Never mutate fixed `/tmp/<slug>` or `/var/tmp/<slug>` paths in tests. Never rely on manual `mkdtemp`, `mkstemp`, `mktemp`, or `NamedTemporaryFile(delete=False)` cleanup.
3. Behavioral Domain Naming (`SCAR-TEST-22`): Place permanent tests under `./tests/` or the language test directory. Use behavioral domain names instead of bare issue numbers. Record the ticket identifier in the test docstring.
