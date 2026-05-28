import requests

from target_information_collector.evidence.evidence_store import EvidenceStore
from target_information_collector.shared.models import EvidenceSource, EvidenceType
from target_information_collector.shared.config import settings

class GitHubAgent:
    BASE_URL = "https://api.github.com"

    def collect(self, store: EvidenceStore) -> None:
        username = store.target.github_username

        if username:
            self._collect_by_username(store, username)
            return

        self._search_by_identity(store)

    def _get_headers(self):
        headers = {"Accept": "application/vnd.github.v3+json"}
        if settings.github_token:
            headers["Authorization"] = f"token {settings.github_token}"
        return headers

    def _search_by_identity(self, store: EvidenceStore) -> None:
        # Ricerca larga: GitHub è molto rigido, quindi cerchiamo solo il nome
        # Il nostro algoritmo di scoring filtrerà poi il candidato giusto localmente.
        query = f'"{store.target.full_name}" in:name'

        try:
            response = requests.get(
                f"{self.BASE_URL}/search/users",
                params={"q": query, "per_page": 10},
                headers=self._get_headers(),
                timeout=30,
            )

            if response.status_code >= 400:
                store.add_evidence(
                    source=EvidenceSource.GITHUB,
                    evidence_type=EvidenceType.ERROR,
                    value=f"GitHub search failed: HTTP {response.status_code}",
                    confidence=0.0,
                    raw_data={
                        "query": query,
                        "body": response.text[:1000],
                    },
                )
                return

            data = response.json()

            for item in data.get("items", []):
                username = item.get("login")

                if username:
                    self._collect_by_username(store, username)

        except requests.RequestException as exc:
            store.add_evidence(
                source=EvidenceSource.GITHUB,
                evidence_type=EvidenceType.ERROR,
                value=str(exc),
                confidence=0.0,
                raw_data={"phase": "github_search"},
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

        profile_text = " ".join(
            str(value)
            for value in [
                login,
                display_name,
                bio,
                company,
                location,
                email,
                profile_url,
            ]
            if value
        )

        confidence = self._score_github_profile(store, profile_text, login)

        # FIX: Scartiamo i risultati di ricerca che non matchano il nostro target
        if confidence < 0.45:
            return

        if profile_url:
            store.add_evidence(
                source=EvidenceSource.GITHUB,
                evidence_type=EvidenceType.PROFILE,
                value=login or profile_url,
                url=profile_url,
                platform="github",
                username=login,
                title=display_name,
                description=bio,
                confidence=confidence,
                raw_data=user_data,
            )

            store.add_candidate(
                platform="github",
                url=profile_url,
                username=login,
                display_name=display_name,
                confidence=confidence,
                matched_context=self._matched_context(store, profile_text),
                raw_data=user_data,
            )

        if display_name:
            store.add_evidence(
                source=EvidenceSource.GITHUB,
                evidence_type=EvidenceType.IDENTITY,
                value=display_name,
                url=profile_url,
                platform="github",
                username=login,
                confidence=confidence,
                raw_data={"field": "name"},
            )

        if bio:
            store.add_evidence(
                source=EvidenceSource.GITHUB,
                evidence_type=EvidenceType.ROLE,
                value=bio,
                url=profile_url,
                platform="github",
                username=login,
                confidence=confidence,
                raw_data={"field": "bio"},
            )

        if company:
            store.add_evidence(
                source=EvidenceSource.GITHUB,
                evidence_type=EvidenceType.ORGANIZATION,
                value=company,
                url=profile_url,
                platform="github",
                username=login,
                confidence=confidence,
                raw_data={"field": "company"},
            )

        if location:
            store.add_evidence(
                source=EvidenceSource.GITHUB,
                evidence_type=EvidenceType.LOCATION,
                value=location,
                url=profile_url,
                platform="github",
                username=login,
                confidence=confidence,
                raw_data={"field": "location"},
            )

        if email:
            store.add_evidence(
                source=EvidenceSource.GITHUB,
                evidence_type=EvidenceType.EMAIL,
                value=email,
                url=profile_url,
                platform="github",
                username=login,
                confidence=confidence,
                raw_data={"field": "email"},
            )

        self._collect_repositories(store, username, profile_url, confidence)

    def _fetch_user(self, store: EvidenceStore, username: str) -> dict | None:
        try:
            response = requests.get(
                f"{self.BASE_URL}/users/{username}",
                headers=self._get_headers(),
                timeout=30,
            )

            if response.status_code >= 400:
                store.add_evidence(
                    source=EvidenceSource.GITHUB,
                    evidence_type=EvidenceType.ERROR,
                    value=f"GitHub user fetch failed: HTTP {response.status_code}",
                    confidence=0.0,
                    raw_data={
                        "username": username,
                        "body": response.text[:1000],
                    },
                )
                return None

            return response.json()

        except requests.RequestException as exc:
            store.add_evidence(
                source=EvidenceSource.GITHUB,
                evidence_type=EvidenceType.ERROR,
                value=str(exc),
                confidence=0.0,
                raw_data={
                    "phase": "github_user_fetch",
                    "username": username,
                },
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
                params={
                    "per_page": 30,
                    "sort": "updated",
                },
                headers=self._get_headers(),
                timeout=30,
            )

            if response.status_code >= 400:
                store.add_evidence(
                    source=EvidenceSource.GITHUB,
                    evidence_type=EvidenceType.ERROR,
                    value=f"GitHub repos fetch failed: HTTP {response.status_code}",
                    confidence=0.0,
                    raw_data={
                        "username": username,
                        "body": response.text[:1000],
                    },
                )
                return

            repos = response.json()

            for repo in repos:
                self._store_repo_evidence(
                    store=store,
                    username=username,
                    profile_url=profile_url,
                    profile_confidence=profile_confidence,
                    repo=repo,
                )

        except requests.RequestException as exc:
            store.add_evidence(
                source=EvidenceSource.GITHUB,
                evidence_type=EvidenceType.ERROR,
                value=str(exc),
                confidence=0.0,
                raw_data={
                    "phase": "github_repos_fetch",
                    "username": username,
                },
            )

    def _store_repo_evidence(
        self,
        store: EvidenceStore,
        username: str,
        profile_url: str | None,
        profile_confidence: float,
        repo: dict,
    ) -> None:
        repo_url = repo.get("html_url")
        repo_name = repo.get("name")
        description = repo.get("description")
        language = repo.get("language")
        topics = repo.get("topics") or []

        if language:
            store.add_evidence(
                source=EvidenceSource.GITHUB,
                evidence_type=EvidenceType.TECH_STACK,
                value=language,
                url=repo_url or profile_url,
                platform="github",
                username=username,
                title=repo_name,
                description=description,
                confidence=profile_confidence,
                raw_data={
                    "field": "language",
                    "repo": repo,
                },
            )

        for topic in topics:
            store.add_evidence(
                source=EvidenceSource.GITHUB,
                evidence_type=EvidenceType.TECH_STACK,
                value=topic,
                url=repo_url or profile_url,
                platform="github",
                username=username,
                title=repo_name,
                description=description,
                confidence=profile_confidence,
                raw_data={
                    "field": "topic",
                    "repo": repo,
                },
            )

        if description:
            self._extract_context_from_repo_description(
                store=store,
                username=username,
                repo_url=repo_url,
                repo_name=repo_name,
                description=description,
                confidence=profile_confidence,
                repo=repo,
            )

    def _extract_context_from_repo_description(
        self,
        store: EvidenceStore,
        username: str,
        repo_url: str | None,
        repo_name: str | None,
        description: str,
        confidence: float,
        repo: dict,
    ) -> None:
        lower = description.lower()

        if "cybersecurity" in lower or "cyber security" in lower:
            store.add_evidence(
                source=EvidenceSource.GITHUB,
                evidence_type=EvidenceType.TECH_STACK,
                value="Cybersecurity",
                url=repo_url,
                platform="github",
                username=username,
                title=repo_name,
                description=description,
                confidence=confidence,
                raw_data={"repo": repo},
            )

        if "penetration testing" in lower:
            store.add_evidence(
                source=EvidenceSource.GITHUB,
                evidence_type=EvidenceType.TECH_STACK,
                value="Penetration Testing",
                url=repo_url,
                platform="github",
                username=username,
                title=repo_name,
                description=description,
                confidence=confidence,
                raw_data={"repo": repo},
            )

        if "machine learning" in lower:
            store.add_evidence(
                source=EvidenceSource.GITHUB,
                evidence_type=EvidenceType.TECH_STACK,
                value="Machine Learning",
                url=repo_url,
                platform="github",
                username=username,
                title=repo_name,
                description=description,
                confidence=confidence,
                raw_data={"repo": repo},
            )

    def _score_github_profile(
        self,
        store: EvidenceStore,
        profile_text: str,
        username: str | None,
    ) -> float:
        lower = profile_text.lower()
        score = 0.0

        full_name = store.target.full_name.lower()

        if full_name in lower:
            score += 0.40

        if username and self._username_matches_name(store.target.full_name, username):
            score += 0.15

        for term in store.get_strong_context_terms():
            if term.lower() in lower:
                score += 0.15

        if store.target.email_domain and store.target.email_domain.lower() in lower:
            score += 0.10

        return round(max(0.0, min(score, 1.0)), 3)

    def _matched_context(
        self,
        store: EvidenceStore,
        text: str,
    ) -> list[str]:
        lower = text.lower()
        values = []

        for term in store.get_context_terms():
            if term.lower() in lower:
                values.append(term)

        return store.unique(values)

    def _username_matches_name(self, full_name: str, username: str) -> bool:
        normalized_username = self._normalize(username)

        parts = [
            self._normalize(part)
            for part in full_name.split()
            if len(part) > 2
        ]

        return all(part in normalized_username for part in parts)

    def _normalize(self, value: str) -> str:
        return "".join(ch.lower() for ch in value.lower() if ch.isalnum())