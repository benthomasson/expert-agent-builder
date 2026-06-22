---
name: expert-build
description: Build expert agents from documented domains
argument-hint: "[init|chunk-pdf|chunk-docs|summarize|propose-beliefs|accept-beliefs|pipeline|derive-review-repair|index-sources|cert-coverage|exam|status]"
allowed-tools: Bash(expert-build *), Bash(uvx *ftl-expert-build*), Read, Grep, Glob
---

# Expert Agent Builder

Build External Epistemic Memory (EEM) from documented domains.

An EEM is knowledge that lives outside the model, carries its justifications with it, and makes "how do you know that?" answerable by traversing justification chains. Unlike RAG (which retrieves by similarity but lacks justification), EEM maintains justified beliefs with truth values (IN/OUT), retraction cascades, contradiction records (nogoods), and derivation depth in a persistent belief maintenance system.

The architecture is hybrid: a symbolic BMS (based on Doyle's 1979 TMS) manages justifications, cascades, and backtracking, while LLMs handle semantic tasks — deriving new beliefs, reviewing existing ones, and detecting contradictions. The derive-then-review cycle intentionally over-derives, then a review pass catches errors and retraction cascades propagate corrections automatically.

This tool automates the EEM construction pipeline:
chunk documents → generate summaries → extract beliefs → derive/review/repair → build search indexes.

See https://llmeem.ai/ for the full specification.

## How to Run

```bash
expert-build [command]
# or
uvx --from ftl-expert-build expert-build [command]
```

## Commands

### init
Bootstrap a new expert agent repo.
```bash
expert-build init rhcsa --domain "Red Hat Certified System Administrator"
```
Creates: CLAUDE.md, expert-build.md, sources/, reasons.db

### chunk-pdf
Chunk a PDF into section-based source documents.
```bash
expert-build chunk-pdf sources/paper.pdf --prefix doyle-1979 --source-label 'Doyle 1979, "A Truth Maintenance System"'
expert-build chunk-pdf sources/paper.pdf --dry-run
```
Writes chunks to `sources/chunks/` with provenance frontmatter. Tracks progress in `.chunked-{prefix}` manifest.

### chunk-docs
Chunk large .md/.py/.txt files by structural boundaries.
```bash
expert-build chunk-docs --input-dir ~/git/my-project --recursive
expert-build chunk-docs --threshold 20000 --dry-run
```
Splits markdown by headings, Python by class/def boundaries, falls back to fixed-size windows. Writes to `sources/chunks/`.

### summarize
Generate summaries from source documents using an LLM.
```bash
expert-build summarize --parallel 4
expert-build summarize --input-dir ~/git/my-project --recursive --parallel 4
expert-build summarize --limit 10 --model gemini
```
Reads .md, .py, and .txt files from `sources/`, writes summaries to `entries/`.

### propose-beliefs
Extract candidate beliefs from summaries for review.
```bash
expert-build propose-beliefs --parallel 4
expert-build propose-beliefs --batch-size 10 --model claude
expert-build propose-beliefs --entry entries/2026/06/01/specific-file.md
```
Writes `proposed-beliefs.md` with `[ACCEPT]` or `[REJECT]` markers. LLM recommends accept/reject.

### accept-beliefs
Import accepted beliefs from the proposals file.
```bash
expert-build accept-beliefs
```
Reads `proposed-beliefs.md`, adds each `[ACCEPT]` belief to reasons.db.

### pipeline
Run end-to-end EEM construction (9 stages).
```bash
expert-build pipeline --sources-dir ~/git/my-project --parallel 4 --recursive
expert-build pipeline --pdf paper.pdf --domain "TMS"
expert-build pipeline --resume
```
Stages: ingest → summarize → extract → derive/review/repair/dedup loop → export → index.

### derive-review-repair
Run derive/review/repair loop on existing beliefs.
```bash
expert-build derive-review-repair --rounds 5
expert-build derive-review-repair --model gemini
```

### index-sources
Build FTS5 search database from source documents.
```bash
expert-build index-sources --input-dir ~/git/my-project --recursive
expert-build index-sources --input-dir entries/ --type summary
expert-build index-sources --rebuild
```
Creates `rag_fts.db` compatible with `reasons search-sources`.

### cert-coverage
Map certification objectives to beliefs, report coverage gaps.
```bash
expert-build cert-coverage objectives/ex200.md
expert-build cert-coverage objectives/ex200.md --model claude
```

### exam
Run practice questions through LLM with belief context, discover gaps.
```bash
expert-build exam questions/practice.md
expert-build exam questions/practice.md --limit 20 --output results.md
```

### status
Show pipeline progress.
```bash
expert-build status
```

## Natural Language

If the user says:
- "build an expert agent for X" → `expert-build init X`
- "chunk this paper" → `expert-build chunk-pdf <path>`
- "chunk these docs" → `expert-build chunk-docs --input-dir <path>`
- "summarize the sources" → `expert-build summarize`
- "extract beliefs" → `expert-build propose-beliefs`
- "run the pipeline" → `expert-build pipeline`
- "refine the beliefs" → `expert-build derive-review-repair`
- "build the search index" → `expert-build index-sources`
- "check coverage" → `expert-build cert-coverage <file>`
- "run the exam" → `expert-build exam <file>`
- "how are we doing" → `expert-build status`
