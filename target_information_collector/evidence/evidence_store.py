from target_information_collector.shared.models import CandidateProfile, Evidence
from target_information_collector.shared.text import canonical_url


class EvidenceStore:
    def __init__(self) -> None:
        self._candidates: dict[tuple[str, str], CandidateProfile] = {}
        self._evidence: dict[tuple[str, str, str], Evidence] = {}

    def add_candidates(self, candidates: list[CandidateProfile]) -> None:
        for candidate in candidates:
            key = (
                candidate.platform,
                canonical_url(str(candidate.url)).casefold(),
            )
            current = self._candidates.get(key)
            if current is None or candidate.explicit:
                self._candidates[key] = candidate

    def candidates(self) -> list[CandidateProfile]:
        return list(self._candidates.values())

    def add_evidence(self, evidence: list[Evidence]) -> None:
        for item in evidence:
            key = (
                item.evidence_type.value,
                item.value.casefold(),
                str(item.url or "").casefold().rstrip("/"),
            )
            current = self._evidence.get(key)
            if current is None or item.confidence > current.confidence:
                self._evidence[key] = item

    def evidence(self) -> list[Evidence]:
        return list(self._evidence.values())
