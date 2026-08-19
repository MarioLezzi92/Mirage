import re
from collections.abc import Callable

from target_information_collector.agents.base_agent import DiscoveryAgent, ProfileAgent
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
    normalize,
    profile_username,
)

ProgressCallback = Callable[[str, int, int, str], None]


class CollectorPipeline:
    """Discovery -> routing per piattaforma -> raccolta evidenze."""

    def __init__(
        self,
        discovery_agents: list[DiscoveryAgent],
        profile_agents: dict[str, ProfileAgent],
    ) -> None:
        self.discovery_agents = discovery_agents
        self.profile_agents = profile_agents

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
                output = agent.discover(target)
                store.add_candidates(output.candidates)
                store.add_evidence(output.evidence)
            except Exception as exc:
                errors.append(f"discovery/{agent.name}: {exc}")
            if progress:
                progress("Ricerca", index + 1, discovery_total, agent.name)

        candidates = store.candidates()
        fallback_added = False
        if not candidates and "facebook" in self.profile_agents:
            store.add_candidates(
                self._facebook_fallbacks(target, store.evidence())
            )
            candidates = store.candidates()
            fallback_added = True
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
            elif candidate.platform in blocked_platforms:
                label = f"{candidate.platform}: provider non disponibile"
            elif agent is None:
                if candidate.platform not in missing_platforms:
                    warnings.append(
                        f"provider {candidate.platform} non configurato: "
                        "i candidati restano nel raw"
                    )
                    missing_platforms.add(candidate.platform)
            else:
                try:
                    contextual_target = self._with_collected_context(
                        target,
                        store.evidence(),
                        exclude_platform=candidate.platform,
                    )
                    collected = agent.collect(contextual_target, candidate)
                    store.add_evidence(collected)
                    new_candidates = self._crosslinked_candidates(collected)
                    if not fallback_added and candidate.platform == "github":
                        fallbacks = self._facebook_fallbacks(
                            target,
                            store.evidence(),
                        )
                        new_candidates.extend(fallbacks)
                        fallback_added = bool(fallbacks)
                    store.add_candidates(new_candidates)
                    if self._certain_profile(candidate, collected):
                        certain_platforms.add(candidate.platform)
                    candidates = self._prioritized_candidates(
                        store.candidates(),
                        candidates[:index + 1],
                    )
                    if not collected:
                        warnings.append(
                            f"candidato non verificato: {candidate.url}"
                        )
                except Exception as exc:
                    errors.append(
                        f"profile/{candidate.platform}/{candidate.url}: {exc}"
                    )
                    if getattr(exc, "stop_platform", False):
                        blocked_platforms.add(candidate.platform)

            index += 1
            if index == len(candidates) and not fallback_added:
                fallback_added = True
                if "facebook" in self.profile_agents:
                    store.add_candidates(
                        self._facebook_fallbacks(target, store.evidence())
                    )
                    candidates = store.candidates()
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

    @classmethod
    def _prioritized_candidates(
        cls,
        candidates: list[CandidateProfile],
        processed: list[CandidateProfile],
    ) -> list[CandidateProfile]:
        processed_keys = {cls._candidate_key(item) for item in processed}
        remaining = [
            item
            for item in candidates
            if cls._candidate_key(item) not in processed_keys
        ]
        remaining.sort(
            key=lambda item: (
                0 if item.explicit else
                1 if item.discovered_by == "cross_profile_fallback" else
                2
            )
        )
        return [*processed, *remaining]

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
            )
            for item in evidence
            if item.evidence_type == EvidenceType.PROFILE
            and item.url
            and item.metadata.get("crosslink")
        ]

    @staticmethod
    def _facebook_fallbacks(
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

        identifiers = [target.email or "", target.github_username or ""]
        identifiers.extend(
            item.value
            for item in evidence
            if item.platform != "facebook"
            and item.evidence_type == EvidenceType.EMAIL
            and item.confidence >= 0.7
        )
        identifiers.extend(
            str(item.metadata.get("username") or "")
            for item in evidence
            if item.platform == "github"
            and item.evidence_type == EvidenceType.PROFILE
            and item.confidence >= 0.8
        )

        suffixes: list[str] = []
        for identifier in identifiers:
            for digits in re.findall(r"(?<!\d)\d{2,4}(?!\d)", identifier):
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

    @staticmethod
    def _with_collected_context(
        target: TargetInput,
        evidence: list[Evidence],
        exclude_platform: str | None = None,
    ) -> TargetInput:
        # Gli omonimi della stessa piattaforma non devono confermarsi a vicenda.
        evidence = [
            item for item in evidence if item.platform != exclude_platform
        ]

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
            }
        )