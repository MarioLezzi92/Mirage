import requests

from target_information_collector.agents.base_agent import BaseAgent
from target_information_collector.evidence.evidence_normalizer import EvidenceNormalizer
from target_information_collector.evidence.evidence_store import EvidenceStore
from target_information_collector.shared.config import settings
from target_information_collector.shared.models import EvidenceSource, EvidenceType


class LinkedInAgent(BaseAgent):
    PLATFORM = "linkedin"
    SOURCE = EvidenceSource.LINKEDIN

    MIN_SCRAPE_CONFIDENCE = 0.75

    def __init__(self):
        self.normalizer = EvidenceNormalizer()

    def collect(self, store: EvidenceStore) -> None:
        self._promote_input_linkedin(store)
        self._promote_web_linkedin_candidates(store)

        if settings.apify_token and settings.apify_linkedin_profile_actor_id:
            self._collect_via_apify(store)

        self._extract_linkedin_context(store)

    def _promote_input_linkedin(self, store: EvidenceStore) -> None:
        if not store.target.linkedin_url:
            return

        url = self.normalizer.normalize_url(store.target.linkedin_url)
        username = self.normalizer.extract_username(url)

        if not url:
            return

        store.add_evidence(
            source=EvidenceSource.INPUT,
            evidence_type=EvidenceType.PROFILE,
            value=url,
            url=url,
            platform=self.PLATFORM,
            username=username,
            confidence=1.0,
            raw_data={
                "field": "linkedin_url",
                "seeded_from_input": True,
            },
        )

        store.add_candidate(
            platform=self.PLATFORM,
            url=url,
            username=username,
            display_name=store.target.full_name,
            confidence=1.0,
            matched_context=store.get_context_terms(),
            raw_data={"seeded_from_input": True},
        )

    def _promote_web_linkedin_candidates(self, store: EvidenceStore) -> None:
        for evidence in store.evidence:
            if evidence.platform != self.PLATFORM:
                continue

            if evidence.evidence_type not in {
                EvidenceType.PUBLIC_LINK,
                EvidenceType.SOCIAL_HINT,
                EvidenceType.WEB_MENTION,
                EvidenceType.PROFILE,
            }:
                continue

            if not evidence.url:
                continue

            title = evidence.title or evidence.value or ""
            description = evidence.description or ""
            text = f"{title} {description} {evidence.url}"

            confidence = self.calculate_base_score(
                store=store,
                text=text,
                username=evidence.username,
                seeded=False,
                strong_match_weight=0.12,
            )

            if confidence < 0.45:
                continue

            store.add_candidate(
                platform=self.PLATFORM,
                url=evidence.url,
                username=evidence.username,
                display_name=title,
                confidence=confidence,
                matched_context=self.matched_context(store, text),
                raw_data={"source_evidence": evidence.model_dump(mode="json")},
            )

    def _collect_via_apify(self, store: EvidenceStore) -> None:
        urls_to_scrape = self._urls_to_scrape(store)

        if not urls_to_scrape:
            return

        sync_url = (
            f"https://api.apify.com/v2/acts/"
            f"{settings.apify_linkedin_profile_actor_id}"
            f"/run-sync-get-dataset-items?token={settings.apify_token}"
        )

        payload = {
            "urls": urls_to_scrape,
            "mode": "profiles",
            "includeAbout": False,
            "includePosts": False,
            "includeEngagement": False,
            "discoverEmails": False,
            "enrichEmployeeLocation": False,
            "forceRefresh": False,
            "validateSlugs": True,
            "dryRun": False,
        }

        headers = {"Content-Type": "application/json"}

        try:
            print(f"\n[DEBUG] Lancio Apify per {self.PLATFORM}")
            print(f"[DEBUG] Actor: {settings.apify_linkedin_profile_actor_id}")
            print(f"[DEBUG] Payload: {payload}")

            response = requests.post(
                sync_url,
                json=payload,
                headers=headers,
                timeout=180,
            )

            if response.status_code not in (200, 201):
                self._add_error(
                    store=store,
                    message=f"Errore API Apify: {response.status_code} - {response.text}",
                    raw_data={"payload": payload},
                )
                return

            results = response.json()
            print(f"[DEBUG] Oggetti LinkedIn trovati: {len(results)}\n")

            for item in results:
                self._store_apify_profile(store, item)

        except Exception as exc:
            self._add_error(
                store=store,
                message=f"Errore durante lo scraping attivo di LinkedIn: {str(exc)}",
                raw_data={"payload": payload},
            )

    def _urls_to_scrape(self, store: EvidenceStore) -> list[str]:
        urls = []

        for candidate in store.candidates:
            if candidate.platform != self.PLATFORM or not candidate.url:
                continue

            raw_data = candidate.raw_data or {}
            is_seeded = raw_data.get("seeded_from_input") is True
            is_strong = candidate.confidence >= self.MIN_SCRAPE_CONFIDENCE

            if not (is_seeded or is_strong):
                continue

            url = self.normalizer.normalize_url(candidate.url)

            if url:
                urls.append(url)

        return list(dict.fromkeys(urls))

    def _store_apify_profile(self, store: EvidenceStore, item: dict) -> None:
        profile_url = self._extract_profile_url(item)

        if not profile_url:
            return

        username = (
            item.get("username")
            or self.normalizer.extract_username(profile_url)
        )

        name = self._first_present(
            item,
            "name",
            "fullName",
            "full_name",
            "title",
        )

        headline = self._first_present(
            item,
            "headline",
            "occupation",
        )

        summary = self._first_present(
            item,
            "summary",
            "about",
            "description",
        )

        location = self._first_present(
            item,
            "location",
            "address",
        )

        company = self._first_present(
            item,
            "company",
            "currentCompany",
        )

        education = item.get("education") or item.get("educations") or ""
        positions = item.get("positions") or item.get("experience") or item.get("experiences") or ""
        skills = item.get("skills") or ""

        combined_text = self._join_text(
            name,
            headline,
            summary,
            location,
            company,
            education,
            positions,
            skills,
        )

        if not combined_text:
            return

        store.add_evidence(
            source=self.SOURCE,
            evidence_type=EvidenceType.PROFILE,
            value=combined_text,
            url=profile_url,
            platform=self.PLATFORM,
            username=username,
            title=name or None,
            description=headline or summary or None,
            confidence=0.95,
            raw_data=item,
        )

        if name:
            store.add_evidence(
                source=self.SOURCE,
                evidence_type=EvidenceType.IDENTITY,
                value=name,
                url=profile_url,
                platform=self.PLATFORM,
                username=username,
                title=name,
                confidence=0.95,
                raw_data={"derived_from": "linkedin_apify_profile"},
            )

    def _extract_linkedin_context(self, store: EvidenceStore) -> None:
        for evidence in store.evidence:
            if evidence.platform != self.PLATFORM:
                continue

            title = evidence.title or evidence.value or ""
            description = evidence.description or ""
            text = f"{title} {description}"

            if not text.strip():
                continue

            confidence = max(evidence.confidence, 0.6)

            self.extract_common_context(
                store=store,
                evidence=evidence,
                text=text,
                confidence=confidence,
                platform=self.PLATFORM,
                source=self.SOURCE,
            )

    def _extract_profile_url(self, item: dict) -> str | None:
        raw_url = self._first_present(
            item,
            "url",
            "profileUrl",
            "linkedinUrl",
            "linkedInUrl",
        )

        return self.normalizer.normalize_url(raw_url)

    def _first_present(self, data: dict, *keys: str):
        for key in keys:
            value = data.get(key)

            if value:
                return value

        return ""

    def _join_text(self, *values) -> str:
        return " ".join(str(value) for value in values if value).strip()

    def _add_error(self, store: EvidenceStore, message: str, raw_data: dict | None = None) -> None:
        store.add_evidence(
            source=self.SOURCE,
            evidence_type=EvidenceType.ERROR,
            value=message,
            platform=self.PLATFORM,
            confidence=0.0,
            raw_data=raw_data or {},
        )