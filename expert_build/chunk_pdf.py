"""Chunk a PDF paper into section-by-section entries."""

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from .llm import check_model_available, invoke_sync
from .prompts import CHUNK_PDF_IDENTIFY_SECTIONS, CHUNK_PDF_SECTION


def extract_text_by_page(pdf_path: Path) -> list[str]:
    """Extract text from each page of a PDF using pypdf."""
    try:
        from pypdf import PdfReader
    except ImportError:
        print("ERROR: pypdf is required for chunk-pdf.")
        print("Install: uv pip install pypdf")
        sys.exit(1)

    reader = PdfReader(str(pdf_path))
    pages = []
    for page in reader.pages:
        text = page.extract_text() or ""
        pages.append(text)
    return pages


def check_text_quality(pages: list[str]) -> bool:
    """Detect scanned PDFs with no text layer."""
    total_chars = sum(len(p.strip()) for p in pages)
    avg_chars = total_chars / len(pages) if pages else 0
    return avg_chars > 100


def format_pages_for_llm(pages: list[str], start: int = 0, end: int | None = None) -> str:
    """Format page text with [Page N] markers."""
    if end is None:
        end = len(pages)
    parts = []
    for i in range(start, min(end, len(pages))):
        parts.append(f"[Page {i + 1}]\n{pages[i]}")
    return "\n\n".join(parts)


def identify_sections(pages: list[str], model: str, timeout: int) -> list[dict]:
    """Use LLM to identify top-level sections in the paper."""
    full_text = format_pages_for_llm(pages)

    if len(full_text) > 200000:
        full_text = full_text[:200000] + "\n\n[Truncated — paper continues]"

    prompt = CHUNK_PDF_IDENTIFY_SECTIONS.format(text=full_text)
    result = invoke_sync(prompt, model=model, timeout=timeout)

    json_match = re.search(r"\[.*\]", result, re.DOTALL)
    if not json_match:
        raise ValueError(f"Could not parse section list from LLM response:\n{result[:500]}")

    sections = json.loads(json_match.group())

    for s in sections:
        s["start_page"] = int(s["start_page"])
        s["end_page"] = int(s["end_page"])
        s["number"] = str(s["number"])

    return sections


def slugify(text: str) -> str:
    """Convert text to kebab-case slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")[:60]


def generate_section_entry(
    pages: list[str],
    section: dict,
    source_label: str,
    model: str,
    timeout: int,
) -> str:
    """Generate entry content for one section."""
    start = section["start_page"] - 1
    end = section["end_page"]
    section_text = format_pages_for_llm(pages, start, end)

    prompt = CHUNK_PDF_SECTION.format(
        section_number=section["number"],
        section_title=section["title"],
        start_page=section["start_page"],
        end_page=section["end_page"],
        source_label=source_label,
        section_text=section_text,
    )

    return invoke_sync(prompt, model=model, timeout=timeout)


def make_entry_filename(prefix: str, section: dict) -> str:
    """Generate entry filename: {prefix}-s{number}-{slug}."""
    title_slug = slugify(section["title"])
    return f"{prefix}-s{section['number']}-{title_slug}"


def cmd_chunk_pdf(args):
    """Chunk a PDF paper into section-by-section entries."""
    from .caffeinate import hold as _caffeinate

    _caffeinate()

    pdf_path = Path(args.pdf).resolve()
    if not pdf_path.exists():
        print(f"PDF not found: {pdf_path}")
        sys.exit(1)

    if pdf_path.suffix.lower() != ".pdf":
        print(f"Not a PDF file: {pdf_path}")
        sys.exit(1)

    if not check_model_available(args.model):
        print(f"Model not available: {args.model}")
        sys.exit(1)

    prefix = args.prefix or slugify(pdf_path.stem)
    source_label = args.source_label or pdf_path.stem

    print(f"Reading PDF: {pdf_path}")
    pages = extract_text_by_page(pdf_path)
    print(f"  {len(pages)} pages extracted")

    if not pages:
        print("ERROR: No pages found in PDF.")
        sys.exit(1)

    if not check_text_quality(pages):
        print("ERROR: PDF appears to be scanned with no text layer.")
        print("OCR the PDF first (e.g., with ocrmypdf) and try again.")
        sys.exit(1)

    print("Identifying sections...")
    try:
        sections = identify_sections(pages, args.model, args.timeout)
    except Exception as e:
        print(f"ERROR identifying sections: {e}")
        sys.exit(1)

    print(f"  Found {len(sections)} sections:")
    for s in sections:
        print(f"    {s['number']}. {s['title']} (pp. {s['start_page']}-{s['end_page']})")

    if args.dry_run:
        print("\n(dry run — no entries created)")
        return

    manifest = Path(f".chunked-{prefix}")
    done = set()
    if manifest.exists():
        done = set(manifest.read_text().strip().split("\n"))

    created = 0
    skipped = 0

    for section in sections:
        filename = make_entry_filename(prefix, section)

        if filename in done:
            print(f"  SKIP (already chunked): {filename}")
            skipped += 1
            continue

        print(f"  Chunking: Section {section['number']} — {section['title']}...")

        try:
            content = generate_section_entry(
                pages, section, source_label, args.model, args.timeout,
            )
        except Exception as e:
            print(f"    ERROR: {e}")
            continue

        title = f"Section {section['number']}: {section['title']}"
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".md", delete=False,
            ) as tmp:
                tmp.write(content)
                tmp_path = tmp.name

            result = subprocess.run(
                ["entry", "create", filename, title, "--content-file", tmp_path],
                capture_output=True,
                text=True,
            )

            Path(tmp_path).unlink(missing_ok=True)

            if result.returncode == 0:
                print(f"    -> {result.stdout.strip()}")
            else:
                result = subprocess.run(
                    ["entry", "create", filename, title, "--content", content],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    print(f"    -> {result.stdout.strip()}")
                else:
                    print(f"    WARN: entry create failed: {result.stderr.strip()}")
                    continue
        except FileNotFoundError:
            print("  ERROR: entry CLI not found. Install with: uv tool install entry")
            sys.exit(1)

        with manifest.open("a") as f:
            f.write(f"{filename}\n")
        done.add(filename)

        created += 1

    print(f"\nChunked {created} sections ({skipped} already done)")
    if created:
        print("Next: expert-build propose-beliefs")
