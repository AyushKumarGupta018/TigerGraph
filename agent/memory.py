"""Case memory over closed_cases_history.csv plus our own written cases.

5,565 finished investigations (4,665 confirmed fraud, 900 cleared) are
the only place the truth is written down - so retrieval quality here
directly moves the accuracy score. Similarity stays explainable on
purpose: same customer, same card, connected-card overlap, same
pattern. An analyst can see exactly why a prior case was cited.
"""


class CaseMemory:
    def __init__(self, graph_client):
        self.graph = graph_client
        self._cache = None

    def _all_cases(self) -> list[dict]:
        if self._cache is None:
            # Closed history + anything this run has already written -
            # case 12 can cite the case the agent closed as case 3.
            self._cache = self.graph.closed_cases()
        return self._cache + self.graph.written_cases()

    def find_similar(self, customer_id: str, card_id: str, pattern_hint: str,
                     connected_cards: list[str]) -> list[dict]:
        """Top prior cases by explainable overlap, with outcomes."""
        results = []
        connected = set(connected_cards or [])
        for pc in self._all_cases():
            score, reasons = 0.0, []
            if pc.get("customer_id") == customer_id:
                score += 0.5
                reasons.append("same customer")
            if pc.get("card_id") == card_id:
                score += 0.3
                reasons.append("same card")
            # connected_card_ids is pipe-separated in the history file.
            pc_connected = set(str(pc.get("connected_card_ids", "") or "").split("|")) - {"", "nan"}
            overlap = connected & (pc_connected | {pc.get("card_id", "")})
            if overlap:
                score += 0.4
                reasons.append(f"shared card(s): {', '.join(sorted(overlap))}")
            if pattern_hint and pattern_hint not in ("none",) and pc.get("pattern") == pattern_hint:
                score += 0.2
                reasons.append(f"same pattern ({pattern_hint})")
            if score > 0:
                results.append({
                    "case_id": pc.get("case_id") or pc.get("graph_case_id"),
                    "pattern": pc.get("pattern"), "outcome": pc.get("outcome") or pc.get("verdict"),
                    "notes": (str(pc.get("analyst_notes", "") or pc.get("summary", "")))[:300],
                    "similarity": round(min(score, 1.0), 2), "why_similar": reasons,
                })
        return sorted(results, key=lambda r: -r["similarity"])[:4]

    def record(self, case_dict: dict) -> None:
        """A resolved case joins the corpus for later investigations."""
        self.graph.upsert_case(case_dict)
