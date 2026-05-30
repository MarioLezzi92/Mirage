import re

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

    ORG_STOPWORDS = {
        "di",
        "del",
        "della",
        "delle",
        "degli",
        "dei",
        "of",
        "the",
        "and",
        "for",
        "at",
        "in",
        "studi",
        "studies",
    }

    ORG_TRANSLATIONS = {
        "universita": "university",
        "università": "university",
        "univ": "university",
        "university": "university",
        "istituto": "institute",
        "institute": "institute",
        "politecnico": "polytechnic",
        "polytechnic": "polytechnic",
        "college": "college",
        "school": "school",
    }

    def __init__(self):
        self.normalizer = EvidenceNormalizer()

    def collect(self, store: EvidenceStore) -> None:
        if store.target.github_username:
            self._collect_by_username(store, store.target.github_username)
            return

        self._promote_web_candidates(store)
        self._search_by_identity(store)
        self._extract_github_context(store)

    def _headers(self) -> dict:
        headers = {"Accept": "application/vnd.github.v3+json"}

        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"

        return headers

    def _promote_web_candidates(self, store: EvidenceStore) -> None:
        usernames = set()

        for evidence in store.evidence:
            if evidence.platform != self.PLATFORM:
                continue

            if evidence.evidence_type not in {
                EvidenceType.PROFILE,
                EvidenceType.PUBLIC_LINK,
                EvidenceType.WEB_MENTION,
            }:
                continue

            username = evidence.username or self.normalizer.extract_username(evidence.url)

            if username:
                usernames.add(username)

        for username in usernames:
            self._collect_by_username(store, username)

    def _search_by_identity(self, store: EvidenceStore) -> None:
        name = store.target.full_name.strip()
        compact_name = name.replace(" ", "")

        queries = [
            f'"{name}" in:fullname',
            f'{compact_name} in:login',
            f'{name} in:fullname',
            f'"{name}" in:name',
        ]

        seen = set()

        for query in queries:
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
                    continue

                for item in response.json().get("items", []):
                    username = item.get("login")

                    if not username or username in seen:
                        continue

                    seen.add(username)
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

        confidence = self._calculate_github_score(
            store=store,
            user_data=user_data,
            profile_text=profile_text,
            username=login,
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

    def _calculate_github_score(
        self,
        store: EvidenceStore,
        user_data: dict,
        profile_text: str,
        username: str | None,
    ) -> float:
        score = 0.0
        lower_text = profile_text.lower()

        if store.target.github_username and username:
            if self.normalize(store.target.github_username) == self.normalize(username):
                score += 0.50

        if store.target.full_name.lower() in lower_text:
            score += 0.30

        if username and self.username_matches_name(store.target.full_name, username):
            score += 0.15

        if self._organization_matches(store.target.company, profile_text):
            score += 0.30

        if self._location_matches(store, profile_text):
            score += 0.15

        if self._role_matches(store, profile_text):
            score += 0.10

        if store.target.email and user_data.get("email") == store.target.email:
            score += 0.30

        if store.target.email_domain:
            email = user_data.get("email") or ""

            if store.target.email_domain.lower() in email.lower():
                score += 0.15

        return round(max(0.0, min(score, 1.0)), 3)

    def _organization_matches(self, target_org: str | None, profile_text: str) -> bool:
        if not target_org or not profile_text:
            return False

        target_tokens = self._organization_tokens(target_org)
        profile_tokens = self._organization_tokens(profile_text)

        if not target_tokens or not profile_tokens:
            return False

        overlap = target_tokens.intersection(profile_tokens)

        if not overlap:
            return False

        ratio = len(overlap) / len(target_tokens)

        if ratio >= 0.60:
            return True

        important_target_tokens = {
            token
            for token in target_tokens
            if token not in {"university", "institute", "college", "school", "polytechnic"}
        }

        if important_target_tokens and important_target_tokens.issubset(profile_tokens):
            return True

        return False

    def _organization_tokens(self, text: str) -> set[str]:
        normalized = self._normalize_text(text)
        raw_tokens = re.split(r"[^a-z0-9]+", normalized)

        tokens = set()

        for token in raw_tokens:
            if not token:
                continue

            if len(token) <= 2:
                continue

            if token in self.ORG_STOPWORDS:
                continue

            token = self.ORG_TRANSLATIONS.get(token, token)

            if token in self.ORG_STOPWORDS:
                continue

            tokens.add(token)

        return tokens

    def _location_matches(self, store: EvidenceStore, profile_text: str) -> bool:
        values = []

        if store.target.location:
            values.append(store.target.location)

        values.extend(store.target.cities)

        return self._contains_any(profile_text, values)

    def _role_matches(self, store: EvidenceStore, profile_text: str) -> bool:
        values = []

        if store.target.role:
            values.append(store.target.role)

        return self._contains_any(profile_text, values)

    def _contains_any(self, text: str, values: list[str]) -> bool:
        lower = text.lower()
        return any(value and value.lower() in lower for value in values)

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

    def _extract_github_context(self, store: EvidenceStore) -> None:
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

            self.extract_common_context(
                store=store,
                evidence=evidence,
                text=text,
                confidence=max(evidence.confidence, 0.6),
                platform=self.PLATFORM,
                source=self.SOURCE,
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

        for source, target in replacements.items():
            normalized = normalized.replace(source, target)

        return normalized

    def _join_text(self, *values) -> str:
        return " ".join(str(value) for value in values if value)