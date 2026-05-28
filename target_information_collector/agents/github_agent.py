import requests

from target_information_collector.agents.base_agent import BaseAgent
from target_information_collector.evidence.evidence_normalizer import EvidenceNormalizer
from target_information_collector.evidence.evidence_store import EvidenceStore
from target_information_collector.shared.config import settings
from target_information_collector.shared.models import EvidenceSource, EvidenceType


class GitHubAgent(BaseAgent):
    BASE_URL = "https://api.github.com"
    PLATFORM = "github"
    SOURCE = EvidenceSource.GITHUB

    SEARCH_LIMIT = 10
    REPO_LIMIT = 30
    MIN_PROFILE_CONFIDENCE = 0.45

    def __init__(self):
        self.normalizer = EvidenceNormalizer()

    def collect(self, store: EvidenceStore) -> None:
        if store.target.github_username:
            self._collect_by_username(store, store.target.github_username)
            return

        self._search_by_identity(store)

    def _headers(self) -> dict:
        headers = {"Accept": "application/vnd.github.v3+json"}

        if settings.github_token:
            headers["Authorization"] = f"token {settings.github_token}"

        return headers

    def _search_by_identity(self, store: EvidenceStore) -> None:
        query = f'"{store.target.full_name}" in:name'

        try:
            response = requests.get(
                f"{self.BASE_URL}/search/users",
                params={"q": query, "per_page": self.SEARCH_LIMIT},
                headers=self._headers(),
                timeout=30,
            )

            if response.status_code >= 400:
                self._add_error(
                    store=store,
                    message=f"GitHub search failed: HTTP {response.status_code}",
                    raw_data={"query": query, "body": response.text[:1000]},
                )
                return

            for item in response.json().get("items", []):
                username = item.get("login")

                if username:
                    self._collect_by_username(store, username)

        except requests.RequestException as exc:
            self._add_error(
                store=store,
                message=str(exc),
                raw_data={"phase": "github_search", "query": query},
            )

    def _collect_by_username(self, store: EvidenceStore, username: str) -> None:
        user_data = self._fetch_user(store, username)

        if not user_data:
            return

        profile_url = user_data.get("html_url")
        login = user_data.get("login")
        display_name = user_data.get("name")
        bio = user_data.get("bio")
        company = user_data.get("company")
        location = user_data.get("location")
        email = user_data.get("email")

        profile_text = self._join_text(
            login,
            display_name,
            bio,
            company,
            location,
            email,
            profile_url,
        )

        confidence = self.calculate_base_score(
            store=store,
            text=profile_text,
            username=login,
            seeded=bool(store.target.github_username),
            strong_match_weight=0.15,
        )

        if confidence < self.MIN_PROFILE_CONFIDENCE:
            return

        if profile_url:
            self._add_profile_evidence(
                store=store,
                user_data=user_data,
                profile_url=profile_url,
                login=login,
                display_name=display_name,
                bio=bio,
                confidence=confidence,
            )

            store.add_candidate(
                platform=self.PLATFORM,
                url=profile_url,
                username=login,
                display_name=display_name,
                confidence=confidence,
                matched_context=self.matched_context(store, profile_text),
                raw_data=user_data,
            )

        self._add_basic_evidence(
            store=store,
            profile_url=profile_url,
            login=login,
            display_name=display_name,
            bio=bio,
            company=company,
            location=location,
            email=email,
            confidence=confidence,
        )

        self._collect_repositories(
            store=store,
            username=username,
            profile_url=profile_url,
            profile_confidence=confidence,
        )

    def _fetch_user(self, store: EvidenceStore, username: str) -> dict | None:
        try:
            response = requests.get(
                f"{self.BASE_URL}/users/{username}",
                headers=self._headers(),
                timeout=30,
            )

            if response.status_code >= 400:
                self._add_error(
                    store=store,
                    message=f"GitHub user fetch failed: HTTP {response.status_code}",
                    raw_data={"username": username, "body": response.text[:1000]},
                )
                return None

            return response.json()

        except requests.RequestException as exc:
            self._add_error(
                store=store,
                message=str(exc),
                raw_data={"phase": "github_user_fetch", "username": username},
            )
            return None

    def _collect_repositories(
        self,
        store: EvidenceStore,
        username: str,
        profile_url: str | None,
        profile_confidence: float,
    ) -> None:
        try:
            response = requests.get(
                f"{self.BASE_URL}/users/{username}/repos",
                params={"per_page": self.REPO_LIMIT, "sort": "updated"},
                headers=self._headers(),
                timeout=30,
            )

            if response.status_code >= 400:
                self._add_error(
                    store=store,
                    message=f"GitHub repos fetch failed: HTTP {response.status_code}",
                    raw_data={"username": username, "body": response.text[:1000]},
                )
                return

            for repo in response.json():
                self._store_repo_evidence(
                    store=store,
                    username=username,
                    profile_url=profile_url,
                    profile_confidence=profile_confidence,
                    repo=repo,
                )

        except requests.RequestException as exc:
            self._add_error(
                store=store,
                message=str(exc),
                raw_data={"phase": "github_repos_fetch", "username": username},
            )

    def _add_profile_evidence(
        self,
        store: EvidenceStore,
        user_data: dict,
        profile_url: str,
        login: str | None,
        display_name: str | None,
        bio: str | None,
        confidence: float,
    ) -> None:
        store.add_evidence(
            source=self.SOURCE,
            evidence_type=EvidenceType.PROFILE,
            value=login or profile_url,
            url=profile_url,
            platform=self.PLATFORM,
            username=login,
            title=display_name,
            description=bio,
            confidence=confidence,
            raw_data=user_data,
        )

    def _add_basic_evidence(
        self,
        store: EvidenceStore,
        profile_url: str | None,
        login: str | None,
        display_name: str | None,
        bio: str | None,
        company: str | None,
        location: str | None,
        email: str | None,
        confidence: float,
    ) -> None:
        fields = [
            (EvidenceType.IDENTITY, display_name, "name"),
            (EvidenceType.ROLE, bio, "bio"),
            (EvidenceType.ORGANIZATION, company, "company"),
            (EvidenceType.LOCATION, location, "location"),
            (EvidenceType.EMAIL, email, "email"),
        ]

        for evidence_type, value, field_name in fields:
            if not value:
                continue

            store.add_evidence(
                source=self.SOURCE,
                evidence_type=evidence_type,
                value=value,
                url=profile_url,
                platform=self.PLATFORM,
                username=login,
                confidence=confidence,
                raw_data={"field": field_name},
            )

    def _store_repo_evidence(
        self,
        store: EvidenceStore,
        username: str,
        profile_url: str | None,
        profile_confidence: float,
        repo: dict,
    ) -> None:
        repo_url = repo.get("html_url") or profile_url
        repo_name = repo.get("name")
        description = repo.get("description")
        language = repo.get("language")
        topics = repo.get("topics") or []

        if language:
            self._add_tech_stack(
                store=store,
                username=username,
                url=repo_url,
                title=repo_name,
                description=description,
                value=language,
                confidence=profile_confidence,
                raw_data={"field": "language", "repo": repo},
            )

        for topic in topics:
            self._add_tech_stack(
                store=store,
                username=username,
                url=repo_url,
                title=repo_name,
                description=description,
                value=topic,
                confidence=profile_confidence,
                raw_data={"field": "topic", "repo": repo},
            )

        for term in self.normalizer.extract_tech_stack_terms(description or ""):
            self._add_tech_stack(
                store=store,
                username=username,
                url=repo_url,
                title=repo_name,
                description=description,
                value=term,
                confidence=profile_confidence,
                raw_data={"field": "description", "repo": repo},
            )

    def _add_tech_stack(
        self,
        store: EvidenceStore,
        username: str,
        url: str | None,
        title: str | None,
        description: str | None,
        value: str,
        confidence: float,
        raw_data: dict,
    ) -> None:
        store.add_evidence(
            source=self.SOURCE,
            evidence_type=EvidenceType.TECH_STACK,
            value=value,
            url=url,
            platform=self.PLATFORM,
            username=username,
            title=title,
            description=description,
            confidence=confidence,
            raw_data=raw_data,
        )

    def _add_error(self, store: EvidenceStore, message: str, raw_data: dict) -> None:
        store.add_evidence(
            source=self.SOURCE,
            evidence_type=EvidenceType.ERROR,
            value=message,
            confidence=0.0,
            raw_data=raw_data,
        )

    def _join_text(self, *values) -> str:
        return " ".join(str(value) for value in values if value)