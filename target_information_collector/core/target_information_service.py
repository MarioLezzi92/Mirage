import json
from pathlib import Path

from target_information_collector.core.collector_pipeline import CollectorPipeline
from target_information_collector.core.identity_resolver import IdentityResolver
from target_information_collector.core.structured_profile_builder import StructuredProfileBuilder
from target_information_collector.shared.models import Evidence, PublicProfile, TargetInput
from target_information_collector.storage.json_writer import JsonProfileWriter


class TargetInformationService:
    def __init__(
        self,
        use_mock_raw: bool = False,
        mock_raw_filename: str = "mario-lezzi-raw-1.json",
    ):
        self.use_mock_raw = use_mock_raw
        self.mock_raw_filename = mock_raw_filename

        self.pipeline = CollectorPipeline()
        self.writer = JsonProfileWriter()
        self.structured_builder = StructuredProfileBuilder()
        self.identity_resolver = IdentityResolver()

    def collect(self, target: TargetInput) -> dict:
        raw_data, raw_filename = self._get_raw_data(target)
        structured_filename = self._build_structured_profile(target, raw_data)

        return {
            "status": "success",
            "raw_file": raw_filename,
            "structured_file": structured_filename,
        }

    def _get_raw_data(self, target: TargetInput) -> tuple[dict, str]:
        if self.use_mock_raw:
            print(f"🛠️ MODALITÀ TEST: caricamento raw locale da {self.mock_raw_filename}")
            raw_path = self._raw_dir() / self.mock_raw_filename

            with open(raw_path, "r", encoding="utf-8") as file:
                return json.load(file), self.mock_raw_filename

        print("Avvio raccolta dati dal vivo...")
        raw_data = self.pipeline.collect_raw(target)
        raw_filename = self.writer.save(target.full_name, raw_data)

        return raw_data, raw_filename

    def _build_structured_profile(self, target: TargetInput, raw_data: dict) -> str:
        evidence_objects = [
            Evidence(**evidence)
            for evidence in raw_data.get("evidence", [])
        ]

        public_profiles = [
            PublicProfile(**candidate)
            for candidate in raw_data.get("candidates", [])
        ]

        resolved = self.identity_resolver.resolve(
            target=target,
            candidates=[],
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
                for profile in resolved.get("public_profiles", [])
            ],
            "evidence": raw_data.get("evidence", []),
            "tech_stack": self._extract_unique_values(evidence_objects, "tech_stack"),
            "contact": self._build_contact(evidence_objects),
        }

        return self.structured_builder.build_from_raw(target_profile)

    def _build_contact(self, evidence_objects: list[Evidence]) -> dict | None:
        emails = []

        for evidence in evidence_objects:
            if str(evidence.evidence_type) != "email":
                continue

            if not evidence.value:
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

    def _extract_unique_values(
        self,
        evidence_objects: list[Evidence],
        evidence_type: str,
    ) -> list[str]:
        values = {
            evidence.value
            for evidence in evidence_objects
            if str(evidence.evidence_type) == evidence_type and evidence.value
        }

        return sorted(values)

    def _is_document_dorking_evidence(self, evidence: Evidence) -> bool:
        raw_data = evidence.raw_data or {}
        return raw_data.get("phase") == "document_dorking"

    def _raw_dir(self) -> Path:
        return Path(__file__).resolve().parent.parent / "data" / "raw"

    def _unique(self, values: list[str]) -> list[str]:
        seen = set()
        output = []

        for value in values:
            cleaned = str(value).strip()

            if not cleaned:
                continue

            key = cleaned.lower()

            if key in seen:
                continue

            seen.add(key)
            output.append(cleaned)

        return output