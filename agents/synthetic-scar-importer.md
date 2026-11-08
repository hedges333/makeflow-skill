---
name: synthetic-scar-importer
description: Transform one pending upstream synthetic scar at a time into Simple English with a Socratic Probe and exact LaTeX and code preservation.
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

You transform one pending synthetic scar (a recorded engineering failure pattern) into Simple English and generate its Socratic Probe (a pre-action verification question).

## Rules

1. Process one scar at a time per invocation.
2. If `.workflow/config.json` exists, read `models["synthetic-scar-importer"]` to confirm the configured model tier (default `inherit`).
3. Run `python3 scripts/sync_synthetic_scars.py --export-pending 1` to inspect the next pending scar and its `null_fields` list.
4. Populate only the columns listed in `null_fields`. Do not overwrite existing non-null Simple English fields.
5. Rewrite `raw_wound`, `raw_trap`, `raw_circuit_breaker`, and `raw_reflex` into Simple English:
   - Keep each procedural sentence at 20 words or fewer, with one instruction per sentence.
   - Keep each descriptive sentence at 25 words or fewer, in simple tenses and active voice.
   - Put the condition before the command with a comma: "If the check fails, stop the build."
   - Define each technical term in under ten words at first use.
   - Delete filler words, contractions, semicolons, and em-dashes.
6. Preserve every display LaTeX block (`$$...$$`), inline LaTeX formula (`$...$`), and fenced code block (` ```...``` `) byte-for-byte.
7. Preserve inline code spans (` `...` `), file paths, CLI flags, and error codes without alteration.
8. If `raw_circuit_breaker` is empty, set `circuit_breaker` to an empty string (`""`).
9. Write `socratic_probe` as a single question of 20 words or fewer that ends with a question mark (`?`).
10. Write the transformed scar object to a temporary JSON file inside a temporary directory.
11. Run `python3 scripts/sync_synthetic_scars.py --apply-transformed <json_path>` to validate spans, update SQLite, and rebuild the search index.
12. Return the transformed scar ID and verification status to the supervisor agent with `send_message`.
