"""GraphRAG grounding.

The LLM never sees raw tables - it gets a curated context pack:
  1. DOCUMENT SIDE - the official HHGOA_README.md (fraud policy, the
     five typologies, SAR guidance) chunked by heading, plus any extra
     markdown in DATA_DIR/policy/. Retrieval is transparent keyword
     scoring; swap in TigerGraph vector search for scale.
  2. GRAPH SIDE - evidence claims with provenance, and similar closed
     cases with outcomes, already distilled to short statements.
"""
import re
from pathlib import Path

from .config import settings


class PolicyRetriever:
    def __init__(self, data_dir: Path | None = None):
        base = Path(data_dir or settings.data_dir)
        # Candidate sources, in priority order: the official README (it
        # contains the binding policy), then any supplementary docs.
        self.sources = [Path("HHGOA_README.md"), base / "README.md"]
        self.sources += sorted((base / "policy").glob("*.md")) if (base / "policy").exists() else []
        self._chunks: list[dict] = []
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        seen = set()
        for path in self.sources:
            if not path.exists() or path.name in seen:
                continue
            seen.add(path.name)
            for sec in re.split(r"\n(?=#{2,3} )", path.read_text(encoding="utf-8", errors="replace")):
                if not sec.strip():
                    continue
                title = sec.strip().splitlines()[0].lstrip("# ").strip()
                self._chunks.append({"source": path.name, "title": title, "text": sec.strip()[:2500]})

    def retrieve(self, query: str, k: int = 4) -> list[dict]:
        self._load()
        terms = set(re.findall(r"[a-z_0-9]+", query.lower()))
        scored = []
        for chunk in self._chunks:
            words = set(re.findall(r"[a-z_0-9]+", chunk["text"].lower()))
            overlap = len(terms & words)
            if overlap:
                scored.append((overlap, chunk))
        return [c for _, c in sorted(scored, key=lambda x: -x[0])[:k]]


def build_context(question: str, evidence_claims: list[dict], similar_cases: list[dict],
                  retriever: PolicyRetriever) -> str:
    """Assemble the grounded context pack for one LLM call."""
    parts = ["=== EVIDENCE FROM THE KNOWLEDGE GRAPH (with provenance) ==="]
    for e in evidence_claims:
        parts.append(f"- {e['claim']}  [source={e['source']} ref={e['ref']}]")
    if not evidence_claims:
        parts.append("- (none gathered)")
    if similar_cases:
        parts.append("\n=== SIMILAR CLOSED CASES (case memory) ===")
        for sc in similar_cases:
            parts.append(f"- {sc['case_id']} [{sc.get('pattern')}] outcome={sc.get('outcome')} "
                         f"(because: {', '.join(sc.get('why_similar', []))}): {sc.get('notes', '')}")
    chunks = retriever.retrieve(question)
    if chunks:
        parts.append("\n=== BINDING POLICY / TYPOLOGY EXCERPTS ===")
        for ch in chunks:
            parts.append(f"--- {ch['source']} :: {ch['title']} ---\n{ch['text']}")
    return "\n".join(parts)
