import re
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
    USERNAME_LIMIT = 12

    BAD_USERNAMES = {
        "p",
        "reel",
        "reels",
        "stories",
        "explore",
        "tags",
        "direct",
    }

    def __init__(self):
        self.normalizer = EvidenceNormalizer()

    def collect(self, store: EvidenceStore) -> None:
        self.promote_seeded_links(store, self.PLATFORM)
        self._promote_web_candidates(store)
        self._add_username_guess_candidates(store)

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

            username = evidence.username or self.normalizer.extract_username(evidence.url)
            title = evidence.title or evidence.value or ""
            description = evidence.description or ""
            text = self._join_text(title, description, evidence.url)

            confidence = self.calculate_base_score(
                store=store,
                text=text,
                username=username,
                seeded=False,
                strong_match_weight=0.12,
            )

            if confidence < self.MIN_CANDIDATE_CONFIDENCE:
                continue

            store.add_candidate(
                platform=self.PLATFORM,
                url=self._profile_url(username),
                username=username,
                display_name=title,
                confidence=confidence,
                matched_context=self.matched_context(store, text),
                raw_data={
                    "source": "instagram_web_candidate",
                    "source_evidence": evidence.model_dump(mode="json"),
                },
            )

    def _add_username_guess_candidates(self, store: EvidenceStore) -> None:
        for username in self._username_guesses(store.target.full_name):
            store.add_candidate(
                platform=self.PLATFORM,
                url=self._profile_url(username),
                username=username,
                display_name=store.target.full_name,
                confidence=0.40,
                matched_context=["username_guess"],
                raw_data={"source": "instagram_username_guess"},
            )

    def _collect_via_apify(self, store: EvidenceStore) -> None:
        usernames = self._usernames_to_scrape(store)

        if not usernames:
            return

        print(f"[DEBUG][instagram] Username mandati ad Apify: {usernames}")

        sync_url = (
            f"https://api.apify.com/v2/acts/"
            f"{settings.apify_instagram_profile_actor_id}"
            f"/run-sync-get-dataset-items?token={settings.apify_token}"
        )

        payload = {"usernames": usernames}
        headers = {"Content-Type": "application/json"}

        try:
            response = requests.post(sync_url, json=payload, headers=headers, timeout=90)

            if response.status_code not in (200, 201):
                self._add_error(
                    store=store,
                    message=f"Errore API Apify Instagram: {response.status_code} - {response.text}",
                    raw_data={"payload": payload},
                )
                return

            results = response.json()

            if not isinstance(results, list):
                self._add_error(
                    store=store,
                    message="Risposta Apify Instagram non valida",
                    raw_data={"payload": payload, "response": results},
                )
                return

            for item in results:
                self._store_apify_profile(store, item)

        except Exception as exc:
            self._add_error(
                store=store,
                message=f"Errore durante scraping Instagram: {str(exc)}",
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

        usernames = list(dict.fromkeys(usernames))
        usernames.sort(key=lambda item: self._username_priority(store, item))

        return usernames[: self.USERNAME_LIMIT]

    def _store_apify_profile(self, store: EvidenceStore, item: dict) -> None:
        username = item.get("username")

        if not username or self._is_bad_username(username):
            return

        profile_url = self.normalizer.normalize_url(
            item.get("url") or self._profile_url(username)
        )

        if not profile_url:
            return

        full_name = item.get("fullName") or item.get("name") or ""
        biography = item.get("biography") or item.get("biographyText") or ""
        combined_text = self._join_text(username, full_name, biography)

        if not combined_text:
            return

        print(f"[DEBUG][instagram] Profilo Apify salvato: {profile_url}")
        print(f"[DEBUG][instagram] Testo profilo: {combined_text[:250]}")

        matched_context = self.matched_context(store, combined_text)

        store.add_candidate(
            platform=self.PLATFORM,
            url=profile_url,
            username=username,
            display_name=full_name,
            confidence=0.90,
            matched_context=matched_context,
            raw_data={
                "source": "instagram_apify_profile",
                "profile": item,
            },
        )

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

        if self._username_matches_full_name(store.target.full_name, username):
            store.add_evidence(
                source=self.SOURCE,
                evidence_type=EvidenceType.IDENTITY,
                value=store.target.full_name,
                url=profile_url,
                platform=self.PLATFORM,
                username=username,
                title=full_name or None,
                description=biography or None,
                confidence=0.90,
                raw_data={"derived_from": "instagram_username"},
            )

        self._store_bio_context(store, profile_url, username, full_name, biography)

    def _store_bio_context(
        self,
        store: EvidenceStore,
        profile_url: str,
        username: str,
        full_name: str,
        biography: str,
    ) -> None:
        text = self._join_text(full_name, biography)
        lowered = text.lower()

        for location in self._known_locations(store):
            if location.lower() in lowered:
                store.add_evidence(
                    source=self.SOURCE,
                    evidence_type=EvidenceType.LOCATION,
                    value=location,
                    url=profile_url,
                    platform=self.PLATFORM,
                    username=username,
                    title=full_name or None,
                    description=biography or None,
                    confidence=0.85,
                    raw_data={"derived_from": "instagram_bio"},
                )

        if self._mentions_target_organization(store, lowered):
            store.add_evidence(
                source=self.SOURCE,
                evidence_type=EvidenceType.ORGANIZATION,
                value=store.target.company,
                url=profile_url,
                platform=self.PLATFORM,
                username=username,
                title=full_name or None,
                description=biography or None,
                confidence=0.85,
                raw_data={"derived_from": "instagram_bio"},
            )

            store.add_evidence(
                source=self.SOURCE,
                evidence_type=EvidenceType.EDUCATION,
                value=store.target.company,
                url=profile_url,
                platform=self.PLATFORM,
                username=username,
                title=full_name or None,
                description=biography or None,
                confidence=0.85,
                raw_data={"derived_from": "instagram_bio"},
            )

    def _extract_instagram_context(self, store: EvidenceStore) -> None:
        for evidence in store.evidence:
            if evidence.platform != self.PLATFORM:
                continue

            text = self._join_text(evidence.title, evidence.description, evidence.value)

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

            self._extract_age_hints(store, evidence, text, confidence)

    def _extract_age_hints(self, store: EvidenceStore, evidence, text: str, confidence: float) -> None:
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

    def _username_guesses(self, full_name: str) -> list[str]:
        parts = [
            self._normalize_part(part)
            for part in full_name.split()
            if self._normalize_part(part)
        ]

        if len(parts) < 2:
            return []

        first = parts[0]
        last = parts[-1]

        return [
            f"{first}_{last}",
            f"{first}.{last}",
            f"{first}{last}",
            f"{last}_{first}",
            f"{last}.{first}",
            f"{last}{first}",
        ]

    def _username_priority(self, store: EvidenceStore, username: str) -> tuple[int, str]:
        normalized = self._normalize_part(username)
        parts = [
            self._normalize_part(part)
            for part in store.target.full_name.split()
            if self._normalize_part(part)
        ]

        if len(parts) >= 2 and all(part in normalized for part in parts):
            return (0, username.lower())

        return (1, username.lower())

    def _known_locations(self, store: EvidenceStore) -> list[str]:
        values = []

        if store.target.location:
            values.append(store.target.location)

        values.extend(store.target.cities)

        for evidence in store.evidence:
            if evidence.evidence_type == EvidenceType.LOCATION and evidence.value:
                values.append(evidence.value)

        output = []

        for value in self._unique(values):
            output.append(value)

            for piece in str(value).split(","):
                cleaned = piece.strip()

                if len(cleaned) > 2:
                    output.append(cleaned)

        return self._unique(output)

    def _mentions_target_organization(self, store: EvidenceStore, lowered_text: str) -> bool:
        if not store.target.company:
            return False

        terms = self._organization_terms(store.target.company)

        return any(term in lowered_text for term in terms)

    def _organization_terms(self, organization: str) -> list[str]:
        lowered = organization.lower()

        terms = [lowered]

        if "salerno" in lowered:
            terms.extend(["unisa", "dinfunisa", "dipartimento di informatica"])

        return self._unique(terms)

    def _username_matches_full_name(self, full_name: str, username: str | None) -> bool:
        if not username:
            return False

        normalized_username = self._normalize_part(username)
        parts = [
            self._normalize_part(part)
            for part in full_name.split()
            if len(part) > 2
        ]

        return bool(parts) and all(part in normalized_username for part in parts)

    def _profile_url(self, username: str | None) -> str | None:
        if not username:
            return None

        return f"https://instagram.com/{username.strip('/')}"

    def _is_bad_instagram_url(self, url: str) -> bool:
        lower = url.lower()

        return any(
            part in lower
            for part in ["/p/", "/reel/", "/reels/", "/stories/", "/explore/", "/tags/", "/direct/"]
        )

    def _is_bad_username(self, username: str) -> bool:
        return username.lower().strip("/") in self.BAD_USERNAMES

    def _normalize_part(self, value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value).lower())

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