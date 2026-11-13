# Technical English language guidelines: index and agentic rationale

This directory defines the open technical English specification for documentation, plans, commit messages, and pull request descriptions.

Each rule derives from public-domain United States government standards and open-source technical writing guides.
You can distribute and adapt these files under the repository MIT license.

## 1. AI transformer self-attention mechanics and agentic rationale

Large language models use scaled dot-product self-attention (a mechanism that weighs token relationships) to parse instructions and context:

$$\text{Attention}(Q, K, V) = \text{softmax}\left(\frac{QK^T}{\sqrt{d_k}}\right)V$$

In this formula, $Q$ represents query vectors, $K$ represents key vectors, $V$ represents value vectors, and $d_k$ represents key dimension.
Each token distributes a finite probability mass of attention weights across preceding tokens in the context window.
Ambiguous grammar, long clause distance, nested parentheticals, and lexical polysemy (one word carrying multiple meanings) dilute attention weights across competing interpretations.
Controlled technical English removes structural ambiguity so AI agents and human operators parse one deterministic meaning.

1. Short subject-verb-object clause distance keeps actors, actions, and targets adjacent inside local attention heads.
2. Eliminating nested parenthetical remarks prevents attention dispersion across interrupted syntactic frames.
3. Eliminating polysemy (using one word for one meaning) prevents lexical drift across multi-turn reasoning chains.
4. Condition-first ordering allows an agent to prune non-matching execution branches before reading the command token sequence.
5. Deleting zero-information filler words reduces prompt and state token consumption by 25 to 40 percent.

## 2. Core quantitative structural rules

Follow these ten quantitative structural rules across all technical prose:

1. Keep procedural sentences at 20 words or fewer, with one instruction per sentence.
2. Keep descriptive sentences at 25 words or fewer, with one main fact per sentence.
3. Keep paragraphs at 6 sentences or fewer, and begin each paragraph with a topic sentence.
4. Limit noun clusters (consecutive nouns acting as modifiers) to 3 words or fewer.
5. Write in the active voice and use simple tenses (simple present, simple past, simple future).
6. If a step depends on a condition, place the condition before the command with a comma.
7. Define every new technical term in under 10 words at first use.
8. Enforce Rule 8 (contraction ban): do not use contractions (write `do not` instead of `don't`).
9. Enforce Rule 9 (punctuation ban): never use semicolons or em-dashes in prose.
10. Replace Latin abbreviations (`e.g.`, `i.e.`, `etc.`, `via`) with plain English phrases.

## 3. Operational modes, token preservation, and validator word-counting rules

### 3.1 Operational modes

Two operational modes govern vocabulary selection across the specification:

- **Plain Mode** (default): Permits standard software engineering nouns (`webhook`, `endpoint`, `mutex`, `component`, `interface`, `parameter`, `argument`) and verbs (`compile`, `deploy`, `merge`, `delete`, `implement`).
- **Strict Mode**: Restricts general vocabulary to the simple-word replacement dictionary in `01_us_federal_plain_language.md` and requires explicit definitions for all domain terms.

### 3.2 Byte-for-byte code and LaTeX token preservation

When you edit, compress, or paraphrase technical text, preserve all executable and mathematical syntax byte-for-byte:

1. Preserve fenced code blocks (` ```...``` `) and inline code spans (`` `...` ``) without alteration.
2. Preserve display LaTeX formulas (`$$...$$`) and inline LaTeX formulas (`$...$`) without alteration.
3. Preserve file paths, CLI flags, environment variables, and error codes exactly as written.

### 3.3 Validator tokenization and word-counting rules

The validator in `scripts/test_scar_candidate.py` enforces deterministic sentence splitting and word counting through `ScarCandidateValidator` and three helper functions:

| Validator component | Exact tokenization behavior | Word-count impact |
| :--- | :--- | :--- |
| `mask_code_and_math_spans` | Replaces fenced code (`__FENCED_CODE__`), inline code (`__INLINE_CODE__`), display math (`__DISPLAY_MATH__`), and inline math (`__INLINE_MATH__`) with atomic placeholder tokens. | Prevents periods, question marks, or semicolons inside code or math from splitting sentences or triggering prose punctuation errors. |
| `split_into_sentences` | Masks protected spans first, then splits text on the regular expression `(?<=[.!?])(?:\s+|$)`. | Treats each sentence ending with `.`, `!`, or `?` as an independent unit for word-limit checks. |
| `count_words_in_sentence` | Splits the masked sentence on whitespace and strips `_PUNCTUATION_CHARS` (`string.punctuation + "«»“”‘’"`) from each token. | Counts each masked span (`__INLINE_CODE__`, `__INLINE_MATH__`), hyphenated compound (`read-only`), or solid token (`15ms`) as 1 word. Counts spaced numbers with units (`15 ms`, `64 GB`) as 2 words. Strips standalone punctuation tokens to 0 words. |
| `ScarCandidateValidator` | Validates `wound` and `trap` at 25 words or fewer per sentence, and `circuit_breaker` and `reflex` at 20 words or fewer per sentence. | Validates `socratic_probe` at up to 3 question sentences, where each sentence ends with `?` and contains 20 words or fewer. |

## 4. Algorithmic sentence-splitting and compression recipes

When a sentence exceeds the 20-word procedural limit or 25-word descriptive limit, apply these five deterministic splitting algorithms.

### 4.1 Splitting compound causal (`because`, `as a result`) sentences

If a descriptive sentence joins a failure event and its root cause with `because`, split the clause into two sentences:

1. State the observed symptom or failure event in the first sentence.
2. Begin the second sentence with the explicit subject and state the cause or consequence.

- **Before (27 words)**: `The background worker process terminated unexpectedly with exit code 137 during the nightly batch migration because the in-memory cursor buffered all ten million rows without streaming pagination.`
- **After (12 words + 10 words)**: `The background worker process terminated with exit code 137 during batch migration. The in-memory cursor buffered ten million rows without streaming pagination.`

### 4.2 Splitting nonrestrictive relative (`, which`) clauses

If a trailing `, which` clause introduces a secondary effect, replace the comma and `which` with a period:

1. End the first sentence before `, which`.
2. Start the second sentence with the explicit noun antecedent (`This mutation`, `This lock`, `The daemon`).

- **Before (27 words)**: `The interactive rebase operation rewrites the commit history of the active feature branch, which immediately invalidates the cryptographic Merkle tree hash stored inside the local approval receipt.`
- **After (11 words + 11 words)**: `The rebase operation rewrites the commit history of the feature branch. This rewrite invalidates the Merkle tree hash in the approval receipt.`

### 4.3 Splitting introductory and trailing participial (`-ing`) clauses

Participial phrases (`Running...`, `...causing...`) hide the subject and blur temporal sequence.
Convert the participle into an explicit finite verb or a `Before` / `If` clause:

1. Replace introductory `-ing` phrases with a time or condition clause (`When you run...`, `Before you start...`).
2. Replace trailing `, causing...` phrases with a separate sentence that names the subject.

- **Before (27 words)**: `Executing the integration test suite without isolating the database environment variable, the local test runner connects directly to the shared staging cluster, corrupting active user account records.`
- **After (11 words + 6 words)**: `Without an isolated database variable, the test runner connects to staging. This connection corrupts active user records.`

### 4.4 Splitting multi-instruction procedural sentences into numbered steps

If a procedural sentence chains multiple actions with `and`, `then`, or `before`, split each action into a numbered step:

1. Place at most one imperative command in each numbered step.
2. Follow mutation commands with an explicit `Verify that` confirmation step.

- **Before (28 words)**: `Stage all modified files in the working tree, compute the Git tree object hash into the state receipt file, and run the static analysis linter before dispatching review.`
- **After (8 words + 10 words + 9 words)**:
  1. `Stage all modified files in the working tree.`
  2. `Write the Git tree hash to the state receipt file.`
  3. `Verify that the static analysis linter passes before review.`

### 4.5 Unpacking stacked noun clusters and nested parentheticals

If a sentence contains four or more modifier nouns or nested parentheses, unpack the modifiers with prepositions (`for`, `in`, `from`):

1. Keep noun clusters to 3 words or fewer.
2. Promote essential parenthetical facts into a standalone sentence, or delete non-essential remarks.

- **Before (26 words)**: `Update the container runtime environment variable configuration validation script (which runs during worker pre-flight initialization (before native dynamic library loading)) to reject missing temporary directory paths.`
- **After (9 words + 11 words)**: `Update the validation script for container runtime environment variables. The script runs before native library loading and rejects missing paths.`

## 5. Zero-token phrase deletion table

Agents frequently generate conversational throat-clearing, meta-commentary, hedging, and redundant verb wrappers.
Delete zero-information phrases outright (`DELETE (0 tokens)`) or replace verbose wrappers with a single direct word:

| Verbose or AI-slop phrase | Deterministic replacement | Category |
| :--- | :--- | :--- |
| `it is important to note that` | `DELETE (0 tokens)` | Meta-commentary |
| `it should be noted that` | `DELETE (0 tokens)` | Meta-commentary |
| `it is worth mentioning that` | `DELETE (0 tokens)` | Meta-commentary |
| `please be advised that` | `DELETE (0 tokens)` | Meta-commentary |
| `keep in mind that` | `DELETE (0 tokens)` | Meta-commentary |
| `as a matter of fact` | `DELETE (0 tokens)` | Throat-clearing |
| `for all intents and purposes` | `DELETE (0 tokens)` | Throat-clearing |
| `in the final analysis` | `DELETE (0 tokens)` | Throat-clearing |
| `at the end of the day` | `DELETE (0 tokens)` | Throat-clearing |
| `needless to say` | `DELETE (0 tokens)` | Throat-clearing |
| `it goes without saying that` | `DELETE (0 tokens)` | Throat-clearing |
| `in order to ensure that` | `to verify that` | Verbose purpose clause |
| `in order to` | `to` | Verbose purpose clause |
| `for the purpose of` | `to` | Verbose purpose clause |
| `with the objective of` | `to` | Verbose purpose clause |
| `in an effort to` | `to` | Verbose purpose clause |
| `make sure that you always` | `always` | Redundant command wrapper |
| `be sure to` | `DELETE (0 tokens)` (use imperative verb) | Redundant command wrapper |
| `take action to` | `DELETE (0 tokens)` (use action verb) | Redundant command wrapper |
| `proceed to` | `DELETE (0 tokens)` (use action verb) | Redundant command wrapper |
| `has the ability to` | `can` | Verbose modal |
| `is capable of` | `can` | Verbose modal |
| `is able to` | `can` | Verbose modal |
| `has the potential to` | `can` | Verbose modal |
| `in the process of` | `currently` or `DELETE (0 tokens)` | Aspectual filler |
| `in the event that` | `if` | Conditional filler |
| `under circumstances in which` | `when` or `if` | Conditional filler |
| `on the grounds that` | `because` | Causal filler |
| `due to the fact that` | `because` | Causal filler |
| `in light of the fact that` | `because` | Causal filler |

## 6. Worked scar and Socratic probe compression patterns

When `agents/synthetic-scar-importer.md` or `agents/socratic-prompt-writer.md` compresses scars, `ScarCandidateValidator` checks sentence limits while preserving inline code and `$...$` / `$$...$$` math byte-for-byte.

| Scar field | Word limit | Worked `Before` and `After` compression example |
| :--- | :--- | :--- |
| `wound` | $\le 25$ words per sentence | **Before (27 words)**: It is important to note that the test suite connected directly to the live production database cluster when the environment variable $DB_URI \neq \text{test}$ was omitted during local execution.<br>**After (15 words)**: The test suite connected to the production database when the environment variable $DB_URI \neq \text{test}$ was omitted. |
| `trap` | $\le 25$ words per sentence | **Before (28 words)**: Relying on implicit default connection strings in the global session fixture causes unisolated test runners to mutate live records whenever the operator forgets to export a test profile.<br>**After (16 words)**: Default connection strings in global fixtures mutate live records when the operator omits a test profile. |
| `circuit_breaker` | $\le 20$ words per sentence | **Before (25 words)**: Before you proceed to execute any database migration test, make sure that you always inspect `pytest.ini` to verify that the target host points to localhost.<br>**After (13 words)**: Before running database tests, inspect `pytest.ini` and verify that the host uses localhost. |
| `reflex` | $\le 20$ words per sentence | **Before (24 words)**: In order to ensure that live data remains safe, configure every test fixture to use an in-memory SQLite URI and abort immediately if $$N_{\text{prod}} > 0$$.<br>**After (13 words)**: Point every test fixture to an in-memory SQLite URI and abort if $$N_{\text{prod}} > 0$$. |
| `socratic_probe` | $\le 20$ words per question (ends with `?`, max 3) | **Before (25 words)**: Have you verified whether or not every new test in this diff executes hermetically in memory without opening a socket to a live database instance?<br>**After (13 words)**: Does every test run in memory without connecting to a live database socket? |

## 7. Cross-standard precedence hierarchy and conflict-resolution matrix

### 7.1 Specification modules and source attribution

This specification synthesizes five public-domain and open standards into six modular guides:

| Module file | Standard or source | License or public domain status | Precedence rank |
| :--- | :--- | :--- | :--- |
| [`00_index_and_agentic_rationale.md`](./00_index_and_agentic_rationale.md) | Agentic attention synthesis and constitutional overrides | MIT License | Rank 1 (constitutional layer, overrides `01` through `05`) |
| [`04_google_developer_style_guide.md`](./04_google_developer_style_guide.md) | Google Developer Documentation Style Guide | `CC-BY-4.0` (Copyright Google LLC) | Rank 2 (software terminology, code spans, casing, and alerts) |
| [`02_mil_std_38784b_technical_manuals.md`](./02_mil_std_38784b_technical_manuals.md) | `MIL-STD-38784B` Technical Manuals Standard | Public Domain (`17 U.S.C. § 105`) | Rank 3 (numbered procedural steps and troubleshooting mapping) |
| [`03_nasa_sp_7084_technical_grammar.md`](./03_nasa_sp_7084_technical_grammar.md) | `NASA SP-7084` Grammar and Punctuation Handbook | Public Domain (`17 U.S.C. § 105`) | Rank 4 (unit modifiers, grammar, and SI unit spacing) |
| [`01_us_federal_plain_language.md`](./01_us_federal_plain_language.md) | U.S. Federal Plain Language Guidelines (`plainlanguage.gov`) | Public Domain (`17 U.S.C. § 105`) | Rank 5 (active voice, nominalizations, and general vocabulary) |
| [`05_attempto_controlled_english_cnl.md`](./05_attempto_controlled_english_cnl.md) | Attempto Controlled English (`ACE 6.7`, University of Zurich) | Open CNL Specification | Rank 6 (ranked **last** on surface syntax, retained for formal logic) |

### 7.2 Cross-standard conflict-resolution matrix

When rules in spokes `01` through `05` disagree, apply this deterministic conflict-resolution matrix:

| Conflict domain | Competing module rules | Deterministic winning rule |
| :--- | :--- | :--- |
| **Surface syntax vs. formal CNL parser rules** | `05_attempto_controlled_english_cnl.md` (`ACE 6.7`) bans commas after `if`-clauses, bans fronted conditions, requires `!` on commands, hyphenates phrasal verbs (`fill-in`), allows `he/she`, and requires `there is` for existential introduction. | Rank `05_attempto_controlled_english_cnl.md` **last** on surface syntax. Follow `00` through `04`: keep commas after introductory `if`-clauses, place conditions first, end imperative commands with `.`, write un-hyphenated phrasal verbs (`fill in`, `log in`) and proper names, ban `he/she`, and avoid expletive `there is` in general prose. |
| **Formal logic, quantifiers, and anaphora** | General grammar guides (`01` through `04`) allow informal pronoun references and implicit quantifier scope. | Retain `05_attempto_controlled_english_cnl.md` for universal vs. existential quantifier scope ($\forall$ vs. $\exists$), `each of` distributive plurals, the 4 anaphora laws (Accessibility, Recency, Specificity, Reflexivity), Socratic probe logic, and banning vague degree words (`properly`, `appropriately`). |
| **Alert callout hierarchy (`DANGER`, `WARNING`, `CAUTION`, `NOTE`)** | `MIL-STD-38784B` (`02`) uses 3 tiers (`WARNING` for personal injury, `CAUTION` for equipment damage, `NOTE`), whereas Google Style (`04`) uses `Caution` and `Warning` for software risks. | Use a unified four-tier hierarchy: `DANGER` for physical injury, loss of life, or hardware damage (what `02` calls `WARNING`), `WARNING` for irreversible actions, permanent data loss, security vulnerabilities, or service outages (`04`), `CAUTION` for proceeding carefully to prevent unexpected behavior or configuration drift (`04`), and `NOTE` for supplementary factual context with zero commands. |
| **Heading and title capitalization** | `NASA SP-7084` (`03`) recommends Headline Style capitalization for headings and titles, whereas Google Style (`04`) and Federal Plain Language (`01`) recommend sentence case. | Use **sentence case** for all document titles, section headings (`#`, `##`, `###`), table column headers, and captions (`04` overrides `03`). Capitalize only the first word, proper nouns, and exact technical identifiers. |
| **Number formatting, commas, and unit spacing** | `00` previously showed solid `15ms`, `03` omits commas in 4-digit numbers (`1000`) and spaces SI units (`15 ms`), and `01`/`04` use commas in 4+ digit numbers (`1,000`). | Use commas in numbers with four or more digits (`1,000`, `2,500`), use numerals for quantities, limits, and durations (`5 minutes`, `15 seconds`), and put a space between every numeral and its unit symbol (`15 ms`, `64 GB`). |
| **First-person plural `we` vs. second-person `you`** | `01` and `03` allow `we` for the authoring organization, whereas `04` discourages `we` in user instructions. | Permit `we`, `our`, and `us` when referring to the author group or engineering team (`01`, `03`). Address the operator as `you` in procedural steps (`01`, `02`, `04`), and name the software component explicitly when the system acts. |
| **Causal `because` vs. temporal `since`** | `01` Section 8 permits `since` as a replacement for `inasmuch as`, whereas `03` and `04` restrict `since` to time. | Use `because` for causation and `since` only for elapsed time (`04` overrides `01`). |
| **Observable verification verbs (`verify that`)** | `01` Section 8 maps `ascertain` to `make sure`, whereas `02` and `04` ban vague `ensure` and `make sure`. | Use `verify that` or `confirm that` for observable checks instead of `ensure` or `make sure` (`02` and `04` override `01`). |
| **Software engineering domain vocabulary** | `01` Section 8 replaces `delete`, `component`, `implement`, `interface`, and `option` with general-public words (`drop`, `part`, `carry out`, `choice`). | In `Plain Mode`, preserve exact software terms (`delete`, `component`, `implement`, `interface`, `option`, `parameter`, `argument`) over `01` Section 8 general-public replacements. |
| **Contractions and prose punctuation (`Rule 8`, `Rule 9`)** | `01` and `04` encourage conversational contractions (`don't`, `isn't`), and `03` permits semicolons and em-dashes in compound clauses. | Constitutional `Rule 8` and `Rule 9` in `00_index_and_agentic_rationale.md` override `01`, `03`, and `04`: never use contractions, semicolons, or em-dashes in prose. |
