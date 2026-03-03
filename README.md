# expert-agent-builder

Build expert agents from documented domains. Automates the knowledge pipeline: fetch docs, generate entries, extract beliefs, map certification coverage, run practice exams.

## Install

```bash
uv tool install git+https://github.com/benthomasson/expert-agent-builder
```

## Quick Start

```bash
# Bootstrap a new expert agent
expert-build init rhcsa --domain "Red Hat Certified System Administrator"

# Fetch documentation
expert-build fetch-docs https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/ --depth 2

# Generate entries from sources
expert-build summarize

# Extract beliefs for review
expert-build propose-beliefs
# Edit proposed-beliefs.md, mark entries as [ACCEPT] or [REJECT]
expert-build accept-beliefs

# Check certification coverage
expert-build cert-coverage objectives/ex200.md

# Run practice exam
expert-build exam questions/ex200-practice.md

# Check progress
expert-build status
```

## Prerequisites

Requires these CLI tools to be installed:
- `entry` — creates chronological entries
- `beliefs` — manages belief registry
- `shared-enterprise` — indexes entries into SQLite

## Pipeline

```
fetch-docs → summarize → propose-beliefs → accept-beliefs → cert-coverage → exam
     ↓            ↓              ↓                ↓               ↓           ↓
  sources/    entries/    proposed-beliefs.md   beliefs.md    coverage report  nogoods
```
