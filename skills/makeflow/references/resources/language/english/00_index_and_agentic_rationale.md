# Technical English Language Guidelines: Index and Agentic Rationale

This directory defines the open technical English specification for documentation, plans, commit messages, and pull request descriptions.

Each rule derives from public-domain United States government standards and open-source technical writing guides.
You can distribute and adapt these files under the repository MIT license.

## 1. Why Controlled Technical English Improves AI Agent Reasoning

Large language models use self-attention (a mechanism that weighs token relationships) to parse instructions and context.
Ambiguous grammar, long clauses, and multi-meaning words divide attention weights across competing interpretations.
Controlled technical English removes structural ambiguity so agents and humans parse one deterministic meaning.

1. Short sentences keep subject, verb, and object tokens close together inside attention windows.
2. One word per meaning prevents lexical drift across long multi-turn reasoning chains.
3. Condition-first ordering allows an agent to prune irrelevant execution branches before reading the command.
4. Eliminating filler words reduces context token consumption by 25 to 40 percent.

## 2. Core Quantitative Structural Rules

Follow these ten rules across all technical prose:

1. Keep procedural sentences at 20 words or fewer, with one instruction per sentence.
2. Keep descriptive sentences at 25 words or fewer, with one main fact per sentence.
3. Keep paragraphs at 6 sentences or fewer, and begin each paragraph with a topic sentence.
4. Limit noun clusters (consecutive nouns acting as modifiers) to 3 words or fewer.
5. Write in the active voice and use simple tenses (simple present, simple past, simple future).
6. If a step depends on a condition, place the condition before the command with a comma.
7. Define every new technical term in under 10 words at first use.
8. Do not use contractions (write `do not` instead of `don't`).
9. Never use semicolons or em-dashes in prose.
10. Replace Latin abbreviations (`e.g.`, `i.e.`, `etc.`, `via`) with plain English phrases.

## 3. Operational Modes and Token Preservation

Two operational modes govern vocabulary selection:

- **Plain Mode** (Default): Permits standard software nouns (`webhook`, `endpoint`, `mutex`) and technical verbs (`compile`, `deploy`, `merge`, `serialize`).
- **Strict Mode**: Restricts general vocabulary to the simple-word replacement dictionary in `01_us_federal_plain_language.md` and requires explicit definitions for all domain terms.

When you edit or paraphrase technical text, preserve all executable and mathematical syntax byte-for-byte:

1. Preserve fenced code blocks (` ```...``` `) and inline code spans (`` `...` ``) without alteration.
2. Preserve display LaTeX formulas (`$$...$$`) and inline LaTeX formulas (`$...$`) without alteration.
3. Preserve file paths, CLI flags, environment variables, and error codes exactly as written.
4. Count hyphenated compounds (`read-only`), numbers with units (`15ms`), and inline code spans as single words.

## 4. Specification Modules and Source Attribution

This specification synthesizes five public-domain and open standards into six modular guides:

| Module File | Standard / Source | License / Public Domain Status | Primary Scope |
| :--- | :--- | :--- | :--- |
| [`00_index_and_agentic_rationale.md`](./00_index_and_agentic_rationale.md) | Agentic Attention Synthesis | MIT License | Quantitative limits, modes, and token preservation |
| [`01_us_federal_plain_language.md`](./01_us_federal_plain_language.md) | U.S. Federal Plain Language Guidelines (`plainlanguage.gov`) | Public Domain (`17 U.S.C. § 105`) | Active voice, hidden verbs, and simple-word dictionary |
| [`02_mil_std_38784b_technical_manuals.md`](./02_mil_std_38784b_technical_manuals.md) | `MIL-STD-38784B` Technical Manuals Standard | Public Domain (`17 U.S.C. § 105`) | `WARNING` / `CAUTION` / `NOTE` alerts and procedures |
| [`03_nasa_sp_7084_technical_grammar.md`](./03_nasa_sp_7084_technical_grammar.md) | `NASA SP-7084` Grammar and Punctuation Handbook | Public Domain (`17 U.S.C. § 105`) | Noun clusters, unit modifiers, and punctuation |
| [`04_google_developer_style_guide.md`](./04_google_developer_style_guide.md) | Google Developer Documentation Style Guide | `CC-BY-4.0` (Copyright Google LLC) | Code spans, CLI flags, clause order, and Latin terms |
| [`05_attempto_controlled_english_cnl.md`](./05_attempto_controlled_english_cnl.md) | Attempto Controlled English (`ACE`, University of Zurich) | Open CNL Specification | Pronoun resolution, quantifiers, and `if-then` logic |
