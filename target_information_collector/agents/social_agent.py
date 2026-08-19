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
from target_information_collector.shared.text import owner_name_matches


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

    def collect(self, target: TargetInput, candidate: CandidateProfile) -> list[Evidence]:
        rows = self.provider.fetch(str(candidate.url), candidate.username)
        if not rows:
            raise RuntimeError("il provider non ha restituito dati del profilo")
        best: tuple[float, int, list[Evidence]] | None = None
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
                if best is None or item[:2] > best[:2]:
                    best = item
        return best[2] if best else []

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
