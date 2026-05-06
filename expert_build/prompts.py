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

EXAM_JUDGE = """\
You are grading an exam answer. Determine if the student's answer is \
semantically correct — it does not need to match the expected answer word \
for word, but it must convey the same key facts and reasoning.

Question: {question}

Expected answer: {expected}

Student's answer: {got}

Evaluate whether the student's answer captures the essential meaning of the \
expected answer. Minor differences in wording, additional correct detail, or \
different but valid approaches should count as correct. Missing key facts or \
fundamentally wrong reasoning should count as wrong.

Format your response as:
VERDICT: CORRECT or WRONG
EXPLANATION: <one sentence>
"""

CHUNK_PDF_IDENTIFY_SECTIONS = """\
You are analyzing the structure of an academic paper. Given the full text below, \
identify every major section of the paper.

For each section, output a JSON array of objects with these fields:
- "number": the section number as it appears in the paper (e.g., "1", "2", "3")
- "title": the section title
- "start_page": the first page of the section (1-indexed, using the [Page N] markers)
- "end_page": the last page of the section (1-indexed)

Use top-level sections only (not subsections). Include front matter \
(abstract, introduction) and back matter (bibliography, appendices, references) \
as separate sections.

Output ONLY the JSON array, no other text.

---

PAPER TEXT (pages numbered in brackets):

{text}
"""

CHUNK_PDF_SECTION = """\
You are creating a detailed study entry for one section of an academic paper. \
The entry should capture the section's content faithfully — definitions, arguments, \
algorithms, examples, and formal notation — while being readable as a standalone \
document.

Structure your output exactly as:

# Section {section_number}: {section_title} (pp. {start_page}-{end_page})

**Source:** {source_label}, pp. {start_page}-{end_page}

## [Subsection headings as appropriate]

[Detailed content: transcribe key definitions verbatim, summarize arguments, \
reproduce formal notation and algorithms, include examples from the paper. \
Write 1500-4000 words. Be thorough and faithful to the source.]

## Key Claims

[6-10 bullet points. Each should be a single, specific, testable assertion — \
a definition, design decision, algorithm property, or factual claim from this section.]

---

SECTION TEXT (from the paper):

{section_text}
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
