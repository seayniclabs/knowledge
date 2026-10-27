---
name: meeting-notes
description: Create structured meeting notes as a Markdown file with attendees, decisions, action items and follow-up, and optionally save them to an Open Notebook notebook. Use when the user asks to write up a meeting, make meeting notes, record decisions and action items from a call, or turn a transcript into notes.
license: MIT
source: https://github.com/seayniclabs/knowledge
author: Seaynic Labs
---

# Meeting notes

Write one Markdown file per meeting in the current working directory. Keep the file plain, so it opens in any editor.

## Usage

- "Meeting notes for Weekly Sync" uses the title in the request.
- A request without a title asks the user for one before anything is written.

## Steps

1. **Get the title.** Use the title the user gave. If none, ask for one.
2. **Get the attendees.** Ask: "Who attended? (comma-separated names)". Keep the answer as a list.
3. **Get the project (optional).** Ask: "Which project is this for? (press Enter to skip)". Leave the `project:` line out when the answer is empty.
4. **Create the folder.** Run `mkdir -p "Meeting Notes"` in the working directory.
5. **Build the file name.** Use `YYYY-MM-DD-<slug>.md` with today's date. The slug is the title in lowercase, with spaces turned into hyphens, other characters removed and repeated hyphens collapsed. "Weekly Sync" on 2026-03-12 becomes `2026-03-12-weekly-sync.md`.
6. **Check for a clash.** If the file already exists, tell the user and ask before overwriting it.
7. **Write the file** with the template below. Fill in the date, the attendees, the project and the title. Fill the decisions, actions and discussion from what the user gives you or from a transcript they paste.
8. **Report the path.** Print the absolute path of the file. Do not open it.

## Template

```markdown
---
tags: [meeting]
created: YYYY-MM-DD
updated: YYYY-MM-DD
status: active
attendees: [Name One, Name Two]
project: Project Name
---

# Meeting Title

## Context

<!-- One or two sentences: what the meeting was about and why it happened now. -->

## Decisions

- **Decision:** what was decided
  - *Rationale:* why

## Action Items

| Action | Owner | Due date | Status |
|--------|-------|----------|--------|
| | | | Open |

## Discussion notes

<!-- Free text, in the order the topics came up. -->

## Follow-up

<!-- What must happen before the next meeting. -->
```

## Rules

- Write attendees as a YAML list on one line: `[Alice, Bob, Carol]`.
- Leave out the `project:` line when no project was given. Do not leave it empty.
- Leave a table row empty when the owner or due date was not stated. Do not guess an owner.

## Save to Open Notebook (optional)

Only when the user asks, and only when the Knowledge tools are available. Call `open_notebook_note_create` with the notebook the user names, the title, and the file's text. Call it without `confirm` first and show the preview. Call again with `confirm=true` after the user agrees. The user approves the save in the MCP client.
