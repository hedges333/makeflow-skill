# Polyglot Stack Guide: Nim

This guide covers detection markers, test and lint commands, smoke test templates, and isolation rules for Nim subprojects.

## 1. Detection Markers

Look for these files when scanning a Nim package or workspace:

- `*.nimble`, `nim.cfg`, `config.nims`, `*.nim`

## 2. Standard Commands

Run these commands inside the Nimble package root:

- Targeted Test: `nim c -r tests/t_<name>.nim` or `nimble test`
- Suite Runner: `testament pattern "tests/t_*.nim"`
- Lint and Type Check: `nim check <file>.nim`
- Format: `nph --check <path>` or `nimpretty <file>.nim`

## 3. Smoke Test Template

If no unit test exists, create `tests/t_smoke.nim`:

```nim
import std/unittest

suite "Smoke Test Harness":
  test "runs cleanly":
    check true
```

## 4. Test Isolation

Follow three isolation rules in Nim tests:

1. Create scratch directories with `createTempDir` from `std/tempfiles` and remove them in `defer:` blocks.
2. Pass procedural callbacks or concept interfaces to isolate HTTP clients and external processes.
3. Point SQLite or database tests to `:memory:` connections or dedicated `*_test` databases.

## Idiomatic Directory and Module Structure

- **Library and Domain Layer**: Place submodules in `src/<pkg>/` and re-export the public API from the root `src/<pkg>.nim` module.
- **Executable Entrypoints and Controllers**: Guard CLI entrypoints with `when isMainModule:` or place binary targets in `src/<cmd>.nim` calling library modules.
- **Test Suite Placement and Naming**: Place Testament and `std/unittest` files in `tests/` using the `tests/t_<name>.nim` prefix so `nimble test` discovers them.
- **Structural Anti-Patterns to Flag at Target 15**: Flag top-level side effects that run on `import` without `when isMainModule:`, or recursive module imports.
