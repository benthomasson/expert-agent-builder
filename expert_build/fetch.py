"""Fetch documentation from URLs and convert to markdown."""

import re
import time
from datetime import date
from fnmatch import fnmatch
from pathlib import Path
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup, NavigableString, Tag


def html_to_markdown(element: Tag) -> str:
    """Convert an HTML element to markdown."""
    parts = []
    _convert(element, parts)
    text = "".join(parts)
    # Clean up excessive whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _convert(element, parts):
    """Recursively convert HTML elements to markdown."""
    if isinstance(element, NavigableString):
        text = str(element)
        if text.strip():
            parts.append(text)
        elif text:
            parts.append(" ")
        return

    if not isinstance(element, Tag):
        return

    tag = element.name

    if tag in ("script", "style", "nav", "footer", "header"):
        return

    if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        level = int(tag[1])
        parts.append(f"\n\n{'#' * level} ")
        for child in element.children:
            _convert(child, parts)
        parts.append("\n\n")

    elif tag == "p":
        parts.append("\n\n")
        for child in element.children:
            _convert(child, parts)
        parts.append("\n\n")

    elif tag in ("ul", "ol"):
        parts.append("\n")
        for i, li in enumerate(element.find_all("li", recursive=False)):
            prefix = f"{i + 1}. " if tag == "ol" else "- "
            parts.append(prefix)
            for child in li.children:
                _convert(child, parts)
            parts.append("\n")
        parts.append("\n")

    elif tag == "pre":
        code = element.find("code")
        lang = ""
        if code and code.get("class"):
            for cls in code["class"]:
                if cls.startswith("language-"):
                    lang = cls[len("language-"):]
                    break
        text = element.get_text()
        parts.append(f"\n```{lang}\n{text}\n```\n")

    elif tag == "code":
        # Inline code (not inside pre)
        if element.parent and element.parent.name != "pre":
            parts.append(f"`{element.get_text()}`")
        else:
            parts.append(element.get_text())

    elif tag in ("strong", "b"):
        parts.append("**")
        for child in element.children:
            _convert(child, parts)
        parts.append("**")

    elif tag in ("em", "i"):
        parts.append("*")
        for child in element.children:
            _convert(child, parts)
        parts.append("*")

    elif tag == "a":
        href = element.get("href", "")
        parts.append("[")
        for child in element.children:
            _convert(child, parts)
        parts.append(f"]({href})")

    elif tag == "table":
        _convert_table(element, parts)

    elif tag == "br":
        parts.append("\n")

    elif tag in ("div", "section", "article", "main", "span", "dd", "dt", "dl",
                 "blockquote", "figure", "figcaption", "details", "summary"):
        for child in element.children:
            _convert(child, parts)

    elif tag == "img":
        alt = element.get("alt", "")
        src = element.get("src", "")
        parts.append(f"![{alt}]({src})")

    else:
        for child in element.children:
            _convert(child, parts)


def _convert_table(table: Tag, parts):
    """Convert an HTML table to markdown."""
    rows = table.find_all("tr")
    if not rows:
        return

    parts.append("\n")
    for i, row in enumerate(rows):
        cells = row.find_all(["th", "td"])
        cell_texts = [cell.get_text(strip=True) for cell in cells]
        parts.append("| " + " | ".join(cell_texts) + " |\n")
        if i == 0:
            parts.append("| " + " | ".join(["---"] * len(cell_texts)) + " |\n")
    parts.append("\n")


def slugify_url(url: str) -> str:
    """Convert URL path to a filename-safe slug."""
    parsed = urlparse(url)
    path = parsed.path.strip("/").replace("/", "-") or "index"
    slug = re.sub(r"[^\w-]", "", path)
    return slug[:80]


def matches_patterns(url: str, include: str | None, exclude: str | None) -> bool:
    """Check if URL matches include/exclude patterns."""
    if include and not fnmatch(url, include):
        return False
    if exclude and fnmatch(url, exclude):
        return False
    return True


def fetch_sitemap(url: str, client: httpx.Client) -> list[str]:
    """Fetch URLs from a sitemap.xml."""
    resp = client.get(url, follow_redirects=True)
    resp.raise_for_status()
    root = ElementTree.fromstring(resp.text)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = []
    for loc in root.findall(".//sm:loc", ns):
        if loc.text:
            urls.append(loc.text)
    # Also try without namespace
    if not urls:
        for loc in root.findall(".//loc"):
            if loc.text:
                urls.append(loc.text)
    return urls


def cmd_fetch_docs(args):
    """Fetch documentation from URLs and save as markdown."""
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    base_domain = urlparse(args.url).netloc

    headers = {"User-Agent": "expert-build/0.1 (documentation fetcher)"}

    with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
        if args.sitemap:
            urls = fetch_sitemap(args.url, client)
            urls = [u for u in urls if matches_patterns(u, args.include, args.exclude)]
            print(f"Found {len(urls)} URLs in sitemap")
            queue = [(u, 0) for u in urls]
        else:
            queue = [(args.url, 0)]

        visited = set()
        fetched = 0

        while queue:
            url, depth = queue.pop(0)
            if url in visited:
                continue
            if depth > args.depth:
                continue

            visited.add(url)

            try:
                resp = client.get(url)
                resp.raise_for_status()
            except httpx.HTTPError as e:
                print(f"  SKIP {url}: {e}")
                continue

            content_type = resp.headers.get("content-type", "")
            if "html" not in content_type:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")

            # Find content area using selector list
            content_element = None
            for selector in args.selector.split(","):
                selector = selector.strip()
                content_element = soup.select_one(selector)
                if content_element:
                    break
            if not content_element:
                content_element = soup.body or soup

            md = html_to_markdown(content_element)

            if not md.strip():
                continue

            slug = slugify_url(url)
            out_path = output_dir / f"{slug}.md"

            # Write with frontmatter
            frontmatter = (
                f"---\n"
                f"source: {url}\n"
                f"fetched: {date.today().isoformat()}\n"
                f"---\n\n"
            )
            out_path.write_text(frontmatter + md)
            fetched += 1
            print(f"  {out_path}")

            # Discover links for crawling (only if not sitemap mode)
            if not args.sitemap and depth < args.depth:
                for a in soup.find_all("a", href=True):
                    href = urljoin(url, a["href"])
                    # Strip fragment
                    href = href.split("#")[0]
                    # Only follow same-domain links
                    if urlparse(href).netloc == base_domain:
                        if href not in visited and matches_patterns(href, args.include, args.exclude):
                            queue.append((href, depth + 1))

            if args.delay > 0:
                time.sleep(args.delay)

    print(f"\nFetched {fetched} pages to {output_dir}/")
