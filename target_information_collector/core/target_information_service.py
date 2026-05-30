from target_information_collector.core.collector_pipeline import CollectorPipeline
from target_information_collector.core.identity_resolver import IdentityResolver
from target_information_collector.core.structured_profile_builder import StructuredProfileBuilder
from target_information_collector.shared.models import Evidence, PublicProfile, TargetInput
from target_information_collector.storage.json_writer import JsonProfileWriter


class TargetInformationService:
    MIN_PROFILE_LINK_CONFIDENCE = 0.45
    MIN_STRUCTURED_CONFIDENCE = 0.55

    def __init__(self):
        self.pipeline = CollectorPipeline()
        self.writer = JsonProfileWriter()
        self.structured_builder = StructuredProfileBuilder()
        self.identity_resolver = IdentityResolver()

    def collect_live(self, target: TargetInput) -> dict:
        raw_data = self.pipeline.collect_raw(target)
        raw_filename = self.writer.save(target.full_name, raw_data)
        structured_filename = self.build_from_raw(target, raw_data)

        return {
            "status": "success",
            "raw_file": raw_filename,
            "structured_file": structured_filename,
        }

    def collect_from_raw(
        self,
        target: TargetInput,
        raw_data: dict,
        raw_filename: str,
    ) -> dict:
        structured_filename = self.build_from_raw(target, raw_data)

        return {
            "status": "success",
            "raw_file": raw_filename,
            "structured_file": structured_filename,
        }

    def build_from_raw(self, target: TargetInput, raw_data: dict) -> str:
        evidence_objects = [
            Evidence(**evidence)
            for evidence in raw_data.get("evidence", [])
        ]

        public_profiles = self._build_public_profiles(
            raw_candidates=raw_data.get("candidates", []),
            evidence_objects=evidence_objects,
        )

        resolved = self.identity_resolver.resolve(
            target=target,
            candidates=raw_data.get("candidates", []),
            profiles=public_profiles,
            evidence=evidence_objects,
        )

        target_profile = {
            "target": target.model_dump(mode="json"),
            "identity_candidates": [
                candidate.model_dump(mode="json")
                for candidate in resolved.get("identity_candidates", [])
            ],
            "public_profiles": [
                profile.model_dump(mode="json")
                for profile in public_profiles
            ],
            "evidence": raw_data.get("evidence", []),
            "tech_stack": self._extract_tech_stack(evidence_objects),
            "contact": self._build_contact(evidence_objects),
        }

        return self.structured_builder.build_from_raw(target_profile)

    def _build_public_profiles(
        self,
        raw_candidates: list[dict],
        evidence_objects: list[Evidence],
    ) -> list[PublicProfile]:
        profiles = []

        for candidate in raw_candidates:
            url = candidate.get("url")
            platform = candidate.get("platform")

            if not url or not platform:
                continue

            profiles.append(
                PublicProfile(
                    platform=platform,
                    url=url,
                    username=candidate.get("username"),
                    confidence=float(candidate.get("confidence") or 0.0),
                )
            )

        for evidence in evidence_objects:
            evidence_type = self._value(evidence.evidence_type)

            if evidence_type not in {"profile", "public_link"}:
                continue

            if not evidence.url or not evidence.platform:
                continue

            if evidence.confidence < self.MIN_PROFILE_LINK_CONFIDENCE:
                continue

            if self._is_document_dorking_evidence(evidence):
                continue

            profiles.append(
                PublicProfile(
                    platform=evidence.platform,
                    url=evidence.url,
                    username=evidence.username,
                    confidence=evidence.confidence,
                )
            )

        return self._deduplicate_profiles(profiles)

    def _build_contact(self, evidence_objects: list[Evidence]) -> dict | None:
        emails = []

        for evidence in evidence_objects:
            if self._value(evidence.evidence_type) != "email":
                continue

            if not evidence.value:
                continue

            if evidence.confidence < self.MIN_STRUCTURED_CONFIDENCE:
                continue

            if self._is_document_dorking_evidence(evidence):
                continue

            emails.append(evidence.value)

        emails = self._unique(emails)

        if not emails:
            return None

        return {
            "email": emails[0],
            "status": "PUBLIC_CONFIRMED",
            "confidence": 0.85,
            "campaign_eligible": True,
            "reason": "Found during public OSINT collection",
            "evidence": [],
        }

    def _extract_tech_stack(self, evidence_objects: list[Evidence]) -> list[str]:
        values = []

        for evidence in evidence_objects:
            if self._value(evidence.evidence_type) != "tech_stack":
                continue

            if not evidence.value:
                continue

            if evidence.confidence < self.MIN_STRUCTURED_CONFIDENCE:
                continue

            if self._is_document_dorking_evidence(evidence):
                continue

            source = self._value(evidence.source)
            platform = self._value(evidence.platform)

            if source not in {"github", "linkedin"} and platform not in {"github", "linkedin"}:
                continue

            values.append(evidence.value)

        return self._unique(values)

    def _is_document_dorking_evidence(self, evidence: Evidence) -> bool:
        return (evidence.raw_data or {}).get("phase") == "document_dorking"

    def _deduplicate_profiles(self, profiles: list[PublicProfile]) -> list[PublicProfile]:
        by_key = {}

        for profile in profiles:
            key = self._canonical_key(profile.url, profile.platform)

            if key not in by_key:
                by_key[key] = profile
                continue

            if profile.confidence > by_key[key].confidence:
                by_key[key] = profile

        return list(by_key.values())

    def _canonical_key(self, url: str, platform: str) -> tuple[str, str]:
        cleaned_url = str(url).strip().lower()

        cleaned_url = cleaned_url.replace("https://www.", "https://")
        cleaned_url = cleaned_url.replace("http://www.", "http://")
        cleaned_url = cleaned_url.replace("https://web.", "https://")
        cleaned_url = cleaned_url.replace("http://web.", "http://")

        if cleaned_url.endswith("/"):
            cleaned_url = cleaned_url[:-1]

        return cleaned_url, str(platform).lower().strip()

    def _value(self, value) -> str:
        if value is None:
            return ""

        if hasattr(value, "value"):
            return str(value.value).lower().strip()

        return str(value).lower().strip()

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