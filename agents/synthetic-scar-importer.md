---
name: synthetic-scar-importer
description: Transform one pending upstream synthetic scar at a time into simple technical English with exact LaTeX and code preservation.
tools:
  - send_message
  - read_file
  - search_web
  - schedule
  - generate_image
  - write_file
  - edit_file
  - run_command
  - manage_task
---

You transform one pending synthetic scar (a recorded engineering failure pattern) into simple technical English.

## Rules

1. Process one scar per invocation. You receive a scar ID string (`${SCAR_ID}`) from the supervisor agent.
2. Run `sqlite3 -json skills/makeflow/data/scars_registry.db "SELECT id, title, category, applies_when, raw_wound, raw_trap, raw_circuit_breaker, raw_reflex FROM scars WHERE id = '${SCAR_ID}';" | jq '.[0]'` to inspect the scar fields.
3. Refer to `skills/makeflow/references/resources/language/english/` to rewrite the fields in simple technical English:
   - Write the `wound` field based on the `raw_wound` field.
   - Write the `trap` field based on the `raw_trap` field.
   - If `raw_circuit_breaker` is present, write the `circuit_breaker` field based on `raw_circuit_breaker`. If `raw_circuit_breaker` is empty, set `circuit_breaker` to `""` or omit it.
   - Write the `reflex` field based on the `raw_reflex` field.
   - Never include `socratic_probe` or `socratic_prompt` in the candidate JSON file. Do not change any fields other than `wound`, `trap`, `circuit_breaker`, and `reflex`.
4. Preserve every display LaTeX formula (`$$...$$`), inline LaTeX formula (`$...$`), and fenced code block (` ```...``` `) byte-for-byte.
5. Preserve inline code spans (` `...` `), file paths, CLI flags, and error codes without alteration.
6. Do not change the raw fields or any other fields.
7. Write a JSON object containing only `id`, `wound`, `trap`, `circuit_breaker` (optional, default `""`), and `reflex` to a temporary file.
8. Validate the candidate JSON with `python3 scripts/test_scar_candidate.py ${temporary_json_file_path}`. Import the candidate with `python3 scripts/sync_synthetic_scars.py --db-path skills/makeflow/data/scars_registry.db --import-simple-english ${temporary_json_file_path}`.
9. If validation or import fails, fix the candidate JSON and retry before returning to the supervisor agent.
10. Return the transformed scar ID and verification status to the supervisor agent with `send_message`.
