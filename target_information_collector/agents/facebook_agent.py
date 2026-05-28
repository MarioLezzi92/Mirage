import html
import re
import time
from urllib.parse import urljoin

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
    MIN_WEB_CANDIDATE_CONFIDENCE = 0.40

    def __init__(self):
        self.normalizer = EvidenceNormalizer()

    def collect(self, store: EvidenceStore) -> None:
        self.promote_seeded_links(store, self.PLATFORM)
        self._promote_web_candidates(store)
        self._discover_public_profiles(store)

        if settings.apify_token and settings.apify_facebook_profile_actor_id:
            self._collect_via_apify(store)

        self._extract_facebook_context(store)

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

            if not evidence.url or self._is_bad_facebook_url(evidence.url):
                continue

            title = evidence.title or evidence.value or ""
            description = evidence.description or ""
            text = f"{title} {description} {evidence.url}"

            confidence = self.calculate_base_score(
                store=store,
                text=text,
                username=evidence.username,
                seeded=False,
                strong_match_weight=0.08,
            )

            if confidence < self.MIN_WEB_CANDIDATE_CONFIDENCE:
                continue

            store.add_candidate(
                platform=self.PLATFORM,
                url=evidence.url,
                username=evidence.username or self.normalizer.extract_username(evidence.url),
                display_name=title,
                confidence=confidence,
                matched_context=self.matched_context(store, text),
                raw_data={
                    "source": "facebook_web_candidate",
                    "source_evidence": evidence.model_dump(mode="json"),
                },
            )

    def _discover_public_profiles(self, store: EvidenceStore) -> None:
        public_url = self._public_search_url(store.target.full_name)

        try:
            page_html = self._load_public_search(public_url)

            if not page_html:
                self._add_error(
                    store=store,
                    message="Facebook public search non ha restituito HTML utile.",
                    url=public_url,
                    raw_data={"phase": "facebook_public_search"},
                )
                return

            profiles = self._extract_public_profiles(
                page_html=page_html,
                full_name=store.target.full_name,
            )

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

    def _load_public_search(self, public_url: str) -> str:
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
        expected_name = self._normalize_name(full_name)
        profiles = []
        seen = set()

        for href, label in self._extract_links(page_html):
            name = self._clean_text(label)

            if self._normalize_name(name) != expected_name:
                continue

            url = self.normalizer.normalize_url(urljoin("https://www.facebook.com", href))

            if not url or self._is_bad_facebook_url(url):
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

    def _store_public_profile_candidate(
        self,
        store: EvidenceStore,
        profile: dict,
        public_url: str,
    ) -> None:
        url = self.normalizer.normalize_url(profile["url"])

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

                response = requests.post(
                    sync_url,
                    json=payload,
                    headers=headers,
                    timeout=120,
                )

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
                        raw_data={
                            "payload": payload,
                            "response": results,
                        },
                    )
                    continue

                print(f"[DEBUG][facebook] Oggetti Apify per {url}: {len(results)}")

                if not results:
                    self._add_error(
                        store=store,
                        message=f"Apify non ha restituito oggetti per {url}",
                        url=url,
                        raw_data={"payload": payload},
                    )
                    continue

                for item in results:
                    self._store_apify_profile(store, item, fallback_url=url)

            except Exception as exc:
                self._add_error(
                    store=store,
                    message=f"Errore durante lo scraping attivo di Facebook per {url}: {str(exc)}",
                    url=url,
                    raw_data={"payload": payload},
                )

    def _urls_to_scrape(self, store: EvidenceStore) -> list[str]:
        urls = []

        for candidate in store.candidates:
            if candidate.platform != self.PLATFORM or not candidate.url:
                continue

            url = self.normalizer.normalize_url(candidate.url)

            if not url or self._is_bad_facebook_url(url):
                continue

            raw_data = candidate.raw_data or {}
            is_seeded = raw_data.get("seeded_from_input") is True
            is_public_search = raw_data.get("source") == "facebook_public_search"

            if not (is_seeded or is_public_search):
                continue

            urls.append(url)

        return list(dict.fromkeys(urls))

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

        profile_url = self.normalizer.normalize_url(profile.get("url") or fallback_url)

        if not profile_url:
            return

        username = self.normalizer.extract_username(profile_url)
        name = profile.get("name") or ""
        intro = profile.get("intro") or ""

        about = profile.get("about") or {}
        work = about.get("work") or ""
        college = about.get("college") or ""
        school = about.get("secondary_school") or ""

        city = profile.get("current_city") or ""
        hometown = profile.get("hometown") or ""

        about_public_text = self._about_public_text(profile)

        combined_text = self._join_text(
            name,
            intro,
            work,
            college,
            school,
            city,
            hometown,
            about_public_text,
        )

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

            if evidence.url and not self.normalizer.is_social_profile_url(evidence.url):
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

    def _about_public_text(self, profile: dict) -> str:
        about_public = profile.get("about_public") or []
        values = []

        for item in about_public:
            text = item.get("text")

            if text:
                values.append(text)

        return self._join_text(*values)

    def _public_search_url(self, full_name: str) -> str:
        slug = "-".join(part for part in full_name.strip().split() if part)
        return f"https://www.facebook.com/public/{slug}/"

    def _is_bad_facebook_url(self, url: str) -> bool:
        if not url:
            return True

        if not self.normalizer.is_social_profile_url(url):
            return True

        return "facebook.com" not in url.lower()

    def _clean_text(self, value: str) -> str:
        value = re.sub(r"<[^>]+>", " ", value)
        value = html.unescape(value)
        value = re.sub(r"\s+", " ", value)
        return value.strip()

    def _normalize_name(self, value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", value.lower())

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