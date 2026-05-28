import requests

from target_information_collector.agents.base_agent import BaseAgent
from target_information_collector.evidence.evidence_normalizer import EvidenceNormalizer
from target_information_collector.evidence.evidence_store import EvidenceStore
from target_information_collector.shared.config import settings
from target_information_collector.shared.models import EvidenceSource, EvidenceType


class InstagramAgent(BaseAgent):
    PLATFORM = "instagram"
    SOURCE = EvidenceSource.INSTAGRAM

    MIN_CANDIDATE_CONFIDENCE = 0.45

    def __init__(self):
        self.normalizer = EvidenceNormalizer()

    def collect(self, store: EvidenceStore) -> None:
        self.promote_seeded_links(store, self.PLATFORM)
        self._promote_web_candidates(store)

        if settings.apify_token and settings.apify_instagram_profile_actor_id:
            self._collect_via_apify(store)

        self._extract_instagram_context(store)

    def _promote_web_candidates(self, store: EvidenceStore) -> None:
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

            if not evidence.url or self._is_bad_instagram_url(evidence.url):
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

            if confidence < self.MIN_CANDIDATE_CONFIDENCE:
                continue

            store.add_candidate(
                platform=self.PLATFORM,
                url=evidence.url,
                username=evidence.username or self.normalizer.extract_username(evidence.url),
                display_name=title,
                confidence=confidence,
                matched_context=self.matched_context(store, text),
                raw_data={"source_evidence": evidence.model_dump(mode="json")},
            )

    def _collect_via_apify(self, store: EvidenceStore) -> None:
        usernames = self._usernames_to_scrape(store)

        if not usernames:
            return

        sync_url = (
            f"https://api.apify.com/v2/acts/"
            f"{settings.apify_instagram_profile_actor_id}"
            f"/run-sync-get-dataset-items?token={settings.apify_token}"
        )

        payload = {"usernames": usernames}
        headers = {"Content-Type": "application/json"}

        try:
            response = requests.post(
                sync_url,
                json=payload,
                headers=headers,
                timeout=60,
            )

            if response.status_code not in (200, 201):
                self._add_error(
                    store=store,
                    message=f"Errore API Apify Instagram: {response.status_code} - {response.text}",
                    raw_data={"payload": payload},
                )
                return

            results = response.json()

            for item in results:
                self._store_apify_profile(store, item)

        except Exception as exc:
            self._add_error(
                store=store,
                message=f"Errore durante lo scraping attivo di Instagram: {str(exc)}",
                raw_data={"payload": payload},
            )

    def _usernames_to_scrape(self, store: EvidenceStore) -> list[str]:
        usernames = []

        for candidate in store.candidates:
            if candidate.platform != self.PLATFORM:
                continue

            username = candidate.username

            if not username and candidate.url:
                username = self.normalizer.extract_username(candidate.url)

            if username and not self._is_bad_username(username):
                usernames.append(username)

        return list(dict.fromkeys(usernames))

    def _store_apify_profile(self, store: EvidenceStore, item: dict) -> None:
        username = item.get("username")
        profile_url = self.normalizer.normalize_url(
            item.get("url") or f"https://instagram.com/{username}" if username else None
        )

        if not profile_url:
            return

        full_name = item.get("fullName") or ""
        biography = item.get("biography") or item.get("biographyText") or ""
        combined_text = self._join_text(full_name, biography)

        if not combined_text:
            return

        store.add_evidence(
            source=self.SOURCE,
            evidence_type=EvidenceType.PROFILE,
            value=combined_text,
            url=profile_url,
            platform=self.PLATFORM,
            username=username,
            title=full_name or None,
            description=biography or None,
            confidence=0.90,
            raw_data=item,
        )

    def _extract_instagram_context(self, store: EvidenceStore) -> None:
        for evidence in store.evidence:
            if evidence.platform != self.PLATFORM:
                continue

            text = self._join_text(
                evidence.title,
                evidence.description,
                evidence.value,
            )

            if not text:
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

            self._extract_age_hints(
                store=store,
                evidence=evidence,
                text=text,
                confidence=confidence,
            )

    def _extract_age_hints(
        self,
        store: EvidenceStore,
        evidence,
        text: str,
        confidence: float,
    ) -> None:
        for token in text.split():
            cleaned = token.strip(".,;:()[]{}<>|/")

            if not cleaned.isdigit():
                continue

            age = int(cleaned)

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
        bad_parts = [
            "/p/",
            "/reel/",
            "/reels/",
            "/stories/",
            "/explore/",
            "/tags/",
            "/direct/",
        ]

        return any(part in lower for part in bad_parts)

    def _is_bad_username(self, username: str) -> bool:
        return username.lower() in {
            "p",
            "reel",
            "reels",
            "stories",
            "explore",
            "tags",
            "direct",
        }

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