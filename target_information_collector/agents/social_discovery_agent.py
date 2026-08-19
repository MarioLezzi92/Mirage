from typing import Protocol

from target_information_collector.agents.base_agent import DiscoveryAgent
from target_information_collector.shared.models import (
    CandidateProfile,
    DiscoveryOutput,
    TargetInput,
)
from target_information_collector.shared.text import (
    canonical_url,
    is_profile_url,
    normalize,
    platform_from_url,
    profile_username,
)


class SocialDiscoveryProvider(Protocol):
    def find_profiles(
        self,
        profile_names: list[str],
        platforms: list[str],
    ) -> list[str]: ...


class SocialDiscoveryAgent(DiscoveryAgent):
    """Scopre profili social senza dipendere dal motore di ricerca web."""

    name = "social_discovery"
    platforms = ("instagram", "facebook", "linkedin")

    def __init__(
        self,
        provider: SocialDiscoveryProvider,
        max_candidates_per_platform: int = 5,
    ) -> None:
        self.provider = provider
        self.max_candidates_per_platform = max_candidates_per_platform

    def discover(self, target: TargetInput) -> DiscoveryOutput:
        profile_names = self._profile_names(target)
        likely_instagram = [
            f"https://instagram.com/{name}"
            for name in profile_names
            if " " not in name
        ][:3]
        urls = [
            *likely_instagram,
            *self.provider.find_profiles(
                profile_names,
                list(self.platforms),
            ),
        ]

        ranked: list[tuple[int, int, CandidateProfile]] = []
        seen: set[tuple[str, str]] = set()
        for index, url in enumerate(urls):
            platform = platform_from_url(url)
            if platform not in self.platforms or not is_profile_url(url, platform):
                continue

            normalized_url = canonical_url(url)
            key = (platform, normalized_url.casefold())
            if key in seen:
                continue
            seen.add(key)

            username = profile_username(normalized_url, platform)
            ranked.append(
                (
                    self._username_rank(target.full_name, username),
                    index,
                    CandidateProfile(
                        platform=platform,
                        url=normalized_url,
                        username=username,
                        discovered_by=self.name,
                    ),
                )
            )

        counts: dict[str, int] = {}
        candidates: list[CandidateProfile] = []
        for _, _, candidate in sorted(ranked, key=lambda item: (item[0], item[1])):
            count = counts.get(candidate.platform, 0)
            if count >= self.max_candidates_per_platform:
                continue
            candidates.append(candidate)
            counts[candidate.platform] = count + 1

        return DiscoveryOutput(candidates=candidates)

    @staticmethod
    def _profile_names(target: TargetInput) -> list[str]:
        parts = normalize(target.full_name).split()
        names = [target.full_name]
        if len(parts) >= 2:
            first, last = parts[0], parts[-1]
            names = [
                f"{first}_{last}",
                f"{first}.{last}",
                f"{first}{last}",
                target.full_name,
                f"{last}_{first}",
                f"{last}.{first}",
                f"{last}{first}",
            ]
        if target.github_username:
            names.insert(0, target.github_username)
        return list(dict.fromkeys(name for name in names if name))

    @staticmethod
    def _username_rank(full_name: str, username: str | None) -> int:
        if not username:
            return 3
        parts = normalize(full_name).split()
        if len(parts) < 2:
            return 3

        compact_username = "".join(normalize(username).split())
        first_last = f"{parts[0]}{parts[-1]}"
        last_first = f"{parts[-1]}{parts[0]}"
        variants = (first_last, last_first)
        if compact_username in variants:
            return 0
        if any(compact_username.startswith(value) for value in variants):
            return 1
        if parts[0] in compact_username and parts[-1] in compact_username:
            return 2
        return 3
