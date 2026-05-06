---
name: expert-build
description: Build expert agents from documented domains
argument-hint: "[init|chunk-pdf|fetch-docs|summarize|propose-beliefs|accept-beliefs|cert-coverage|exam|status]"
allowed-tools: Bash(expert-build *), Bash(uvx *expert-agent-builder*), Read, Grep, Glob
---

# Expert Agent Builder

Build expert agents from documented domains by automating the knowledge pipeline:
chunk PDF / fetch docs → generate entries → extract beliefs → map coverage → run exams.

## How to Run

```bash
expert-build [command]
# or
uvx --from git+https://github.com/benthomasson/expert-agent-builder expert-build [command]
```

## Commands

### init
Bootstrap a new expert agent repo.
```bash
expert-build init rhcsa --domain "Red Hat Certified System Administrator"
```
Creates: CLAUDE.md, expert-build.md, entries/, sources/, reasons.db

### chunk-pdf
Chunk a PDF paper into section-by-section entries for deep analysis.
```bash
expert-build chunk-pdf sources/paper.pdf --prefix doyle-1979 --source-label 'Doyle 1979, "A Truth Maintenance System"'
expert-build chunk-pdf sources/paper.pdf --dry-run  # preview sections only
expert-build chunk-pdf sources/paper.pdf --model gemini --timeout 900
```
Reads the PDF, uses an LLM to identify sections, then generates one entry per section with detailed content and key claims. Tracks progress in `.chunked-{prefix}` manifest for idempotent reruns.

### fetch-docs
Fetch documentation from URLs, convert to markdown.
```bash
expert-build fetch-docs https://docs.example.com/ --depth 2
expert-build fetch-docs https://docs.example.com/sitemap.xml --sitemap
```
Saves to `sources/` with frontmatter recording source URL and fetch date.

### summarize
Generate entries from fetched source documents using an LLM.
```bash
expert-build summarize
expert-build summarize --limit 10 --model claude
```
Reads `sources/`, calls LLM for structured summaries, creates entries via `entry create`.

### propose-beliefs
Extract candidate beliefs from entries for human review.
```bash
expert-build propose-beliefs
expert-build propose-beliefs --batch-size 10 --model claude
```
Writes `proposed-beliefs.md` with `[ACCEPT/REJECT]` markers. Human reviews and edits.

### accept-beliefs
Import accepted beliefs from the proposals file.
```bash
expert-build accept-beliefs
```
Reads `proposed-beliefs.md`, adds each `[ACCEPT]` entry to the reasons database.

### cert-coverage
Map certification objectives to beliefs, report coverage gaps.
```bash
expert-build cert-coverage objectives/ex200.md
expert-build cert-coverage objectives/ex200.md --model claude
```
Reports COVERED objectives (with matching beliefs) and GAPS.

### exam
Run practice questions through LLM with belief context, discover nogoods.
```bash
expert-build exam questions/ex200-practice.md
expert-build exam questions/ex200-practice.md --limit 20
expert-build exam questions/ex200-practice.md --output results.md
expert-build exam questions/ex200-practice.md -o results/exam-2026-03-04.md --model gemini
```
Options:
- `--output, -o` — Save results to markdown file (per-question detail, model responses, by-objective breakdown)
- `--model` — Model to use (default: claude)
- `--beliefs-file` — Path to reasons database (default: reasons.db)
- `--limit` — Max questions to process

Wrong answers are recorded as nodes in the reasons database.

### status
Show pipeline progress.
```bash
expert-build status
```

## Natural Language

If the user says:
- "build an expert agent for X" → `expert-build init X`
- "chunk this paper" → `expert-build chunk-pdf <path>`
- "analyze this PDF" → `expert-build chunk-pdf <path>`
- "fetch the docs" → `expert-build fetch-docs <url>`
- "summarize the sources" → `expert-build summarize`
- "extract beliefs" → `expert-build propose-beliefs`
- "check coverage" → `expert-build cert-coverage <file>`
- "run the exam" → `expert-build exam <file>`
- "how are we doing" → `expert-build status`
