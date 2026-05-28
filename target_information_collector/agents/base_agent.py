from target_information_collector.evidence.evidence_store import EvidenceStore
from target_information_collector.shared.models import EvidenceSource, EvidenceType


class BaseAgent:
    def promote_seeded_links(self, store: EvidenceStore, platform: str) -> None:
        for evidence in store.evidence:
            if evidence.platform != platform:
                continue

            if evidence.source != EvidenceSource.INPUT:
                continue

            if not evidence.url:
                continue

            store.add_candidate(
                platform=platform,
                url=evidence.url,
                username=evidence.username,
                display_name=store.target.full_name,
                confidence=0.75,
                matched_context=store.get_context_terms(),
                raw_data={
                    "seeded_from_input": True,
                    "source_evidence": evidence.model_dump(mode="json"),
                },
            )

    def extract_common_context(
        self,
        store: EvidenceStore,
        evidence,
        text: str,
        confidence: float,
        platform: str,
        source: EvidenceSource | str,
    ) -> None:
        matched_items = [
            (EvidenceType.IDENTITY, [store.target.full_name]),
            (EvidenceType.LOCATION, self._target_locations(store)),
            (EvidenceType.EDUCATION, store.target.education),
            (EvidenceType.ORGANIZATION, self._target_organizations(store)),
            (EvidenceType.ROLE, [store.target.role] if store.target.role else []),
        ]

        for evidence_type, values in matched_items:
            for value in self._matched_values(text, values):
                self._add_context_evidence(
                    store=store,
                    source=source,
                    evidence=evidence,
                    evidence_type=evidence_type,
                    value=value,
                    confidence=confidence,
                    platform=platform,
                )

    def calculate_base_score(
        self,
        store: EvidenceStore,
        text: str,
        username: str | None,
        seeded: bool = False,
        strong_match_weight: float = 0.15,
    ) -> float:
        lower = text.lower()
        score = 0.0
        strong_matches = 0

        if seeded:
            score += 0.40

        if store.target.full_name.lower() in lower:
            score += 0.35

        if username and self.username_matches_name(store.target.full_name, username):
            score += 0.10

        for term in store.get_strong_context_terms():
            if term.lower() in lower:
                strong_matches += 1
                score += strong_match_weight

        if store.target.email_domain and store.target.email_domain.lower() in lower:
            score += 0.10

        if not seeded and strong_matches == 0:
            score = min(score, 0.44)

        return round(max(0.0, min(score, 1.0)), 3)

    def matched_context(self, store: EvidenceStore, text: str) -> list[str]:
        return self._matched_values(text, store.get_context_terms())

    def username_matches_name(self, full_name: str, username: str) -> bool:
        normalized_username = self.normalize(username)
        name_parts = [
            self.normalize(part)
            for part in full_name.split()
            if len(part) > 2
        ]

        if not name_parts:
            return False

        return all(part in normalized_username for part in name_parts)

    def normalize(self, value: str) -> str:
        return "".join(ch.lower() for ch in str(value) if ch.isalnum())

    def _matched_values(self, text: str, values: list[str]) -> list[str]:
        lower = text.lower()
        matches = []

        for value in values:
            if value and value.lower() in lower:
                matches.append(value)

        return self._unique(matches)

    def _target_locations(self, store: EvidenceStore) -> list[str]:
        values = []

        if store.target.location:
            values.append(store.target.location)

        values.extend(store.target.cities)

        return values

    def _target_organizations(self, store: EvidenceStore) -> list[str]:
        values = []

        if store.target.company:
            values.append(store.target.company)

        if store.target.department:
            values.append(store.target.department)

        return values

    def _add_context_evidence(
        self,
        store: EvidenceStore,
        source: EvidenceSource | str,
        evidence,
        evidence_type: EvidenceType,
        value: str,
        confidence: float,
        platform: str,
    ) -> None:
        store.add_evidence(
            source=source,
            evidence_type=evidence_type,
            value=value,
            url=evidence.url,
            platform=platform,
            username=evidence.username,
            title=evidence.title,
            description=evidence.description,
            confidence=confidence,
            raw_data={
                "derived_from": f"{platform}_context",
            },
        )

    def _unique(self, values: list[str]) -> list[str]:
        seen = set()
        output = []

        for value in values:
            if not value:
                continue

            cleaned = str(value).strip()

            if not cleaned:
                continue

            key = cleaned.lower()

            if key in seen:
                continue

            seen.add(key)
            output.append(cleaned)

        return output