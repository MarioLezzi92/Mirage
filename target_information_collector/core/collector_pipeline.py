import re
from collections.abc import Callable

from target_information_collector.agents.base_agent import DiscoveryAgent, ProfileAgent
from target_information_collector.core.identity_matcher import IdentityMatcher
from target_information_collector.evidence.evidence_store import EvidenceStore
from target_information_collector.shared.models import (
    CandidateProfile,
    CollectionResult,
    Evidence,
    EvidenceType,
    TargetInput,
)
from target_information_collector.shared.text import (
    canonical_url,
    email_owner_matches,
    normalize,
    platform_from_url,
    profile_owner_matches,
    profile_username,
    tokens,
)

ProgressCallback = Callable[[str, int, int, str], None]


class CollectorPipeline:
    """Discovery -> routing per piattaforma -> raccolta evidenze."""

    def __init__(
        self,
        discovery_agents: list[DiscoveryAgent],
        profile_agents: dict[str, ProfileAgent],
        max_candidates_per_platform: int = 5,
    ) -> None:
        self.discovery_agents = discovery_agents
        self.profile_agents = profile_agents
        self.max_candidates_per_platform = max_candidates_per_platform

    def collect(
        self,
        target: TargetInput,
        progress: ProgressCallback | None = None,
    ) -> CollectionResult:
        store = EvidenceStore()
        errors: list[str] = []
        warnings: list[str] = []
        missing_platforms: set[str] = set()
        blocked_platforms: set[str] = set()

        discovery_total = len(self.discovery_agents)
        for index, agent in enumerate(self.discovery_agents):
            if progress:
                progress("Ricerca", index, discovery_total, agent.name)
            try:
                discovery_target = (
                    self._with_profile_hypotheses(target, store.candidates())
                    if agent.name == "web"
                    else target
                )
                output = agent.discover(discovery_target)
                store.add_candidates(output.candidates)
                store.add_evidence(output.evidence)
            except Exception as exc:
                errors.append(f"discovery/{agent.name}: {exc}")
            if progress:
                progress("Ricerca", index + 1, discovery_total, agent.name)

        if "facebook" in self.profile_agents:
            store.add_candidates(
                self._facebook_fallbacks(target, store.evidence())
            )
        ranking_target = self._with_collected_context(
            target,
            store.evidence(),
        )
        candidates = self._enrichment_candidates(
            ranking_target,
            store.candidates(),
        )
        if progress and not candidates:
            progress("Profili", 0, 0, "nessun candidato")
        index = 0
        certain_platforms: set[str] = set()
        while index < len(candidates):
            candidate = candidates[index]
            label = f"{candidate.platform}: {candidate.username or candidate.url}"
            if progress:
                progress("Profili", index, len(candidates), label)
            agent = self.profile_agents.get(candidate.platform)
            if candidate.platform in certain_platforms:
                label = f"{candidate.platform}: già verificato"
            elif agent is None:
                if candidate.platform not in missing_platforms:
                    warnings.append(
                        f"provider {candidate.platform} non configurato: "
                        "i candidati restano nel raw"
                    )
                    missing_platforms.add(candidate.platform)
            else:
                contextual_target = self._with_collected_context(
                    target,
                    store.evidence(),
                    exclude_platform=candidate.platform,
                )
                collected: list[Evidence] = []
                if candidate.platform in blocked_platforms:
                    label = f"{candidate.platform}: fallback discovery"
                else:
                    try:
                        collected = agent.collect(contextual_target, candidate)
                    except Exception as exc:
                        errors.append(
                            f"profile/{candidate.platform}/{candidate.url}: {exc}"
                        )
                        if getattr(exc, "stop_platform", False):
                            blocked_platforms.add(candidate.platform)

                fallback = getattr(agent, "collect_from_discovery", None)
                if not collected and fallback:
                    collected = fallback(contextual_target, candidate)

                store.add_evidence(collected)
                new_candidates = self._crosslinked_candidates(collected)
                if collected and candidate.platform != "facebook":
                    new_candidates.extend(
                        self._facebook_fallbacks(target, store.evidence())
                    )
                store.add_candidates(new_candidates)
                resolved = IdentityMatcher.resolve_profiles(
                    target,
                    store.evidence(),
                )
                if self._certain_profile(candidate, collected) or any(
                    item.platform == candidate.platform
                    for item in resolved.values()
                ):
                    certain_platforms.add(candidate.platform)
                ranking_target = self._with_collected_context(
                    target,
                    store.evidence(),
                )
                candidates = self._enrichment_candidates(
                    ranking_target,
                    store.candidates(),
                    candidates[:index + 1],
                    certain_platforms,
                )
                if not collected:
                    detail = str(getattr(agent, "last_rejection", "")).strip()
                    warnings.append(
                        f"candidato non verificato: {candidate.url}"
                        + (f" ({detail})" if detail else "")
                    )

            index += 1
            if progress:
                progress("Profili", index, len(candidates), label)

        return CollectionResult(
            target=target,
            candidates=store.candidates(),
            evidence=store.evidence(),
            errors=errors,
            warnings=warnings,
            active_profile_agents=sorted(self.profile_agents),
        )

    @staticmethod
    def _certain_profile(
        candidate: CandidateProfile,
        evidence: list[Evidence],
    ) -> bool:
        threshold = (
            0.8
            if candidate.discovered_by == "cross_profile_fallback"
            else 0.9
        )
        return any(
            item.evidence_type == EvidenceType.PROFILE
            and item.confidence >= threshold
            for item in evidence
        )

    @staticmethod
    def _candidate_key(candidate: CandidateProfile) -> tuple[str, str]:
        return (
            candidate.platform,
            canonical_url(str(candidate.url)).casefold(),
        )

    @staticmethod
    def _crosslinked_candidates(evidence: list[Evidence]) -> list[CandidateProfile]:
        return [
            CandidateProfile(
                platform=item.platform,
                url=item.url,
                username=profile_username(str(item.url), item.platform),
                discovered_by=f"{item.source}_crosslink",
                explicit=True,
                related_profiles=[
                    str(value)
                    for value in item.metadata.get("related_profiles", [])
                ],
            )
            for item in evidence
            if item.evidence_type == EvidenceType.PROFILE
            and item.url
            and item.metadata.get("crosslink")
        ]

    @classmethod
    def _facebook_fallbacks(
        cls,
        target: TargetInput,
        evidence: list[Evidence],
    ) -> list[CandidateProfile]:
        facebook_scores = [
            item.confidence
            for item in evidence
            if item.platform == "facebook"
            and item.evidence_type == EvidenceType.PROFILE
        ]
        if facebook_scores and max(facebook_scores) >= 0.8:
            return []

        name_parts = normalize(target.full_name).split()
        if len(name_parts) < 2:
            return []

        trusted = cls._trusted_context_evidence(target, evidence)
        identifiers = [target.email or "", target.github_username or ""]
        identifiers.extend(
            item.value
            for item in trusted
            if item.platform != "facebook"
            and item.evidence_type == EvidenceType.EMAIL
            and item.confidence >= 0.7
        )
        identifiers.extend(
            str(item.metadata.get("username") or "")
            for item in trusted
            if item.platform in {"github", "instagram"}
            and item.evidence_type == EvidenceType.PROFILE
            and item.confidence >= 0.7
        )

        suffixes: list[str] = []
        for identifier in identifiers:
            for digits in re.findall(r"(?<!\d)\d{1,4}(?!\d)", identifier):
                suffix = digits[-2:]
                if suffix not in suffixes:
                    suffixes.append(suffix)

        base = f"{name_parts[0]}.{name_parts[-1]}"
        return [
            CandidateProfile(
                platform="facebook",
                url=f"https://facebook.com/{base}.{suffix}",
                username=f"{base}.{suffix}",
                discovered_by="cross_profile_fallback",
            )
            for suffix in suffixes[:2]
        ]

    def _enrichment_candidates(
        self,
        target: TargetInput,
        candidates: list[CandidateProfile],
        processed: list[CandidateProfile] | None = None,
        resolved_platforms: set[str] | None = None,
    ) -> list[CandidateProfile]:
        """Limita le chiamate costose senza eliminare candidati dal raw."""
        processed = processed or []
        resolved_platforms = resolved_platforms or set()
        processed_keys = {self._candidate_key(item) for item in processed}
        counts: dict[str, int] = {}
        for item in processed:
            counts[item.platform] = counts.get(item.platform, 0) + 1

        indexed = [
            (index, item)
            for index, item in enumerate(candidates)
            if self._candidate_key(item) not in processed_keys
            and item.platform not in resolved_platforms
        ]
        grouped: dict[str, list[tuple[int, CandidateProfile]]] = {}
        for item in indexed:
            grouped.setdefault(item[1].platform, []).append(item)

        selected: list[CandidateProfile] = []
        platform_order = ("github", "linkedin", "instagram", "facebook")
        ordered_platforms = [
            *[name for name in platform_order if name in grouped],
            *sorted(set(grouped) - set(platform_order)),
        ]
        for platform in ordered_platforms:
            items = grouped[platform]
            readable_facebook = any(
                not self._opaque_facebook(item)
                and profile_owner_matches(
                    target.full_name,
                    item.title,
                    item.username,
                )
                for _, item in items
            )
            opaque_used = False
            ranked = sorted(
                items,
                key=lambda pair: self._candidate_priority(
                    target,
                    pair[1],
                    pair[0],
                ),
            )
            for _, candidate in ranked:
                if (
                    not candidate.explicit
                    and platform != "github"
                    and not profile_owner_matches(
                        target.full_name,
                        candidate.title,
                        candidate.username,
                    )
                ):
                    continue

                context_score = self._context_score(target, candidate)
                if self._opaque_facebook(candidate) and context_score < 0.7:
                    if readable_facebook or opaque_used:
                        continue
                    opaque_used = True

                if (
                    counts.get(platform, 0)
                    >= (
                        max(self.max_candidates_per_platform, 10)
                        if platform == "github"
                        else self.max_candidates_per_platform
                    )
                    and not candidate.explicit
                ):
                    continue
                selected.append(candidate)
                counts[platform] = counts.get(platform, 0) + 1

        return [*processed, *selected]

    @staticmethod
    def _with_profile_hypotheses(
        target: TargetInput,
        candidates: list[CandidateProfile],
    ) -> TargetInput:
        """Passa al web gli handle già scoperti senza considerarli verificati."""
        ranked = sorted(
            candidates,
            key=lambda item: (
                0 if item.explicit else 1 if item.platform == "github" else 2,
            ),
        )
        hypotheses: dict[str, str] = {}
        for candidate in ranked:
            if not candidate.username:
                continue
            # Un handle composto soltanto da nome+cognome è troppo comune
            # per collegare omonimi su piattaforme diverse. Restano invece
            # utili handle distintivi, abbreviazioni e suffissi numerici.
            if (
                not candidate.explicit
                and CollectorPipeline._plain_name_handle(
                    target.full_name,
                    candidate.username,
                )
            ):
                continue
            hypotheses[str(candidate.url)] = candidate.username
            if len(hypotheses) >= 10:
                break
        return target.model_copy(update={"profile_hypotheses": hypotheses})

    @staticmethod
    def _plain_name_handle(full_name: str, username: str) -> bool:
        parts = normalize(full_name).split()
        if len(parts) < 2:
            return False
        compact = "".join(normalize(username).split())
        return compact in {
            f"{parts[0]}{parts[-1]}",
            f"{parts[-1]}{parts[0]}",
        }

    @classmethod
    def _candidate_priority(
        cls,
        target: TargetInput,
        candidate: CandidateProfile,
        discovery_index: int,
    ) -> tuple[int, float, int, int, int]:
        context_score = cls._context_score(target, candidate)
        username_matches = profile_owner_matches(
            target.full_name,
            username=candidate.username,
        )
        priority = (
            0 if candidate.explicit else
            1 if cls._distinctive_related_profile(target, candidate) else
            2 if context_score >= 0.7 else
            3 if candidate.discovered_by == "cross_profile_fallback" else
            4 if (
                candidate.platform == "facebook"
                and candidate.discovered_by == "facebook_search"
            ) else
            5 if username_matches else
            6
        )
        return (
            priority,
            -context_score,
            0 if username_matches else 1,
            1 if cls._opaque_facebook(candidate) else 0,
            discovery_index,
        )

    @classmethod
    def _distinctive_related_profile(
        cls,
        target: TargetInput,
        candidate: CandidateProfile,
    ) -> bool:
        for url in candidate.related_profiles:
            platform = platform_from_url(url)
            username = profile_username(url, platform)
            if username and not cls._plain_name_handle(
                target.full_name,
                username,
            ):
                return True
        return False

    @staticmethod
    def _context_score(
        target: TargetInput,
        candidate: CandidateProfile,
    ) -> float:
        context = candidate.context or " ".join(
            value for value in (candidate.title, candidate.snippet) if value
        )
        if not context:
            return 0.0

        matcher = IdentityMatcher()
        score = matcher.score_text(target, context)[0]
        if not target.corroboration:
            return score

        excluded = (
            tokens(target.full_name)
            | matcher.IDENTITY_GENERIC_TOKENS
            | matcher.CONTEXT_WEAK_TOKENS
        )
        candidate_tokens = tokens(context) - excluded
        corroboration_tokens = tokens(
            " ".join(target.corroboration)
        ) - excluded
        if matcher._contexts_match(
            candidate_tokens,
            corroboration_tokens,
            allow_single=True,
        ):
            score += 0.2
        return min(score, 1.0)

    @staticmethod
    def _opaque_facebook(candidate: CandidateProfile) -> bool:
        if candidate.platform != "facebook":
            return False
        username = (candidate.username or "").casefold()
        return username.startswith("pfbid") or username.isdigit()

    @classmethod
    def _with_collected_context(
        cls,
        target: TargetInput,
        evidence: list[Evidence],
        exclude_platform: str | None = None,
    ) -> TargetInput:
        evidence = cls._trusted_context_evidence(
            target,
            evidence,
            exclude_platform,
        )

        def values(evidence_type: EvidenceType) -> list[str]:
            return [
                item.value
                for item in evidence
                if item.evidence_type == evidence_type
            ]

        def unique(items: list[str]) -> list[str]:
            output: list[str] = []
            seen: set[str] = set()
            for item in items:
                key = item.casefold().strip()
                if key and key not in seen:
                    output.append(item.strip())
                    seen.add(key)
            return output

        companies = values(EvidenceType.COMPANY)
        roles = values(EvidenceType.ROLE)
        corroboration = unique(
            [
                *target.corroboration,
                *(
                    item.value
                    for item in evidence
                    if item.confidence >= 0.7
                    and item.evidence_type in {
                        EvidenceType.WEB_MENTION,
                        EvidenceType.ROLE,
                        EvidenceType.BIO,
                        EvidenceType.COMPANY,
                        EvidenceType.EDUCATION,
                    }
                ),
            ]
        )
        return target.model_copy(
            update={
                "company": target.company or (companies[0] if companies else None),
                "role": target.role or (roles[0] if roles else None),
                "cities": unique(
                    [*target.cities, *values(EvidenceType.LOCATION)]
                ),
                "education": unique(
                    [*target.education, *values(EvidenceType.EDUCATION)]
                ),
                "corroboration": corroboration,
            }
        )

    @staticmethod
    def _trusted_context_evidence(
        target: TargetInput,
        evidence: list[Evidence],
        exclude_platform: str | None = None,
    ) -> list[Evidence]:
        """Un candidato incerto non può diventare prova per altri profili."""
        filtered = [
            item for item in evidence if item.platform != exclude_platform
        ]
        trusted_urls = set(IdentityMatcher.resolve_profiles(target, filtered))
        verified_web_identity = any(
            item.platform == "web"
            and item.evidence_type == EvidenceType.EMAIL
            and email_owner_matches(target.full_name, item.value)
            for item in filtered
        )
        return [
            item
            for item in filtered
            if (
                item.url
                and canonical_url(str(item.url)).casefold() in trusted_urls
            )
            or (verified_web_identity and item.platform == "web")
        ]
