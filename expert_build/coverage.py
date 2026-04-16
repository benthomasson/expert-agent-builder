"""Certification coverage mapping."""

import re
import sys
from pathlib import Path

from reasons_lib.api import list_nodes

from .llm import check_model_available, invoke_sync
from .prompts import CERT_MATCH

REASONS_DB = "reasons.db"


def parse_objectives(filepath: Path) -> list[dict]:
    """Parse certification objectives from markdown.

    Expected format:
        # Domain Name
        - Objective text here
        - Another objective

        # Another Domain
        - More objectives
    """
    text = filepath.read_text()
    objectives = []
    current_domain = "general"
    obj_id = 0

    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("# "):
            current_domain = line[2:].strip()
        elif line.startswith("- ") or line.startswith("* "):
            obj_id += 1
            objectives.append({
                "id": f"OBJ-{obj_id:03d}",
                "domain": current_domain,
                "text": line[2:].strip(),
            })

    return objectives


def load_beliefs(db_path: str = REASONS_DB) -> list[dict]:
    """Load IN beliefs from the reasons database."""
    result = list_nodes(status="IN", db_path=db_path)
    return [{"id": n["id"], "text": n["text"]} for n in result["nodes"]]


def keyword_match(objective_text: str, belief_text: str) -> float:
    """Simple keyword overlap score between objective and belief."""
    stop_words = {"the", "a", "an", "is", "are", "was", "were", "be", "been",
                  "being", "have", "has", "had", "do", "does", "did", "will",
                  "would", "could", "should", "may", "might", "can", "shall",
                  "to", "of", "in", "for", "on", "with", "at", "by", "from",
                  "as", "into", "through", "during", "before", "after", "and",
                  "or", "but", "not", "no", "if", "then", "than", "that",
                  "this", "it", "its", "all", "each", "any", "use", "using"}

    def tokenize(text):
        words = re.findall(r"\w+", text.lower())
        return {w for w in words if w not in stop_words and len(w) > 2}

    obj_words = tokenize(objective_text)
    belief_words = tokenize(belief_text)

    if not obj_words or not belief_words:
        return 0.0

    overlap = obj_words & belief_words
    return len(overlap) / len(obj_words) if obj_words else 0.0


def cmd_cert_coverage(args):
    """Map certification objectives to beliefs, report coverage."""
    obj_path = Path(args.objectives_file)
    if not obj_path.exists():
        print(f"Objectives file not found: {obj_path}")
        sys.exit(1)

    db_path = str(args.beliefs_file)
    if not Path(db_path).exists():
        print(f"Reasons database not found: {db_path}")
        sys.exit(1)

    objectives = parse_objectives(obj_path)
    beliefs = load_beliefs(db_path=db_path)

    if not objectives:
        print(f"No objectives found in {obj_path}")
        print("Expected format: # Domain\\n- Objective text")
        return

    print(f"=== Certification Coverage Report ===")
    print(f"Objectives: {len(objectives)}")
    print(f"Beliefs: {len(beliefs)} IN\n")

    use_llm = args.model and check_model_available(args.model)
    if args.model and not use_llm:
        print(f"WARNING: Model {args.model} not available, falling back to keyword matching\n")

    covered = []
    gaps = []

    for obj in objectives:
        matches = []

        if use_llm:
            # Use LLM for semantic matching
            beliefs_text = "\n".join(f"- {b['id']}: {b['text']}" for b in beliefs)
            prompt = CERT_MATCH.format(objective=obj["text"], beliefs=beliefs_text)
            try:
                result = invoke_sync(prompt, model=args.model, timeout=120)
                if result.strip().upper() != "NONE":
                    for line in result.strip().split("\n"):
                        bid = line.strip().strip("-").strip()
                        if any(b["id"] == bid for b in beliefs):
                            matches.append((bid, 1.0))
            except Exception:
                pass

        if not matches:
            # Fall back to keyword matching
            for belief in beliefs:
                score = keyword_match(obj["text"], belief["text"])
                if score >= 0.3:
                    matches.append((belief["id"], score))

        matches.sort(key=lambda x: x[1], reverse=True)

        if matches:
            covered.append((obj, matches))
        else:
            gaps.append(obj)

    # Report
    if covered:
        print(f"COVERED ({len(covered)}/{len(objectives)}, {100 * len(covered) // len(objectives)}%):\n")
        for obj, matches in covered:
            print(f"  {obj['id']} [{obj['domain']}] {obj['text']}")
            for bid, score in matches[:3]:
                print(f"    -> {bid} ({score:.2f})")
        print()

    if gaps:
        print(f"GAPS ({len(gaps)}/{len(objectives)}, {100 * len(gaps) // len(objectives)}%):\n")
        for obj in gaps:
            print(f"  {obj['id']} [{obj['domain']}] {obj['text']}")
            print(f"    No matching beliefs found.")
        print()

    # Summary by domain
    domains = {}
    for obj in objectives:
        d = obj["domain"]
        if d not in domains:
            domains[d] = {"total": 0, "covered": 0}
        domains[d]["total"] += 1

    for obj, _ in covered:
        domains[obj["domain"]]["covered"] += 1

    print("BY DOMAIN:")
    for domain, counts in sorted(domains.items()):
        pct = 100 * counts["covered"] // counts["total"] if counts["total"] else 0
        bar = "***" if pct < 50 else ""
        print(f"  {domain}: {counts['covered']}/{counts['total']} ({pct}%) {bar}")
