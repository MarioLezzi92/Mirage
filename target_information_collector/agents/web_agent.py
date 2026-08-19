from typing import Protocol
from urllib.parse import urlparse

from target_information_collector.agents.base_agent import DiscoveryAgent
from target_information_collector.core.identity_matcher import IdentityMatcher
from target_information_collector.shared.models import (
    CandidateProfile,
    DiscoveryOutput,
    Evidence,
    EvidenceType,
    SearchResult,
    TargetInput,
)
from target_information_collector.shared.text import (
    canonical_url,
    is_profile_url,
    owner_name_matches,
    platform_from_url,
    profile_username,
)


class SearchProvider(Protocol):
    def search(self, queries: list[str]) -> list[SearchResult]: ...


class WebAgent(DiscoveryAgent):
    name = "web"
    ARTICLE_SECTIONS = {
        "article", "articles", "blog", "event", "events", "news",
        "notizia", "notizie", "post", "posts", "press", "stories", "story",
    }
    LOW_QUALITY_MENTION_DOMAINS = {
        "idcrawl.com",
        "peekyou.com",
        "pipl.com",
        "socialcatfish.com",
        "spokeo.com",
        "truepeoplesearch.com",
        "whitepages.com",
    }

    def __init__(
        self,
        provider: SearchProvider,
        matcher: IdentityMatcher,
        max_candidates_per_platform: int = 5,
        name: str | None = None,
    ) -> None:
        self.provider = provider
        self.matcher = matcher
        self.max_candidates_per_platform = max_candidates_per_platform
        self.name = name or self.name

    def discover(self, target: TargetInput) -> DiscoveryOutput:
        candidates: list[CandidateProfile] = []
        evidence: list[Evidence] = []
        candidate_pool: dict[
            tuple[str, str],
            tuple[float, int, CandidateProfile],
        ] = {}

        for index, result in enumerate(self.provider.search(self._queries(target))):
            url = str(result.url)
            platform = platform_from_url(url)
            context = f"{result.title} {result.snippet}".strip()
            score, reasons = self.matcher.score_text(target, context)
            if score < 0.6:
                continue

            if platform == "web":
                if self._low_quality_mention(url):
                    continue
                title_score, _ = self.matcher.score_text(
                    target,
                    result.title,
                )
                if score >= 0.7 and (
                    title_score >= 0.6 or self._article_page(url)
                ):
                    evidence.append(
                        Evidence(
                            source=self.name,
                            platform="web",
                            evidence_type=EvidenceType.WEB_MENTION,
                            value=result.title or result.snippet or url,
                            url=result.url,
                            confidence=score,
                            metadata={"reasons": reasons},
                        )
                    )
            else:
                if not is_profile_url(url, platform):
                    continue
                if not (
                    owner_name_matches(target.full_name, result.title)
                    or owner_name_matches(target.full_name, result.snippet)
                ):
                    continue
                canonical = canonical_url(url)
                key = (platform, canonical.casefold())
                candidate = CandidateProfile(
                    platform=platform,
                    url=canonical,
                    username=profile_username(canonical, platform),
                    discovered_by=self.name,
                    title=result.title,
                    snippet=result.snippet,
                    context=context,
                )
                current = candidate_pool.get(key)
                if current is None or score > current[0]:
                    candidate_pool[key] = (score, index, candidate)

        grouped: dict[str, list[tuple[float, int, CandidateProfile]]] = {}
        for item in candidate_pool.values():
            grouped.setdefault(item[2].platform, []).append(item)
        for items in grouped.values():
            items.sort(key=lambda item: (-item[0], item[1]))
            candidates.extend(
                item[2] for item in items[:self.max_candidates_per_platform]
            )

        return DiscoveryOutput(candidates=candidates, evidence=evidence)

    @classmethod
    def _low_quality_mention(cls, url: str) -> bool:
        host = urlparse(url).netloc.casefold().removeprefix("www.")
        return any(
            host == domain or host.endswith(f".{domain}")
            for domain in cls.LOW_QUALITY_MENTION_DOMAINS
        )

    @classmethod
    def _article_page(cls, url: str) -> bool:
        sections = {
            part.casefold()
            for part in urlparse(url).path.split("/")
            if part
        }
        return bool(sections & cls.ARTICLE_SECTIONS)

    @staticmethod
    def _queries(target: TargetInput) -> list[str]:
        name = f'"{target.full_name}"'
        contexts = [
            value
            for value in (
                target.company,
                target.role,
                *target.cities,
                *target.education,
            )
            if value
        ]
        queries = [name]
        if target.company:
            queries.extend(
                f'{name} "{alias}"'
                for alias in sorted(
                    IdentityMatcher.organization_aliases(target.company)
                )[:3]
            )
        queries.extend(f'{name} "{value}"' for value in contexts[:4])
        queries.append(f"{name} site:scholar.google.com/citations")
        queries.append(
            f"{name} -site:linkedin.com -site:github.com "
            "-site:instagram.com -site:facebook.com"
        )
        # Le varianti più probabili dello username vengono cercate prima delle
        # query generiche, così non vengono escluse dal limite dei candidati.
        parts = [part for part in target.full_name.casefold().split() if part]
        if len(parts) >= 2:
            first, last = parts[0], parts[-1]
            likely_handles = (
                f"{first}_{last}",
                f"{first}.{last}",
                f"{first}{last}",
            )
            for domain in ("instagram.com", "facebook.com"):
                for handle in likely_handles:
                    queries.append(f"site:{domain} inurl:{handle}")

        for domain in (
            "site:linkedin.com/in",
            "site:github.com",
            "site:instagram.com",
            "site:facebook.com",
        ):
            queries.append(f"{name} {domain}")
            if contexts:
                queries.append(f'{name} "{contexts[0]}" {domain}')
        return list(dict.fromkeys(queries))