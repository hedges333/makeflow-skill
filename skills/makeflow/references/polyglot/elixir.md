# Polyglot Stack Guide: Elixir

This guide covers detection markers, test and lint commands, smoke test templates, and database isolation for Elixir.

## 1. Detection Markers

Look for these files when scanning an Elixir application:

- `mix.exs`, `mix.lock`

## 2. Standard Commands

Run these commands inside the Mix project directory:

- Targeted Test: `mix test <path_to_test>`
- Format: `mix format --check-formatted`
- Lint: `mix credo --strict`

## 3. Smoke Test Template

If no test exists, create `test/smoke_test.exs`:

```elixir
defmodule SmokeTest do
  use ExUnit.Case

  test "smoke test harness" do
    assert true
  end
end
```

## 4. Test Database Isolation

Follow three database isolation rules in Elixir tests:

1. Enable `Ecto.Adapters.SQL.Sandbox` so every test runs inside an isolated SQL transaction that rolls back on exit.
2. In `config/test.exs`, make sure that `database:` points to a dedicated test database such as `<app>_test`.
3. Set `pool: Ecto.Adapters.SQL.Sandbox` so database writes never leak across test runs.

## Idiomatic Directory and Module Structure

- **Library and Domain Layer**: Place domain contexts, schemas, and pure modules in `lib/<app>/`. Keep contexts independent of web transport layers.
- **Executable Entrypoints and Controllers**: Keep Phoenix controllers in `lib/<app>_web/` and Mix tasks in `lib/mix/tasks/` as thin callers of `lib/<app>/`.
- **Test Suite Placement and Naming**: Mirror the `lib/` tree inside `test/` using the `*_test.exs` suffix and load `test/test_helper.exs` first.
- **Structural Anti-Patterns to Flag at Target 15**: Flag `lib/<app>/` modules that call `lib/<app>_web/`, raw `Repo` queries in controllers, or tests without `_test.exs`.
