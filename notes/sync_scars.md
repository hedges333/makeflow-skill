# Syncing Synthetic Scars

Update `skills/makeflow/data/scars_registry.db` with the three maintainer targets and `make install`.

## Quick Run

```bash
make sync-scars
make import-scars MODEL=gemini-4-hydrogen-high WORKERS=8
make probe-scars MODEL=gemini-4-neon-high WORKERS=6
make test-sync-scars test-scar-candidate
make install
```

Run `agy models` to list valid `MODEL` identifiers for `agy` (Antigravity CLI).

## Steps

1. **`make sync-scars`** (`scripts/sync_synthetic_scars.py`): Fetches `https://syntheticscars.org/registry.json`, updates changed `ss_id` rows in place, inserts unseen scars, leaves `source = 'makeflow'` rows untouched, and prints pipeline counts.
2. **`make import-scars`** (`scripts/import_synthetic_scars.py`): Runs `agy --agent synthetic-scar-importer ... --print=<prompt>` for one isolated scar per call, validates Simple English fields with `ScarCandidateValidator`, and commits each row on the main thread without touching `socratic_probe`.
3. **`make probe-scars`** (`scripts/write_scar_probes.py`): Runs `agy --agent socratic-probe-writer ... --print=<prompt>` for one isolated scar per call, validates `socratic_probe` with `validate_socratic_probe()`, enriches metadata, and commits each row on the main thread.
4. **`make test-sync-scars test-scar-candidate` and `make install`**: Verifies the scar suites, validates the plugin, and installs updates into `~/.gemini/config/plugins/makeflow`.

## Options and Safety

- Pass `MODEL=<name>` (`--model <name>`) to select an `agy` model.
- Pass `WORKERS=<n>` (`--workers <n>`) to run up to `n` parallel single-scar calls (default `1`).
- Pass `TIMEOUT=<sec>` (`--timeout <sec>`) to set the per-call timeout in seconds (default `120`).
- Pass `MAX_RETRIES=<n>` (`--max-retries <n>`) to set retries per scar for validation or transient errors (default `2`).
- Pass `MAX_ERRORS=<n>` (`--max-errors <n>`) to set the consecutive transient error limit before aborting (default `5`).
- Pass `VERBOSE=1` (`--verbose`) to write buffered per-scar attempt traces to `stderr` without interleaving.
- Pass `PRINT_RESULT=1` (`--print-result`) to print each committed scar row as formatted JSON to `stdout`.
- Transient errors include timeouts, rate limits, network errors, and 5xx exits.
- Both scripts retry transient errors per scar up to `MAX_RETRIES` times.
- A circuit breaker stops a batch after repeated consecutive failures.
- If consecutive transient failures reach `MAX_ERRORS`, the script stops unstarted scars and drains active workers.
- Each committed scar resets the consecutive transient error counter to zero.
- Fatal launch, flag, model, and auth errors fail fast with zero retries.
- If you press `Ctrl+C`, the script sends `SIGTERM` to active workers. It preserves committed rows and rebuilds `scars_fts` (the SQLite search index).
