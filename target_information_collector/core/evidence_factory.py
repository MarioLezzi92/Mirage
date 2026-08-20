from target_information_collector.shared.models import Evidence, EvidenceType, ProfileData
from target_information_collector.shared.text import platform_from_url


class EvidenceFactory:
    def from_profile(
        self,
        profile: ProfileData,
        confidence: float,
        reasons: list[str],
    ) -> list[Evidence]:
        common = {
            "source": profile.platform,
            "platform": profile.platform,
            "url": profile.url,
            "confidence": confidence,
        }
        evidence = [
            Evidence(
                **common,
                evidence_type=EvidenceType.PROFILE,
                value=str(profile.url),
                metadata={
                    "full_name": profile.full_name,
                    "username": profile.username,
                    "reasons": reasons,
                },
            )
        ]
        for evidence_type, values in (
            (EvidenceType.EMAIL, profile.emails),
            (EvidenceType.ROLE, [profile.role] if profile.role else []),
            (EvidenceType.BIO, [profile.bio] if profile.bio else []),
            (EvidenceType.COMPANY, [profile.company] if profile.company else []),
            (EvidenceType.LOCATION, profile.locations),
            (EvidenceType.EDUCATION, profile.education),
            (EvidenceType.TECH_STACK, profile.tech_stack),
        ):
            for value in values:
                evidence.append(
                    Evidence(
                        **common,
                        evidence_type=evidence_type,
                        value=value,
                    )
                )
        for link in profile.crosslinks:
            evidence.append(
                Evidence(
                    source=profile.platform,
                    platform=platform_from_url(link),
                    evidence_type=EvidenceType.PROFILE,
                    value=link,
                    url=link,
                    confidence=confidence,
                    metadata={
                        "crosslink": True,
                        "related_profiles": [str(profile.url)],
                    },
                )
            )
        return evidence
