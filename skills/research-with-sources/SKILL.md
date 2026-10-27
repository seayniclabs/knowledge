---
name: research-with-sources
description: Answer a question from the user's own Open Notebook sources and notes, citing each claim to a source, and flag where two sources contradict each other. Use when the user asks what their notes say about a topic, wants an answer backed by their saved material, or asks to compare what sources say.
license: MIT
source: https://github.com/seayniclabs/knowledge
author: Seaynic Labs
---

# Research with sources

Answer from the user's notebooks, not from memory. Every claim names the source it came from. When two sources disagree, show both.

## 1. Pick the notebook

Call `open_notebook_notebooks`. If the user named a notebook, use its `id`. If not, search all notebooks by leaving out `notebook_id`, and list the notebook names of the hits.

## 2. Search in rounds

1. Call `open_notebook_search` with `mode="text"` and the two or three most specific words of the question. Use `limit=10`.
2. If there are fewer than three usable hits, try two more phrasings: a synonym, and the name of the thing the question is about. Stop after three rounds.
3. Call `open_notebook_search` with `mode="vector"` only when text search finds nothing. Vector search needs an embedding model on the server. If it returns an error, say that it is not set up.

Page with `offset=<next_offset>` only when the first page holds nothing relevant.

## 3. Read the sources

For the best five hits:

- `kind` is `source`: call `open_notebook_source_get` with `max_chars=8000`.
- `kind` is `note`: call `open_notebook_note_get` with `max_chars=8000`.

Read the `snippet` of the rest. Do not open them unless a claim needs them.

## 4. Write the answer

Lead with the answer in one or two sentences. Then give the supporting points. Cite each claim in square brackets with a short label that matches the source list: `[S1]`, `[S2]`.

```
<Answer in one or two sentences.> [S1][S3]

Sources
[S1] <title> (source:<id>, notebook: <name>)
[S2] <title> (note:<id>, notebook: <name>)
```

A claim with no source gets no citation, and you say it is your own inference.

## 5. Flag contradictions

When two sources disagree on a number, a date, a name or a conclusion, do not pick one silently. Write:

> Sources disagree. [S1] says <claim>. [S2] says <claim>. [S1] is <newer / a primary source / a summary>, so <what that means>. Check <what would settle it>.

Compare dates from the `updated` or `created` fields when they are present.

## 6. Say what is missing

End with one line on what the notebooks do not cover, for example: "No source in these notebooks covers pricing after 2025."

## Do not

- Do not invent a citation or a source id. Use only ids the tools returned.
- Do not save the answer to a notebook unless the user asks. When they ask, use `open_notebook_note_create` without `confirm` first, show the preview, and let the user approve the save.
