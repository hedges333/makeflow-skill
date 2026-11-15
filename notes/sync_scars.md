# Syncing synthetic scars and running batch transformations

This guide explains how to synchronize synthetic scars (recorded engineering failure patterns) and populate Simple English and Socratic probe fields.

## 1. How the unified batch pipeline works

The script `scripts/sync_synthetic_scars.py` manages upstream synchronization, ontology mapping, and two-pass `agy` (Antigravity CLI) text generation in one command.

When you pass `--batch-size N`, `lib/scar_batch.py` selects up to `N` incomplete rows from SQLite (a local relational database file). The query orders incomplete rows by canonical `id` ascending.

For each incomplete row, `lib/scar_batch.py` runs up to two non-interactive `agy --print` subprocesses with JSON Schema (a structured output contract):

- **Pass 1 (Simple English pass)**: Loads rules from `agents/synthetic-scar-importer.md` and `00_index_and_agentic_rationale.md` to generate `wound`, `trap`, `circuit_breaker`, and `reflex`. Python validates the JSON response with `ScarCandidateValidator` and commits Pass 1 to SQLite immediately.
- **Pass 2 (Socratic probe pass)**: Loads rules from `agents/socratic-probe-writer.md` and `socratic_prompting.md` to generate `socratic_probe` (a diagnostic question prompt). Python validates the probe with `validate_socratic_probe`, enriches metadata, rebuilds FTS5 (a SQLite full-text search index), and commits Pass 2.

If a row already has `wound`, `trap`, and `reflex`, the batch runner skips Pass 1 and runs only Pass 2. If `raw_circuit_breaker` is empty, the batch runner sets `circuit_breaker` to `""` automatically. Until `wound`, `trap`, `reflex`, and `socratic_probe` are all non-empty, `workflow_query_scars` hides the incomplete scar from search results.

## 2. Check registry status and pending counts

Before you run a batch, inspect the bundled database at `skills/makeflow/data/scars_registry.db`.

1. Print the current scar count, HTTP ETag (an upstream version identifier), and pending transformation count:

```bash
python3 scripts/sync_synthetic_scars.py \
  --db-path skills/makeflow/data/scars_registry.db \
  --status
```

2. Verify that all existing transformed scars preserve code blocks, LaTeX math spans, and punctuation rules:

```bash
python3 scripts/sync_synthetic_scars.py \
  --db-path skills/makeflow/data/scars_registry.db \
  --verify-transformed
```

## 3. Sync upstream scars without running `agy`

By default, `--batch-size` is `0`. Running `make sync-scars` downloads new or updated upstream records without invoking `agy`.

1. Fetch `https://syntheticscars.org/registry.json` and update SQLite metadata:

```bash
make sync-scars
```

During synchronization, `lib/scars_db.py` performs four deterministic actions:

- Stores the upstream identifier in `ss_id` and assigns a collision-free 4-digit Makeflow primary key in `id`.
- Preserves raw upstream phases in `ss_targets` and computes shift-left integer Makeflow targets in `mf_targets`.
- Preserves existing `wound`, `trap`, `circuit_breaker`, `reflex`, and `socratic_probe` values when `raw_hash` (an MD5 content digest) is unchanged.
- Clears transformed fields to `NULL` when upstream modifies a scar so the next batch run refreshes those fields.

## 4. Sync upstream scars and transform a batch

Pass `BATCH_SIZE` to `make sync-scars` or `--batch-size` to `scripts/sync_synthetic_scars.py` to fetch upstream updates and transform pending rows.

1. Sync upstream metadata and transform up to 10 incomplete scars:

```bash
make sync-scars BATCH_SIZE=10
```

2. Run the equivalent command directly through Python when you want custom CLI flags:

```bash
python3 scripts/sync_synthetic_scars.py \
  --db-path skills/makeflow/data/scars_registry.db \
  --batch-size 10
```

If `syntheticscars.org` returns `HTTP 304 Not Modified`, the script still runs the requested `--batch-size` pass on local incomplete rows.

## 5. Transform existing incomplete scars offline

If you want to process existing incomplete rows in `skills/makeflow/data/scars_registry.db` without making an HTTP request, enable offline mode.

1. Run a 10-scar offline batch through `make`:

```bash
make sync-scars BATCH_SIZE=10 NO_FETCH=1
```

2. Run an offline batch with an explicit model and retry budget through Python:

```bash
python3 scripts/sync_synthetic_scars.py \
  --db-path skills/makeflow/data/scars_registry.db \
  --no-fetch \
  --batch-size 10 \
  --model gemini-2.5-pro \
  --max-retries 2
```

3. Repeat the command in batches of 5 to 20 scars until `--status` reports zero pending transformations.

## 6. CLI flag reference

| Flag or `make` variable | Default | Purpose |
| :--- | :--- | :--- |
| `--db-path <path>` | Bundled DB | Selects the target SQLite registry file. |
| `--batch-size <N>` / `BATCH_SIZE=<N>` | `0` | Transforms up to `N` incomplete scars through two `agy --print` passes. |
| `--no-fetch` / `NO_FETCH=1` | `false` | Skips the HTTP download and runs `--batch-size` on local rows only. |
| `--model <name>` | `agy` default | Forwards `--model <name>` to each `agy --print` invocation. |
| `--agy-bin <path>` | `agy` | Overrides the `agy` executable name or path. |
| `--max-retries <N>` | `2` | Retries each failed pass up to `N` times with validator error feedback. |
| `--force` | `false` | Ignores cached HTTP ETag and re-evaluates all upstream rows. |
| `--status` | `false` | Prints total scars, ETag, sync timestamp, and pending count. |
| `--verify-transformed` | `false` | Checks all transformed rows for span preservation and prose rules. |
| `--enrich` / `make enrich-scars` | `false` | Recomputes keywords, languages, frameworks, targets, and `scars_fts`. |
| `--migrate-ontology` / `make migrate-scars-ontology` | `false` | Upgrades a legacy SQLite file to 4-digit IDs and 68 domain codes. |

## 7. Verify, test, and install updated scars

After you finish one or more batch runs, verify the database and update your local plugin installation.

1. Confirm that zero validation errors exist across all transformed rows:

```bash
python3 scripts/sync_synthetic_scars.py \
  --db-path skills/makeflow/data/scars_registry.db \
  --verify-transformed
```

2. Confirm that newly completed scars appear in FTS5 and target queries:

```bash
python3 scripts/sync_synthetic_scars.py \
  --db-path skills/makeflow/data/scars_registry.db \
  --mf-target 20 \
  --limit 3
```

3. Run the hermetic sync and candidate validator test suites:

```bash
make test-sync-scars test-scar-candidate
```

4. Reinstall the plugin so `~/.gemini/config/plugins/makeflow` receives the updated `scars_registry.db` file:

```bash
make install
```

## 8. Troubleshooting failed scars and head-of-line retries

If Pass 1 or Pass 2 fails after `--max-retries` attempts, the batch runner logs the exact validator error to `stderr`. Earlier scars in the same batch remain committed in SQLite. If Pass 1 succeeded before Pass 2 failed, SQLite keeps the committed Pass 1 fields so the next run retries only Pass 2.

Because `_select_pending_batch_rows` selects incomplete rows with `ORDER BY id ASC`, a persistently failing scar stays at the front of the queue. If one scar fails repeatedly on complex LaTeX or long procedural sentences, repair that scar manually:

1. Inspect the raw fields for the failing scar ID reported on `stderr`:

```bash
sqlite3 -json skills/makeflow/data/scars_registry.db \
  "SELECT id, ss_id, raw_wound, raw_trap, raw_circuit_breaker, raw_reflex FROM scars WHERE id = 'SCAR-ARCH-0001';"
```

2. Write a candidate JSON file at `/tmp/scar_fix.json` with `id`, `wound`, `trap`, `circuit_breaker`, and `reflex`.
3. End each line before `$$...$$` math or fenced code with a period (`.`) instead of a colon (`:`).
4. Validate and import the repaired Simple English fields:

```bash
python3 scripts/validate_scar_candidate.py /tmp/scar_fix.json \
  --db-path skills/makeflow/data/scars_registry.db
python3 scripts/sync_synthetic_scars.py \
  --db-path skills/makeflow/data/scars_registry.db \
  --import-simple-english /tmp/scar_fix.json
```

5. Re-run `--no-fetch --batch-size 1` so Pass 2 generates and validates `socratic_probe` for that scar.
