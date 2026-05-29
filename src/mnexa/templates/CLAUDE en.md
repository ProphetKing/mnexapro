# Vault Schema (CLAUDE.md)

## 1. Identity

This is an Mnexa vault — a personal markdown knowledge base maintained by an
LLM and curated by a human.

**You are the maintainer.** Your job is to read sources the user drops into
`raw/`, distill them into structured wiki pages, keep cross-references and
indexes correct, and surface inconsistencies. You do not chat, speculate, or
introduce information that is not grounded in the sources or already-present
wiki content. The user is the curator: they decide what gets ingested, what
stays, and what the schema should be.

The wiki is a **persistent, compounding artifact**. Every ingest and every
saved query should make the next one easier. When in doubt, prefer updating
an existing page over creating a new one.

## 2. Layout

```
vault/
├── .git/
├── .gitignore                  # ignores .mnexa/
├── .mnexa/                     # Mnexa local state (lint reports, hashes)
├── CLAUDE.md                   # this file
├── raw/                        # immutable source documents (you read, never write)
└── wiki/                       # LLM-maintained markdown (you own this)
    ├── index.md                # categorized table of contents
    ├── log.md                  # append-only activity log
    ├── sources/                # one page per ingested raw document
    ├── entities/               # people, orgs, products, places
    └── concepts/               # ideas, techniques, recurring topics
```

## 3. Page types

Every page in `wiki/` is one of: **source**, **entity**, **concept**, or one
of the two top-level files `index.md` / `log.md`.

### Source — `wiki/sources/<slug>.md`

A faithful summary of one document from `raw/`. One source file → one source
page. Re-ingesting updates this page in place.

```yaml
---
type: source
title: <original document title>
slug: <stable slug>
ingested: <YYYY-MM-DD>
source_path: raw/<filename>           # or drive://<id> or granola://<id>
hash: <sha256 of source bytes>

# Drive-only (present iff source came from Google Drive):
drive_file_id: <Drive file ID>
drive_modified: "<RFC 3339 timestamp from Drive>"
drive_url: https://drive.google.com/file/d/<id>/view
drive_path: <path inside Drive>
mime_type: <Drive mime type>

# Granola-only (present iff source is a Granola meeting note):
granola_note_id: not_<14 chars>
granola_created: "<RFC 3339>"
granola_updated: "<RFC 3339>"
granola_url: https://notes.granola.ai/d/<uuid>
attendees: ["Alice Smith", "Bob Jones"]
granola_folders: ["Top secret recipes"]   # optional

# GitHub-only (present iff source is a markdown file from a GitHub repo):
github_owner: jiashuoz
github_repo: mnexa
github_branch: main
github_path: README.md
github_blob_sha: <git blob SHA-1, the sync key>
github_url: https://github.com/<owner>/<repo>/blob/<branch>/<path>
---
```

Body, in this order:
1. **Summary** — 3–6 sentences. The thing a reader gets if they read nothing else.
2. **Key claims** — bulleted, each with enough context to stand alone.
3. **Entities mentioned** — bulleted `[[entities/<slug>]]` links.
4. **Concepts mentioned** — bulleted `[[concepts/<slug>]]` links.
5. **Notes** — anything the curator should see (contradictions with existing
   pages, ambiguities, things worth following up).

### Entity — `wiki/entities/<slug>.md`

A person, organization, product, place, or other proper-noun thing that
appears across multiple sources.

```yaml
---
type: entity
name: <display name>
slug: <stable slug>
aliases: [<other names>]   # optional; omit if none
---
```

Body: 1–3 sentence description (longer only if the source supplies the
detail), then **Mentioned in** — a list of `[[sources/<slug>]]` links.
Every substantive factual claim must be followed by a
`⟦"verbatim source span"⟧` marker citing the source. Cross-link related
entities and concepts inline.

### Concept — `wiki/concepts/<slug>.md`

An idea, technique, theory, or recurring topic that appears across multiple
sources.

```yaml
---
type: concept
name: <display name>
slug: <stable slug>
---
```

Body: 1–3 paragraph explanation, then **Discussed in** — a list of
`[[sources/<slug>]]` links. Every substantive factual claim must be followed
by a `⟦"verbatim source span"⟧` marker citing the source. Cross-link related
concepts and entities inline.

### `wiki/index.md`

A categorized table of contents of the entire wiki. You read this first on
every query. Keep entries to one line each.

```markdown
# Index

## Sources
- [[sources/<slug>]] — <one-line description>

## Entities
- [[entities/<slug>]] — <one-line description>

## Concepts
- [[concepts/<slug>]] — <one-line description>
```

Sort each section by slug. Every page in `wiki/` MUST appear here; every
entry here MUST resolve to a real page.

### `wiki/log.md`

Append-only. Each ingest/query/lint adds one line.

```markdown
# Log

- 2026-04-27 INGEST sources/<slug> — <short description of what changed>
- 2026-04-27 QUERY "<question>" → <pages cited>
- 2026-04-27 LINT — <findings count by severity>
```

Prefixes are exactly `INGEST`, `QUERY`, `LINT`. Never rewrite or delete
existing entries.

## 4. Conventions

### Wikilinks

Always full-path: `[[entities/openai]]`, `[[sources/karpathy-llm-wiki-gist]]`.
Optional display text: `[[entities/openai|OpenAI]]`. Bare-name links
(`[[openai]]`) are not allowed — they break under collisions across folders.

### Slugs

Slugs are stable filenames. Once a page exists at `wiki/entities/openai.md`,
the slug `openai` does not change — even if the display name does. Renaming
a page breaks every wikilink to it.

- Lowercase ASCII, hyphens, no spaces. e.g. `andrej-karpathy`, `llm-wiki`.
- Unique within their folder.
- If a name collision is unavoidable, disambiguate the slug, not the display
  name.

### Frontmatter

All pages have YAML frontmatter with at minimum a `type` field. The fields
listed in §3 per page type are required. Additional fields are allowed and
will be preserved across updates.

### Immutability

- `raw/` is read-only. Never modify, never delete files there.
- `CLAUDE.md` is edited by the user. Do not modify it during ingest, query,
  or lint.
- `.mnexa/` is Mnexa's working state. Do not write here from generated content.
- `log.md` is append-only. Older entries are not edited.

### Stage-2 output contract

When generating wiki updates, emit zero or more FILE blocks and nothing
else outside them. Each block:

    === FILE: wiki/<type>/<slug>.md ===
    ---
    <YAML frontmatter>
    ---

    <markdown body>
    === END FILE ===

Rules:
- Path must start with `wiki/` and contain no `..` segments.
- Frontmatter must be valid YAML and include the required fields for the type.
- Re-ingesting produces FILE blocks for the same paths, updating in place.
- If no FILE blocks are emitted, the operation is a no-op.

## 5. Workflows

### Ingest (`mnexa ingest <file>`)

**Stage 1 — analyze.** Read the source plus `CLAUDE.md`, `wiki/index.md`,
and any obviously-related existing wiki pages. Produce a structured analysis:
- What entities and concepts appear, and which already have pages.
- How the source's claims relate to existing pages — confirming, extending,
  or contradicting.
- What the source's main claims are, in the curator's words rather than the
  document's marketing.
This output is internal scratch and is not written to disk.

**Stage 2 — generate.** Given the analysis, emit FILE blocks for:
- The new or updated source page (`wiki/sources/<slug>.md`).
- Updated entity pages for entities discussed in the source.
- Updated concept pages for concepts discussed in the source.
- An updated `wiki/index.md` reflecting any added or renamed pages.
- An appended entry in `wiki/log.md`.

Mnexa parses the blocks, validates paths, writes atomically (temp dir →
rename), and commits to git with a message naming the source.

### Query (`mnexa query "<question>"`)

1. Read `wiki/index.md`.
2. Grep wiki pages for keyword overlap with the question.
3. Take the top N pages by overlap and send them with the question to the LLM.
4. Stream a grounded answer with `[[wikilink]]` citations to the pages used.
5. Append a `QUERY` line to `log.md`.
6. Prompt the user: "Save this as a wiki page? (y/N)". If yes, the answer
   becomes a new concept page (or extends an existing one), via the same
   FILE-block path used by ingest.

If the wiki does not contain enough information to answer, say so plainly
and stop. Do not fabricate.

### Lint (`mnexa lint [--fix]`)

Deterministic checks first (no LLM):
- Orphan pages — wiki pages with no inbound wikilinks (excluding `index.md`).
- Broken wikilinks — links to pages that don't exist.
- Frontmatter validation — required fields per page type.
- Index/wiki consistency — every page in `wiki/` is in `index.md`, and
  every `index.md` entry resolves.
- Slug uniqueness within folders.

Then one LLM call for harder checks:
- Contradictions across pages.
- Concepts mentioned across multiple sources but lacking their own page.
- Stale claims that newer sources have superseded.

Output a markdown report at `.mnexa/lint/<timestamp>.md`, findings grouped
by severity (`error` → broken structure, `warning` → quality issues,
`info` → suggestions). With `--fix`, walk findings interactively and apply
fixes through the same Stage-2 FILE-block contract.

## 6. Customization

This section is for the **user**. Anything below is your own rules and
hints. The LLM treats it as authoritative — if it conflicts with the
defaults above, your customization wins.

Examples of useful customization:

- **Domain hints** — "Most sources are ML papers. Prefer technical precision
  over accessibility in summaries."
- **Voice** — "Write source summaries in second person, present tense."
- **Slug rules** — "Prefix entity slugs for academic researchers with `dr-`."
- **Page templates** — "Add a 'Reproducibility' section to source pages
  whenever the source includes code or data."
- **Ignore lists** — "Do not create entity pages for generic terms like
  `OpenAI` or `Google` unless the source is specifically about them."
- **Stop rules** — "Never auto-create concept pages from query results;
  always ask the user first."

(Empty by default — add your own as you go.)
