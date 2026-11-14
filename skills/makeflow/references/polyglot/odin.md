# Polyglot Stack Guide: Odin

This guide covers detection markers, built-in test and static check commands, smoke test templates, and memory isolation rules for Odin packages.

## 1. Detection Markers

Look for these files when scanning an Odin subproject:

- `ols.json`, `*.odin`

## 2. Standard Commands

Run these commands inside the Odin package or workspace directory:

- Targeted Test: `odin test <package_dir> -define:ODIN_TEST_NAMES=<pkg>.<test_name>` or `odin test <package_dir>`
- Static Check and Lint: `odin check <package_dir> -vet -strict-style`
- Format Check: `diff -u <file>.odin <(odinfmt <file>.odin)`
- Format Write: `odinfmt -w <path>`

## 3. Smoke Test Template

If no unit test exists, create `tests/smoke_test.odin`:

```odin
package tests

import "core:testing"

@(test)
smoke_test_harness :: proc(t: ^testing.T) {
	testing.expect(t, true, "Smoke test harness runs")
}
```

## 4. Test Isolation and Memory Safety

Follow three rules in Odin tests:

1. Keep the built-in `core:testing` tracking allocator enabled so `odin test` catches memory leaks and bad frees automatically.
2. Write temporary test files inside isolated scratch directories and clean them up with `defer`.
3. In Raylib, Sokol, or SDL game projects, favor Data Locality, Object Pool, and Spatial Partition patterns from [`14_design_patterns_and_architecture.md`](../14_design_patterns_and_architecture.md).

## Idiomatic Directory and Module Structure

- **Library and Domain Layer**: Group reusable procedures into directory-scoped packages under `src/<pkg>/`. Every `.odin` file in one directory shares the same `package` name.
- **Executable Entrypoints and Controllers**: Place thin `main :: proc()` entrypoints in `src/main/` or `cmd/<app>/`. Import domain packages one-way.
- **Test Suite Placement and Naming**: Write `@(test)` procedures in `*_test.odin` files inside the package directory or inside a dedicated `tests/` package.
- **Structural Anti-Patterns to Flag at Target 15**: Flag multiple `package` names in one directory, circular package imports, or business logic inside `main :: proc()`.
