# Simple English Synthetic Scar Import Goal Directive

This document provides the `/goal` directive to transform pending synthetic scars (recorded engineering failure patterns) into Simple English.

The supervisor agent queries one pending scar ID at a time from SQLite (a local relational database file). This query omits `null` columns so the `synthetic-scar-importer` subagent (an isolated worker agent) never sees `socratic_probe: null`.

## Operator `/goal` Directive

```text
/goal Transform all pending upstream synthetic scars in skills/makeflow/data/scars_registry.db into Simple English one scar at a time.

Follow these steps in a loop until zero pending scars remain:

1. Query the next pending scar ID without displaying null fields:
   sqlite3 skills/makeflow/data/scars_registry.db "SELECT id FROM scars WHERE wound IS NULL OR trap IS NULL OR circuit_breaker IS NULL OR reflex IS NULL ORDER BY id LIMIT 1;"
2. If the query returns a scar ID, invoke the synthetic-scar-importer subagent with only that scar ID string.
3. Wait for synthetic-scar-importer to validate the candidate JSON with scripts/test_scar_candidate.py and import it with:
   python3 scripts/sync_synthetic_scars.py --db-path skills/makeflow/data/scars_registry.db --import-simple-english <temporary_json_file_path>
4. Repeat steps 1 through 3 until the query returns no rows.
5. When zero pending scars remain, verify the database status and transformations:
   python3 scripts/sync_synthetic_scars.py --db-path skills/makeflow/data/scars_registry.db --status
   python3 scripts/sync_synthetic_scars.py --db-path skills/makeflow/data/scars_registry.db --verify-transformed
```
