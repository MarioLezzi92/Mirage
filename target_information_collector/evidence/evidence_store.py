from target_information_collector.evidence.evidence_normalizer import EvidenceNormalizer
from target_information_collector.shared.models import (
    CandidateProfile,
    Evidence,
    EvidenceSource,
    EvidenceType,
    TargetInput,
)


class EvidenceStore:
    def __init__(self, target: TargetInput):
        self.target = target
        self.normalizer = EvidenceNormalizer()
        self.evidence: list[Evidence] = []
        self.candidates: list[CandidateProfile] = []

        self._seed_from_input()

    def _seed_from_input(self) -> None:
        self.add_evidence(
            source=EvidenceSource.INPUT,
            evidence_type=EvidenceType.IDENTITY,
            value=self.target.full_name,
            confidence=1.0,
            raw_data={"field": "full_name"},
        )

        for city in self.target.cities:
            self.add_evidence(
                source=EvidenceSource.INPUT,
                evidence_type=EvidenceType.LOCATION,
                value=city,
                confidence=1.0,
                raw_data={"field": "cities"},
            )

        if self.target.location:
            self.add_evidence(
                source=EvidenceSource.INPUT,
                evidence_type=EvidenceType.LOCATION,
                value=self.target.location,
                confidence=1.0,
                raw_data={"field": "location"},
            )

        for contact in self.target.contacts:
            self.add_evidence(
                source=EvidenceSource.INPUT,
                evidence_type=EvidenceType.EMAIL,
                value=contact,
                confidence=1.0,
                raw_data={"field": "contacts"},
            )

        if self.target.email:
            self.add_evidence(
                source=EvidenceSource.INPUT,
                evidence_type=EvidenceType.EMAIL,
                value=self.target.email,
                confidence=1.0,
                raw_data={"field": "email"},
            )

        if self.target.company:
            self.add_evidence(
                source=EvidenceSource.INPUT,
                evidence_type=EvidenceType.ORGANIZATION,
                value=self.target.company,
                confidence=1.0,
                raw_data={"field": "company"},
            )

        if self.target.role:
            self.add_evidence(
                source=EvidenceSource.INPUT,
                evidence_type=EvidenceType.ROLE,
                value=self.target.role,
                confidence=1.0,
                raw_data={"field": "role"},
            )

        for education in self.target.education:
            self.add_evidence(
                source=EvidenceSource.INPUT,
                evidence_type=EvidenceType.EDUCATION,
                value=education,
                confidence=1.0,
                raw_data={"field": "education"},
            )

        for link in self.target.public_links:
            normalized_url = self.normalizer.normalize_url(link)
            platform = self.normalizer.detect_platform(normalized_url)
            username = self.normalizer.extract_username(normalized_url)

            self.add_evidence(
                source=EvidenceSource.INPUT,
                evidence_type=EvidenceType.PUBLIC_LINK,
                value=normalized_url,
                url=normalized_url,
                platform=platform,
                username=username,
                confidence=1.0,
                raw_data={"field": "public_links"},
            )

            if platform:
                self.add_candidate(
                    platform=platform,
                    url=normalized_url,
                    username=username,
                    confidence=0.75,
                    raw_data={"seeded_from_input": True},
                )

    def add_evidence(
        self,
        source: EvidenceSource | str,
        evidence_type: EvidenceType | str,
        value: str | None,
        url: str | None = None,
        platform: str | None = None,
        username: str | None = None,
        title: str | None = None,
        description: str | None = None,
        confidence: float = 0.0,
        raw_data: dict | None = None,
    ) -> Evidence:
        normalized_url = self.normalizer.normalize_url(url) if url else None

        evidence = Evidence(
            source=source,
            evidence_type=evidence_type,
            value=value,
            url=normalized_url,
            platform=platform,
            username=username,
            title=title,
            description=description,
            confidence=confidence,
            raw_data=raw_data or {},
        )

        if not self._evidence_exists(evidence):
            self.evidence.append(evidence)

        return evidence

    def add_candidate(
        self,
        platform: str,
        url: str,
        username: str | None = None,
        display_name: str | None = None,
        confidence: float = 0.0,
        matched_context: list[str] | None = None,
        raw_data: dict | None = None,
    ) -> CandidateProfile:
        normalized_url = self.normalizer.normalize_url(url)

        candidate = CandidateProfile(
            platform=platform,
            url=normalized_url,
            username=username,
            display_name=display_name,
            confidence=confidence,
            matched_context=matched_context or [],
            raw_data=raw_data or {},
        )

        existing = self._find_candidate(platform, normalized_url)

        if existing:
            if candidate.confidence > existing.confidence:
                existing.confidence = candidate.confidence

            if candidate.display_name and not existing.display_name:
                existing.display_name = candidate.display_name

            if candidate.username and not existing.username:
                existing.username = candidate.username

            existing.matched_context = self.unique(
                existing.matched_context + candidate.matched_context
            )

            existing.raw_data.update(candidate.raw_data)

            return existing

        self.candidates.append(candidate)
        return candidate

    def get_evidence_by_url(self, url: str) -> list[Evidence]:
        normalized_url = self.normalizer.normalize_url(url)

        return [
            item
            for item in self.evidence
            if item.url == normalized_url
        ]

    def get_evidence_by_type(self, evidence_type: EvidenceType | str) -> list[Evidence]:
        return [
            item
            for item in self.evidence
            if item.evidence_type == evidence_type
        ]

    def get_candidates_by_platform(self, platform: str) -> list[CandidateProfile]:
        return [
            candidate
            for candidate in self.candidates
            if candidate.platform == platform
        ]

    def get_context_terms(self) -> list[str]:
        values = [self.target.full_name]

        for evidence_type in [
            EvidenceType.LOCATION,
            EvidenceType.EDUCATION,
            EvidenceType.ORGANIZATION,
            EvidenceType.ROLE,
            EvidenceType.EMAIL,
            EvidenceType.TECH_STACK,
        ]:
            values.extend(
                item.value
                for item in self.get_evidence_by_type(evidence_type)
                if item.value
            )

        if self.target.email_domain:
            values.append(self.target.email_domain)

        return self.unique(values)

    def get_strong_context_terms(self) -> list[str]:
        values = []

        for evidence_type in [
            EvidenceType.LOCATION,
            EvidenceType.EDUCATION,
            EvidenceType.ORGANIZATION,
            EvidenceType.EMAIL,
        ]:
            values.extend(
                item.value
                for item in self.get_evidence_by_type(evidence_type)
                if item.value
            )

        return self.unique(values)

    def get_social_search_terms(self) -> list[str]:
        name = self.target.full_name
        queries = []

        for term in self.get_strong_context_terms():
            queries.append(f'"{name}" "{term}"')
            queries.append(f'"{name}" "{term}" Facebook')
            queries.append(f'"{name}" "{term}" Instagram')
            queries.append(f'"{name}" "{term}" site:facebook.com')
            queries.append(f'"{name}" "{term}" site:instagram.com')

        return self.unique(queries)

    def as_raw_dict(self) -> dict:
        return {
            "target": self.target.model_dump(mode="json"),
            "evidence": [
                item.model_dump(mode="json")
                for item in self.evidence
            ],
            "candidates": [
                item.model_dump(mode="json")
                for item in self.candidates
            ],
        }

    def _evidence_exists(self, evidence: Evidence) -> bool:
        for item in self.evidence:
            if (
                item.source == evidence.source
                and item.evidence_type == evidence.evidence_type
                and item.value == evidence.value
                and item.url == evidence.url
            ):
                return True

        return False

    def _find_candidate(
        self,
        platform: str,
        url: str,
    ) -> CandidateProfile | None:
        for candidate in self.candidates:
            if candidate.platform == platform and candidate.url == url:
                return candidate

        return None

    @staticmethod
    def unique(values: list[str]) -> list[str]:
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