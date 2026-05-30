import html
import re
import time
from urllib.parse import urljoin, urlparse, urlunparse

import requests

from target_information_collector.agents.base_agent import BaseAgent
from target_information_collector.evidence.evidence_normalizer import EvidenceNormalizer
from target_information_collector.evidence.evidence_store import EvidenceStore
from target_information_collector.shared.config import settings
from target_information_collector.shared.models import EvidenceSource, EvidenceType


class FacebookAgent(BaseAgent):
    PLATFORM = "facebook"
    SOURCE = EvidenceSource.FACEBOOK

    PUBLIC_SEARCH_LIMIT = 12
    APIFY_URL_LIMIT = 12
    MIN_WEB_CANDIDATE_CONFIDENCE = 0.40

    BAD_FACEBOOK_PATH_PARTS = {
        "photos", "photo", "posts", "post", "videos", "video", "watch",
        "reel", "reels", "stories", "story", "groups", "pages", "events",
        "friends", "followers", "following", "about",
    }

    EDUCATION_KEYS = {"college", "school", "secondary_school", "university", "education"}
    LOCATION_KEYS = {"current_city", "hometown", "location", "city"}
    ORG_KEYS = {"work", "company", "employer", "organization"}
    ROLE_KEYS = {"intro", "bio", "headline", "job_title", "position"}

    def __init__(self):
        self.normalizer = EvidenceNormalizer()

    def collect(self, store: EvidenceStore) -> None:
        self.promote_seeded_links(store, self.PLATFORM)
        self._promote_web_candidates(store)
        self._discover_public_profiles(store)

        if settings.apify_token and settings.apify_facebook_profile_actor_id:
            self._collect_via_apify(store)

        self._extract_facebook_context(store)

    def _discover_public_profiles(self, store: EvidenceStore) -> None:
        public_url = self._public_search_url(store.target.full_name)

        try:
            profiles = self._load_public_profiles_with_selenium(public_url, store.target.full_name)

            print(f"[DEBUG][facebook] Public search URL: {public_url}")
            print(f"[DEBUG][facebook] Profili exact-name trovati: {len(profiles)}")

            for profile in profiles[: self.PUBLIC_SEARCH_LIMIT]:
                self._store_public_profile_candidate(store, profile, public_url)

        except Exception as exc:
            self._add_error(
                store=store,
                message=f"Errore durante Facebook public search con Selenium: {str(exc)}",
                url=public_url,
                raw_data={"phase": "facebook_public_search"},
            )

    def _load_public_profiles_with_selenium(self, public_url: str, full_name: str) -> list[dict]:
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
        except Exception as exc:
            raise RuntimeError("Selenium non è installato. Esegui: pip install selenium") from exc

        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1365,1200")
        options.add_argument("--lang=it-IT")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        driver = webdriver.Chrome(service=Service(), options=options)

        try:
            driver.get(public_url)
            time.sleep(4)
            self._try_accept_cookies(driver)
            time.sleep(1)
            self._scroll_public_page(driver)

            profiles = []
            profiles.extend(self._extract_profiles_from_dom(driver, full_name))
            profiles.extend(self._extract_profiles_from_html(driver.page_source or "", full_name))

            return self._deduplicate_profiles(profiles)

        finally:
            driver.quit()

    def _scroll_public_page(self, driver) -> None:
        last_height = 0

        for _ in range(5):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1.5)

            height = driver.execute_script("return document.body.scrollHeight") or 0

            if height == last_height:
                break

            last_height = height

    def _try_accept_cookies(self, driver) -> None:
        labels = [
            "Consenti tutti i cookie",
            "Accetta tutti",
            "Allow all cookies",
            "Accept all",
            "Accetta",
        ]

        for label in labels:
            try:
                buttons = driver.find_elements("xpath", f"//*[contains(text(), '{label}')]")

                for button in buttons:
                    if button.is_displayed() and button.is_enabled():
                        button.click()
                        return

            except Exception:
                continue

    def _extract_profiles_from_dom(self, driver, full_name: str) -> list[dict]:
        profiles = []

        try:
            anchors = driver.find_elements("tag name", "a")
        except Exception:
            return profiles

        for anchor in anchors:
            try:
                href = anchor.get_attribute("href")
                label = anchor.text or anchor.get_attribute("aria-label") or ""
            except Exception:
                continue

            profile = self._build_public_profile(href, label, full_name)

            if profile:
                profiles.append(profile)

        return profiles

    def _extract_profiles_from_html(self, page_html: str, full_name: str) -> list[dict]:
        profiles = []

        matches = re.findall(
            r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            page_html,
            flags=re.IGNORECASE | re.DOTALL,
        )

        for href, label in matches:
            profile = self._build_public_profile(html.unescape(href), label, full_name)

            if profile:
                profiles.append(profile)

        return profiles

    def _build_public_profile(
        self,
        href: str | None,
        label: str | None,
        full_name: str,
    ) -> dict | None:
        if not href:
            return None

        url = self._facebook_profile_root(urljoin("https://www.facebook.com", href))

        if not url:
            return None

        username = self.normalizer.extract_username(url)
        clean_label = self._clean_text(label or "")

        if not self._is_exact_target_profile(full_name, clean_label, username):
            return None

        return {
            "name": clean_label or full_name,
            "url": url,
        }

    def _is_exact_target_profile(
        self,
        full_name: str,
        label: str | None,
        username: str | None,
    ) -> bool:
        expected = self._normalize_name(full_name)
        normalized_label = self._normalize_name(label or "")

        if normalized_label == expected:
            return True

        return self._username_matches_full_name(full_name, username)

    def _store_public_profile_candidate(
        self,
        store: EvidenceStore,
        profile: dict,
        public_url: str,
    ) -> None:
        url = self._facebook_profile_root(profile["url"])

        if not url:
            return

        username = self.normalizer.extract_username(url)

        print(f"[DEBUG][facebook] Public candidate: {url}")

        store.add_candidate(
            platform=self.PLATFORM,
            url=url,
            username=username,
            display_name=profile["name"],
            confidence=0.45,
            matched_context=[store.target.full_name],
            raw_data={
                "source": "facebook_public_search",
                "public_search_url": public_url,
            },
        )

        store.add_evidence(
            source=self.SOURCE,
            evidence_type=EvidenceType.PUBLIC_LINK,
            value=profile["name"],
            url=url,
            platform=self.PLATFORM,
            username=username,
            title=profile["name"],
            confidence=0.45,
            raw_data={
                "derived_from": "facebook_public_search",
                "public_search_url": public_url,
            },
        )

    def _promote_web_candidates(self, store: EvidenceStore) -> None:
        for evidence in store.evidence:
            if evidence.platform != self.PLATFORM:
                continue

            if evidence.evidence_type not in {
                EvidenceType.WEB_MENTION,
                EvidenceType.PROFILE,
                EvidenceType.PUBLIC_LINK,
                EvidenceType.SOCIAL_HINT,
            }:
                continue

            root_url = self._facebook_profile_root(evidence.url)

            if not root_url:
                continue

            title = evidence.title or evidence.value or ""
            description = evidence.description or ""
            username = evidence.username or self.normalizer.extract_username(root_url)
            text = self._join_text(title, description, root_url)

            confidence = self.calculate_base_score(
                store=store,
                text=text,
                username=username,
                seeded=False,
                strong_match_weight=0.08,
            )

            if confidence < self.MIN_WEB_CANDIDATE_CONFIDENCE:
                continue

            store.add_candidate(
                platform=self.PLATFORM,
                url=root_url,
                username=username,
                display_name=title,
                confidence=confidence,
                matched_context=self.matched_context(store, text),
                raw_data={
                    "source": "facebook_web_candidate",
                    "source_evidence": evidence.model_dump(mode="json"),
                },
            )

    def _collect_via_apify(self, store: EvidenceStore) -> None:
        urls_to_scrape = self._urls_to_scrape(store)

        if not urls_to_scrape:
            print("[DEBUG][facebook] Nessun URL da mandare ad Apify.")
            return

        print(f"[DEBUG][facebook] URL mandati ad Apify: {urls_to_scrape}")

        sync_url = (
            f"https://api.apify.com/v2/acts/"
            f"{settings.apify_facebook_profile_actor_id}"
            f"/run-sync-get-dataset-items?token={settings.apify_token}"
        )

        headers = {"Content-Type": "application/json"}

        for url in urls_to_scrape:
            payload = {
                "endpoint": "details_by_url",
                "max_posts": 0,
                "urls_text": url,
            }

            try:
                print(f"[DEBUG][facebook] Lancio Apify per: {url}")

                response = requests.post(sync_url, json=payload, headers=headers, timeout=120)

                if response.status_code not in (200, 201):
                    self._add_error(
                        store=store,
                        message=f"Errore API Apify per {url}: {response.status_code} - {response.text}",
                        url=url,
                        raw_data={"payload": payload},
                    )
                    continue

                results = response.json()

                if not isinstance(results, list):
                    self._add_error(
                        store=store,
                        message=f"Risposta Apify Facebook non valida per {url}",
                        url=url,
                        raw_data={"payload": payload, "response": results},
                    )
                    continue

                print(f"[DEBUG][facebook] Oggetti Apify per {url}: {len(results)}")

                for item in results:
                    self._store_apify_profile(store, item, fallback_url=url)

            except Exception as exc:
                self._add_error(
                    store=store,
                    message=f"Errore durante scraping Facebook per {url}: {str(exc)}",
                    url=url,
                    raw_data={"payload": payload},
                )

    def _urls_to_scrape(self, store: EvidenceStore) -> list[str]:
        urls = []

        for candidate in store.candidates:
            if candidate.platform != self.PLATFORM or not candidate.url:
                continue

            url = self._facebook_profile_root(candidate.url)

            if not url:
                continue

            raw_data = candidate.raw_data or {}
            source = raw_data.get("source")

            is_seeded = raw_data.get("seeded_from_input") is True
            is_public_search = source == "facebook_public_search"
            is_web_candidate = source == "facebook_web_candidate"

            if not (is_seeded or is_public_search or is_web_candidate):
                continue

            if is_web_candidate and candidate.confidence < self.MIN_WEB_CANDIDATE_CONFIDENCE:
                continue

            urls.append(url)

        urls = list(dict.fromkeys(urls))
        urls.sort(key=lambda item: self._facebook_url_priority(store, item))

        return urls[: self.APIFY_URL_LIMIT]

    def _facebook_url_priority(self, store: EvidenceStore, url: str) -> tuple[int, str]:
        username = self.normalizer.extract_username(url) or ""
        normalized_username = self._normalize_name(username)

        name_parts = [
            self._normalize_name(part)
            for part in store.target.full_name.split()
            if len(part) > 2
        ]

        exact_name_username = bool(name_parts) and all(
            part in normalized_username
            for part in name_parts
        )

        if exact_name_username and username != "people":
            return (0, url.lower())

        if username == "people":
            return (1, url.lower())

        return (2, url.lower())

    def _store_apify_profile(
        self,
        store: EvidenceStore,
        item: dict,
        fallback_url: str,
    ) -> None:
        profile = item.get("profile") or {}

        if not profile:
            self._add_error(
                store=store,
                message=f"Apify ha restituito item senza profile per {fallback_url}",
                url=fallback_url,
                raw_data={"item": item},
            )
            return

        profile_url = self._facebook_profile_root(profile.get("url") or fallback_url)

        if not profile_url:
            return

        username = self.normalizer.extract_username(profile_url)
        name = profile.get("name") or ""
        combined_text = self._profile_text(profile)

        print(f"[DEBUG][facebook] Profilo Apify salvato: {profile_url}")
        print(f"[DEBUG][facebook] Testo profilo: {combined_text[:250]}")

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
            confidence=0.90,
            raw_data=item,
        )

        if self._normalize_name(name) == self._normalize_name(store.target.full_name):
            store.add_evidence(
                source=self.SOURCE,
                evidence_type=EvidenceType.IDENTITY,
                value=name,
                url=profile_url,
                platform=self.PLATFORM,
                username=username,
                confidence=0.90,
                raw_data={"derived_from": "facebook_apify_profile"},
            )

        store.add_candidate(
            platform=self.PLATFORM,
            url=profile_url,
            username=username,
            display_name=name,
            confidence=0.90,
            matched_context=self.matched_context(store, combined_text),
            raw_data={
                "source": "facebook_apify_profile",
                "profile": profile,
            },
        )

        self._store_structured_profile_evidence(
            store=store,
            profile=profile,
            profile_url=profile_url,
            username=username,
        )

    def _store_structured_profile_evidence(
        self,
        store: EvidenceStore,
        profile: dict,
        profile_url: str,
        username: str | None,
    ) -> None:
        flat_items = self._flatten_profile_values(profile)

        for key, value in flat_items:
            cleaned = self._clean_text(value)

            if not cleaned:
                continue

            if key in self.LOCATION_KEYS:
                self._add_profile_field_evidence(
                    store=store,
                    evidence_type=EvidenceType.LOCATION,
                    value=cleaned,
                    url=profile_url,
                    username=username,
                    field=key,
                    raw_profile=profile,
                )

            elif key in self.EDUCATION_KEYS or self._looks_like_education(cleaned):
                self._add_profile_field_evidence(
                    store=store,
                    evidence_type=EvidenceType.EDUCATION,
                    value=cleaned,
                    url=profile_url,
                    username=username,
                    field=key,
                    raw_profile=profile,
                )

            elif key in self.ORG_KEYS:
                self._add_profile_field_evidence(
                    store=store,
                    evidence_type=EvidenceType.ORGANIZATION,
                    value=cleaned,
                    url=profile_url,
                    username=username,
                    field=key,
                    raw_profile=profile,
                )

            elif key in self.ROLE_KEYS:
                self._add_profile_field_evidence(
                    store=store,
                    evidence_type=EvidenceType.ROLE,
                    value=cleaned,
                    url=profile_url,
                    username=username,
                    field=key,
                    raw_profile=profile,
                )

    def _add_profile_field_evidence(
        self,
        store: EvidenceStore,
        evidence_type: EvidenceType,
        value: str,
        url: str,
        username: str | None,
        field: str,
        raw_profile: dict,
    ) -> None:
        store.add_evidence(
            source=self.SOURCE,
            evidence_type=evidence_type,
            value=value,
            url=url,
            platform=self.PLATFORM,
            username=username,
            confidence=0.90,
            raw_data={
                "field": field,
                "derived_from": "facebook_apify_profile",
                "profile_name": raw_profile.get("name"),
            },
        )

    def _flatten_profile_values(self, profile: dict) -> list[tuple[str, str]]:
        values = []

        def visit(node, key: str = ""):
            if isinstance(node, dict):
                for child_key, child_value in node.items():
                    visit(child_value, child_key)

            elif isinstance(node, list):
                for item in node:
                    visit(item, key)

            elif isinstance(node, str):
                values.append((key, node))

        visit(profile)
        return values

    def _profile_text(self, profile: dict) -> str:
        about = profile.get("about") or {}

        values = [
            profile.get("name"),
            profile.get("intro"),
            profile.get("bio"),
            profile.get("current_city"),
            profile.get("hometown"),
            about.get("work"),
            about.get("college"),
            about.get("secondary_school"),
            self._about_public_text(profile),
        ]

        for _, value in self._flatten_profile_values(about):
            values.append(value)

        return self._join_text(*values)

    def _extract_facebook_context(self, store: EvidenceStore) -> None:
        for evidence in store.evidence:
            if evidence.platform != self.PLATFORM:
                continue

            if evidence.evidence_type not in {
                EvidenceType.PROFILE,
                EvidenceType.PUBLIC_LINK,
                EvidenceType.WEB_MENTION,
                EvidenceType.SOCIAL_HINT,
            }:
                continue

            root_url = self._facebook_profile_root(evidence.url)

            if evidence.url and not root_url:
                continue

            text = self._join_text(evidence.title, evidence.description, evidence.value)

            if not text:
                continue

            self.extract_common_context(
                store=store,
                evidence=evidence,
                text=text,
                confidence=max(evidence.confidence, 0.6),
                platform=self.PLATFORM,
                source=self.SOURCE,
            )

    def _about_public_text(self, profile: dict) -> str:
        about_public = profile.get("about_public") or []
        values = []

        for item in about_public:
            if isinstance(item, dict) and item.get("text"):
                values.append(item["text"])
            elif isinstance(item, str):
                values.append(item)

        return self._join_text(*values)

    def _public_search_url(self, full_name: str) -> str:
        slug = "-".join(part for part in full_name.strip().split() if part)
        return f"https://www.facebook.com/public/{slug}/"

    def _facebook_profile_root(self, url: str | None) -> str | None:
        if not url:
            return None

        normalized_url = self.normalizer.normalize_url(url)

        if not normalized_url:
            return None

        parsed = urlparse(normalized_url)
        netloc = parsed.netloc.lower()

        if "facebook.com" not in netloc:
            return None

        parts = [part for part in parsed.path.split("/") if part]

        if not parts:
            return None

        first = parts[0].strip()

        if not first or first in self.BAD_FACEBOOK_PATH_PARTS:
            return None

        if first == "profile.php":
            return normalized_url

        if first == "people":
            return normalized_url

        if len(parts) > 1 and parts[1].lower() in self.BAD_FACEBOOK_PATH_PARTS:
            parts = [first]

        return urlunparse(("https", "facebook.com", "/" + parts[0], "", "", ""))

    def _username_matches_full_name(self, full_name: str, username: str | None) -> bool:
        if not username:
            return False

        normalized_username = self._normalize_name(username)

        name_parts = [
            self._normalize_name(part)
            for part in full_name.split()
            if len(part) > 2
        ]

        return bool(name_parts) and all(part in normalized_username for part in name_parts)

    def _deduplicate_profiles(self, profiles: list[dict]) -> list[dict]:
        seen = set()
        output = []

        for profile in profiles:
            root_url = self._facebook_profile_root(profile.get("url"))

            if not root_url or root_url in seen:
                continue

            seen.add(root_url)
            output.append({
                "name": profile.get("name") or "",
                "url": root_url,
            })

        return output

    def _looks_like_education(self, value: str) -> bool:
        lowered = value.lower()

        return any(
            term in lowered
            for term in ["università", "universita", "university", "school", "college", "istituto"]
        )

    def _clean_text(self, value: str) -> str:
        value = re.sub(r"<[^>]+>", " ", str(value))
        value = html.unescape(value)
        value = re.sub(r"\s+", " ", value)
        return value.strip()

    def _normalize_name(self, value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value).lower())

    def _join_text(self, *values) -> str:
        return " ".join(str(value) for value in values if value).strip()

    def _add_error(
        self,
        store: EvidenceStore,
        message: str,
        url: str | None = None,
        raw_data: dict | None = None,
    ) -> None:
        store.add_evidence(
            source=self.SOURCE,
            evidence_type=EvidenceType.ERROR,
            value=message,
            url=url,
            platform=self.PLATFORM,
            confidence=0.0,
            raw_data=raw_data or {},
        )