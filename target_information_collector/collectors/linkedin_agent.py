import requests
from target_information_collector.collectors.base_agent import BaseAgent
from target_information_collector.evidence.evidence_store import EvidenceStore
from target_information_collector.shared.models import EvidenceSource, EvidenceType
from target_information_collector.shared.config import settings

class LinkedInAgent(BaseAgent):
    PLATFORM = "linkedin"
    SOURCE = EvidenceSource.LINKEDIN

    def collect(self, store: EvidenceStore) -> None:
        self._promote_input_linkedin(store)
        self._promote_web_linkedin_candidates(store)
        
        # Esegue lo scraping attivo via Apify se configurato e se ci sono candidati
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
                strong_match_weight=0.12
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
            if candidate.platform == self.PLATFORM and candidate.url:
                urls_to_scrape.append(candidate.url)

        urls_to_scrape = list(set(urls_to_scrape))
        if not urls_to_scrape:
            return

        sync_url = f"https://api.apify.com/v2/acts/{settings.apify_linkedin_profile_actor_id}/run-sync-get-dataset-items?token={settings.apify_token}"
        payload = {"urls": urls_to_scrape}
        headers = {"Content-Type": "application/json"}

        try:
            print(f"\n[DEBUG] Lancio Apify per {self.PLATFORM}!")
            print(f"[DEBUG] URL: {sync_url}")
            print(f"[DEBUG] Payload: {payload}")
            
            response = requests.post(sync_url, json=payload, headers=headers, timeout=60)
            if response.status_code == 200:
                results = response.json()
                
                print(f"[DEBUG] Oggetti trovati: {len(results)}\n")
                
                for item in results:
                    summary = item.get("summary") or ""
                    positions = str(item.get("positions", ""))
                    skills = str(item.get("skills", ""))
                    combined_text = f"{summary} {positions} {skills}"
                    profile_url = item.get("url") or item.get("profileUrl")
                    username = item.get("username") or (store.extract_username(profile_url) if profile_url else None)

                    if combined_text.strip():
                        store.add_evidence(
                            source=self.SOURCE,
                            evidence_type=EvidenceType.PROFILE,
                            value=combined_text,
                            url=profile_url,
                            platform=self.PLATFORM,
                            username=username,
                            confidence=0.95,
                            raw_data=item
                        )
        except Exception as e:
            store.add_evidence(
                source=self.SOURCE,
                evidence_type=EvidenceType.ERROR,
                value=f"Errore durante lo scraping attivo di LinkedIn: {str(e)}",
                confidence=0.0
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
            self.extract_common_context(store, evidence, text, confidence, self.PLATFORM, self.SOURCE)