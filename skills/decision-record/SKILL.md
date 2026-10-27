---
name: decision-record
description: Create a lightweight Architecture Decision Record (ADR) as a numbered Markdown file in a Decisions folder, with context, decision, consequences and alternatives. Use when the user wants to record a technical or product decision, write an ADR, document why something was chosen, or log a choice for later review.
license: MIT
source: https://github.com/seayniclabs/knowledge
author: Seaynic Labs
---

# Decision record

Write one ADR per decision in a `Decisions/` folder in the current working directory. Each ADR is numbered, dated and stays in the folder.

## Steps

1. **Get the title.** Use the title the user gave, for example "Use Postgres over SQLite". If none, ask: "What decision are you recording? (short title)".
2. **Get the context.** Ask: "What situation or problem led to this decision?" Use the answer, and expand it to two or three sentences that name the constraints.
3. **Get the project (optional).** Ask: "Which project is this for? (press Enter to skip)".
4. **Find the next number.** If `Decisions/` does not exist, the number is `ADR-001`. Otherwise read the `adr_number:` line of each `.md` file in the folder, take the highest, and add one. Write it with three digits.
5. **Build the file name.** Use `YYYY-MM-DD-<slug>.md`. The slug is the title in lowercase, with characters other than letters, digits and hyphens removed, spaces turned into hyphens, repeated hyphens collapsed and the ends trimmed. "Use Postgres over SQLite" on 2026-03-12 becomes `2026-03-12-use-postgres-over-sqlite.md`.
6. **Create the folder** with `mkdir -p Decisions` if needed.
7. **Write the file** with the template below.
8. **Report the result.** Print the absolute path, the ADR number, and this line: "Fill in Decision, Consequences and Alternatives. The template has the headings ready."

## Template

```markdown
---
tags: [decision]
created: YYYY-MM-DD
updated: YYYY-MM-DD
status: proposed
adr_number: ADR-NNN
superseded_by:
project: <project name, or leave out the line>
---

# ADR-NNN: Decision Title

## Status

**Proposed**, YYYY-MM-DD

## Context

<Two or three sentences: the situation, the constraints and the forces at play.>

## Decision

<What is being decided, in one or two sentences, in the active voice.>

## Consequences

### Easier

-

### Harder or riskier

-

## Alternatives considered

| Option | Pros | Cons | Why not chosen |
|--------|------|------|----------------|
| | | | |

## Review date

<When to revisit this decision, for example "after six months in production" or "before v2.0". Write "none" when there is no date.>
```

## Rules

- Use the same date for `created` and `updated`.
- Leave `superseded_by` empty. Fill it in only when a later ADR replaces this one.
- Leave the `project:` line out when no project was given.
- Do not open the file. Report the path.
- Never change the number or the title of an existing ADR. A changed decision gets a new ADR that supersedes the old one.
