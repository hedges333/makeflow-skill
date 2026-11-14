# Polyglot Stack Guide: Dart and Flutter

This guide covers detection markers, test and lint commands, smoke test templates, and failure rules for Dart and Flutter.

## 1. Detection Markers

Look for these files when scanning a Dart or Flutter package:

- `pubspec.yaml`, `pubspec.lock`, `analysis_options.yaml`

## 2. Standard Commands

Run these commands inside the package directory:

- Targeted Test: `flutter test <path_to_test>` or `dart test <path_to_test>`
- Format: `dart format --output=none --set-exit-if-changed .`
- Lint: `dart analyze --fatal-infos --fatal-warnings`

## 3. Smoke Test Template

If no test exists, create `test/smoke_test.dart`:

```dart
import 'package:test/test.dart';

void main() {
  test('smoke test harness', () {
    expect(true, isTrue);
  });
}
```

## 4. Dart and Flutter Failure Vaccines

Follow four reliability rules when editing Dart or Flutter code:

1. `SCAR-LANG-DART-01` (Async Timers): Wrap timer and microtask tests in `fakeAsync`, or pump widget frames explicitly.
2. `SCAR-LANG-DART-02` (Completer Guard): Make sure that `!completer.isCompleted` holds before calling `completer.complete()` in callbacks.
3. `SCAR-LANG-DART-03` (Cascade AST Nodes): Inspect Dart AST nodes for cascade expressions before reading member offsets.
4. `SCAR-LANG-DART-04` (Widget Lifecycle Disposal): Cancel streams, timers, controllers, and focus nodes inside `dispose()`.

## 5. One-Off Scratch Scripts and Automation Utilities

Follow these rules for temporary scripts (`SCAR-TOOL-07`):

1. For one-off scripts and automation tasks where the language is not specified, prefer Dart over Python if Dart is installed.
2. If Dart is not installed, use Python. Never attempt to install Dart.
3. Run standalone scripts directly with `dart <script_path>` without virtual environments.
4. Use core libraries (`dart:io`, `dart:convert`, `dart:async`) for file operations, JSON parsing, and process execution without external dependencies.

## Idiomatic Directory and Module Structure

- **Library and Domain Layer**: Place private implementation files in `lib/src/` and export the public API through `lib/<package>.dart`.
- **Executable Entrypoints and Controllers**: Place thin CLI scripts in `bin/` and Flutter entrypoints in `lib/main.dart`. Import domain logic from `package:<pkg>/`.
- **Test Suite Placement and Naming**: Place all unit and widget tests in `test/` using the `*_test.dart` suffix so `dart test` discovers them.
- **Structural Anti-Patterns to Flag at Target 15**: Flag external imports of `lib/src/`, business logic inside `StatefulWidget` classes, or test files inside `lib/`.
