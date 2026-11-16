# Changelog

All notable changes to this project appear in this file.
The format follows Keep a Changelog and Semantic Versioning.
Historical ticket records before the first tagged release live in `.workflow/history.md`.

## [Unreleased]

### Added

- Add `CHANGELOG.md` and the two-stage Target 90 release workflow.
- Add `lib/target90_guard.py` for shared hook and MCP Target 90 gating.
- Add interactive Target 90 release prompting for SemVer patch, minor, or major cuts.
- Add Target 90 ticket resolution comments for GitHub issues and local tickets.
- Add 21 polyglot TDD guides, code documentation standards, and tree verification checks.
- Add stacked SQLite Synthetic Scars search (`workflow_query_scars`) and local scar capture.
- Add AST scrubbing, cryptographically sealed TDD receipts (`witness-red`, `verify-green`), and turn boundary guards.
- GH-228: Enforce ticket-ID prefixes on Target 90 changelog bullets with extensible Strategy and Registry support.
- GH-232: Add SCAR-ARCH-09 upstream root-cause scar, SCAR-ARCH-0009 SQLite row, and planning, TDD, and review directives.

### Changed

- Load `serverInfo.version` dynamically from `./plugin.json` in `mcp/server.py`.
- Set author metadata in `./plugin.json` to Mark Hedges.
- Align hook and MCP commit gates on `docs(changelog):`, `docs(release):`, `chore(release):`, and `docs(scars):`.
- Change terminal titles to start with the bare directory name and trailing slash instead of the project name.
- GH-212: Clarify optional query description when callers provide filters in workflow_query_scars MCP schema.

### Fixed

- Route feature commits touching `CHANGELOG.md` to Target 50 instead of Target 90.
- Allow `ci.quiescenceSeconds=0` and `MAKEFILE_WORKFLOW_CI_QUIESCENCE_SECONDS=0` across hooks, configuration loaders, and `workflow_poll_ci`.
- Run MCP server tests through the `unittest` runner in `make test-mcp` and CI workflows, and split `TestMcpServer` into discrete methods.
