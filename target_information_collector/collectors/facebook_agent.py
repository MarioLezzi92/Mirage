import html
import re
import time
from urllib.parse import urljoin, urlparse, urlunparse

import requests

from target_information_collector.collectors.base_agent import BaseAgent
from target_information_collector.evidence.evidence_store import EvidenceStore
from target_information_collector.shared.config import settings
from target_information_collector.shared.models import EvidenceSource, EvidenceType


class FacebookAgent(BaseAgent):
    PLATFORM = "facebook"
    SOURCE = EvidenceSource.FACEBOOK

    PUBLIC_SEARCH_LIMIT = 12

    def collect(self, store: EvidenceStore) -> None:
        self._promote_seeded_facebook_links(store)
        self._promote_web_facebook_candidates(store)
        self._discover_public_facebook_profiles(store)

        if settings.apify_token and getattr(settings, "apify_facebook_profile_actor_id", None):
            self._collect_via_apify(store)

        self._extract_facebook_context(store)

    def _promote_seeded_facebook_links(self, store: EvidenceStore) -> None:
        for evidence in store.evidence:
            if evidence.platform != self.PLATFORM:
                continue

            if evidence.source != EvidenceSource.INPUT:
                continue

            if not evidence.url:
                continue

            url = self._canonical_url(evidence.url)

            if self._is_bad_facebook_url(url):
                continue

            store.add_candidate(
                platform=self.PLATFORM,
                url=url,
                username=evidence.username or store.extract_username(url),
                display_name=store.target.full_name,
                confidence=0.75,
                matched_context=store.get_context_terms(),
                raw_data={"seeded_from_input": True},
            )

    def _promote_web_facebook_candidates(self, store: EvidenceStore) -> None:
        for evidence in store.evidence:
            if evidence.platform != self.PLATFORM:
                continue

            if evidence.evidence_type not in {
                EvidenceType.WEB_MENTION,
                EvidenceType.PROFILE,
                EvidenceType.PUBLIC_LINK,
            }:
                continue

            if not evidence.url:
                continue

            url = self._canonical_url(evidence.url)

            if self._is_bad_facebook_url(url):
                continue

            title = evidence.title or evidence.value or ""
            description = evidence.description or ""
            text = f"{title} {description} {url}"

            confidence = self.calculate_base_score(
                store=store,
                text=text,
                username=evidence.username,
                seeded=False,
                strong_match_weight=0.08,
            )

            if confidence < 0.40:
                continue

            store.add_candidate(
                platform=self.PLATFORM,
                url=url,
                username=evidence.username or store.extract_username(url),
                display_name=evidence.title or evidence.value or store.target.full_name,
                confidence=confidence,
                matched_context=self.matched_context(store, text),
                raw_data={"source_evidence": evidence.model_dump(mode="json")},
            )

    def _discover_public_facebook_profiles(self, store: EvidenceStore) -> None:
        public_url = self._public_search_url(store.target.full_name)

        try:
            page_html = self._load_public_search_with_selenium(public_url)

            if not page_html:
                store.add_evidence(
                    source=self.SOURCE,
                    evidence_type=EvidenceType.ERROR,
                    value="Facebook public search non ha restituito HTML utile.",
                    url=public_url,
                    platform=self.PLATFORM,
                    confidence=0.0,
                )
                return

            profiles = self._extract_public_profiles(page_html, store.target.full_name)

            for profile in profiles[: self.PUBLIC_SEARCH_LIMIT]:
                username = store.extract_username(profile["url"])

                store.add_candidate(
                    platform=self.PLATFORM,
                    url=profile["url"],
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
                    url=profile["url"],
                    platform=self.PLATFORM,
                    username=username,
                    title=profile["name"],
                    confidence=0.45,
                    raw_data={
                        "derived_from": "facebook_public_search",
                        "public_search_url": public_url,
                    },
                )

        except Exception as exc:
            store.add_evidence(
                source=self.SOURCE,
                evidence_type=EvidenceType.ERROR,
                value=f"Errore durante Facebook public search con Selenium: {str(exc)}",
                url=public_url,
                platform=self.PLATFORM,
                confidence=0.0,
            )

    def _load_public_search_with_selenium(self, public_url: str) -> str:
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
        except Exception as exc:
            raise RuntimeError(
                "Selenium non è installato. Esegui: pip install selenium"
            ) from exc

        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1365,900")
        options.add_argument("--lang=it-IT")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )

        driver = webdriver.Chrome(service=Service(), options=options)

        try:
            driver.get(public_url)
            time.sleep(4)

            self._try_accept_cookies(driver)
            time.sleep(1)

            return driver.page_source or ""

        finally:
            driver.quit()

    def _try_accept_cookies(self, driver) -> None:
        possible_texts = [
            "Consenti tutti i cookie",
            "Accetta tutti",
            "Allow all cookies",
            "Accept all",
            "Accetta",
        ]

        for text in possible_texts:
            try:
                buttons = driver.find_elements("xpath", f"//*[contains(text(), '{text}')]")

                for button in buttons:
                    if button.is_displayed() and button.is_enabled():
                        button.click()
                        return

            except Exception:
                continue

    def _extract_public_profiles(self, page_html: str, full_name: str) -> list[dict]:
        profiles = []
        seen = set()
        expected_name = self._normalize_name(full_name)

        for href, label in self._extract_links(page_html):
            name = self._clean_text(label)

            if self._normalize_name(name) != expected_name:
                continue

            url = self._canonical_url(urljoin("https://www.facebook.com", href))

            if self._is_bad_facebook_url(url):
                continue

            if url in seen:
                continue

            seen.add(url)
            profiles.append({"name": name, "url": url})

        return profiles

    def _extract_links(self, page_html: str) -> list[tuple[str, str]]:
        matches = re.findall(
            r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            page_html,
            flags=re.IGNORECASE | re.DOTALL,
        )

        return [(html.unescape(href), label) for href, label in matches]

    def _collect_via_apify(self, store: EvidenceStore) -> None:
        urls_to_scrape = []

        for candidate in store.candidates:
            if candidate.platform != self.PLATFORM:
                continue

            if not candidate.url:
                continue

            url = self._canonical_url(candidate.url)

            if self._is_bad_facebook_url(url):
                continue

            raw_data = candidate.raw_data or {}

            is_seeded = raw_data.get("seeded_from_input") is True
            is_public_search = raw_data.get("source") == "facebook_public_search"
            is_strong_web_candidate = candidate.confidence >= 0.70

            if not (is_seeded or is_public_search or is_strong_web_candidate):
                continue

            urls_to_scrape.append(url)

        urls_to_scrape = list(dict.fromkeys(urls_to_scrape))

        if not urls_to_scrape:
            return

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
                print(f"\n[DEBUG] Lancio Apify per {self.PLATFORM} - URL: {url}")

                response = requests.post(sync_url, json=payload, headers=headers, timeout=120)

                if response.status_code not in (200, 201):
                    store.add_evidence(
                        source=self.SOURCE,
                        evidence_type=EvidenceType.ERROR,
                        value=f"Errore API Apify per {url}: {response.status_code} - {response.text}",
                        url=url,
                        platform=self.PLATFORM,
                        confidence=0.0,
                    )
                    continue

                results = response.json()
                print(f"[DEBUG] Oggetti trovati per {url}: {len(results)}\n")

                for item in results:
                    profile = item.get("profile") or {}

                    if not profile:
                        continue

                    profile_url = self._canonical_url(profile.get("url") or url)
                    found_username = store.extract_username(profile_url)

                    name = profile.get("name") or ""
                    intro = profile.get("intro") or ""

                    about = profile.get("about") or {}
                    work = about.get("work") or ""
                    college = about.get("college") or ""
                    school = about.get("secondary_school") or ""

                    city = profile.get("current_city") or ""
                    hometown = profile.get("hometown") or ""

                    combined_text = " ".join(
                        str(value)
                        for value in [name, intro, work, college, school, city, hometown]
                        if value
                    )

                    if not combined_text.strip():
                        continue

                    store.add_evidence(
                        source=self.SOURCE,
                        evidence_type=EvidenceType.PROFILE,
                        value=combined_text,
                        url=profile_url,
                        platform=self.PLATFORM,
                        username=found_username,
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
                            username=found_username,
                            confidence=0.90,
                            raw_data={"derived_from": "facebook_apify_profile"},
                        )

            except Exception as exc:
                store.add_evidence(
                    source=self.SOURCE,
                    evidence_type=EvidenceType.ERROR,
                    value=f"Errore durante lo scraping attivo di Facebook per {url}: {str(exc)}",
                    url=url,
                    platform=self.PLATFORM,
                    confidence=0.0,
                )

    def _extract_facebook_context(self, store: EvidenceStore) -> None:
        for candidate in store.candidates:
            if candidate.platform != self.PLATFORM:
                continue

            raw_data = candidate.raw_data or {}

            if "source_evidence" not in raw_data:
                continue

            source_ev = raw_data["source_evidence"]
            title = source_ev.get("title") or ""
            description = source_ev.get("description") or ""
            text = f"{title} {description}".lower()

            self._extract_locations(store, candidate, text, candidate.confidence)
            self._extract_roles(store, candidate, text, candidate.confidence)

        for evidence in store.evidence:
            if evidence.platform == self.PLATFORM and evidence.source == self.SOURCE:
                text = str(evidence.value)
                self.extract_common_context(
                    store,
                    evidence,
                    text,
                    evidence.confidence,
                    self.PLATFORM,
                    self.SOURCE,
                )

    def _extract_locations(self, store: EvidenceStore, evidence, text: str, confidence: float) -> None:
        for location in store.target.cities + [store.target.location]:
            if location and location.lower() in text:
                store.add_evidence(
                    source=self.SOURCE,
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
                source=self.SOURCE,
                evidence_type=EvidenceType.ROLE,
                value=store.target.role,
                url=evidence.url,
                platform=self.PLATFORM,
                username=evidence.username,
                confidence=confidence,
                raw_data={"derived_from": "facebook_snippet"},
            )

    def _public_search_url(self, full_name: str) -> str:
        slug = "-".join(part for part in full_name.strip().split() if part)
        return f"https://www.facebook.com/public/{slug}/"

    def _is_bad_facebook_url(self, url: str) -> bool:
        if not url:
            return True

        parsed = urlparse(url.lower())
        domain = parsed.netloc.lower()
        path_parts = [part for part in parsed.path.split("/") if part]

        if "facebook.com" not in domain:
            return True

        bad_parts = {
            "photo.php",
            "permalink.php",
            "story.php",
            "login",
            "groups",
            "pages",
            "watch",
            "events",
            "posts",
            "photos",
            "photo",
            "videos",
            "reel",
            "reels",
            "public",
        }

        if any(part in bad_parts for part in path_parts):
            return True

        if parsed.path in {"", "/"}:
            return True

        return False

    def _canonical_url(self, url: str | None) -> str:
        if not url:
            return ""

        parsed = urlparse(url.strip())

        scheme = parsed.scheme or "https"
        netloc = parsed.netloc.lower()

        if netloc in {"www.facebook.com", "web.facebook.com", "m.facebook.com", "mbasic.facebook.com"}:
            netloc = "facebook.com"
        elif netloc.startswith("www."):
            netloc = netloc[4:]

        path = parsed.path

        if path != "/" and path.endswith("/"):
            path = path[:-1]

        query = parsed.query if path.lower().endswith("profile.php") else ""

        return urlunparse((scheme, netloc, path, "", query, ""))

    def _clean_text(self, value: str) -> str:
        value = re.sub(r"<[^>]+>", " ", value)
        value = html.unescape(value)
        value = re.sub(r"\s+", " ", value)
        return value.strip()

    def _normalize_name(self, value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", value.lower())