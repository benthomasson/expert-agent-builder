"""Prompt templates for expert agent builder."""

SUMMARIZE = """\
You are an expert technical writer creating structured study notes.

Given the following documentation page, create a concise summary suitable for \
building domain expertise. Structure your output as:

## Overview
One paragraph summarizing what this page covers.

## Key Concepts
Bulleted list of the most important facts, definitions, and concepts.

## Commands and Syntax
Any commands, configuration syntax, or procedures described (with examples).

## Relationships
How this topic connects to other topics in the domain.

## Exam-Relevant Points
Facts that are likely to be tested on a certification exam.

---

SOURCE DOCUMENT:

{content}
"""

PROPOSE_BELIEFS = """\
You are extracting factual claims from study notes to build a belief registry.

For each significant factual claim in the entries below, output a proposed belief \
in this exact format:

### [ACCEPT/REJECT] <belief-id-in-kebab-case>
<one-line factual claim>
- Source: <path to the entry file>

Rules:
- Each belief should be a single, testable factual claim
- Use kebab-case IDs that are descriptive (e.g., rhel9-default-filesystem-xfs)
- Prefer specific facts over vague generalizations
- Include commands, paths, config values when relevant
- Do NOT include opinions or subjective assessments
- Aim for 3-8 beliefs per entry (not every sentence is a belief)

---

ENTRIES:

{entries}
"""

EXAM_ANSWER = """\
You are a domain expert answering an exam question. Use only the knowledge \
provided in your beliefs below. If you are unsure, say so.

Your current knowledge (beliefs):
{beliefs}

Question: {question}
{choices}

Provide your answer and brief explanation. Format:
ANSWER: <letter or text>
EXPLANATION: <one paragraph>
"""

CERT_MATCH = """\
Given a certification objective and a list of beliefs, determine which beliefs \
(if any) cover this objective. Return the belief IDs that match, one per line. \
If none match, return "NONE".

Objective: {objective}

Beliefs:
{beliefs}

Matching belief IDs (one per line, or NONE):
"""
