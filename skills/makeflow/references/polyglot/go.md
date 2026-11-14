# Polyglot Stack Guide: Go

This guide covers detection markers, test and lint commands, smoke test templates, and isolation rules for Go.

## 1. Detection Markers

Look for these files when scanning a Go module:

- `go.mod`, `go.sum`

## 2. Standard Commands

Run these commands inside the Go module directory:

- Targeted Test: `go test -v ./<package>/...`
- Format: `gofmt -s -l .` (write mode: `gofmt -s -w .`)
- Lint: `golangci-lint run`

## 3. Smoke Test Template

If no `*_test.go` file exists, create `smoke_test.go`:

```go
package main

import "testing"

func TestSmokeHarness(t *testing.T) {
	if false {
		t.Fatal("Smoke test failed")
	}
}
```

## 4. Test Isolation

Follow three isolation rules in Go tests:

1. Simulate SQL queries with `github.com/DATA-DOG/go-sqlmock` when testing without a database process.
2. For integration tests, point connection strings to `localhost:5432/<app>_test`, never to shared or production servers.
3. Create temporary test directories with `t.TempDir()`. Go deletes those directories automatically when each test finishes.

## Idiomatic Directory and Module Structure

- **Library and Domain Layer**: Place private application packages in `internal/<pkg>/` so the Go compiler blocks external module imports. Use `pkg/` for shared libraries.
- **Executable Entrypoints and Controllers**: Place each binary entrypoint in `cmd/<app>/main.go`. Keep `package main` limited to flag parsing, wiring, and signal handling.
- **Test Suite Placement and Naming**: Co-locate `*_test.go` files in the same directory as the package under test, not in a separate `tests/` tree.
- **Structural Anti-Patterns to Flag at Target 15**: Flag business logic inside `cmd/`, separate Java-style `tests/` folders for unit tests, or catch-all `util` packages.
