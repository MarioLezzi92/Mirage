from typing import Protocol

from target_information_collector.agents.base_agent import ProfileAgent
from target_information_collector.core.evidence_factory import EvidenceFactory
from target_information_collector.core.identity_matcher import IdentityMatcher
from target_information_collector.core.profile_normalizer import ProfileNormalizer
from target_information_collector.shared.models import (
    CandidateProfile,
    Evidence,
    ProfileData,
    TargetInput,
)
from target_information_collector.shared.text import (
    owner_name_matches,
    profile_owner_matches,
)


class SocialProvider(Protocol):
    def fetch(self, url: str, username: str | None) -> list[dict]: ...


class SocialAgent(ProfileAgent):
    """Un solo agent parametrico per LinkedIn, Instagram e Facebook."""

    def __init__(
        self,
        platform: str,
        provider: SocialProvider,
        matcher: IdentityMatcher,
        threshold: float = 0.7,
    ) -> None:
        self.platform = platform
        self.provider = provider
        self.matcher = matcher
        self.threshold = threshold
        self.normalizer = ProfileNormalizer()
        self.factory = EvidenceFactory()
        self.last_rejection = ""

    def collect(self, target: TargetInput, candidate: CandidateProfile) -> list[Evidence]:
        self.last_rejection = ""
        if not candidate.explicit and not profile_owner_matches(
            target.full_name,
            candidate.title,
            candidate.username,
        ):
            self.last_rejection = "la pagina cita il target ma non gli appartiene"
            return []
        rows = self.provider.fetch(str(candidate.url), candidate.username)
        if not rows:
            raise RuntimeError("il provider non ha restituito dati del profilo")
        best: tuple[float, int, list[Evidence]] | None = None
        rejected: tuple[float, list[str]] | None = None
        for raw in rows:
            profile = self.normalizer.normalize(self.platform, str(candidate.url), raw)
            if not profile.username:
                profile.username = candidate.username
            if not profile.full_name and owner_name_matches(
                target.full_name,
                candidate.title,
            ):
                profile.full_name = target.full_name
            if self._richness(profile) == 0:
                self.last_rejection = "dati provider non riconosciuti"
                continue
            score, reasons = self.matcher.score(
                target,
                profile,
                discovery_context=candidate.context,
                explicit=candidate.explicit,
            )
            if score >= self.threshold:
                profile = self.normalizer.enrich_from_discovery(
                    profile,
                    target,
                    candidate.title,
                    candidate.snippet,
                )
                item = (
                    score,
                    self._richness(profile),
                    self.factory.from_profile(profile, score, reasons),
                )
                self._attach_relations(item[2], candidate.related_profiles)
                if best is None or item[:2] > best[:2]:
                    best = item
            elif rejected is None or score > rejected[0]:
                rejected = (score, reasons)
        if best:
            return best[2]
        if rejected:
            self.last_rejection = (
                f"score {rejected[0]:.2f}: {', '.join(rejected[1])}"
            )
        return []

    def collect_from_discovery(
        self,
        target: TargetInput,
        candidate: CandidateProfile,
    ) -> list[Evidence]:
        """Fallback prudente quando lo scraper è vuoto o non disponibile."""
        context = candidate.context or f"{candidate.title} {candidate.snippet}".strip()
        owner_matches = profile_owner_matches(
            target.full_name,
            candidate.title,
            candidate.username,
        )
        if not candidate.explicit and (not context or not owner_matches):
            if not self.last_rejection:
                self.last_rejection = "discovery senza identità verificabile"
            return []

        profile = ProfileData(
            platform=self.platform,
            url=candidate.url,
            full_name=target.full_name,
            username=candidate.username,
        )
        score, reasons = self.matcher.score(
            target,
            profile,
            discovery_context=context,
            explicit=candidate.explicit,
        )
        if score < self.threshold:
            if not self.last_rejection:
                self.last_rejection = f"score discovery {score:.2f}"
            return []

        profile = self.normalizer.enrich_from_discovery(
            profile,
            target,
            candidate.title,
            candidate.snippet,
        )
        evidence = self.factory.from_profile(
            profile,
            score,
            [*reasons, "verifica tramite discovery pubblica"],
        )
        self._attach_relations(evidence, candidate.related_profiles)
        return evidence

    @staticmethod
    def _attach_relations(
        evidence: list[Evidence],
        related_profiles: list[str],
    ) -> None:
        if not related_profiles:
            return
        for item in evidence:
            if item.evidence_type.value == "profile":
                item.metadata["related_profiles"] = related_profiles
                item.metadata["search_crosslink"] = True
                break

    @staticmethod
    def _richness(profile: ProfileData) -> int:
        return sum(
            bool(value)
            for value in (
                profile.full_name,
                profile.role,
                profile.bio,
                profile.company,
                profile.locations,
                profile.education,
                profile.emails,
                profile.tech_stack,
            )
        )
