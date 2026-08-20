import re
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
    email_owner_matches,
    is_profile_url,
    platform_from_url,
    profile_owner_matches,
    profile_username,
    normalize,
    tokens,
)


class SearchProvider(Protocol):
    def search(self, queries: list[str]) -> list[SearchResult]: ...


class WebAgent(DiscoveryAgent):
    name = "web"
    EMAIL_PATTERN = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
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
            tuple[bool, float, int, CandidateProfile],
        ] = {}
        result_index = 0

        def consume(
            results: list[SearchResult],
            corroborated: bool = False,
            mentions_only: bool = False,
        ) -> None:
            nonlocal result_index
            for result in results:
                index = result_index
                result_index += 1
                url = str(result.url)
                platform = platform_from_url(url)
                if mentions_only and platform != "web":
                    continue
                host = urlparse(url).netloc.casefold().removeprefix("www.")
                context = f"{result.title} {result.snippet} {host}".strip()
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
                        evidence.extend(
                            Evidence(
                                source=self.name,
                                platform="web",
                                evidence_type=EvidenceType.EMAIL,
                                value=email,
                                url=result.url,
                                confidence=score,
                            )
                            for email in self.EMAIL_PATTERN.findall(context)
                            if email_owner_matches(target.full_name, email)
                        )
                    continue

                if not is_profile_url(url, platform):
                    continue
                username = profile_username(url, platform)
                if not profile_owner_matches(
                    target.full_name,
                    result.title,
                    username,
                ):
                    continue
                canonical = canonical_url(url)
                key = (platform, canonical.casefold())
                related_profiles = self._related_profiles(target, result.query)
                candidate = CandidateProfile(
                    platform=platform,
                    url=canonical,
                    username=profile_username(canonical, platform),
                    discovered_by=(
                        f"{self.name}_cross_profile"
                        if related_profiles
                        else f"{self.name}_corroborated"
                        if corroborated
                        else self.name
                    ),
                    title=result.title,
                    snippet=result.snippet,
                    context=context,
                    related_profiles=related_profiles,
                )
                current = candidate_pool.get(key)
                quality = (bool(related_profiles), score)
                if current is None or quality > current[:2]:
                    candidate_pool[key] = (*quality, index, candidate)

        consume(self.provider.search(self._queries(target)))
        has_mentions = any(
            item.evidence_type == EvidenceType.WEB_MENTION
            for item in evidence
        )
        if self.name == "web" and not has_mentions:
            try:
                consume(
                    self.provider.search(self._mention_queries(target)),
                    mentions_only=True,
                )
            except Exception:
                # Le mentions sono enrichment opzionale: un problema nella
                # ricerca dedicata non deve invalidare il resto del profilo.
                pass

        followup_queries = self._corroboration_queries(
            target,
            evidence,
            candidate_pool,
        )
        if self.name == "web" and followup_queries:
            try:
                consume(
                    self.provider.search(followup_queries),
                    corroborated=True,
                )
            except Exception:
                # Il secondo passaggio è un miglioramento best-effort: una
                # sua indisponibilità non deve cancellare la discovery base.
                pass

        grouped: dict[
            str,
            list[tuple[bool, float, int, CandidateProfile]],
        ] = {}
        for item in candidate_pool.values():
            grouped.setdefault(item[3].platform, []).append(item)
        for items in grouped.values():
            items.sort(key=lambda item: (-item[0], -item[1], item[2]))
            candidates.extend(
                item[3] for item in items[:self.max_candidates_per_platform]
            )

        return DiscoveryOutput(candidates=candidates, evidence=evidence)

    @classmethod
    def _corroboration_queries(
        cls,
        target: TargetInput,
        evidence: list[Evidence],
        candidate_pool: dict[
            tuple[str, str],
            tuple[bool, float, int, CandidateProfile],
        ],
    ) -> list[str]:
        excluded = (
            tokens(target.full_name)
            | tokens(target.company or "")
            | IdentityMatcher.IDENTITY_GENERIC_TOKENS
            | IdentityMatcher.CONTEXT_WEAK_TOKENS
        )
        groups: list[list[str]] = []
        for item in evidence:
            if (
                item.evidence_type != EvidenceType.WEB_MENTION
                or item.confidence < 0.7
            ):
                continue
            allowed = tokens(item.value) - excluded
            group: list[str] = []
            for word in normalize(item.value).split():
                if word in allowed and word not in group:
                    group.append(word)
            if group:
                groups.append(group[:3])
        if not groups:
            return []

        distinctive = max(
            groups,
            key=lambda group: (len(group), sum(map(len, group))),
        )
        corroboration = set(distinctive)
        missing: list[str] = []
        for platform in ("linkedin", "instagram", "facebook"):
            platform_candidates = [
                item[3]
                for key, item in candidate_pool.items()
                if key[0] == platform
            ]
            if not any(
                cls._candidate_has_context(target, candidate, corroboration)
                for candidate in platform_candidates
            ):
                missing.append(platform)

        name = f'"{target.full_name}"'
        # Una query con tutte le parole può privilegiare post e pagine che
        # citano il target, nascondendo il profilo personale. La parola più
        # distintiva (es. "accademico") porta invece il profilo con quella
        # stessa evidenza nel titolo o nella bio. La frase completa rimane un
        # secondo tentativo, nella medesima chiamata al provider.
        strongest = max(distinctive, key=lambda word: (len(word), word))
        phrase = " ".join(f'"{word}"' for word in distinctive)
        queries: list[str] = []
        for platform in missing:
            queries.append(f'{name} "{strongest}" site:{platform}.com')
            if len(distinctive) > 1:
                queries.append(f"{name} {phrase} site:{platform}.com")
        return queries

    @staticmethod
    def _candidate_has_context(
        target: TargetInput,
        candidate: CandidateProfile,
        corroboration: set[str],
    ) -> bool:
        context = candidate.context or " ".join(
            value for value in (candidate.title, candidate.snippet) if value
        )
        if not context:
            return False
        if IdentityMatcher().score_text(target, context)[0] >= 0.7:
            return True
        candidate_tokens = (
            tokens(context)
            - tokens(target.full_name)
            - IdentityMatcher.IDENTITY_GENERIC_TOKENS
            - IdentityMatcher.CONTEXT_WEAK_TOKENS
        )
        return IdentityMatcher._contexts_match(
            candidate_tokens,
            corroboration,
            allow_single=True,
        )

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
        if target.email:
            queries.append(f'{name} "{target.email}"')
        queries.extend(
            f'{name} "{username}"'
            for username in target.profile_hypotheses.values()
            if username and normalize(username) != normalize(target.full_name)
        )
        if target.company:
            queries.extend(
                f'{name} "{alias}"'
                for alias in sorted(
                    IdentityMatcher.organization_aliases(target.company)
                )[:3]
            )
        queries.extend(f'{name} "{value}"' for value in contexts[:4])
        queries.append(f"{name} site:scholar.google.com/citations")
        queries.append(f"{name} email")
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

    @staticmethod
    def _mention_queries(target: TargetInput) -> list[str]:
        """Secondo passaggio mirato quando la discovery generica è vuota."""
        name = f'"{target.full_name}"'
        without_socials = (
            "-site:linkedin.com -site:github.com "
            "-site:instagram.com -site:facebook.com"
        )
        queries = [
            f"{name} intervista {without_socials}",
            f"{name} interview {without_socials}",
            f"{name} articolo {without_socials}",
            f"{name} news {without_socials}",
        ]
        contexts = [
            value
            for value in (
                target.company,
                target.role,
                *target.education,
                *target.cities,
            )
            if value
        ]
        queries.extend(
            f'{name} "{value}" {without_socials}'
            for value in contexts[:2]
        )
        return list(dict.fromkeys(queries))

    @staticmethod
    def _related_profiles(target: TargetInput, query: str) -> list[str]:
        quoted_terms = {
            normalize(value)
            for value in re.findall(r'"([^\"]+)"', query)
            if value.strip()
        }
        if not quoted_terms:
            return []
        return [
            url
            for url, username in target.profile_hypotheses.items()
            if normalize(username) != normalize(target.full_name)
            and normalize(username) in quoted_terms
        ]
