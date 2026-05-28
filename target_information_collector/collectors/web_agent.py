import requests
from target_information_collector.collectors.base_agent import BaseAgent
from target_information_collector.evidence.evidence_normalizer import EvidenceNormalizer
from target_information_collector.evidence.evidence_store import EvidenceStore
from target_information_collector.shared.config import settings
from target_information_collector.shared.models import EvidenceSource, EvidenceType

class WebAgent(BaseAgent):
    BASE_URL = "https://api.apify.com/v2"

    def __init__(self):
        self.normalizer = EvidenceNormalizer()

    def collect_base(self, store: EvidenceStore) -> None:
        queries = self._build_base_queries(store)
        self._run_and_store(store=store, queries=queries, phase="base_web_search")

    def collect_social_contextual(self, store: EvidenceStore) -> None:
        queries = self._build_social_contextual_queries(store)
        self._run_and_store(store=store, queries=queries, phase="social_contextual_web_search")

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
            store.add_evidence(
                source=EvidenceSource.WEB,
                evidence_type=EvidenceType.ERROR,
                value="No web search queries generated",
                confidence=0.0,
                raw_data={"phase": phase},
            )
            return

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
            "resultsPerPage": 10,
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
                timeout=60,
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
            return [{"collector_error": True, "error": str(exc), "actor_id": actor_id, "payload": payload}]
        except ValueError as exc:
            return [{"collector_error": True, "error": f"Invalid JSON response: {str(exc)}", "actor_id": actor_id, "payload": payload}]

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
        confidence = self._score_result(store, text, result_class)

        if confidence < 0.35:
            return

        evidence_type = self._map_result_class_to_evidence_type(result_class)

        store.add_evidence(
            source=EvidenceSource.WEB,
            evidence_type=evidence_type,
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

        for location in self.normalizer.extract_known_locations(text):
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

        for education in self.normalizer.extract_known_education(text):
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

    def _build_base_queries(self, store: EvidenceStore) -> list[str]:
        name = store.target.full_name.strip()
        queries = [
            f'"{name}"', f'"{name}" LinkedIn', f'"{name}" GitHub',
            f'"{name}" site:linkedin.com/in', f'"{name}" site:github.com', f'"{name}" email'
        ]
        for term in store.get_context_terms():
            if term == name:
                continue
            queries.append(f'"{name}" "{term}"')
            queries.append(f'"{name}" "{term}" LinkedIn')
            queries.append(f'"{name}" "{term}" site:linkedin.com/in')
        return store.unique(queries)[:25]

    def _build_social_contextual_queries(self, store: EvidenceStore) -> list[str]:
        name = store.target.full_name.strip()
        queries = [
            f'"{name}" Facebook', f'"{name}" Instagram',
            f'"{name}" site:facebook.com', f'"{name}" site:instagram.com'
        ]

        high_value_terms = store.get_strong_context_terms()
        for term in high_value_terms:
            queries.append(f'"{name}" "{term}" Facebook')
            queries.append(f'"{name}" "{term}" site:facebook.com')
            queries.append(f'"{name}" "{term}" Instagram')
            queries.append(f'"{name}" "{term}" site:instagram.com')

        return store.unique(queries)[:40]

    def _score_result(self, store: EvidenceStore, text: str, result_class: str) -> float:
        lower = text.lower()
        score = 0.0
        name = store.target.full_name.lower()

        if name in lower:
            score += 0.40

        for term in store.get_strong_context_terms():
            if term.lower() in lower:
                score += 0.12

        if store.target.email_domain and store.target.email_domain.lower() in lower:
            score += 0.10

        bonus = {
            "professional_profile": 0.15,
            "technical_profile": 0.15,
            "social_profile_candidate": 0.08,
            "institutional_reference": 0.08,
            "social_contextual_mention": -0.10,
            "web_mention": 0.00,
        }
        score += bonus.get(result_class, 0.0)
        return round(max(0.0, min(score, 1.0)), 3)

    def _map_result_class_to_evidence_type(self, result_class: str) -> EvidenceType:
        if result_class in {"professional_profile", "technical_profile"}:
            return EvidenceType.PROFILE
        if result_class == "social_profile_candidate":
            return EvidenceType.PUBLIC_LINK
        if result_class == "institutional_reference":
            return EvidenceType.WEB_MENTION
        if result_class == "social_contextual_mention":
            return EvidenceType.SOCIAL_HINT
        return EvidenceType.WEB_MENTION
