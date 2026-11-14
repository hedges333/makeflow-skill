# Polyglot Stack Guide: Haxe

This guide covers detection markers, test and lint commands, smoke test templates, and isolation rules for Haxe applications and game projects.

## 1. Detection Markers

Look for these files when scanning a Haxe subproject:

- `build.hxml`, `test.hxml`, `*.hxml`, `haxelib.json`, `project.xml`, `*.hx`

## 2. Standard Commands

Run these commands inside the Haxe project root:

- Targeted Test: `haxe test.hxml` or `haxe -cp src -cp test -lib utest -main TestMain --interp`
- Lint: `haxelib run checkstyle -s src -s test`
- Format: `haxelib run formatter --check -s src -s test` (write mode: `haxelib run formatter -s src -s test`)

## 3. Smoke Test Template

If no unit test exists, create `test/SmokeTest.hx` with `utest`:

```haxe
package;

import utest.Assert;
import utest.Test;

class SmokeTest extends Test {
    function testSmokeHarness() {
        Assert.isTrue(true);
    }
}
```

## 4. Test Isolation and Game Architecture

Follow three rules in Haxe tests:

1. Execute pure domain and gameplay tests on the `--interp` or HashLink target so tests run without window servers.
2. Isolate file writes inside temporary folders and delete them during `teardown()`.
3. In HaxeFlixel, Heaps, or OpenFL game projects, apply the Object Pool, Component, and State patterns from [`14_design_patterns_and_architecture.md`](../14_design_patterns_and_architecture.md).

## Idiomatic Directory and Module Structure

- **Library and Domain Layer**: Place domain classes in `src/<pkg>/` with lowercase `package` declarations matching the folder path and `UpperCamelCase.hx` filenames.
- **Executable Entrypoints and Controllers**: Keep `static function main()` thin inside `src/Main.hx`. Wire targets and dispatch directly into domain packages.
- **Test Suite Placement and Naming**: Place `utest` or `munit` suites in `test/` using `*Test.hx` class files and a `TestMain.hx` runner.
- **Structural Anti-Patterns to Flag at Target 15**: Flag `package` declarations that mismatch directory paths, platform APIs in pure domain classes, or tests inside `src/`.

## Code Documentation Standards and Tree Assembly

- **Project Convention Detection**: Inspect `build.hxml` for `-xml` type flags and `dox` configurations at Target 15 (`SCAR-PROC-159`).
- **Idiomatic Doc Comment Standard**: Write Haxe `/** ... **/` doc comments above public types, fields, and methods in `src/`.
- **Documentation Tree Assembly Command**: Run `haxe -xml docs.xml` and `haxelib run dox -i docs.xml` to build the API reference tree.
- **Documentation Anti-Patterns to Flag at Target 40**: Flag `//` line comments used instead of `/** ... **/` on public APIs or failed `dox` XML builds.
