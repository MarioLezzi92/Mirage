import requests

from target_information_collector.collectors.base_agent import BaseAgent
from target_information_collector.evidence.evidence_store import EvidenceStore
from target_information_collector.shared.config import settings
from target_information_collector.shared.models import EvidenceSource, EvidenceType


class LinkedInAgent(BaseAgent):
    PLATFORM = "linkedin"
    SOURCE = EvidenceSource.LINKEDIN

    MIN_SCRAPE_CONFIDENCE = 0.75

    def collect(self, store: EvidenceStore) -> None:
        self._promote_input_linkedin(store)
        self._promote_web_linkedin_candidates(store)

        if settings.apify_token and getattr(settings, "apify_linkedin_profile_actor_id", None):
            self._collect_via_apify(store)

        self._extract_linkedin_context(store)

    def _promote_input_linkedin(self, store: EvidenceStore) -> None:
        if not store.target.linkedin_url:
            return

        url = store.normalize_url(store.target.linkedin_url)
        username = store.extract_username(url)

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
            raw_data={
                "seeded_from_input": True,
            },
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
                raw_data={
                    "source_evidence": evidence.model_dump(mode="json"),
                },
            )

    def _collect_via_apify(self, store: EvidenceStore) -> None:
        urls_to_scrape = []

        for candidate in store.candidates:
            if candidate.platform != self.PLATFORM or not candidate.url:
                continue

            raw_data = candidate.raw_data or {}
            is_seeded = raw_data.get("seeded_from_input") is True
            is_strong = candidate.confidence >= self.MIN_SCRAPE_CONFIDENCE

            if not (is_seeded or is_strong):
                continue

            urls_to_scrape.append(candidate.url)

        urls_to_scrape = list(dict.fromkeys(urls_to_scrape))

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
            "discoverEmails": True,
            "enrichEmployeeLocation": True,
            "forceRefresh": False,
            "validateSlugs": True,
            "streamDataset": False,
            "dryRun": False,
        }
        
        headers = {"Content-Type": "application/json"}

        try:
            print(f"\n[DEBUG] Lancio Apify per {self.PLATFORM}")
            print(f"[DEBUG] Actor: {settings.apify_linkedin_profile_actor_id}")
            print(f"[DEBUG] Payload: {payload}")

            response = requests.post(sync_url, json=payload, headers=headers, timeout=180)

            if response.status_code not in (200, 201):
                store.add_evidence(
                    source=self.SOURCE,
                    evidence_type=EvidenceType.ERROR,
                    value=f"Errore API Apify: {response.status_code} - {response.text}",
                    confidence=0.0,
                )
                return

            results = response.json()
            print(f"[DEBUG] Oggetti LinkedIn trovati: {len(results)}\n")

            for item in results:
                self._store_apify_profile(store, item)

        except Exception as exc:
            store.add_evidence(
                source=self.SOURCE,
                evidence_type=EvidenceType.ERROR,
                value=f"Errore durante lo scraping attivo di LinkedIn: {str(exc)}",
                confidence=0.0,
            )

    def _store_apify_profile(self, store: EvidenceStore, item: dict) -> None:
        profile_url = (
            item.get("url")
            or item.get("profileUrl")
            or item.get("linkedinUrl")
            or item.get("linkedInUrl")
        )

        username = item.get("username") or (
            store.extract_username(profile_url) if profile_url else None
        )

        name = (
            item.get("name")
            or item.get("fullName")
            or item.get("full_name")
            or item.get("title")
            or ""
        )

        headline = item.get("headline") or item.get("occupation") or ""
        summary = item.get("summary") or item.get("about") or item.get("description") or ""
        location = item.get("location") or item.get("address") or ""
        company = item.get("company") or item.get("currentCompany") or ""
        education = item.get("education") or item.get("educations") or ""
        positions = item.get("positions") or item.get("experience") or item.get("experiences") or ""
        skills = item.get("skills") or ""

        combined_text = " ".join(
            str(value)
            for value in [
                name,
                headline,
                summary,
                location,
                company,
                education,
                positions,
                skills,
            ]
            if value
        )

        if not combined_text.strip():
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
                store,
                evidence,
                text,
                confidence,
                self.PLATFORM,
                self.SOURCE,
            )