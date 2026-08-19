from target_information_collector.agents.base_agent import DiscoveryAgent
from target_information_collector.shared.models import (
    CandidateProfile,
    DiscoveryOutput,
    Evidence,
    EvidenceType,
    TargetInput,
)
from target_information_collector.shared.text import platform_from_url, profile_username


class InputAgent(DiscoveryAgent):
    name = "input"

    def discover(self, target: TargetInput) -> DiscoveryOutput:
        candidates: list[CandidateProfile] = []
        evidence: list[Evidence] = []

        if target.email:
            evidence.append(
                Evidence(
                    source=self.name,
                    platform="input",
                    evidence_type=EvidenceType.EMAIL,
                    value=target.email,
                    confidence=1.0,
                )
            )

        for link in target.public_links:
            url = str(link)
            platform = platform_from_url(url)
            if platform == "web":
                evidence.append(
                    Evidence(
                        source=self.name,
                        platform="web",
                        evidence_type=EvidenceType.WEB_MENTION,
                        value=url,
                        url=link,
                        confidence=1.0,
                    )
                )
            else:
                candidates.append(
                    CandidateProfile(
                        platform=platform,
                        url=link,
                        username=profile_username(url, platform),
                        discovered_by=self.name,
                        explicit=True,
                    )
                )
        return DiscoveryOutput(candidates=candidates, evidence=evidence)
