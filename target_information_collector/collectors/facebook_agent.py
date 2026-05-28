import requests
from target_information_collector.collectors.base_agent import BaseAgent
from target_information_collector.evidence.evidence_store import EvidenceStore
from target_information_collector.shared.models import EvidenceSource, EvidenceType
from target_information_collector.shared.config import settings

class FacebookAgent(BaseAgent):
    PLATFORM = "facebook"
    SOURCE = EvidenceSource.FACEBOOK

    def collect(self, store: EvidenceStore) -> None:
        self._promote_seeded_facebook_links(store)
        self._promote_web_facebook_candidates(store)
        
        # Esegue lo scraping attivo via Apify se configurato e se ci sono candidati
        if settings.apify_token and getattr(settings, "apify_facebook_profile_actor_id", None):
            self._collect_via_apify(store)
            
        self._extract_facebook_context(store)

    def _promote_seeded_facebook_links(self, store: EvidenceStore) -> None:
        for evidence in store.evidence:
            if evidence.platform == self.PLATFORM and evidence.source == EvidenceSource.INPUT and evidence.url:
                store.add_candidate(
                    platform=self.PLATFORM,
                    url=evidence.url,
                    username=evidence.username,
                    display_name=store.target.full_name,
                    confidence=0.75,
                    matched_context=store.get_context_terms(),
                    raw_data={"seeded_from_input": True},
                )

    def _promote_web_facebook_candidates(self, store: EvidenceStore) -> None:
        for evidence in store.evidence:
            if evidence.platform == self.PLATFORM and evidence.evidence_type in {EvidenceType.WEB_MENTION, EvidenceType.PROFILE, EvidenceType.PUBLIC_LINK}:
                if not evidence.url:
                    continue
                
                # Ignora link di servizio di Facebook
                if self._is_bad_facebook_url(evidence.url):
                    continue
                    
                title = evidence.title or evidence.value or ""
                description = evidence.description or ""
                text = f"{title} {description} {evidence.url}"
                
                confidence = self.calculate_base_score(
                    store=store,
                    text=text,
                    username=evidence.username,
                    seeded=False,
                    strong_match_weight=0.08
                )
                
                if confidence < 0.40:
                    continue
                    
                store.add_candidate(
                    platform=self.PLATFORM,
                    url=evidence.url,
                    username=evidence.username,
                    display_name=evidence.title or evidence.value or store.target.full_name,
                    confidence=confidence,
                    matched_context=self.matched_context(store, text),
                    raw_data={"source_evidence": evidence.model_dump(mode="json")},
                )

    def _is_bad_facebook_url(self, url: str) -> bool:
        lower = url.lower()
        bad_parts = ["photo.php", "permalink.php", "/groups/", "/pages/", "/watch/", "story.php", "events/"]
        return any(part in lower for part in bad_parts)

    def _collect_via_apify(self, store: EvidenceStore) -> None:
        urls_to_scrape = []
        for candidate in store.candidates:
            if candidate.platform == self.PLATFORM and candidate.url:
                urls_to_scrape.append(candidate.url)

        urls_to_scrape = list(set(urls_to_scrape))
        if not urls_to_scrape:
            return

        sync_url = f"https://api.apify.com/v2/acts/{settings.apify_facebook_profile_actor_id}/run-sync-get-dataset-items?token={settings.apify_token}"
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
                    about = item.get("about") or item.get("biography") or ""
                    posts = " ".join([p.get("text", "") for p in item.get("posts", []) if isinstance(p, dict)])
                    combined_text = f"{about} {posts}"
                    profile_url = item.get("url")
                    username = item.get("username") or (store.extract_username(profile_url) if profile_url else None)

                    if combined_text.strip():
                        store.add_evidence(
                            source=self.SOURCE,
                            evidence_type=EvidenceType.PROFILE,
                            value=combined_text,
                            url=profile_url,
                            platform=self.PLATFORM,
                            username=username,
                            confidence=0.90,
                            raw_data=item
                        )
        except Exception as e:
            store.add_evidence(
                source=self.SOURCE,
                evidence_type=EvidenceType.ERROR,
                value=f"Errore durante lo scraping attivo di Facebook: {str(e)}",
                confidence=0.0
            )

    def _extract_facebook_context(self, store: EvidenceStore) -> None:
        # Estrazione iniziale dai frammenti web/candidati scoperti
        for candidate in store.candidates:
            if candidate.platform != self.PLATFORM:
                continue
            raw_data = candidate.raw_data or {}
            if "source_evidence" in raw_data:
                source_ev = raw_data["source_evidence"]
                text = (source_ev.get("title", "") + " " + source_ev.get("description", "")).lower()
                self._extract_locations(store, candidate, text, candidate.confidence)
                self._extract_roles(store, candidate, text, candidate.confidence)
        
        # Estrazione profonda del contesto comune basata sulle evidenze reali scaricate via Apify
        for evidence in store.evidence:
            if evidence.platform == self.PLATFORM and evidence.source == self.SOURCE:
                text = str(evidence.value)
                self.extract_common_context(store, evidence, text, evidence.confidence, self.PLATFORM, self.SOURCE)

    def _extract_locations(self, store: EvidenceStore, evidence, text: str, confidence: float) -> None:
        for location in store.target.cities + [store.target.location]:
            if location and location.lower() in text:
                store.add_evidence(
                    source=EvidenceSource.FACEBOOK,
                    evidence_type=EvidenceType.LOCATION,
                    value=location,
                    url=evidence.url,
                    platform=self.PLATFORM,
                    username=evidence.username,
                    confidence=confidence,
                    raw_data={"derived_from": "facebook_snippet"},
                )

    def _extract_roles(self, store: EvidenceStore, evidence, text: str, confidence: float) -> None:
        if store.target.role and store.target.role.lower() in text:
            store.add_evidence(
                source=EvidenceSource.FACEBOOK,
                evidence_type=EvidenceType.ROLE,
                value=store.target.role,
                url=evidence.url,
                platform=self.PLATFORM,
                username=evidence.username,
                confidence=confidence,
                raw_data={"derived_from": "facebook_snippet"},
            )