# Mnexa

A disciplined wiki maintainer for a personal markdown knowledge base. Throw any file at it — local, a folder, a Google Drive URL, or a Granola meeting note — and an LLM reads it and maintains a structured wiki of source / entity / concept pages with cross-references, an index, and a log. You curate; the LLM does the bookkeeping.

Implementation of the pattern in [Andrej Karpathy's LLM Wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) — read that first; it's the design spec.

## Why

Most LLM document tools are RAG: retrieve chunks at query time, generate from the chunks, throw the synthesis away. Mnexa treats the wiki as a **persistent, compounding artifact** — every ingest updates entity and concept pages once, every query runs against accumulated synthesis instead of re-deriving from raw sources. Open the wiki in Obsidian, Logseq, VS Code, or any markdown editor. The LLM is the maintainer; you are the curator.

## Install

Requires Python 3.12+. Get a Gemini API key at <https://aistudio.google.com/apikey>.

```bash
# from PyPI
uv tool install mnexa            # or: pip install mnexa
# or for development
git clone https://github.com/jiashuoz/mnexa && cd mnexa && uv sync
```

Set `GOOGLE_API_KEY` in your shell or in a `.env` file at the vault root. See [`.env.example`](.env.example).

## Use

```bash
# Create a new vault
mnexa init ~/my-vault
cd ~/my-vault

# Ingest anything — local file, local folder, Google Drive URL, or Granola
mnexa ingest paper.pdf
mnexa ingest ~/Documents/papers/
mnexa ingest "https://drive.google.com/drive/folders/<id>"
mnexa ingest "https://drive.google.com/file/d/<id>"
mnexa ingest "https://app.granola.ai/notes/<id>"
mnexa ingest granola                              # all your Granola notes
mnexa ingest granola --since 2026-04-01           # incremental

# Ask the wiki a question
mnexa query "what does this paper claim?"

# Audit the wiki
mnexa lint
```

Folder ingests support `--yes` / `-y` to skip confirmation and `--limit N` to cap files per run. Re-running an ingest on a folder skips files whose source hasn't changed (Drive: by `modifiedTime`; local: by content hash).

## Vault layout

```
my-vault/
├── .git/
├── .gitignore                  # ignores .mnexa/ and .env
├── .mnexa/                     # Mnexa local state (lint reports)
├── CLAUDE.md                   # the schema — edit §6 to customize
├── raw/                        # immutable source documents
└── wiki/
    ├── index.md                # categorized table of contents
    ├── log.md                  # append-only activity log
    ├── sources/                # one page per ingested document
    ├── entities/               # people, orgs, products, places
    └── concepts/               # ideas, techniques, recurring topics
```

Every successful ingest is a git commit. Free undo, free history, free diff.

## How it works

**Ingest** is a two-stage pipeline:

1. **Analyze** — LLM reads the source plus the schema, index, and obviously-related existing pages. Produces a structured analysis (entities, concepts, claims, contradictions). Internal scratch.
2. **Generate** — LLM emits FILE blocks for the new/updated wiki pages. Mnexa parses, validates paths and frontmatter, **substring-verifies that every `⟦"..."⟧` source-quote marker appears verbatim in the source**, then atomically writes and commits.

The substring verifier is the anti-hallucination floor. If the LLM invents a biographical detail not present in the source, the marker check fails and the ingest aborts with no on-disk changes.

**Query** is a single LLM call against `index.md` + the top-N pages by keyword overlap, streamed to stdout with inline `[[wikilink]]` citations. Drive-sourced pages carry `drive_url:` in their frontmatter, so query answers naturally surface clickable Drive links when relevant — no separate "find files" command.

**Lint** runs deterministic checks first (broken links, frontmatter, index/wiki sync, orphans, ungrounded pages, slug style), then one LLM call for semantic checks (contradictions, stale claims, missing pages, slug typos). Output: `.mnexa/lint/<timestamp>.md`.

## Google Drive

Drive is a transport, not a separate concept. Same `mnexa ingest` command takes a Drive URL or a folder URL; mnexa fetches content in memory, ingests, and stores Drive metadata (`drive_file_id`, `drive_modified`, `drive_url`, `drive_path`, `mime_type`) in the resulting source page's frontmatter. Originals stay in Drive — nothing is downloaded to `raw/`.

Re-syncing is idempotent: walking a folder again skips files whose `drive_modified` matches what's already on disk. Source-page depth adapts to content — a paper gets a full structured page; a tax form or receipt gets a brief one without entity/concept synthesis.

**One-time GCP setup** (required for Drive):

1. Create a project at <https://console.cloud.google.com> and enable the Google Drive API.
2. Create OAuth credentials → "Desktop app" → download the JSON.
3. Set `MNEXA_GOOGLE_CLIENT_ID` and `MNEXA_GOOGLE_CLIENT_SECRET` in your `.env`.
4. On the OAuth consent screen, set User Type = **External**, Publishing status = **Testing**, scope = `drive.readonly`, and add yourself as a test user.

First Drive ingest opens a browser for OAuth; the refresh token is cached at `~/.config/mnexa/google-token.json` and used silently after that.

## Granola

Granola meeting notes work the same way: same `mnexa ingest` command, transport hidden. Auth is just a Bearer token — no OAuth dance.

**Setup**:

1. Generate a personal API key at <https://app.granola.ai> (Business or Enterprise plan required — Granola-side limitation).
2. Set `GRANOLA_API_KEY` in your `.env`.
3. `mnexa ingest granola://note/not_<14-char-id>` to ingest one meeting, or `mnexa ingest granola` to walk your entire notes list. (Granola's web share URLs `notes.granola.ai/d/<uuid>` use a different identifier than the API; you need the `not_*` note ID, not the share URL.)

The big win for this source type is that **participants become entity pages**. After 30 ingested meetings, `entities/alice-smith.md` synthesises every topic you've discussed with her, with verifiable quotes from the transcripts. That's exactly what the wiki pattern is for.

Frontmatter on a Granola-sourced page:

```yaml
type: source
slug: 2026-04-15-design-review
source_path: granola://not_1d3tmYTlCICgjy
granola_note_id: not_1d3tmYTlCICgjy
granola_created: "2026-04-15T14:00:00Z"
granola_updated: "2026-04-15T15:30:00Z"
granola_url: https://notes.granola.ai/d/<uuid>
attendees: ["Alice Smith", "Bob Jones"]
granola_folders: ["Engineering"]
```

`mnexa ingest granola` is idempotent — it walks the notes list, reads existing source-page frontmatter, and skips notes whose `granola_updated` matches. Use `--since YYYY-MM-DD` to only fetch notes updated after a given date.

## LLM

Provider-agnostic via a small `LLMClient` protocol. v0 ships Google Gemini (default `gemini-3-flash-preview`). Set `MNEXA_MODEL` to any `gemini-*` model; set `MNEXA_PROVIDER` to override the auto-inference. Adding Anthropic or OpenAI is ~80 lines plus an extras entry — not shipped because no one needs it yet.

## Status

| | |
|---|---|
| `mnexa init` | ✅ |
| `mnexa ingest` (local file / folder) | ✅ — `.md`, `.txt`, `.pdf`, `.docx` |
| `mnexa ingest` (Google Drive file / folder) | ✅ — adaptive-depth, idempotent re-sync |
| `mnexa ingest` (Granola meeting notes) | ✅ — single note or full list, incremental via `--since` |
| `mnexa query` | ✅ |
| `mnexa lint` | ✅ |
| `mnexa lint --fix` | not yet (v0.1) |
| save query answer as wiki page | not yet (v0.1) |
| Anthropic / OpenAI providers | not yet |
| Notion / other sources | planned |

## Develop

```bash
uv sync --all-extras
uv run pytest         # 54 tests
uv run ruff check .
uv run pyright        # strict
```

Prompts live as files in [`src/mnexa/prompts/`](src/mnexa/prompts) and load via `importlib.resources`. Edit them, rerun, iterate.

## Design notes

- **Pure markdown is the canonical store.** No SQLite, no vector index, no FTS5. Karpathy's gist argues `index.md` is enough at moderate scale; we believe it until measurements say otherwise.
- **Two-stage ingest** is borrowed from [nashsu/llm_wiki](https://github.com/nashsu/llm_wiki); the **deterministic-then-LLM lint tier** is borrowed from [SamurAIGPT/llm-wiki-agent](https://github.com/SamurAIGPT/llm-wiki-agent). The substring-grounding verifier is novel — neither reference project does it.
- Atomic-ish writes via stage-then-rename + `git checkout HEAD --` rollback on failure. The git commit is the durability barrier.
- Gemini context caching is a no-op at our schema size (~3k tokens, below the threshold). The protocol still expresses intent so other providers can honor it.
