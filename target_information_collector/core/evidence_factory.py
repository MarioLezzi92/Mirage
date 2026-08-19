from target_information_collector.shared.models import Evidence, EvidenceType, ProfileData


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
        return evidence
