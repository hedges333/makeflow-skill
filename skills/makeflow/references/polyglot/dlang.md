# Polyglot Stack Guide: D

This guide covers detection markers, test and lint commands, smoke test templates, and isolation rules for D (`dlang`) packages.

## 1. Detection Markers

Look for these files when scanning a D subproject:

- `dub.json`, `dub.sdl`, `*.d`

## 2. Standard Commands

Run these commands inside the Dub package directory:

- Targeted Test: `dub test` or `dmd -unittest -main -run source/<module>.d`
- Lint: `dscanner --styleCheck source/`
- Format Check: `diff -u source/<module>.d <(dfmt source/<module>.d)`
- Format Write: `dfmt --inplace source/<module>.d`

## 3. Smoke Test Template

If no unit test exists, create `source/smoke_test.d`:

```d
module smoke_test;

unittest {
    assert(true, "Smoke test harness runs");
}
```

## 4. Test Isolation

Follow three isolation rules in D tests:

1. Create temporary test directories inside `std.file.tempDir` and remove them inside `scope(exit)` blocks.
2. Place network and storage clients behind `interface` contracts so `unittest` blocks can pass in-memory fakes.
3. Point SQLite or database integration tests to `:memory:` or dedicated `*_test` databases.

## Idiomatic Directory and Module Structure

- **Library and Domain Layer**: Place reusable Dub modules in `source/<pkg>/` with `module <pkg>.<name>` declarations matching the directory hierarchy.
- **Executable Entrypoints and Controllers**: Keep the `main()` entrypoint thin inside `source/app.d`. Delegate argument parsing and work to library modules.
- **Test Suite Placement and Naming**: Use inline `unittest` blocks for module unit checks and place integration test modules in `tests/`.
- **Structural Anti-Patterns to Flag at Target 15**: Flag `module` names that mismatch file paths, heavy logic in `source/app.d`, or network calls in `unittest` blocks.
