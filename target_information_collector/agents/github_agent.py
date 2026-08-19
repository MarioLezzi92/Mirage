from target_information_collector.agents.base_agent import DiscoveryAgent, ProfileAgent
from target_information_collector.core.evidence_factory import EvidenceFactory
from target_information_collector.core.identity_matcher import IdentityMatcher
from target_information_collector.providers.github_provider import GitHubProvider
from target_information_collector.shared.models import (
    CandidateProfile,
    DiscoveryOutput,
    Evidence,
    EvidenceType,
    ProfileData,
    TargetInput,
)
from target_information_collector.shared.text import (
    canonical_url,
    is_profile_url,
    platform_from_url,
)


class GitHubAgent(DiscoveryAgent, ProfileAgent):
    name = "github"
    platform = "github"

    def __init__(
        self,
        provider: GitHubProvider,
        matcher: IdentityMatcher,
        threshold: float = 0.7,
        max_candidates: int = 5,
    ) -> None:
        self.provider = provider
        self.matcher = matcher
        self.threshold = threshold
        self.max_candidates = max_candidates
        self.factory = EvidenceFactory()

    def discover(self, target: TargetInput) -> DiscoveryOutput:
        if target.github_username:
            return DiscoveryOutput(
                candidates=[
                    CandidateProfile(
                        platform=self.platform,
                        url=f"https://github.com/{target.github_username}",
                        username=target.github_username,
                        discovered_by=self.name,
                        explicit=True,
                    )
                ]
            )

        candidates = []
        for item in self.provider.search_users(target.full_name, self.max_candidates):
            username = item.get("login")
            url = item.get("html_url")
            if username and url:
                candidates.append(
                    CandidateProfile(
                        platform=self.platform,
                        url=url,
                        username=username,
                        discovered_by=self.name,
                    )
                )
        return DiscoveryOutput(candidates=candidates)

    def collect(self, target: TargetInput, candidate: CandidateProfile) -> list[Evidence]:
        username = candidate.username
        if not username:
            return []

        raw = self.provider.get_user(username)
        profile = ProfileData(
            platform=self.platform,
            url=raw.get("html_url") or str(candidate.url),
            full_name=raw.get("name"),
            username=raw.get("login") or username,
            bio=raw.get("bio"),
            company=raw.get("company"),
            locations=[raw["location"]] if raw.get("location") else [],
            emails=[raw["email"]] if raw.get("email") else [],
            raw=raw,
        )
        score, reasons = self.matcher.score(
            target,
            profile,
            discovery_context=candidate.context,
            explicit=candidate.explicit,
        )
        if 0 < score < self.threshold:
            try:
                readme = self.provider.get_profile_readme(username)
            except Exception:
                readme = ""
            if readme:
                score, reasons = self.matcher.score(
                    target,
                    profile,
                    discovery_context=f"{candidate.context} {readme}".strip(),
                    explicit=candidate.explicit,
                )
        if score < self.threshold:
            return []

        repositories = self.provider.get_repositories(username)
        profile.tech_stack = self._languages(repositories)
        evidence = self.factory.from_profile(profile, score, reasons)
        try:
            social_accounts = self.provider.get_social_accounts(username)
        except Exception:
            social_accounts = []

        seen: set[str] = set()
        for account in social_accounts:
            url = str(account.get("url") or "")
            platform = platform_from_url(url)
            if (
                platform in {"web", "github"}
                or not is_profile_url(url, platform)
            ):
                continue
            url = canonical_url(url)
            if url.casefold() in seen:
                continue
            seen.add(url.casefold())
            evidence.append(
                Evidence(
                    source="github",
                    platform=platform,
                    evidence_type=EvidenceType.PROFILE,
                    value=url,
                    url=url,
                    confidence=score,
                    metadata={"crosslink": True},
                )
            )
        return evidence

    def _languages(self, repositories: list[dict]) -> list[str]:
        usage: dict[str, int] = {}
        get_languages = getattr(
            self.provider,
            "get_repository_languages",
            None,
        )
        for repository in repositories:
            if repository.get("fork"):
                continue

            languages: dict[str, int] = {}
            if get_languages:
                try:
                    languages = get_languages(repository)
                except Exception:
                    languages = {}

            primary = repository.get("language")
            if not languages and primary:
                languages = {str(primary): 1}

            for language, size in languages.items():
                usage[language] = usage.get(language, 0) + int(size)

        return [
            language
            for language, _ in sorted(
                usage.items(),
                key=lambda item: (-item[1], item[0].casefold()),
            )
        ]
