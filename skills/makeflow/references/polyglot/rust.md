# Polyglot Stack Guide: Rust

This guide covers detection markers, test and lint commands, smoke test templates, and isolation rules for Rust.

## 1. Detection Markers

Look for these files when scanning a Rust crate or workspace:

- `Cargo.toml`, `Cargo.lock`

## 2. Standard Commands

Run these commands inside the crate or workspace root:

- Targeted Test: `cargo test -p <package>` or `cargo test --test <name>`
- Format: `cargo fmt -- --check`
- Lint: `cargo clippy --all-targets -- -D warnings`

## 3. Smoke Test Template

If no integration test exists, create `tests/smoke_test.rs`:

```rust
#[test]
fn test_smoke_harness() {
    assert!(true);
}
```

## 4. Test Isolation

Follow three isolation rules in Rust tests:

1. Create temporary directories with the `tempfile` crate so Rust deletes them automatically on drop.
2. Place database access behind traits and pass mock implementations in unit tests.
3. For database integration tests, use SQLite in-memory connections or local containers through `testcontainers-rs`.

## Idiomatic Directory and Module Structure

- **Library and Domain Layer**: Expose reusable types and functions from `src/lib.rs` and domain submodules so benchmarks and integration tests can import them.
- **Executable Entrypoints and Controllers**: Keep `src/main.rs` and `src/bin/*.rs` as thin CLI wrappers that parse arguments and call `src/lib.rs`.
- **Test Suite Placement and Naming**: Place private unit tests in inline `#[cfg(test)] mod tests` blocks and black-box integration suites in `tests/*.rs`.
- **Structural Anti-Patterns to Flag at Target 15**: Flag core domain logic trapped inside `src/main.rs` where `tests/*.rs` cannot import it, or unguarded `#[cfg(test)]` modules.

## Code Documentation Standards and Tree Assembly

- **Project Convention Detection**: Inspect `Cargo.toml` (`[package.metadata.docs.rs]`) and `src/lib.rs` for `#![warn(missing_docs)]` at Target 15 (`SCAR-PROC-159`).
- **Idiomatic Doc Comment Standard**: Write `rustdoc` `///` item comments and `//!` module headers with `# Errors`, `# Panics`, and `# Examples` sections.
- **Documentation Tree Assembly Command**: Run `RUSTDOCFLAGS="-D warnings" cargo doc --no-deps --all-features` and `cargo test --doc` to verify the tree.
- **Documentation Anti-Patterns to Flag at Target 40**: Flag `pub fn` items returning `Result` without `# Errors`, broken intra-doc links, or failing doctests.
