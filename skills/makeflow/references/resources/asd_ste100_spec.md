# ASD-STE100 Simplified Technical English Specification

This document defines the specification of the ASD-STE100 standard (Issue 9) for technical documentation and software text.

ASD-STE100 is a controlled language (a restricted form of English) designed for clarity and safety.
It eliminates ambiguity, reduces error, and enables reliable translation.

Follow ASD-STE100 rules for language while using standard Markdown for document structure.

## 1. Core Principles and Operational Modes

ASD-STE100 enforces two primary principles:
1. One word has only one meaning and one part of speech.
2. Sentences stay short, direct, and grammatically complete.

Two operational modes exist:
- Plain Mode: Standard software technical writing that permits domain-specific technical nouns and verbs.
- Strict Mode: Full compliance with the official ASD-STE100 dictionary vocabulary.

## 2. Section 1: Words and Vocabulary (Rules 1.1 to 1.14)

1. Use approved words only as their designated part of speech and meaning.
2. In Plain Mode, you can use project domain terms as technical nouns ("webhook", "endpoint", "commit").
3. Do not convert technical nouns into verbs.
Send an event to the webhook. Do not "webhook the event."
4. Use standard industry terms rather than invented jargon or regional slang.
5. Apply one consistent name to an item across the entire document.
Do not use "config" in one section and "settings" in another.
Use "configuration" consistently throughout.
6. In Plain Mode, you can use standard software verbs ("compile", "deploy", "merge", "install", "delete").
When a common approved verb performs the same action, prefer the simpler word.
7. Do not convert technical verbs into nouns.
8. Use American English spelling.

## 3. Section 2: Multi-Word Noun Clusters (Rules 2.1 to 2.2)

1. Limit multi-word noun clusters to a maximum of three consecutive words.
2. If a technical concept requires more than three nouns, restructure the phrase with prepositions ("of", "for", "in", "on").

Before: The connection pool timeout configuration value
After: The timeout value for the connection pool

## 4. Section 3: Verbs and Verb Tenses (Rules 3.1 to 3.7)

1. Use only six permitted verb forms:
- Infinitive ("to run")
- Imperative ("run the test")
- Simple present ("the service runs")
- Simple past ("the service ran")
- Simple future ("the service will run")
- Past participle as an adjective ("the cached response")
2. Do not use past participles as verbs with auxiliary helpers.
Avoid complex verb constructions like present perfect ("has completed") or continuous forms ("is running").
3. Use the "-ing" form only as a technical noun ("logging") or noun modifier, never as a verb.
4. Write in the active voice.
State the actor for every action.
If the agent is the user, address the reader directly as "you."
5. Express actions through verbs rather than nominalized nouns.
Write "compress the archive" instead of "perform compression on the archive."

### The Modal Ladder

Do not use modals like "should", "would", "may", "might", or "could."
Apply the approved modal ladder:
- For mandatory requirements, write "must."
- For possibilities and permissions, write "can."
- For future events, write "will."
- If a condition exists, write "if" instead of inverted modals ("If an error occurs", not "Should an error occur").

## 5. Section 4: Sentence Construction (Rules 4.1 to 4.5)

1. Write short and direct sentences.
2. Do not omit words or use contractions to shorten text.
Keep articles ("the", "a", "an") and relative pronouns ("that").
3. Use vertical lists for sequences with three or more parallel items.
End the lead-in line with a colon.
Capitalize the first letter of each item.
Put a period at the end of each complete sentence item.
4. Use connecting words ("Then", "As a result", "Because") to show logical transitions between related sentences.
5. Place an article or demonstrative adjective ("this", "these") before nouns.
Exception: Omit the article when an identifier immediately follows the noun ("Restart pod `web-01`").

## 6. Section 5: Procedural Writing (Rules 5.1 to 5.5)

Procedural text tells the reader what actions to perform.

1. Keep procedural sentences at 20 words or fewer.
2. Write one instruction per sentence.
Exception: Two actions can share a sentence if they occur simultaneously.
3. Write instructions in the imperative mood: "Stop the database service."
4. If a condition is required, place the condition before the command with a comma: "If the build fails, inspect the log file."
5. Procedural notes provide factual information only.
Never put an instruction or an operational limit inside a note.
The procedure must remain fully operable if a reader removes all notes.

## 7. Section 6: Descriptive Writing (Rules 6.1 to 6.6)

Descriptive text explains concepts and system behavior.

1. Keep descriptive sentences at 25 words or fewer.
2. Present information gradually with one new fact per sentence.
3. Group related sentences into structured paragraphs.
4. Dedicate each paragraph to a single topic.
5. Write no more than six sentences per paragraph.

## 8. Section 7: Safety Instructions (Rules 7.1 to 7.3)

Safety instructions protect personnel, hardware, and data.

1. Use "WARNING" to identify risks of personal injury.
2. Use "CAUTION" to identify risks of equipment damage, software crash, or data loss.
3. State the command or condition first.
4. State the risk and consequence second.

Example:
CAUTION: Do not add the `--force` flag on production databases.
The command deletes all tables without confirmation.

## 9. Section 8: Punctuation and Word Counts (Rules 8.1 to 8.7)

1. Standard punctuation marks are permitted, except the semicolon.
Never use semicolons.
Split the thought into two sentences instead.
2. Do not use em-dashes.
State the exact logical relation ("because", "but", "for example") or write two sentences.
3. Use hyphens to connect words that form a compound unit ("read-only", "fail-fast").
4. Parentheses are permitted for cross-references, abbreviations, and secondary explanations.
5. In vertical lists, the lead-in colon concludes a sentence for word-count limits.
Each item in the list starts a new sentence with its own word budget.
6. Count these elements as single words:
- Hyphenated words ("end-to-end")
- Numbers with units ("15ms", "4GB")
- Backticked code spans and file paths (`make test-static`)
- Proper nouns and official product names

## 10. Section 9: Writing Practices and Recommendations

1. If a word-for-word swap fails, restructure the entire sentence.
2. Use one-word verbs instead of phrasal verbs.
Write "decrease" instead of "go down."
Write "install" instead of "set up."
3. Replace Latin abbreviations with English words:
- Write "for example" instead of "e.g."
- Write "that is" instead of "i.e."
- Delete "etc." and list the actual items.

## 11. Controlled Vocabulary Swaps

Use these standard replacements across technical text:

| Avoid | Use Instead |
|---|---|
| `check`, `verify`, `confirm`, `ensure` | `make sure that` |
| `since` (when meaning cause) | `because` |
| `config`, `settings`, `options` | `configuration` |
| `perform`, `execute` | `do`, `run` |
| `avoid` | `prevent` |
| `repeat` | `do again` |
| `acceptable` | `permitted` |
| `utilize`, `leverage` | `use` |
| `however` | `but` |
| `therefore` | `as a result` |

## 12. Eliminating AI Slop and Conversational Filler

Technical documentation must state facts directly without persuasion.
Guard against common automated writing habits:

1. Delete inflated significance words ("vital", "critical", "crucial", "a testament to").
State the concrete technical fact.
2. Delete decorative triplets and negative parallelism ("not only X, but also Y").
3. Delete conversational openers and closers ("Certainly", "I hope this helps").
4. Delete editorial filler ("it is important to note", "in conclusion").
