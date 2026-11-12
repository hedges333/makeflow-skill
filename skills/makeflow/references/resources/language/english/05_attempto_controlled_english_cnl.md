# Attempto Controlled English (ACE 6.7) Construction, Interpretation, and Troubleshooting Specification

- **Source Standard**: *Attempto Controlled English (ACE 6.7)* Construction Rules (`ace_constructionrules.html`), Interpretation Rules (`ace_interpretationrules.html`), and Troubleshooting Guide (`ace_troubleshooting.html`) from `https://attempto.ifi.uzh.ch/site/docs/`.
- **Attribution**: Adapted from the open Attempto Controlled English specification developed by Norbert E. Fuchs, Kaarel Kaljurand, and Tobias Kuhn at the Department of Informatics, University of Zurich.

Attempto Controlled English is a formal Controlled Natural Language (a subset of English with deterministic first-order logic semantics).
Every ACE sentence is valid English, and the ACE parser assigns exactly one logical meaning to each sentence.
This guide encapsulates the ACE 6.7 Construction Rules, Interpretation Rules, and Troubleshooting Guide for unambiguous technical writing.
In the examples below, a leading asterisk (`*`) marks an invalid construction that ACE rejects.
Curly braces (`{...}`) in interpretation notes mark phrase scope, and square brackets (`[...]`) mark resolved anaphora targets.

## Part I: ACE Vocabulary and Lexical Categories (Construction Rules Section 1)

### 1. Function Words and Fixed Phrases

Function words (predefined words that establish grammatical structure) are fixed in ACE and cannot change.
ACE function words cover nine grammatical categories:

1. Determiners (`a`, `an`, `the`, `no`, `every`, `each`, `some`, `all`, `not every`, `not each`, `not all`).
2. Generalized quantifiers (`at least`, `at most`, `more than`, `less than`, `exactly`).
3. Coordinators (`and`, `or`, `,and`, `,or`).
4. Negation words (`not`, `no`, `provably`).
5. Pronouns (`he`, `she`, `it`, `they`, `him`, `her`, `them`, `his`, `its`, `their`, `itself`, `himself`, `his own`, `you`, `someone`, `nobody`, `everyone`).
6. Query words (`who`, `whose`, `what`, `which`, `how`, `where`, `when`, `how much`, `how many`).
7. Modal auxiliaries (`can`, `cannot`, `can not`, `must`, `have to`, `should`, `may`).
8. Forms of the copula `be` (`is`, `are`) and auxiliary `do` (`does`, `do`).
9. The Saxon genitive marker (`'s`) and predefined fixed phrases (`there is`, `there are`, `it is false that`, `it is not provable that`, `it is necessary that`, `it is possible that`).

### 2. Content Words and Multi-Class Lexicon Rules

Content words (domain words that name entities, actions, and properties) include nouns, verbs, adjectives, adverbs, and prepositions.
Write content words as single words (`code`) or hyphenated compounds (`check-code`, `automated-teller`).
All content words must exist in the built-in lexicon or a user-defined lexicon.
If a word belongs to multiple word classes, define a separate lexicon entry for each word class:

1. **Noun, Adjective, and Verb Overlap**: The word `surface` works as a noun (`The surface of the lens is clean.`), an adjective (`Some surface impurities are removed.`), or a verb (`The diver surfaces.`).
2. **Proper Name and Mass Noun Overlap**: Capitalized chemical names act as singular proper names (`Helium is an element.`), while lowercase forms act as mass nouns (`The balloon contains 20 kg of helium.`).

### 3. Proper Names and Variables

Proper names (capitalized names that denote specific individual or plural entities) stand alone or take `the`.
Hyphenate multi-word proper names (`John`, `Mr-Miller`, `the United-Nations`).

Variables (uppercase symbols that bind and track entities across clauses) use one uppercase letter followed by an optional integer (`X`, `Y`, `D1`, `C1`).
Introduce a variable in apposition immediately after a noun phrase, and then use the variable alone as a noun phrase:

- **Apposition Variable**: `A man X sleeps. X is young.`
- **Conditional Variable**: `If somebody D1 is a dog then D1 is an animal.`
- **Bare Variable**: A variable introduced without a preceding noun acts as shorthand for `something X` (`X weighs 300 kg. X is heavy.` means `Something X weighs 300 kg. X is heavy.`).

### 4. Numbers, Arithmetic Expressions, Strings, Sets, and Lists

Integers (`0`, `12`, `-2`) and real numbers (`0.0`, `0.2`, `3.141`, `-2.718`) are predefined in ACE.
Prefix negative numbers with a minus sign (`-`), and never prefix positive numbers with a plus sign (`+`).

Arithmetic expressions combine numbers, variables, and proper names with the operators `+`, `-`, `*`, `/`, and `^`.
All arithmetic operators are left-associative, and operator precedence evaluates `^` before `*` and `/`, which precede `+` and `-`.
Because `^` is left-associative in ACE, `2 ^ 3 ^ 4` parses as `(2 ^ 3) ^ 4`.
Add parentheses (`2 ^ (3 ^ 4)`) when you require right-nested exponentiation, or write `(1 + 2) / X * 4` to override default precedence.

Strings enclose Unicode characters in double quotes (`""`, `"abc"`).
Escape internal double quotes (`"ab\"cd"`) and backslashes (`"ab\\cd"`) with a leading backslash.
Concatenate strings, variables, and proper names with the left-associative `&` operator (`"abc" & "123"`).

Sets enclose comma-separated elements in braces (`{}`, `{3, 6, [1, 2]}`), and lists enclose elements in brackets (`[]`, `[3, 4, 5, "ab", John, 1+2]`).
Express set and list relations using natural language predicates (`{} is a subset of {3, 6}.`, `X is an element of [3, 4, 5].`).

### 5. Vague Degree Adverbs and Subjective Predicates Replacement Table

Technical specifications must state falsifiable invariants rather than subjective degree words.
Replace the following vague adverbs and adjectives with deterministic, testable predicates:

| Forbidden Vague Word | Ambiguity Defect | Deterministic ACE Replacement |
| :--- | :--- | :--- |
| `properly` | Hides the validation invariant | State the exact postcondition (`returns exit code 0`, `matches the schema`) |
| `appropriately` | Leaves the branch action unspecified | State the exact action for each input condition (`logs a warning and exits`) |
| `correctly` | Assumes an unwritten correctness oracle | Name the verified relation (`the SHA-256 digest equals the receipt hash`) |
| `adequately` | Omits the quantitative threshold | State the numerical bound (`contains at least 20 table rows`) |
| `usually` | Hides the exception condition | State the explicit `if ... then` exception rule |
| `mostly` | Uses non-deterministic frequency | State the exact subset or numerical quantifier (`at least 95 percent of requests`) |
| `fast` | Omits the latency bound | State the maximum duration (`completes in less than 15 ms`) |
| `safe` | Leaves the hazard undefined | State the safety invariant (`uses atomic directory creation with os.mkdir`) |
| `robust` | Conceals error-handling behavior | State which error types the handler catches and how the handler recovers |
| `seamless` | Uses marketing filler without semantics | Delete the word or state the state-transition contract |
| `gracefully` | Hides the shutdown sequence | State the exact teardown steps (`reaps child processes and removes the lock directory`) |
| `efficiently` | Omits complexity or resource limits | State the algorithmic bound or memory cap (`uses at most 64 MB of RAM`) |

## Part II: Simple Sentences, Noun Phrases, Verb Phrases, and Modifiers (Construction Rules Section 2)

### 1. Countable, Mass, Exclusivity, and Measurement Noun Phrases

Every common noun in ACE requires a determiner (a word that introduces a noun phrase and specifies quantity).
Bare common nouns (`*Worker holds lock.`) are syntax errors in ACE.

1. **Singular Countable Noun Phrases**: Use `a`, `the`, `1`, `one`, `no`, `every`, `each`, `not every`, or `not each` (`a card`, `the card`, `1 card`, `one card`, `no card`, `every card`, `each card`, `not every card`, `not each card`). The determiners `a`, `1`, and `one` are synonymous, `every` and `each` are synonymous, and `not every` and `not each` are synonymous. Plural `all cards` is syntactic sugar for `every card`, and `no cards` is syntactic sugar for `no card`.
2. **Plural Countable Noun Phrases**: Unmarked plurals take a collective reading (`the cards`, `some cards`, `3 cards`, `three cards`). Prefix `each of` for a distributive reading (`each of the cards`, `each of some cards`, `each of 3 cards`, `each of three cards`). Integers from 1 to 12 can be spelled out (`twelve cats`) or written as digits (`12 cats`), whereas integers above 12 must use digits.
3. **Mass Noun Phrases**: Mass nouns (nouns that denote uncountable substances) take `some`, `no`, `all`, or `not all` (`some water`, `no water`, `all water`, `not all water`).
4. **Indefinite Pronouns**: Use `someone`/`somebody`, `something`, `no one`/`nobody`, `nothing`, `everyone`/`everybody`, `everything`, `not everyone`/`not everybody`, and `not everything` as complete noun phrases without determiners.
5. **Exclusivity Determiners (`nobody but`, `nothing but`, `no ... but`)**: Follow `nobody but`, `nothing but`, or `no <noun> but` with a bare plural noun, a bare mass noun, or a proper name (`nothing but apples`, `nothing but water`, `nobody but John`, `no man but John`).
6. **Measurement Noun Phrases**: Combine a number, a measurement noun, and an optional `of`-phrase containing a bare mass or plural noun (`2 m`, `31.2°C`, `1.4 l of water`, `3 kg of apples`). Measurement noun phrases accept singular or plural verbs (`3 kg of apples is heavy.`, `3 kg of apples are heavy.`).

### 2. Noun Phrase Modifiers, Apposition, and Genitives

Modify nouns and noun phrases using adjectives, apposition variables, and genitives:

1. **Adjectives**: Place positive, comparative (`richer` or `more rich`), or superlative (`richest` or `most rich`) adjectives before the noun. Conjoin multiple pre-nominal adjectives with `and` (`a rich and satisfied customer`, `a richer and more satisfied customer`).
2. **Variables in Apposition**: Place a variable immediately after a noun phrase (`a customer X`, `the customer C1`).
3. **Genitives (`of`-Phrases, Saxon Genitive `'s`, and Possessive Pronouns)**: Attach `of`-phrases (`a customer of John`), Saxon genitives (`John's customer`, `everybody's customer`, `every man's dog`, `X's dog`), non-reflexive possessive pronouns (`your`, `its`, `his`, `her`, `his/her`, `their`), or reflexive possessive pronouns (`your own`, `its own`, `his own`, `her own`, `his/her own`, `their own`).
4. **Saxon Genitive vs. `of`-Phrase Conjunction**: A Saxon genitive applies only to a single noun (`John and Mary's dog` means `John and {Mary's dog}`), whereas an `of`-phrase governs a conjoined noun phrase (`a dog of John and Mary` means `a dog of {John and Mary}`).

### 3. Verbs, Phrasal Hyphenation, Passive Voice, and the Copula

Declarative and interrogative sentences use third-person simple present tense verbs.
Imperative commands use second-person base verbs.

1. **Intransitive, Transitive, and Ditransitive Verbs**: ACE supports intransitive verbs (`John waits.`), transitive verbs (`John enters a card.`), and ditransitive verbs in both prepositional and double-object forms (`John gives a card to a customer.` or `John gives a customer a card.`).
2. **Hyphenated Phrasal and Prepositional Verbs**: Hyphenate phrasal particles and direct prepositions of transitive prepositional verbs to the verb stem (`John fills-in a form.`, `John looks-up an entry.`, `A steward waits-on the table.`, `John comes-across an interesting story.`, `What does John apply-for?`). Never move a phrasal particle after the direct object (`*He looks it up.` -> `He looks-up the word.`). Do not hyphenate indirect-object prepositions in ditransitive verbs (`John gives a card to a clerk.`).
3. **Passive Voice**: Form passive verb phrases with `is`/`are`, the past participle, and a mandatory `by <agent>` phrase (`A card is entered by John.`, `A customer is given a card by John.`). Passive clauses without `by <agent>` are invalid as passives.
4. **Copula (`be`) and Adjective Comparisons**: Follow the copula `is` or `are` with a noun phrase (`John is a rich customer.`), an adjective phrase (`John is rich.`), concatenated prepositional phrases (`John is in the garden on the hill.`), or a query word (`John is who?`).
5. **Transitive Adjectives and Comparison Targets**: Hyphenate the preposition of a transitive adjective (`John is fond-of Mary.`, `A student is interested-in a course.`). Conjoin intransitive predicative adjectives with `and` (`rich and more important`), but never conjoin a transitive adjective (`*A customer is rich and interested-in a bank.`). Build comparisons with `as ... as` or `more ... than` (`John is as rich as Mary.`, `John is richer than Mary.`), and do not conjoin comparison adjectives (`*A customer is richer than John and important.`). For transitive adjectives, repeat the preposition after `as` or `than` to compare objects instead of subjects (`John is more fond-of Mary than of Sue.` vs. `John is more fond-of Mary than Bill.`).

### 4. Verb Phrase Modifiers, `there is/are` Sentences, and Boolean Formulas

1. **Adverbs and Prepositional Phrase Modifiers**: Place adverbs (`manually`, `more patiently`, `most patiently`) and prepositional phrases (`into a slot`) immediately before or after the verb phrase (`A customer manually inserts a card into a slot.`).
2. **Combining Multiple Verb Modifiers**: Conjoin two or more adverbs with `and` (`A customer inserts a card carefully and manually.`), and concatenate multiple prepositional phrases without a coordinator (`A customer waits in a bank in the morning.`).
3. **Forbidden Adverb Positions**: Never place an adverb or prepositional phrase at the start of a declarative sentence (`*In the morning John waits.`). Never modify an adverb with another adverb (`*John drives extremely carefully.` -> `John drives extremely and carefully.`), and never use sentence-modifying adverbs (`*John only works.`).
4. **Existential `there is/are` Sentences**: Introduce entities without a verb using `there is` or `there are` (`There is a customer.`, `There is some water.`, `There are a cat and 2 dogs.`, `There is X.`, `There is no man who sleeps.`). The noun phrase after `there is/are` cannot be or contain a definite noun phrase, a universal quantifier (`every`), a proper name, a number, an arithmetic expression, a string, a set, or a list. Furthermore, `there is/are` sentences cannot take prepositional phrase modifiers (`*There is a card in a slot.` -> `A card is in a slot.` or `There is a card that is in a slot.`).
5. **Boolean Formulas**: Build standalone declarative sentences from numbers, arithmetic expressions, proper names, and variables with `=`, `\=`, `>`, `>=`, `<`, and `=<` (`10 = 4 + 6.`, `X \= 2.`, `X >= 13.4 and X < 20.`).

## Part III: Composite Sentences, Coordination, and Subordinate Clauses (Construction Rules Section 3)

### 1. Conditional `if ... then` Sentences

Conditional `if ... then` sentences subordinate two simple or composite sentences to express logical implication ($P \implies Q$).

1. Write conditionals in the exact order `If <condition> then <consequence>` (`If John enters a card then the automated-teller accepts it.`).
2. Both the `if`-part and the `then`-part can contain coordinated clauses (`If John enters a card and he waits then the automated-teller accepts the card or rejects it.`).
3. Do not place a comma before `then` in strict ACE syntax. The scope of the `then`-part extends to the end of the coordinated sentence (`{If a man waits and a dog barks} {then a woman smiles and a cat sleeps}.`).

### 2. Sentence Coordination and Exclusive Disjunction

Coordinate complete sentences with `and`, `or`, `,and`, and `,or`.
By default, `and` binds stronger than `or`.
Prefix the coordinator with a comma (`,and` or `,or`) to override the default binding order (`The screen blinks or John waits, and Mary enters a card.`).

In ACE, `or` always expresses inclusive disjunction (either alternative holds, or both hold).
To express exclusive disjunction (one alternative holds, and not both), conjoin each positive alternative with the negation of the other alternative:

- **Inclusive Disjunction (`or`)**: `If the customer works in a bank then he gets a red card or gets a blue card.`
- **Exclusive Disjunction Reformulation**: `If the customer works in a bank then he gets a red card and gets no blue card, or he gets a blue card and gets no red card.`

### 3. Verb Phrase and Noun Phrase Coordination

1. **Verb Phrase Coordination**: Coordinate verb phrases with `and` or `or` (`John waits and eats a burger.`, `John is rich or earns his own income.`).
2. **Noun Phrase Conjunction (`and`)**: Conjoin noun phrases with `and` to create a plural group that subsequent sentences can reference with `they` (`A man and at least 2 women wait. They are tired.`).
3. **Ban on Noun Phrase Disjunction (`*or`)**: ACE forbids noun phrase disjunction with `or`. Rewrite `*John enters a card or a code.` by coordinating the verb phrases instead (`John enters a card or enters a code.`).

### 4. Relative Clauses and Sentence Subordination

1. **Relative Clauses**: Follow a common noun phrase, proper name, or indefinite pronoun with a relative clause introduced by `that`, `who`, `which`, or distributive `each of who` (`a customer that is rich`, `John who waits`, `some men each of who waits`, `everything which is important`). Coordinate relative clauses with `and` or `or` by repeating the relative pronoun (`a customer who is rich and who is well-known`, `a man who waits or who sleeps`).
2. **Object-Clause Subordination (`that` and `to`)**: A transitive attitude verb takes a full subordinate sentence introduced by `that` (`A clerk believes that a customer inserts a card.`). If the main clause and subordinate clause share the same subject (`John wants that John inserts a card.`), use an infinitive `to` complement (`John wants to insert a card.`).
3. **Main-Verb Modifier Placement in Subordination**: Place all adverbs and prepositional phrases that modify the main verb immediately before the main verb (`John seriously in the morning believes that Mary fills-in a form.`, `John eagerly in the morning wants to insert a card.`). Never place modifiers between the main verb and `that` or `to`.

## Part IV: Negation and Modality (Construction Rules Sections 4 and 5)

### 1. Noun Phrase and Verb Phrase Negation

ACE provides logical negation and negation as failure (closed-world unprovability) at the phrase level:

1. **Noun Phrase Negation**: Use `no`, `not every`, `not each`, `not all`, `no one`/`nobody`, `nothing`, or `no ... but` (`no card`, `not every card`, `not all water`, `nothing but water`).
2. **Verb Phrase Logical Negation**: Use `does not`, `do not`, `is not`, or `are not` (`John does not enter a code.`, `The men do not wait.`, `Some water is not drinkable.`, `The men are not tired.`).
3. **Verb Phrase Negation as Failure**: Use `does not provably`, `do not provably`, `is not provably`, or `are not provably` (`John does not provably wait.`, `The men do not provably work.`, `John is not provably a teacher.`, `The men are not provably admitted.`).

### 2. Sentence-Level Logical Negation and Negation as Failure

Negate complete sentences using subordination constructors:

1. **Sentence Logical Negation**: Prefix the sentence with `it is false that ...` or `it is not true that ...` (`It is false that a screen blinks.`). ACE also supports `it is true that ...` for backwards compatibility.
2. **Sentence Negation as Failure**: Prefix the sentence with `it is not provable that ...` (`It is not provable that a screen blinks.`).
3. **Repeating `that` Across Coordinated Clauses**: The scope of `that` ends at the first simple sentence (`{It is false that a man waits} and a dog barks.`). Repeat `that` after each coordinator to negate the entire coordination (`It is false that a screen blinks and that John enters a code.`, `It is not provable that a screen blinks and that John enters a code.`).

### 3. Modal Auxiliaries and Sentence-Level Modality

Express modality (possibility, necessity, recommendation, and admissibility) at the verb phrase or sentence level:

| Modality Category | Verb Phrase Auxiliary (Positive / Negative) | Sentence-Level Constructor (Positive / Negative) |
| :--- | :--- | :--- |
| **Possibility** | `can` / `cannot`, `can not` (`John can wait.`) | `it is possible that ...` / `it is not possible that ...` |
| **Necessity** | `must`, `have to`, `has to` / `does not have to`, `do not have to` | `it is necessary that ...` / `it is not necessary that ...` |
| **Recommendation** | `should` / `should not` (`John should wait.`) | `it is recommended that ...` / `it is not recommended that ...` |
| **Admissibility** | `may` / `may not` (`John may not wait.`) | `it is admissible that ...` / `it is not admissible that ...` |

When coordinating clauses under sentence-level modality, repeat `that` after `and` or `or` (`It is not necessary that John enters a card and that he types a code.`).

### 4. Combining Negation and Coordination (De Morgan's Rules)

In English, `or` under negation often misleads readers.
In ACE, `The ATM does not accept a red card or does not accept a blue card.` means `It is false that the ATM accepts a red card or it is false that the ATM accepts a blue card.` ($\neg R \lor \neg B$).
To state that the ATM rejects both card colors ($\neg R \land \neg B$), use one of three unambiguous forms:

1. **Conjoined Negated Verb Phrases**: `The ATM does not accept a red card and does not accept a blue card.`
2. **Conjoined Object Noun Phrase**: `The ATM does not accept a red card and a blue card.`
3. **Two Separate Sentences**: `The ATM does not accept a red card. The ATM does not accept a blue card.`

## Part V: Quantification and Scope Ordering (Construction Rules Section 6)

### 1. Existential, Universal, and Generalized Numerical Quantifiers

A quantifier (a determiner that fixes entity cardinality and logical scope) falls into one of three classes:

1. **Existential Quantifiers (`a`, `some`, `someone`, `something`)**: In ACE, `a` always means existential quantification ($\exists$), never universal quantification ($\forall$). Therefore, `A cat is an animal.` means `There is a cat that is an animal.`. Write `Every cat is an animal.` to state a universal law.
2. **Universal Quantifiers (`every`, `each`, `all`, `everyone`, `everything`)**: Universal quantification is logically equivalent to a conditional implication. `Every customer enters a card.` has the exact same logical meaning as `If there is a customer then the customer enters a card.`.
3. **Generalized Numerical Quantifiers (`at least N`, `at most N`, `more than N`, `less than N`, `exactly N`)**: Follow a generalized quantifier with a positive integer (`at least 2 cards`, `at most 2 cards`, `more than 10 cards`, `less than 3 cards`, `exactly 3 cards`).

### 2. Collective vs. Distributive Plural Readings

By default, plural noun phrases in ACE have a collective reading in which the group acts together in a single shared event.
Prefix the plural noun phrase with `each of` to specify a distributive reading in which each member participates in an individual event:

- **Collective Reading**: `Two girls lift a table.` (One lifting event where two girls together lift one table.)
- **Distributive Reading**: `Each of two girls lifts a table.` (Two separate lifting events where each girl lifts a table individually.)
- **Generalized Distributive**: `each of at least 2 cards`, `each of at most 2 cards`, `each of more than 10 cards`, `each of less than 3 cards`, `each of exactly 3 cards`.

### 3. Local vs. Global Quantifiers and Surface-Order Scope

The textual position of a quantifier opens its logical scope:

1. **Local Quantifiers (`a`, `some`, `every`, `each`)**: Open scope at their position in the clause and extend to the end of the current simple or coordinated clause (`{Every customer has a card} and {a customer has a code}.`).
2. **Global Quantifiers (`there is ... that ...`, `for every`, `for each`, `for each of`, `for all`)**: Stand at the beginning of a sentence and extend their scope to the very end of the entire sentence, including all coordinated clauses (`{For every customer the customer has a card and the customer has a code}.`).
3. **Surface-Order Scope Determination**: An earlier quantifier in surface word order always scopes over a quantifier that appears later in the sentence:
   - **Existential Over Universal ($\exists \forall$)**: `John assigns a personal-code to every customer.` means `There is a personal-code. If there is a customer then John assigns the customer the personal-code.` (Every customer receives the same shared code.)
   - **Universal Over Existential ($\forall \exists$)**: To state that each customer receives an individual code, place the universal quantifier first using passive voice (`Every customer is assigned a personal-code by John.`), a global quantifier (`For every customer John assigns a personal-code to the customer.`), or a conditional (`If there is a customer then John assigns the customer a personal-code.`).
   - **Reversing Universal Subject Scope**: `Every employee gets an extra holiday.` gives each employee a separate holiday ($\forall \exists$). Write `There is an extra holiday that every employee gets.` when all employees share one specific holiday ($\exists \forall$).

### 4. Quantifier Coordination and Quantifier-Negation Disambiguation

1. **Universal Quantifier with `or`**: `Every tourist has a red card or has a blue card.` means each tourist has at least one of the two card colors ($\forall x (R(x) \lor B(x))$). Write `Every tourist has a red card or every tourist has a blue card.` only when every tourist holds the same color ($\forall x R(x) \lor \forall x B(x)$).
2. **Eliminating `All ... do not` Ambiguity**: In ACE, verb negation distributes before quantifier surface order (`Every card is not valid.` means `If there is a card then it is false that the card is valid.`). Because natural English readers confuse universal negation with partial negation, start universal negations with `No` (`No worker holds a lock.`) and partial negations with `Not every` (`Not every worker holds a lock.`).

## Part VI: Anaphora Resolution and Definite Noun-Phrase Binding (Construction Rules Section 7 and Interpretation Rules)

### 1. Anaphoric Forms and Non-Referable Tokens

Anaphora (binding a pronoun, definite noun phrase, or variable to an earlier antecedent noun phrase) follows deterministic resolution rules in ACE:

1. **Non-Anaphoric Second-Person Pronouns**: `you`, `your`, `yourself`, `yourselves`, and `your own` occur only in imperative commands and refer to the command addressee.
2. **Third-Person Non-Reflexive Pronouns**: `he`, `she`, `he/she`, `it`, `they`, `him`, `her`, `him/her`, `them`, `his`, `her`, `his/her`, `its`, and `their` refer to accessible antecedents outside the current clause subject.
3. **Third-Person Reflexive Pronouns**: `itself`, `himself`, `herself`, `himself/herself`, `themselves`, `his own`, `her own`, `his/her own`, `its own`, and `their own` refer to the subject of the containing clause.
4. **Definite Noun Phrases, Variables, and Proper Names**: Definite noun phrases (`the card`), apposition variables (`X`, `Y`), and repeated proper names (`John`) resolve to prior discourse entities.
5. **Undefined Capitalized Words**: An unknown capitalized word in noun phrase position acts as a singular proper name of undefined gender until a pronoun (`he` or `her`) fixes its gender (`Leonino sleeps. Her fur is wet.`).
6. **Non-Referable Tokens**: Numbers, arithmetic expressions, strings, sets, and lists cannot be referenced anaphorically by pronouns or definite noun phrases.

### 2. The Four Resolution Laws: Accessibility, Recency, Specificity, and Reflexivity

ACE resolves every anaphor using four strict laws:

1. **Accessibility**: A candidate antecedent must reside in an accessible logical scope.
2. **Recency**: Among accessible antecedents that match in gender and number, ACE selects the most recently mentioned noun phrase (`There is a blue ball. There is a red ball. John sees the ball [= the red ball].`).
3. **Specificity**: A definite noun phrase with modifiers (`the blue ball`, `the ball of a boy`, `the man who owns a dog`, `the man whose dog barks`) resolves to the most recent accessible antecedent whose introducing sentence contains all those modifiers. If no accessible antecedent matches, the definite noun phrase introduces a new entity (`John goes to the bank.`).
4. **Reflexivity**: Non-reflexive pronouns (`him`, `his`) cannot bind the subject of their own verb phrase. Thus `John sees his wife.` cannot bind `his` to `John` and fails if no other male antecedent precedes the sentence. Use reflexive `his own` (`John sees his own wife.`) or `himself` (`John sees a man that sees himself.`) to bind the subject.

### 3. Complete Antecedent Accessibility Boundaries

Proper names (`John`, `Mary`) are globally accessible from every sentence.
All other noun phrases (`a card`, `a receipt`, `a car`) are **inaccessible** from subsequent sentences when introduced inside seven blocked scopes:

1. **Negation**: `John does not enter a card. *It is correct.` -> `John does not enter a card that is correct.`
2. **Modal Auxiliaries and Modal Sentences**: `John can enter a card. *It is correct.` -> `John can enter a card that is correct.`
3. **Subordinated Object Clauses**: `Mary believes that John enters a card. *It is correct.` -> `Mary believes that John enters a card that is correct.`
4. **Universal Quantifiers (`every`, `each`, `all`)**: `Every customer has a card. *It is correct.` -> `Every customer has a card that is correct.`
5. **Conditional `if ... then` Sentences**: `If a customer pushes the key then the automatic-teller prints a receipt. *It shows the date.` -> `If a customer pushes the key then the automatic-teller prints a receipt that shows the date.`
6. **Interrogative Queries**: `Does John see a car? *The car is red.` -> `A car is red. Does John see the car?`
7. **Imperative Commands**: `John, identify a car! *The car is red.` -> `A car is red. John, identify the car!`

Two internal accessibility exceptions apply within composite sentences:

- **Condition-to-Consequence (`if` to `then`)**: Antecedents introduced in the `if`-part are accessible inside the `then`-part (`If a customer has a card then he enters it.`).
- **Disjunction (`or`)**: Antecedents introduced in one disjunct are accessible in subsequent disjuncts of the same disjunction, but inaccessible after the sentence ends (`A customer enters a card or drops it. *It is dirty.`).

When recency binds a bare pronoun (`it`) to the wrong noun (`If a credit-card has a signature then it is valid.` binds `it` to `a signature`), replace the pronoun with the exact definite noun phrase (`If a credit-card has a signature then the credit-card is valid.`).

## Part VII: Interrogative Sentences, Commands, and Socratic Probes (Construction Rules Sections 8 and 9)

### 1. Interrogative Sentences (`Yes/No`, `wh-queries`, and `how much/many` Queries)

ACE supports three interrogative sentence forms, each ending with a question mark (`?`):

1. **`Yes/No` Queries**: Ask whether a declarative or negated statement holds (`Does John enter a card?`, `Does John not wait?`, `Do John and Mary wait in a garden?`, `Is the card valid?`, `Is the card not valid?`, `Is it true that a man waits?`, `Is it false that a man waits?`).
2. **`wh-queries` (`who`, `whose`, `what`, `which`, `how`, `where`, `when`)**: Use `who`, `whose`, `what`, and `which` to query subjects or objects (`Who waits?`, `Each of who waits?`, `Whose dog barks?`, `A card of who is valid?`, `Which customer enters a card and types a code?`). Use `how`, `where`, and `when` to query verb modifiers (`How does John enter a card?`, `Where does John wait?`, `When does John wait?`). Query words can stand before the verb (`What does a man not eat?`) or in situ after the verb (`A man does not eat what?`, `John waits where?`, `There is who?`).
3. **`how much` and `how many` Queries**: Query the amount of a mass noun with `how much` (`How much water boils?`) or the count of a plural countable noun with `how many` (`How many men wait?`, `How many men have how many apples?`, `John gives how much food to how many children?`).

### 2. Imperative Sentences (Commands) and ACE Texts

An imperative sentence (a command directed at an explicit addressee) starts with an addressee noun phrase, followed by a comma, a possibly negated uncoordinated verb phrase, and an exclamation mark (`!`):

- **Positive Command**: `John, go to your own bank!`
- **Reflexive Plural Command**: `John and Mary, clean yourselves!`
- **Negated Universal Command**: `Every dog, do not bark!`
- **Genitive Addressee Command**: `John's brother, return the book to Mary!`

An ACE text is a sequence of declarative, interrogative, and imperative sentences.
Declarative, interrogative, and imperative sentences can refer anaphorically to entities introduced in preceding declarative sentences, but never to entities introduced in preceding queries or commands.

### 3. Formal ACE Socratic Probe Patterns for `agents/socratic-prompt-writer.md`

When `agents/socratic-prompt-writer.md` distills synthetic scar fields (`wound`, `trap`, `circuit_breaker`, and `reflex`) into `socratic_probe` questions, follow these ACE interrogative rules:

1. Keep every question sentence at 20 words or fewer, ending with a question mark (`?`).
2. Eliminate bare pronouns (`it`, `they`, `this`) by naming the exact definite noun phrase (`the child process`, `the lock directory`).
3. Formulate affirmative conditional `Yes/No` queries or `wh`-queries that test the candidate plan against the `reflex` invariant:
   - **Trap-to-Reflex Probe**: `If the worker skips <reflex>, can <trap> cause <wound>?`
   - **Circuit-Breaker Probe**: `When <circuit_breaker> occurs, does the handler execute <reflex> before the process exits?`
   - **State-Boundary `wh`-Query**: `What happens to <target noun phrase> if <failure condition> occurs during execution?`

## Part VIII: Deterministic Interpretation and Attachment Rules (Interpretation Rules and Troubleshooting Guide)

### 1. Prepositional Phrase and Adverb Attachment

ACE eliminates structural attachment ambiguity with two deterministic adjunct rules:

1. **Prepositional Phrase Attachment**: Every prepositional phrase (except `of`) attaches to the closest verb phrase, never to a noun phrase (`A customer {enters a card with a code}.`). Only an `of`-phrase modifies the adjacent noun phrase (`a copy of a book`). To modify a noun with any preposition other than `of`, convert the prepositional phrase into a relative clause (`A bank accepts every card that has a blue sign.` instead of `A bank accepts every card with a blue sign.`).
2. **Adverb Attachment**: When an adverb sits between two verbs where it could modify either the preceding or the following verb, the adverb always modifies the preceding verb (`A customer who {enters a card manually} types a code.`).

### 2. Relative Clause Right-Association and Coordinated Relative Clauses

1. **Right Association**: A relative clause always modifies the immediately preceding noun phrase. In `John returns a copy of {a book that is damaged}.`, the relative clause attaches to `a book` rather than `a copy`. Similarly, in `The customer enters a card into {the slot which is invalid}.`, the relative clause attaches to `the slot` rather than `a card`.
2. **Overriding Right Association**: To attach the modifier to the earlier noun phrase, move the relative clause immediately after its target noun (`The customer enters a card which is invalid into the slot.`), split the sentence and repeat the definite head noun (`John returns a copy of a book. The copy is damaged.`), use a pre-nominal adjective (`The customer enters an invalid card into the slot.`), or rewrite `every` as an `if-then` implication (`If a copy of a book has a yellow point then the copy is not available.`).
3. **Repeating Relative Pronouns in Coordination**: A coordinated verb phrase after a relative clause attaches to the main sentence (`The customer enters {a card that is valid} and has a code.`). Repeat `that`, `who`, or `which` to keep the second verb phrase inside the relative clause (`The customer enters {a card that is valid and that has a code}.`).

### 3. Coordinator Binding Order and `of`-Construct Scope

1. **Coordinator Binding Order (`and` > `or` > `,and` > `,or`)**: By default, `and` binds stronger than `or`. Thus `The customer enters a red card or enters a blue card and enters a personal-code.` parses as `R or (B and C)`. Insert `,and` to group the disjunction first (`A client {enters a red card or enters a blue card}, and enters a code.`).
2. **`of`-Construct Conjunction Scope**: An `of`-phrase followed by a noun phrase conjunction scopes over the entire conjunction. Thus `A man sees a dog of Mary and John.` means `A man sees a dog of {Mary and John}`. Place the standalone conjunct first (`A man sees John and a dog of Mary.`) or repeat the verb (`John assesses a degree of Toxicity and assesses a degree of Dehydration.`) to prevent unintended `of`-capture.

### 4. Lexical Tie-Breaking Rules

1. **Countable vs. Mass Noun Overlap (`laundry`)**: When a noun has both countable and mass lexicon entries, ACE prefers the countable reading for `no laundry`, `John's laundry`, and `his laundry`. Enforce the mass reading with `some laundry of John`, `some laundry does not ...`, or `it is false that some laundry ...`.
2. **Passive vs. Adjectival Participle (`entered`)**: A passive clause requires `by <agent>` (`A card is entered by John.`). If `entered` is also defined as an intransitive adjective, `A card is entered.` parses as a copula plus adjective rather than a passive verb.
3. **Transitive vs. Ditransitive Verb Overlap (`regards`)**: If a verb has both transitive (`regard something`) and ditransitive (`regard something as something`) entries, the ditransitive reading takes precedence (`John regards a woman as a friend.`).

### 5. Comprehensive ACE Troubleshooting Reformulation Table (`Avoid -> Use`)

Use the following reference table to diagnose and reformulate rejected or misinterpreted sentences:

| Category | Ambiguous or Rejected Input (`Avoid`) | ACE Parser Defect or Misinterpretation | Deterministic ACE Reformulation (`Use`) |
| :--- | :--- | :--- | :--- |
| **Bare Noun** | `*File is locked by worker.` | Rejects common nouns without determiners | `The file is locked by the worker.` |
| **Sentence-Initial Adjunct** | `*In the morning John waits.` | Rejects sentence-initial prepositional phrases | `John waits in the morning.` |
| **Comma Before `then`** | `*If X > 0, then Y = 1.` | Rejects commas before `then` in strict ACE | `If X > 0 then Y = 1.` |
| **Agentless Passive** | `*A code is entered.` | Parses `entered` as adjective or rejects passive | `A code is entered by a customer.` |
| **Existential Universal** | `A cat is an animal.` | Parses `a` as existential (`There is a cat...`) | `Every cat is an animal.` |
| **Non-Reflexive Subject Binding** | `John sees his wife.` | Cannot bind non-reflexive `his` to subject `John` | `John sees his own wife.` |
| **Noun Phrase Disjunction** | `*John enters a card or a code.` | Rejects `or` between noun phrases | `John enters a card or enters a code.` |
| **`there is` with PP** | `*There is a card in a slot.` | Rejects prepositional phrases on `there is` | `A card is in a slot.` |
| **Adverb on Adverb** | `*John drives extremely carefully.` | Rejects adverbs modifying adverbs | `John drives extremely and carefully.` |
| **Sentence Adverb** | `*John only works.` | Rejects sentence-modifying adverbs | `John works and does nothing else.` |
| **Un-Hyphenated Phrasal Verb** | `John comes across a story.` | Parses `comes` as intransitive + PP `across` | `John comes-across a story.` |
| **Particle Movement** | `*He looks it up.` | Rejects moved phrasal particles | `He looks-up the word.` |
| **PP Modifying Noun** | `A bank accepts every card with a blue sign.` | Attaches `with a blue sign` to verb `accepts` | `A bank accepts every card that has a blue sign.` |
| **Pronoun Recency Clash** | `If a credit-card has a signature then it is valid.` | Binds `it` to most recent noun `a signature` | `If a credit-card has a signature then the credit-card is valid.` |
| **Blocked `if-then` Antecedent** | `If a customer pushes the key then the ATM prints a receipt. *It shows the date.` | `a receipt` inside `then` is inaccessible outside | `If a customer pushes the key then the ATM prints a receipt that shows the date.` |
| **Blocked `every` Antecedent** | `Every customer has a card and a code. *The code matches the card.` | Nouns under `every` are inaccessible in next sentence | `Every customer has a card and a code that matches the card.` |
| **Right-Association Clash** | `John returns a copy of a book that is damaged.` | Attaches `that is damaged` to `a book`, not `a copy` | `John returns a copy of a book. The copy is damaged.` |
| **Relative Clause Coordination** | `The customer enters a card that is valid and has a code.` | Attaches `and has a code` to `The customer` | `The customer enters a card that is valid and that has a code.` |
| **Coordinator Precedence** | `A client enters a red card or enters a blue card and enters a code.` | Binds `and` before `or` (`R or (B and C)`) | `A client enters a red card or enters a blue card, and enters a code.` |
| **Inverted Quantifier Scope** | `John assigns a personal-code to every customer.` | Assigns one shared code to all customers ($\exists \forall$) | `For every customer John assigns a personal-code to the customer.` |
| **Ambiguous Universal Negation** | `All workers do not hold a lock.` | Confuses universal negation with partial negation | `No worker holds a lock.` |
