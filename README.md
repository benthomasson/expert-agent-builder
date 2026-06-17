# expert-agent-builder

Build expert agents from documented domains. Automates the knowledge pipeline: fetch docs, chunk large files, generate summaries, extract beliefs, derive deeper conclusions, review and repair, build FTS5 search indexes.

## Install

```bash
uv tool install ftl-expert-build
```

Requires `ftl-reasons` and either `claude` or `gemini` CLI on PATH.

## Quick Start

```bash
# Bootstrap a new expert agent
expert-build init rhcsa --domain "Red Hat Certified System Administrator"

# Fetch documentation
expert-build fetch-docs https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/ --depth 2

# Generate entries from sources
expert-build summarize --parallel 4

# Extract beliefs for review
expert-build propose-beliefs --parallel 4
# Edit proposed-beliefs.md: LLM marks each as [ACCEPT] or [REJECT]
expert-build accept-beliefs

# Run the full pipeline end-to-end
expert-build pipeline --url https://docs.example.com --parallel 4
```

## Commands

| Command | Description |
|---------|-------------|
| `init` | Bootstrap a new expert agent repo |
| `fetch-docs` | Fetch documentation from URLs |
| `chunk-pdf` | Split PDFs into section-based entries |
| `chunk-docs` | Split large .md/.py/.txt files by structural boundaries |
| `summarize` | Generate entries from source documents via LLM |
| `propose-beliefs` | Extract candidate beliefs from entries via LLM |
| `accept-beliefs` | Import accepted beliefs into reasons.db |
| `cert-coverage` | Map certification objectives to beliefs |
| `exam` | Run practice questions, discover knowledge gaps |
| `pipeline` | Run end-to-end EEM construction (9 stages) |
| `derive-review-repair` | Run derive/review/repair loop on existing beliefs |
| `index-sources` | Build FTS5 chunks database for RAG search |
| `status` | Show pipeline progress |

## Pipeline Stages

```
1. Ingest (fetch-docs / chunk-pdf)
2. Summarize (LLM summaries of source documents)
3. Extract (propose-beliefs + accept-beliefs)
4-7. Derive → Review → Repair → Deduplicate (convergence loop)
8. Export (network.json + README card)
9. Index (FTS5 search database)
```

```bash
# Full pipeline with parallel LLM calls and recursive source discovery
expert-build pipeline --url https://docs.example.com --parallel 4 --recursive

# Resume after a crash
expert-build pipeline --resume

# Run just the knowledge refinement loop
expert-build derive-review-repair --rounds 5
```

## Working with Large Repos

```bash
# Summarize a repo with nested directories
expert-build summarize --input-dir ~/git/my-project --recursive --parallel 4

# Chunk large files before summarizing
expert-build chunk-docs --input-dir ~/git/my-project --recursive

# Build search index
expert-build index-sources --input-dir ~/git/my-project --recursive
expert-build index-sources --input-dir entries/ --recursive --type summary

# Query with reasons
reasons search-sources "kubernetes scheduling" --db rag_fts.db
reasons ask "How does pod scheduling work?" --full-sources rag_fts.db
```

## Features

- **Parallel LLM calls** — `--parallel N` on summarize, propose-beliefs, and pipeline
- **Recursive file discovery** — `--recursive` for nested directory structures
- **Cost tracking** — token counts and costs printed after every command
- **Crash resilience** — incremental writes, pipeline state file with `--resume`
- **JSON pseudo-tool-calling** — structured LLM output with retry for all parsing stages
- **Source provenance** — every entry tracks its source file, URL, and document ID
- **FTS5 indexing** — build search indexes compatible with `reasons search-sources`
