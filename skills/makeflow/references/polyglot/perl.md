# Polyglot Stack Guide: Perl

This guide covers detection markers, test and lint commands, smoke test templates, and isolation rules for Perl.

## 1. Detection Markers

Look for these files when scanning a Perl distribution:

- `Makefile.PL`, `Build.PL`, `cpanfile`, `dist.ini`, `META.json`, `*.pl`, `*.pm`

## 2. Standard Commands

Run these commands inside the Perl distribution directory:

- Targeted Test: `prove -lv t/<test_file>.t` or `make test`
- Format: `perltidy -b <file>`
- Lint: `perlcritic --severity 3 lib/`

## 3. Smoke Test Template

If no test exists, create `t/00-smoke.t`:

```perl
use strict;
use warnings;
use Test::More tests => 1;

ok(1, 'smoke test harness runs cleanly');
```

## 4. Test Database and File Isolation

Follow three isolation rules in Perl tests:

1. Use `DBD::SQLite` with `:memory:` for unit database tests.
2. Simulate SQL statements with `Test::MockDBI` when testing without a database server.
3. Create temporary test directories with `File::Temp` (`tempdir(CLEANUP => 1)`).

## Idiomatic Directory and Module Structure

- **Library and Domain Layer**: Place reusable `.pm` packages in `lib/` where `lib/Foo/Bar.pm` declares `package Foo::Bar;` and ends with a true value.
- **Executable Entrypoints and Controllers**: Place thin `.pl` CLI scripts in `bin/` or `script/`. Parse options and call `lib/` modules immediately.
- **Test Suite Placement and Naming**: Place `Test::More` or `Test2::V0` test scripts in `t/` with the `.t` extension for `prove -lv t/` execution.
- **Structural Anti-Patterns to Flag at Target 15**: Flag business logic inside `.pl` scripts, `.pm` files missing `use strict; use warnings;`, or `.pm` modules outside `lib/`.
