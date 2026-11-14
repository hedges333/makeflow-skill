# Polyglot Stack Guide: GDScript and Godot Engine

This guide covers detection markers, headless test and lint commands, smoke test templates, and isolation rules for GDScript projects in Godot Engine.

## 1. Detection Markers

Look for these files and directories when scanning a Godot subproject:

- `project.godot`, `*.gd`, `*.tscn`, `addons/gut/`, `addons/gdUnit4/`

## 2. Standard Commands

Run these commands inside the Godot project root without opening a graphical window:

- GUT Targeted Test: `godot --headless --path . -s addons/gut/gut_cmdln.gd -gtest=res://test/unit/test_<name>.gd -gexit`
- GdUnit4 Targeted Test: `./addons/gdUnit4/runtest.sh -a test/<name>`
- Lint: `gdlint <path>` and `godot --headless --check-only -s <script>.gd`
- Format: `gdformat --check <path>` (write mode: `gdformat <path>`)

## 3. Smoke Test Template

If no unit test exists, create `test/unit/test_smoke.gd` for GUT:

```gdscript
extends GutTest

func test_smoke_harness() -> void:
	assert_true(true, "Smoke test harness runs")
```

## 4. Test Isolation and Game Architecture

Follow four rules in GDScript tests and gameplay code:

1. Wrap instantiated nodes in `autofree()` or `add_child_autofree()` so orphan nodes do not leak across tests.
2. Write test save files inside `DirAccess.create_temp()` paths instead of shared `user://` profiles.
3. Stub network and platform autoload services with mock nodes or Service Locator interfaces.
4. Apply the Component, State, Object Pool, and Observer signal patterns from [`14_design_patterns_and_architecture.md`](../14_design_patterns_and_architecture.md).

## Idiomatic Directory and Module Structure

- **Library and Domain Layer**: Place pure `RefCounted` domain classes and custom `Resource` definitions in `scripts/` or domain folders without scene dependencies.
- **Executable Entrypoints and Controllers**: Place `.tscn` hierarchies and thin node controllers in `scenes/`. Delegate state transitions and math to domain classes.
- **Test Suite Placement and Naming**: Place GUT or gdUnit4 test scripts in `test/unit/` and `test/integration/` using the `test_<name>.gd` prefix.
- **Structural Anti-Patterns to Flag at Target 15**: Flag monolithic autoload singletons, coupled `get_node("../../...")` paths across scenes, or domain logic trapped inside UI nodes.
