from target_information_collector.evidence.evidence_store import EvidenceStore
from target_information_collector.shared.models import EvidenceSource, EvidenceType

class BaseAgent:
    def promote_seeded_links(self, store: EvidenceStore, platform: str) -> None:
        """Promuove a candidate i link inseriti direttamente in input dall'utente."""
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
        source: EvidenceSource | str
    ) -> None:
        """Estrae i metadati comuni basandosi esclusivamente sui dati di TargetInput."""
        lower = text.lower()

        # 1. Identità
        if store.target.full_name.lower() in lower:
            self._add_evidence_helper(store, evidence, EvidenceType.IDENTITY, store.target.full_name, confidence, platform, source)

        # 2. Location (Città e Location generica)
        locations = [loc for loc in store.target.cities + [store.target.location] if loc]
        for location in locations:
            if location.lower() in lower:
                self._add_evidence_helper(store, evidence, EvidenceType.LOCATION, location, confidence, platform, source)

        # 3. Educazione
        for edu in store.target.education:
            if edu.lower() in lower:
                self._add_evidence_helper(store, evidence, EvidenceType.EDUCATION, edu, confidence, platform, source)

        # 4. Organizzazione / Azienda
        if store.target.company and store.target.company.lower() in lower:
            self._add_evidence_helper(store, evidence, EvidenceType.ORGANIZATION, store.target.company, confidence, platform, source)

        # 5. Ruolo
        if store.target.role and store.target.role.lower() in lower:
            self._add_evidence_helper(store, evidence, EvidenceType.ROLE, store.target.role, confidence, platform, source)

    def _add_evidence_helper(
        self,
        store: EvidenceStore,
        evidence,
        ev_type: EvidenceType,
        value: str,
        confidence: float,
        platform: str,
        source: EvidenceSource | str
    ) -> None:
        store.add_evidence(
            source=source,
            evidence_type=ev_type,
            value=value,
            url=evidence.url,
            platform=platform,
            username=evidence.username,
            title=evidence.title,
            description=evidence.description,
            confidence=confidence,
            raw_data={
                "derived_from": f"{platform}_snippet",
            },
        )

    def calculate_base_score(
        self,
        store: EvidenceStore,
        text: str,
        username: str | None,
        seeded: bool = False,
        strong_match_weight: float = 0.15
    ) -> float:
        """Calcola lo score basandosi sulle evidenze fornite in input."""
        lower = text.lower()
        score = 0.0

        if seeded:
            score += 0.40

        if store.target.full_name.lower() in lower:
            score += 0.35

        if username and self.username_matches_name(store.target.full_name, username):
            score += 0.10

        strong_matches = 0
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
        lower = text.lower()
        values = []
        for term in store.get_context_terms():
            if term.lower() in lower:
                values.append(term)
        return store.unique(values)

    def username_matches_name(self, full_name: str, username: str) -> bool:
        normalized_username = self.normalize(username)
        parts = [
            self.normalize(part)
            for part in full_name.split()
            if len(part) > 2
        ]
        if not parts:
            return False
        return all(part in normalized_username for part in parts)

    def normalize(self, value: str) -> str:
        return "".join(ch.lower() for ch in value.lower() if ch.isalnum())
