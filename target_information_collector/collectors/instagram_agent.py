import requests
from target_information_collector.collectors.base_agent import BaseAgent
from target_information_collector.evidence.evidence_store import EvidenceStore
from target_information_collector.shared.models import EvidenceSource, EvidenceType
from target_information_collector.shared.config import settings

class InstagramAgent(BaseAgent):
    PLATFORM = "instagram"
    SOURCE = EvidenceSource.INSTAGRAM

    def collect(self, store: EvidenceStore) -> None:
        self.promote_seeded_links(store, self.PLATFORM)
        self._promote_web_instagram_candidates(store)
        
        if settings.apify_token and getattr(settings, "apify_instagram_profile_actor_id", None):
            self._collect_via_apify(store)

        self._extract_instagram_context(store)

    def _promote_web_instagram_candidates(self, store: EvidenceStore) -> None:
        for evidence in store.evidence:
            if evidence.platform != self.PLATFORM:
                continue

            if evidence.evidence_type not in {
                EvidenceType.PUBLIC_LINK,
                EvidenceType.SOCIAL_HINT,
                EvidenceType.WEB_MENTION,
            }:
                continue

            if not evidence.url:
                continue

            if self._is_bad_instagram_url(evidence.url):
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
                raw_data={"source_evidence": evidence.model_dump(mode="json")}
            )

    def _collect_via_apify(self, store: EvidenceStore) -> None:
        usernames_to_scrape = []
        for candidate in store.candidates:
            if candidate.platform == self.PLATFORM:
                username = candidate.username
                # SE lo username è vuoto ma abbiamo l'URL, lo estraiamo noi
                if not username and candidate.url:
                    parts = candidate.url.rstrip("/").split("/")
                    if len(parts) > 0:
                        username = parts[-1].split("?")[0]
                
                if username:
                    usernames_to_scrape.append(username)

        usernames_to_scrape = list(set(usernames_to_scrape))
        if not usernames_to_scrape:
            return

        sync_url = f"https://api.apify.com/v2/acts/{settings.apify_instagram_profile_actor_id}/run-sync-get-dataset-items?token={settings.apify_token}"
        payload = {"usernames": usernames_to_scrape}
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
                    biography = item.get("biography") or item.get("biographyText") or ""
                    username = item.get("username")
                    profile_url = f"https://instagram.com/{username}" if username else item.get("url")
                    
                    if biography.strip():
                        store.add_evidence(
                            source=self.SOURCE,
                            evidence_type=EvidenceType.PROFILE,
                            value=biography,
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
                value=f"Errore durante lo scraping attivo di Instagram: {str(e)}",
                confidence=0.0
            )

    def _extract_instagram_context(self, store: EvidenceStore) -> None:
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
            self._extract_age_hints(store, evidence, text, confidence)

    def _extract_age_hints(
        self,
        store: EvidenceStore,
        evidence,
        text: str,
        confidence: float,
    ) -> None:
        tokens = [token.strip(".,;:()[]{}<>") for token in text.split()]
        for token in tokens:
            if not token.isdigit():
                continue

            age = int(token)
            if age < 14 or age > 90:
                continue

            store.add_evidence(
                source=self.SOURCE,
                evidence_type=EvidenceType.SOCIAL_HINT,
                value=f"possible_age:{age}",
                url=evidence.url,
                platform=self.PLATFORM,
                username=evidence.username,
                title=evidence.title,
                description=evidence.description,
                confidence=min(confidence, 0.55),
                raw_data={
                    "hint_type": "possible_age",
                    "age": age,
                    "derived_from": "instagram_snippet",
                },
            )

    def _is_bad_instagram_url(self, url: str) -> bool:
        lower = url.lower()
        bad_parts = ["/p/", "/reel/", "/stories/", "/explore/", "/tags/", "/direct/"]
        return any(part in lower for part in bad_parts)