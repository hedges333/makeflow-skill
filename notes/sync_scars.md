# Syncing synthetic scars with the three maintainer scripts

This guide explains how to update `skills/makeflow/data/scars_registry.db` using three single-purpose scripts and `make install`.

## 1. Sync upstream scars

Run `make sync-scars` to fetch `https://syntheticscars.org/registry.json` and update SQLite (a local relational database file):

```bash
make sync-scars
```

You can also invoke the script directly with Python:

```bash
python3 scripts/sync_synthetic_scars.py
```

The sync script performs four deterministic steps:

1. Backfills a 4-field `raw_hash` (a SHA-256 content digest) once so existing transformed scars stay intact.
2. Updates matched `ss_id` rows in place when raw prose changes, keeping the Makeflow `id` and resetting transformed fields to `NULL`.
3. Sorts unseen `ss_id` records ascending and derives deterministic Makeflow IDs while leaving `source = 'makeflow'` rows untouched.
4. Enriches metadata, rebuilds `scars_fts` (the SQLite full-text search index), updates `meta.db_size_bytes`, and prints scar counts.

## 2. Write Simple English fields

Run `make import-scars` to transform pending scars one scar at a time with `agy` (Antigravity CLI):

```bash
make import-scars
```

Pass `MODEL=<name>` or `--model <name>` to forward an explicit model override to `agy`:

```bash
make import-scars MODEL=gemini-2.5-pro
python3 scripts/import_synthetic_scars.py --model gemini-2.5-pro
```

Python validates each JSON response with `ScarCandidateValidator`, retries validation failures, and commits each valid scar immediately. Press `Ctrl+C` between scars to stop cleanly while preserving committed rows and rebuilding `scars_fts`.

## 3. Write Socratic probes

Run `make probe-scars` to write `socratic_probe` (a short diagnostic question prompt) for scars with complete Simple English fields:

```bash
make probe-scars
```

Pass `MODEL=<name>` or `--model <name>` to forward an explicit model override to `agy`:

```bash
make probe-scars MODEL=gemini-2.5-pro
python3 scripts/write_scar_probes.py --model gemini-2.5-pro
```

Python validates each probe with `validate_socratic_probe()`, enriches metadata, commits immediately, and rebuilds `scars_fts` on exit.

## 4. Test and install updated scars

1. Run the scar pipeline and validator test suites:

```bash
make test-sync-scars test-scar-candidate
```

2. Run `make install` to validate the plugin and update `~/.gemini/config/plugins/makeflow`:

```bash
make install
```
