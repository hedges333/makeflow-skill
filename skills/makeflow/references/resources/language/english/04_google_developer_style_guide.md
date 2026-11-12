# Google Developer Documentation Style Guide

- **Source Standard**: *Google Developer Documentation Style Guide* (`developers.google.com/style`).
- **License and Attribution**: Licensed under the Creative Commons Attribution 4.0 International License (`CC-BY-4.0`), Copyright Google LLC. Adapted and distilled for software engineering agent workflows.

This module encapsulates the *Google Developer Documentation Style Guide* across seven normative parts.
Every rule is structured for deterministic parsing by human engineers and AI coding agents.

## Part I - Voice, Tone, and Global Developer Audience

Clear developer documentation speaks directly to the reader, states facts in the present tense, and avoids idioms that hinder translation.

### 1.1 Address the Reader as `you` and Ban Ambiguous `we`

1. Address the reader or calling agent in the second person (`you`).
2. Do not use first-person plural pronouns (`we`, `our`, `us`, `let us`) in instructions.
3. The pronoun `we` leaves it unclear whether the operator or the software performs the action.
4. Name the exact software component when the system performs an action.

- **Avoid**: `We validate the schema before we start the worker.`
- **Use**: `The startup hook validates the schema before you start the worker.`

### 1.2 Use Simple Present Tense for Deterministic Behavior

1. Describe deterministic software behavior in the simple present tense.
2. Avoid `will` and `would` when documenting current system behavior.
3. Reserve `will` for statements that distinguish a future event from the current state.

- **Avoid**: `The gate will return exit code 2 if the lock fails.`
- **Use**: `If the lock fails, the gate returns exit code 2.`

### 1.3 Place the Condition Before the Instruction

When a sentence puts the command before the condition, an agent or reader can run the command prematurely.

1. State the condition, prerequisite, or environment before the imperative command.
2. Separate the condition clause from the command with a comma.
3. Include `then` in conditional `if...then` statements when needed to prevent ambiguity.

- **Avoid**: `Delete the temporary state directory if the ticket status is complete.`
- **Use**: `If the ticket status is complete, delete the temporary state directory.`

### 1.4 Maintain Direct Tone and Global Localization Readiness

Global localization readiness (writing English that translates accurately into other languages) requires literal, culturally neutral phrasing.

1. Write in a direct, respectful, and objective tone without slang or marketing hype.
2. Do not use exclamation points (`!`), humor, idioms, or pop-culture metaphors.
3. Avoid subjective difficulty claims such as `easy`, `easily`, `simple`, `simply`, `just`, or `obviously`.
4. Avoid time-bound words (`currently`, `now`, `as of this writing`, `soon`, `eventually`) that make documentation stale.
5. Use standard American English spelling and serial commas (`A, B, and C`).

## Part II - Language, Grammar, and Punctuation Mechanics

Consistent mechanics eliminate lexical ambiguity across acronyms, contractions, plurals, pronouns, and numbers.

### 2.1 Abbreviations and Acronyms

An acronym (a word formed from initial letters) saves space only when the audience recognizes it immediately.

1. If an abbreviation or acronym is unfamiliar to the audience, spell it out on first mention.
2. Place the abbreviation in parentheses immediately after the spelled-out term.
3. Capitalize the spelled-out term only if it is a proper noun.
4. Do not use periods in acronyms or initialisms (`API`, `URL`, `US`).
5. Form plural acronyms by adding `s` or `es` without an apostrophe (`APIs`, `VMs`, `OSes`).
6. Never use an acronym or initialism as a verb.
7. Choose the indefinite article (`a` or `an`) based on pronunciation (`a SQL query`, `an SSH key`, `an SAP system`).

- **Avoid**: `Then ssh into your remote VM's using Data Manipulation Language (DML).`
- **Use**: `Use SSH to connect to your remote VMs with data manipulation language (DML).`

### 2.2 Replace Latin Abbreviations with Plain English

Latin abbreviations slow down non-native readers and break sentence boundary tokenizers.

1. Do not use `e.g.`, `i.e.`, `etc.`, `et al.`, `via`, `cf.`, or `viz.` in prose.
2. Replace every Latin abbreviation using the following table:

| Latin Abbreviation (Avoid) | Plain English Replacement (Use) |
| :--- | :--- |
| `e.g.` | `for example` or `such as` |
| `i.e.` | `that is` or `namely` |
| `etc.` | Delete `etc.` and list the specific items, or write `and similar items` |
| `et al.` | `and others` |
| `via` | `through`, `by`, or `using` |
| `cf.` | `compare` or `see` |
| `viz.` | `namely` |

### 2.3 Contractions and Explicit Agentic Override

- **Agentic Override**: The upstream *Google Developer Documentation Style Guide* permits common negation contractions such as `` `don't` ``, `` `isn't` ``, and `` `can't` `` to create an informal tone. In this specification, [`00_index_and_agentic_rationale.md`](./00_index_and_agentic_rationale.md) `Rule 8` overrides that guidance and bans all contractions in prose. Subword tokenizers split contractions into separate tokens, which weakens attention weights on negation markers during multi-turn reasoning.

1. Write `do not`, `is not`, `cannot`, and `you are` instead of `` `don't` ``, `` `isn't` ``, `` `can't` ``, and `` `you're` ``.
2. Never form possessive or noun-verb contractions such as `` `browser's` `` (meaning `browser is`).

### 2.4 Ban Anthropomorphism

Anthropomorphism (attributing human desires, thoughts, or senses to software or hardware) obscures deterministic system mechanics.

1. Never write that software or hardware `wants`, `thinks`, `knows`, `sees`, `expects`, `likes`, or `cares`.
2. State the exact technical condition, input, or return action instead.

- **Avoid**: `The Delimiter object tells the parser where it wants to split the string, and the server sees the new node.`
- **Use**: `The Delimiter object specifies where to split the string, and the server detects the new node.`

### 2.5 Ban Parenthetical Plurals and Ambiguous Pronouns

1. Do not put optional plurals in parentheses such as `file(s)`, `value(s)`, `port(s)`, or `child(ren)`.
2. Use the plural form (`files`) by default, or write `one or more files` when quantity precision matters.
3. Place a plural noun after a singular class name (`Intent objects` instead of `Intents`).
4. Follow demonstrative pronouns (`this`, `that`, `these`, `those`) with an explicit noun (`this configuration`, `these flags`).
5. Use `that` without a preceding comma for restrictive clauses (`the pod that failed`).
6. Use `which` preceded by a comma for nonrestrictive clauses (`the pod, which runs on port 8080, failed`).
7. Use singular `they` and `their` for gender-neutral human references instead of `he/she` or `(s)he`.

### 2.6 Numbers, Units, and Dates

1. Spell out all ordinal numbers in prose (`first`, `second`, `third` rather than `1st`, `2nd`, `3rd`).
2. Spell out cardinal numbers zero through nine in general prose (`four nodes`, `five minutes`).
3. Use numerals for numbers 10 and greater, versions (`version 3`), percentages (`40%`), and technical quantities (`8 pixels`).
4. Insert a space between a numeral and its unit (`64 GB`, `15 ms`), and never pluralize units (`64 GBs`).
5. Use commas as digit-group separators in numbers with four or more digits (`1,532,784 bytes`, `2,000 vertices`).
6. Format number ranges with an unspaced hyphen (`2012-2016`) and dimensions with a lowercase `x` (`192x192`).

## Part III - Formatting, Organization, Headings, Lists, Tables, and Notices

Structured visual hierarchy enables readers and agents to scan procedures, reference parameters, and hazard alerts without losing context.

### 3.1 Task-Oriented Headings and Hierarchy

1. Use sentence case for all document titles and section headings.
2. Start task-oriented headings with a bare infinitive verb (`Create an instance`, `Configure notebook settings`).
3. Do not start headings with gerund `-ing` verb forms (`Creating an instance`, `Migrating data`).
4. Use noun phrases for conceptual headings (`Migration overview`, `Authentication architecture`).
5. Prefix optional procedural sections with `Optional:` (`Optional: Customize your alias`).
6. Maintain strict hierarchical nesting (`#`, `##`, `###`) without skipping heading levels or leaving empty headings.
7. Introduce a group of subheadings with `The following sections describe...` rather than `These sections`.

### 3.2 Numbered Lists, Bulleted Lists, and Description Lists

1. Precede every list with a complete lead-in sentence that ends with a colon (`:`).
2. Do not use partial lead-in sentences that rely on list items to complete the grammar.
3. Use a numbered list when sequence or execution order matters, such as step-by-step procedures.
4. Use a bulleted list when item order does not matter, such as feature options or examples.
5. Use a description list (or bulleted run-in headings followed by a colon) for term-and-definition pairs.
6. Maintain parallel grammatical structure across all items in a list.
7. Capitalize the first word of every list item, and end complete-sentence items with a period.
8. Omit trailing periods on list items that consist of a single word, a code token, or a noun phrase without a verb.

### 3.3 Tables

1. Use a table when each item has three or more properties (`name`, `type`, and `description`).
2. Introduce every table with a complete sentence that references its position (`...as listed in the following table:`).
3. Use sentence case in table column headers, and do not end column headers with punctuation.
4. Do not merge cells (`colspan` or `rowspan`) or place a table inside a sentence.

### 3.4 Notice and Alert Callouts (`Note`, `Caution`, `Warning`)

1. Use notices sparingly so that critical alerts retain visual prominence.
2. Select the notice label that matches the severity of the condition:
   - **Note**: Provides useful supplementary context that is not required to complete the task.
   - **Caution**: Advises the operator to proceed carefully to prevent unexpected behavior or configuration drift.
   - **Warning**: Alerts the operator to an irreversible action, permanent data loss, or security vulnerability.
3. Never place two notices immediately adjacent to each other, and never hide a required procedural step inside a `Note`.

## Part IV - Links and Cross-References

Descriptive link text tells both human readers and autonomous agents the exact destination before they open the target.

### 4.1 Ban Vague Link Text and Require Descriptive Targets

1. Never use vague anchor text such as `click here`, `this file`, `link`, `read more`, `here`, or `this page`.
2. Never expose a raw URL as the visible link text in prose when a descriptive title applies.
3. Write link text that matches the exact title, heading, file basename, or code symbol of the destination.
4. Use `about` instead of `on` in cross-references (`For more information about indexes, see...`).
5. In workspace reports, format every file reference as a clickable `file://` link using the file basename.

- **Avoid**: `Click here to read more about this file at https://example.com/config.html.`
- **Use**: `For more information about configuration keys, see [00_index_and_agentic_rationale.md](file:///abs/path/00_index_and_agentic_rationale.md#L1-L20).`

## Part V - Code in Text, CLI Syntax, API Reference, and UI Elements

Strict typographic separation distinguishes literal machine syntax from surrounding natural-language instructions.

### 5.1 Inline Code Spans and Grammatical Treatment

1. Wrap code symbols in backticks (`` ` ``): class names, method names, function names, data types, and language keywords.
2. Wrap filenames, file paths, directories, environment variables, CLI commands, database columns, and literal values (`` `true` ``) in backticks.
3. Do not put quotation marks around an inline code span unless the quotes are part of the literal string.
4. Never inflect, pluralize, or add a possessive apostrophe to a code span.
5. Place a generic English noun after the code span and inflect that noun instead.

- **Avoid**: ``Call `animal.get` to inspect the `Config`'s fields before `POST`ing the `INT64`s.``
- **Use**: ``Call the `get` method to inspect the `Config` object fields before sending a `POST` request with `INT64` values.``

### 5.2 Placeholder Variables

A placeholder variable represents a string or value that the reader must replace before running a command or code snippet.

1. Format placeholders in uppercase letters with underscore delimiters (`<placeholder_name>` or `PLACEHOLDER_NAME`), such as `<project_id>` or `INSTANCE_NAME`.
2. Never use single letters (`x`), `foo`, `bar`, `baz`, or possessive prefixes (`MY_API_NAME`, `YOUR_KEY`) in placeholders.
3. Explain every placeholder immediately after the command block or code snippet.
4. For a single placeholder, write: `Replace <placeholder_name> with <description>.`
5. For multiple placeholders, write `Replace the following:` followed by a bulleted list in appearance order.

### 5.3 CLI Command Blocks, Flags, Arguments, and Exit Codes

1. Format multi-line or copyable CLI commands in fenced code blocks (` ```bash `).
2. Omit the `$` or `#` prompt from copyable command blocks so operators can copy commands directly.
3. Separate command output (`stdout`) into a distinct block after `The output is similar to the following:`.
4. Wrap CLI flag names (`--flag`, `--repo-root`) and subcommands in backticks in prose.
5. Break long multi-flag commands across lines using a trailing backslash (`\`) preceded by a space.
6. State whether each CLI argument or flag is required or optional, and document its default value.
7. Document every deterministic `exit code` (`0` for success and `2` for Vector 4 Reflex blocks).

### 5.4 API Reference, HTTP Status Codes, and Docstrings

1. Format HTTP methods in backticks followed by the noun `request` (``a `GET` request``, ``a `POST` request``).
2. Refer to HTTP responses as a `status code` with backticks (`an HTTP 400 Bad Request status code`).
3. Begin every `docstring` with a one-line summary in simple present tense (`Validates...`, `Returns...`).
4. Document each `parameter`, the `return` value, and each raised `exception` after the docstring summary.

### 5.5 UI Elements and User Interaction

1. Focus procedural steps on the task goal first, and cite UI labels when needed for clarity.
2. Put visible UI element names (buttons, menus, dialogs, windows, panes, tabs, and fields) in **bold** (`Click **Save**`).
3. If a UI label is all uppercase, write it in sentence case (`Click **Refresh**`).
4. Do not use UI labels as verbs (`In the **Name** field, enter an account name` instead of `**Name** the account`).
5. Use `in` for dialogs, fields, lists, menus, panes, and windows (`In the **File** menu, select **Open**`).
6. Use `on` for pages, tabs, and toolbars (`On the **Deployments** page, click **Add**`).
7. Use `select` and `clear` for checkboxes (not `check`, `uncheck`, or `deselect`), and use `enter` for text input.
8. Format keyboard shortcuts with spelled-out modifier keys and uppercase letters (`Press Control+S`, or `Command+S` on macOS).

## Part VI - Actionable Error Messages and Vector 4 Reflex Design

Error messages must diagnose the exact failure state and give the operator or agent a deterministic recovery path.

### 6.1 Four-Part Error Message and Vector 4 Reflex Structure

When authoring CLI diagnostics, validation failures, or Vector 4 Reflex gates (`emit_reflex`), include these four components:

1. **Exact Failure Cause (`TRIGGER`)**: State the exact invariant or condition that failed in active, objective prose.
2. **Offending Path or Value (`REJECTED`)**: Cite the exact file path, line number, flag name, or invalid input value that triggered the failure.
3. **Deterministic Fix Command (`REFLEX`)**: Provide the exact command or code change required to resolve the error.
4. **No Blame or Filler**: Never blame the user, omit `please` and `sorry`, and avoid humor.

- **Avoid**: `Sorry! Something went wrong with your file. Please check your settings and try again.`
- **Use**: `Configuration validation failed in .workflow/config.json:14: unknown key 'ci.timeout'. Remove 'ci.timeout' or replace it with 'ci.quiescence', then run 'make test-compat'.`

## Part VII - Inclusive Language and Comprehensive Developer A-Z Word List

Inclusive, single-meaning terminology removes cultural bias and prevents lexical drift across human and agent workflows.

### 7.1 Inclusive Engineering Terminology

Replace culturally bound, gendered, or ableist metaphors with concrete engineering terms from the following table:

| Non-Inclusive or Metaphorical Term (Avoid) | Inclusive Engineering Replacement (Use) |
| :--- | :--- |
| `whitelist` / `blacklist` / `graylist` | `allowlist` / `denylist` / `provisional list` (use as nouns, and rewrite verb forms as `allow` or `deny`) |
| `master` / `slave` | `primary` / `replica`, `main` / `worker`, `controller` / `worker`, or `active` / `standby` |
| `sanity check` / `sane` | `coherence check`, `smoke test`, `confidence check`, `preliminary check`, or `valid` |
| `dummy` / `dummy variable` | `placeholder variable`, `stub`, `indicator variable`, or `sample value` |
| `black-box` / `white-box` / `gray-box` | `opaque-box` / `clear-box` / `translucent-box` testing (or `synthetic` / `introspective` monitoring) |
| `grandfathered` / `grandfather clause` | `exempt` or `legacy` |
| `man-in-the-middle (MITM)` | `on-path attacker` or `person-in-the-middle (PITM)` |
| `man-hours` / `manpower` / `manned` | `person-hours` / `workforce` or `staff` / `staffed` or `crewed` |
| `cripple` / `lame` / `blind to` | `slow down` or `degrade` / `ineffective` / `ignore` or `unaware of` |
| `blast radius` / `war room` | `affected area` or `spatial impact` / `situation room` or `incident-management team` |
| `pets vs. cattle` / `tribal knowledge` | `persistent versus dynamic` / `institutional knowledge` or `shared team knowledge` |

### 7.2 Comprehensive Developer Style Guide A-Z Word List

Apply the following high-frequency word usage rules from the upstream *Google Developer Documentation Style Guide* word list:

| Term or Phrase (Avoid / Caution) | Normative Replacement and Usage Rule (Use) |
| :--- | :--- |
| `abort` | Avoid in general prose. Use `stop`, `cancel`, `exit`, or `end` (reserve `abort` for Linux `SIGABRT` signals). |
| `above` / `below` | Do not use for document position or version ranges. Use `preceding` or `earlier` instead of `above`, and `following` or `later` instead of `below`. |
| `aka` | Do not use. Write `also known as` or place the alternative term in parentheses. |
| `allows you to` / `enables you to` | Do not use. Write `lets you` instead (`The API lets you query logs`). |
| `and/or` | Avoid in prose. Write `A or B, or both`. |
| `argument` vs. `parameter` | Use `parameter` for the variable in a function definition or query string, and `argument` for the value passed at call time. |
| `as` / `since` (for causation) | Do not use `as` or `since` to mean causation because both words also mean time. Write `because` for causation. |
| `as of this writing` / `currently` / `now` | Avoid in timeless documentation. State what the software supports today in simple present tense (`Windows is not supported`). |
| `authenticate` vs. `authorize` | Users `authenticate` their identity (`authenticate against`), and authenticated users `authorize` client requests to access resources. |
| `automatically` | Do not use `automagically` or `autoupdate`. Write `automatically` only when distinguishing an automatic action from a manual step. |
| `check` / `uncheck` / `deselect` | Use `select` to mark a checkbox and `clear` to clear a checkbox (not `check`, `uncheck`, or `deselect`). Use `verify` or `confirm` for validation checks. |
| `click` / `click on` / `click here` | Write `click` (never `click on` or `click here`) for mouse targets, `tap` for touch targets, and `press` for keyboard keys. |
| `comprise` | Do not use. Write `consist of`, `contain`, or `include`. |
| `config` / `repo` / `regex` | Do not use clipped jargon in prose. Spell out `configuration`, `repository`, and `regular expression` outside literal code spans. |
| `deprecate` | Means to warn against using an item that will lose support. Do not use `deprecated` to mean `removed`, `deleted`, or `shut down`. |
| `directory` vs. `folder` | Use `directory` in command-line and file-path contexts, and `folder` in GUI contexts. When in doubt, default to `directory`. |
| `disable` | Do not use `disabled` to mean broken. Use `turn off`, `deactivate`, or `unavailable` for UI states and features. |
| `easy` / `easily` / `simple` / `simply` | Delete subjective difficulty claims (`easy`, `easily`, `simple`, `simply`, `obviously`). State the exact steps instead. |
| `enable` / `turn on` | Use `enable` or `turn on` consistently when activating a feature. Never use `enable` to mean `lets you`. |
| `ensure` vs. `verify` | Use `verify` or `confirm` when instructing the operator to check a state (`Verify that port 8080 is open`). Avoid vague `ensure`. |
| `execute` vs. `run` | Prefer the simpler verb `run` for commands, scripts, queries, and tests instead of `execute`. |
| `file name` vs. `filename` | Write `filename` as one word, `file system` as two words, and `path` instead of `filepath` or `pathname`. |
| `foo` / `bar` / `baz` | Do not use metasyntactic jargon in examples. Use descriptive domain names or `<placeholder_name>` tokens. |
| `for instance` | Do not use `for instance` because it collides with compute `instance` nouns. Write `for example` or `such as`. |
| `hover` | Do not use `hover over`. Write `hold the pointer over` when waiting for a UI reaction, or `point to` for focus. |
| `impact` (as a verb) | Do not use `impact` as a verb. Use `affect` as a verb and `impact` only as a noun. |
| `in order to` | Replace `in order to` with `to` unless the longer phrase is required to prevent ambiguity. |
| `instantiate` | Avoid ornate nominalizations when `create an instance of` is clearer for the audience. |
| `just` | Delete `just` as a filler word. If a restriction is intended, write `only`. |
| `k8s` | Do not use `k8s` in prose. Write `Kubernetes`. |
| `kill` / `terminate` | Avoid in general prose. Use `stop`, `cancel`, `exit`, or `end` unless documenting a literal POSIX `kill` or `SIGTERM` command. |
| `leverage` | Do not use `leverage` to mean `use`. Write `use`, `build on`, or `take advantage of`. |
| `login` / `log in` / `sign in` | Prefer `sign in` (verb) and `sign-in` (noun or adjective). Use `log in` (verb) and `login` (noun) only when matching literal tool text. |
| `may` vs. `can` vs. `might` | Do not use `may` for ability or possibility. Use `can` for capability or permission, `might` for uncertain possibility, and `must` for requirements. |
| `once` vs. `after` | Do not use `once` to mean `after` because `once` also means `one time`. Write `after` for temporal sequence. |
| `only` | Place `only` immediately before the word or phrase it modifies (`query only the table` rather than `only query the table`). |
| `parse` | Use `parse` specifically for syntactic analysis of structured text or tokens, not as a synonym for `read` or `understand`. |
| `please` | Omit `please` from procedural instructions and error messages. |
| `populate` | Use `populate` when a system process fills a table or data structure, and `fill in` when a person enters form fields. |
| `recommend` / `should` | Avoid ambiguous `should`. Write `we recommend` only in organizational policy, or state the direct imperative action and its benefit. |
| `seamless` / `robust` / `transparent` | Delete marketing adjectives (`seamless`, `robust`, `cutting-edge`) and ambiguous `transparent` (write `automatic` or `visible`). |
| `specify` vs. `value` | Write `specify a value` or `set the value` rather than leaving `this` or `value` ungrounded. |
| `spin up` | Avoid colloquial `spin up` for virtual machines or services, and write `create` or `start` instead. |
| `sync` | Acceptable short form of `synchronize` without a trailing period. |
| `tarball` / `untar` / `unzip` | Do not use `tarball`, `untar`, `unzip`, `unarchive`, or `uncompress`. Write `tar file` and `extract`. |
| `tl;dr` / `ymmv` / `RTFM` | Never use internet slang abbreviations. Write `To summarize`, `Your results might vary`, or `For more information, see...`. |
| `type` vs. `enter` | Prefer `enter` over `type` because users can enter text by pasting, scripting, or voice input. |
| `URL` | Write `URL` (all caps, no periods). Precede with `a` (`a URL`), and format clickable browser targets with descriptive link text. |
| `utilize` | Do not use `utilize` to mean `use`. Reserve `utilization` for resource consumption metrics (`CPU utilization`). |
| `vice versa` / `vs.` | Do not use `vice versa` (state the reciprocal action explicitly), and spell out `versus` instead of `vs.` in prose. |
| `when` vs. `if` vs. `whether` | Use `if` for a conditional branch that might not occur, `when` for an inevitable event, and `whether` for two alternatives (`whether A or B`). |
| `while` | Use `while` only for concurrent time, never for contrast. Use `although` or `whereas` for contrast. |
