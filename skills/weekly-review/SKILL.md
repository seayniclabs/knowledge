---
name: weekly-review
description: Write a weekly review of what changed in an Open Notebook knowledge base, with done, open and next-week items linked to the notes that show them. Use when the user asks for a weekly review, what they worked on this week, a status summary from their notes, or a standup built from their notebooks.
license: MIT
source: https://github.com/seayniclabs/knowledge
author: Seaynic Labs
---

# Weekly review

Produce a review the user can read in two minutes. Every item names the note it came from. Open Notebook does not report when a source was added, so the review covers notes only.

## Gather

1. Call `open_notebook_notebooks` with `limit=100`. Keep the notebooks whose `updated` date falls in the last 7 days. If the user named a notebook, keep only that one.
2. For each kept notebook, call `open_notebook_notes` with its `notebook_id` and `limit=50`. Page with `next_offset` while `has_more` is true. Keep the notes whose `updated` date falls in the last 7 days.
3. Call `open_notebook_note_get` with `max_chars=1500` for each kept note that the titles alone do not explain. Stop at 15 notes. Name the rest in the review without reading them.

## Sort what you found

- **Done:** notes that record a finished result, a decision or a closed item.
- **Open:** notes that end in a question, a TODO, an unresolved risk or a blocked item.
- **Next week:** any item the notes say is planned, with its due date when one is written.

Put each item in one bucket only. When a note fits two buckets, use the one that matches its last paragraph.

## Write

```
Weekly review, <start date> to <end date>
Notebooks touched: <n>. Notes updated: <n>.

Done
- <item> (<notebook name>: "<note title>")

Open
- <item> (<notebook name>: "<note title>")

Next week
- <item>, due <date or "no date written">
```

Keep each bucket to ten items. Put the rest on a line that reads "and <n> more in <notebook name>".

## Save it (optional)

Only when the user asks. Call `open_notebook_note_create` with the review text and the notebook the user names, without `confirm`. Show the user the preview. Call again with `confirm=true` only after the user agrees to save it. The user approves the save in the MCP client.

## When the data is thin

If fewer than three notes were updated in the window, say so in the first line. Do not pad the review.
