import re

import requests

from target_information_collector.agents.base_agent import BaseAgent
from target_information_collector.evidence.evidence_normalizer import EvidenceNormalizer
from target_information_collector.evidence.evidence_store import EvidenceStore
from target_information_collector.shared.config import settings
from target_information_collector.shared.models import EvidenceSource, EvidenceType


class WebAgent(BaseAgent):
    BASE_URL = "https://api.apify.com/v2"

    BASE_QUERY_LIMIT = 14
    DORKING_QUERY_LIMIT = 3
    SOCIAL_QUERY_LIMIT = 12
    RESULTS_PER_QUERY = 8

    CONTEXT_STOPWORDS = {
        "di",
        "del",
        "della",
        "delle",
        "degli",
        "dei",
        "da",
        "dal",
        "dallo",
        "dalla",
        "of",
        "the",
        "and",
        "for",
        "at",
        "in",
        "studi",
        "studies",
        "universita",
        "università",
        "university",
        "college",
        "school",
        "istituto",
        "institute",
    }

    def __init__(self):
        self.normalizer = EvidenceNormalizer()

    def collect_base(self, store: EvidenceStore) -> None:
        self._run_and_store(
            store=store,
            queries=self._build_base_queries(store),
            phase="base_web_search",
        )

        self._run_and_store(
            store=store,
            queries=self._build_dorking_queries(store),
            phase="document_dorking",
        )

    def collect_social_contextual(self, store: EvidenceStore) -> None:
        self._run_and_store(
            store=store,
            queries=self._build_social_contextual_queries(store),
            phase="social_contextual_web_search",
        )

    def _run_and_store(self, store: EvidenceStore, queries: list[str], phase: str) -> None:
        if not settings.apify_token:
            store.add_evidence(
                source=EvidenceSource.APIFY,
                evidence_type=EvidenceType.ERROR,
                value="APIFY_TOKEN is missing",
                confidence=0.0,
                raw_data={"phase": phase},
            )
            return

        if not queries:
            return

        print(f"[DEBUG][web] Phase: {phase}")
        print(f"[DEBUG][web] Queries: {queries}")

        items = self._run_google_search_actor(queries)

        for item in items:
            if item.get("collector_error"):
                store.add_evidence(
                    source=EvidenceSource.APIFY,
                    evidence_type=EvidenceType.ERROR,
                    value=item.get("error", "Unknown Apify error"),
                    confidence=0.0,
                    raw_data=item,
                )
                continue

            query = item.get("searchQuery", {}).get("term", "batch")

            for result in self._extract_search_results(item):
                self._store_result(store=store, query=query, result=result, phase=phase)

    def _run_google_search_actor(self, queries: list[str]) -> list[dict]:
        actor_id = settings.apify_actor_id
        url = f"{self.BASE_URL}/acts/{actor_id}/run-sync-get-dataset-items"

        payload = {
            "queries": "\n".join(queries),
            "maxPagesPerQuery": 1,
            "resultsPerPage": self.RESULTS_PER_QUERY,
            "countryCode": "it",
            "languageCode": "it",
            "geminiSearch": {"enableGemini": False},
            "perplexitySearch": {"enablePerplexity": False},
            "chatGptSearch": {"enableChatGpt": False},
            "copilotSearch": {"enableCopilot": False},
        }

        try:
            response = requests.post(
                url,
                params={"token": settings.apify_token, "format": "json", "clean": "true"},
                json=payload,
                timeout=180,
            )

            if response.status_code >= 400:
                return [{
                    "collector_error": True,
                    "error": f"HTTP {response.status_code}: {response.text[:1000]}",
                    "actor_id": actor_id,
                    "payload": payload,
                }]

            data = response.json()

            if not isinstance(data, list):
                return [{
                    "collector_error": True,
                    "error": "Apify response is not a list",
                    "actor_id": actor_id,
                    "payload": payload,
                    "response": data,
                }]

            return data

        except requests.exceptions.RequestException as exc:
            return [{
                "collector_error": True,
                "error": str(exc),
                "actor_id": actor_id,
                "payload": payload,
            }]

        except ValueError as exc:
            return [{
                "collector_error": True,
                "error": f"Invalid JSON response: {str(exc)}",
                "actor_id": actor_id,
                "payload": payload,
            }]

    def _extract_search_results(self, item: dict) -> list[dict]:
        results = []

        for result in item.get("organicResults", []):
            results.append({
                "title": result.get("title"),
                "description": result.get("description") or result.get("snippet"),
                "url": result.get("url") or result.get("link"),
            })

        direct_url = item.get("url") or item.get("link")

        if direct_url:
            results.append({
                "title": item.get("title"),
                "description": item.get("description") or item.get("snippet"),
                "url": direct_url,
            })

        return results

    def _store_result(self, store: EvidenceStore, query: str, result: dict, phase: str) -> None:
        title = result.get("title") or ""
        description = result.get("description") or ""
        raw_url = result.get("url")

        url = self.normalizer.normalize_url(raw_url)

        if not url or self.normalizer.is_blocked_url(url):
            return

        platform = self.normalizer.detect_platform(url)
        username = self.normalizer.extract_username(url)
        result_class = self.normalizer.classify_url(url)

        text = f"{title} {description} {url}"
        confidence = self._score_result(store, text, result_class, phase)

        if confidence < 0.35:
            return

        store.add_evidence(
            source=EvidenceSource.WEB,
            evidence_type=self._map_result_class_to_evidence_type(result_class),
            value=title or url,
            url=url,
            platform=platform,
            username=username,
            title=title,
            description=description,
            confidence=confidence,
            raw_data={
                "query": query,
                "phase": phase,
                "result_class": result_class,
                "platform": platform,
                "username": username,
                "title": title,
                "description": description,
                "url": url,
            },
        )

        self._extract_extra_evidence(
            store=store,
            query=query,
            phase=phase,
            text=text,
            url=url,
            platform=platform,
            username=username,
            title=title,
            description=description,
            confidence=confidence,
        )

        if result_class in {"professional_profile", "technical_profile", "social_profile_candidate"}:
            store.add_candidate(
                platform=platform or "web",
                url=url,
                username=username,
                display_name=title,
                confidence=confidence,
                matched_context=self.matched_context(store, text),
                raw_data={
                    "query": query,
                    "phase": phase,
                    "result_class": result_class,
                    "title": title,
                    "description": description,
                },
            )

    def _extract_extra_evidence(
        self,
        store: EvidenceStore,
        query: str,
        phase: str,
        text: str,
        url: str,
        platform: str | None,
        username: str | None,
        title: str,
        description: str,
        confidence: float,
    ) -> None:
        self._add_email_evidence(store, query, phase, text, url, platform, username, title, description, confidence)
        self._add_target_context_evidence(store, query, phase, text, url, platform, username, confidence)
        self._add_tech_stack_evidence(store, query, phase, text, url, platform, username, confidence)

    def _add_email_evidence(
        self,
        store: EvidenceStore,
        query: str,
        phase: str,
        text: str,
        url: str,
        platform: str | None,
        username: str | None,
        title: str,
        description: str,
        confidence: float,
    ) -> None:
        for email in self.normalizer.extract_emails(text):
            store.add_evidence(
                source=EvidenceSource.WEB,
                evidence_type=EvidenceType.EMAIL,
                value=email,
                url=url,
                platform=platform,
                username=username,
                title=title,
                description=description,
                confidence=min(confidence + 0.10, 1.0),
                raw_data={"query": query, "phase": phase, "source_url": url},
            )

    def _add_target_context_evidence(
        self,
        store: EvidenceStore,
        query: str,
        phase: str,
        text: str,
        url: str,
        platform: str | None,
        username: str | None,
        confidence: float,
    ) -> None:
        for location in self._matched_target_locations(store, text):
            store.add_evidence(
                source=EvidenceSource.WEB,
                evidence_type=EvidenceType.LOCATION,
                value=location,
                url=url,
                platform=platform,
                username=username,
                confidence=min(confidence + 0.05, 1.0),
                raw_data={"query": query, "phase": phase, "source_url": url},
            )

        for organization in self._matched_target_organizations(store, text):
            store.add_evidence(
                source=EvidenceSource.WEB,
                evidence_type=EvidenceType.ORGANIZATION,
                value=organization,
                url=url,
                platform=platform,
                username=username,
                confidence=min(confidence + 0.05, 1.0),
                raw_data={"query": query, "phase": phase, "source_url": url},
            )

        for education in self._matched_target_education(store, text):
            store.add_evidence(
                source=EvidenceSource.WEB,
                evidence_type=EvidenceType.EDUCATION,
                value=education,
                url=url,
                platform=platform,
                username=username,
                confidence=min(confidence + 0.05, 1.0),
                raw_data={"query": query, "phase": phase, "source_url": url},
            )

    def _add_tech_stack_evidence(
        self,
        store: EvidenceStore,
        query: str,
        phase: str,
        text: str,
        url: str,
        platform: str | None,
        username: str | None,
        confidence: float,
    ) -> None:
        if platform not in {"github", "linkedin"}:
            return

        for tech_term in self.normalizer.extract_tech_stack_terms(text):
            store.add_evidence(
                source=EvidenceSource.WEB,
                evidence_type=EvidenceType.TECH_STACK,
                value=tech_term,
                url=url,
                platform=platform,
                username=username,
                confidence=min(confidence + 0.05, 1.0),
                raw_data={"query": query, "phase": phase, "source_url": url},
            )

    def _matched_target_locations(self, store: EvidenceStore, text: str) -> list[str]:
        values = []

        if store.target.location:
            values.append(store.target.location)

        values.extend(store.target.cities)
        values.extend(self._context_keywords(store))

        return self._matched_values(text, values)

    def _matched_target_organizations(self, store: EvidenceStore, text: str) -> list[str]:
        values = []

        if store.target.company:
            values.append(store.target.company)

        if store.target.department:
            values.append(store.target.department)

        values.extend(store.target.aliases)

        return self._matched_values(text, values)

    def _matched_target_education(self, store: EvidenceStore, text: str) -> list[str]:
        return self._matched_values(text, store.target.education)

    def _matched_values(self, text: str, values: list[str]) -> list[str]:
        lower = text.lower()
        matches = []

        for value in values:
            if value and value.lower() in lower:
                matches.append(value)

        return self.normalizer.unique(matches)

    def _build_base_queries(self, store: EvidenceStore) -> list[str]:
        name = store.target.full_name.strip()
        context_terms = [term for term in store.get_context_terms() if term and term != name]
        keywords = self._context_keywords(store)

        queries = [
            f'"{name}"',
        ]

        if store.target.company:
            queries.append(f'"{name}" "{store.target.company}"')

        for keyword in keywords:
            queries.append(f'"{name}" "{keyword}"')

        queries.extend([
            f'"{name}" LinkedIn',
            f'"{name}" GitHub',
            f'"{name}" site:linkedin.com/in',
            f'"{name}" site:github.com',
            f'"{name}" email',
        ])

        for term in context_terms:
            queries.append(f'"{name}" "{term}"')
            queries.append(f'"{name}" "{term}" LinkedIn')
            queries.append(f'"{name}" "{term}" site:linkedin.com/in')

        return store.unique(queries)[:self.BASE_QUERY_LIMIT]

    def _build_dorking_queries(self, store: EvidenceStore) -> list[str]:
        name = store.target.full_name.strip()
        queries = []

        for term in store.get_strong_context_terms():
            queries.append(f'"{name}" "{term}" filetype:pdf')

        if store.target.email_domain:
            queries.append(f'"{name}" site:{store.target.email_domain}')
            queries.append(f'"{name}" "{store.target.email_domain}" filetype:pdf')

        return store.unique(queries)[:self.DORKING_QUERY_LIMIT]

    def _build_social_contextual_queries(self, store: EvidenceStore) -> list[str]:
        name = store.target.full_name.strip()

        queries = [
            f'"{name}" Facebook',
            f'"{name}" Instagram',
            f'"{name}" site:facebook.com',
            f'"{name}" site:instagram.com',
        ]

        for term in store.get_strong_context_terms():
            queries.append(f'"{name}" "{term}" Facebook')
            queries.append(f'"{name}" "{term}" site:facebook.com')
            queries.append(f'"{name}" "{term}" Instagram')
            queries.append(f'"{name}" "{term}" site:instagram.com')

        for keyword in self._context_keywords(store):
            queries.append(f'"{name}" "{keyword}" Facebook')
            queries.append(f'"{name}" "{keyword}" Instagram')

        for candidate in store.candidates:
            if candidate.username:
                queries.append(f'"{candidate.username}" site:facebook.com')
                queries.append(f'"{candidate.username}" site:instagram.com')

        return store.unique(queries)[:self.SOCIAL_QUERY_LIMIT]

    def _score_result(self, store: EvidenceStore, text: str, result_class: str, phase: str) -> float:
        lower = text.lower()
        score = 0.0
        name = store.target.full_name.lower()

        if name in lower:
            score += 0.40

        for term in store.get_strong_context_terms():
            if term.lower() in lower:
                score += 0.12

        for keyword in self._context_keywords(store):
            if keyword.lower() in lower:
                score += 0.08

        if store.target.email_domain and store.target.email_domain.lower() in lower:
            score += 0.10

        score += {
            "professional_profile": 0.15,
            "technical_profile": 0.15,
            "social_profile_candidate": 0.08,
            "institutional_reference": 0.12,
            "social_contextual_mention": -0.10,
            "web_mention": 0.00,
        }.get(result_class, 0.0)

        if phase == "document_dorking":
            score += 0.05

        return round(max(0.0, min(score, 1.0)), 3)

    def _map_result_class_to_evidence_type(self, result_class: str) -> EvidenceType:
        if result_class in {"professional_profile", "technical_profile"}:
            return EvidenceType.PROFILE

        if result_class == "social_profile_candidate":
            return EvidenceType.PUBLIC_LINK

        if result_class == "social_contextual_mention":
            return EvidenceType.SOCIAL_HINT

        return EvidenceType.WEB_MENTION

    def _context_keywords(self, store: EvidenceStore) -> list[str]:
        values = []

        raw_sources = [
            store.target.company,
            store.target.department,
            store.target.location,
            *store.target.cities,
            *store.target.education,
            *store.target.aliases,
        ]

        for source in raw_sources:
            values.extend(self._significant_tokens(source))

        return self.normalizer.unique(values)

    def _significant_tokens(self, value: str | None) -> list[str]:
        if not value:
            return []

        normalized = self._normalize_text(value)
        tokens = re.split(r"[^a-z0-9]+", normalized)

        output = []

        for token in tokens:
            if len(token) <= 2:
                continue

            if token in self.CONTEXT_STOPWORDS:
                continue

            output.append(token)

        return output

    def _normalize_text(self, value: str) -> str:
        normalized = str(value).lower()
        replacements = {
            "à": "a",
            "è": "e",
            "é": "e",
            "ì": "i",
            "ò": "o",
            "ù": "u",
        }

        for old, new in replacements.items():
            normalized = normalized.replace(old, new)

        return normalized