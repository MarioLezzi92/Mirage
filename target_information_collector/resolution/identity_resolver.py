from urllib.parse import urlparse, urlunparse

from target_information_collector.shared.models import (
    CandidateStatus,
    EvidenceType,
    IdentityCandidate,
    PublicProfile,
    TargetInput,
)


class IdentityResolver:
    def resolve(
        self,
        target: TargetInput,
        candidates: list,
        profiles: list[PublicProfile],
        evidence,
    ) -> dict:
        resolved_candidates: list[IdentityCandidate] = []
        resolved_profiles: list[PublicProfile] = []

        profile_map = self._merge_profiles(candidates, profiles)

        for profile in profile_map.values():
            profile_evidence = self._evidence_for_profile(profile, evidence)
            text = self._profile_text(profile, profile_evidence)

            positives = self._positive_evidence(target, profile, text, profile_evidence)
            negatives = self._negative_evidence(target, profile_evidence, positives)

            score = self._score(positives, negatives)
            status = self._status(score, negatives)

            identity_candidate = IdentityCandidate(
                candidate_id=f"{profile.platform}:{profile.username or profile.url}",
                platform=profile.platform,
                profile_url=profile.url,
                username=profile.username,
                confidence=score,
                status=status,
                matched_fields=positives,
                positive_evidence=positives,
                negative_evidence=negatives,
                reason=self._reason(status, positives, negatives),
                evidence=profile_evidence,
            )

            resolved_candidates.append(identity_candidate)

            if status != CandidateStatus.REJECTED:
                resolved_profiles.append(profile)

        return {
            "identity_candidates": resolved_candidates,
            "public_profiles": resolved_profiles,
        }

    def _merge_profiles(self, candidates: list, profiles: list[PublicProfile]) -> dict[str, PublicProfile]:
        merged: dict[str, PublicProfile] = {}

        for profile in profiles:
            merged[self._canonical_url(profile.url)] = profile

        for candidate in candidates:
            url = self._get(candidate, "url")
            platform = self._get(candidate, "platform")

            if not url or not platform:
                continue

            key = self._canonical_url(url)
            existing = merged.get(key)

            if existing:
                candidate_confidence = self._get(candidate, "confidence", 0.0)

                if candidate_confidence > existing.confidence:
                    existing.confidence = candidate_confidence

                if not existing.username and self._get(candidate, "username"):
                    existing.username = self._get(candidate, "username")

                continue

            merged[key] = PublicProfile(
                platform=platform,
                url=url,
                username=self._get(candidate, "username"),
                confidence=self._get(candidate, "confidence", 0.0),
            )

        return merged

    def _evidence_for_profile(self, profile: PublicProfile, evidence) -> list:
        profile_key = self._canonical_url(profile.url)

        return [
            ev
            for ev in evidence
            if ev.url and self._canonical_url(ev.url) == profile_key
        ]

    def _positive_evidence(
        self,
        target: TargetInput,
        profile: PublicProfile,
        text: str,
        profile_evidence,
    ) -> list[str]:
        positives = []

        if self._name_matches(target, profile, text):
            positives.append("name")

        if self._username_matches_name(target, profile.username):
            positives.append("username")

        if self._contains_any(text, self._location_terms(target)):
            positives.append("location")

        if self._contains_any(text, self._organization_terms(target)):
            positives.append("organization")

        if self._contains_any(text, self._education_terms(target)):
            positives.append("education")

        if self._contains_any(text, self._role_terms(target)):
            positives.append("role")

        if self._contains_any(text, self._contact_terms(target)):
            positives.append("contact")

        if self._contains_any(text, target.aliases):
            positives.append("alias")

        if self._is_seeded_link(profile, profile_evidence):
            positives.append("seeded_link")

        return self._unique(positives)

    def _negative_evidence(
        self,
        target: TargetInput,
        profile_evidence,
        positives: list[str],
    ) -> list[str]:
        negatives = []

        strong_non_location = {
            "organization",
            "education",
            "contact",
            "alias",
            "seeded_link",
        }

        if any(item in positives for item in strong_non_location):
            return negatives

        candidate_locations = [
            str(ev.value).strip()
            for ev in profile_evidence
            if str(ev.evidence_type) == EvidenceType.LOCATION.value and ev.value
        ]

        target_locations = self._location_terms(target)

        if candidate_locations and target_locations:
            if not self._any_overlap(candidate_locations, target_locations):
                negatives.append("location_conflict")

        return self._unique(negatives)

    def _score(self, positives: list[str], negatives: list[str]) -> float:
        weights = {
            "name": 0.30,
            "username": 0.15,
            "location": 0.16,
            "organization": 0.20,
            "education": 0.18,
            "role": 0.12,
            "contact": 0.25,
            "alias": 0.16,
            "seeded_link": 0.40,
        }

        score = sum(weights.get(item, 0.0) for item in positives)
        score -= 0.30 * len(negatives)

        if positives == ["name"]:
            score = min(score, 0.34)

        if "name" not in positives and "username" not in positives and "seeded_link" not in positives:
            score = min(score, 0.30)

        return round(max(0.0, min(score, 0.99)), 2)

    def _status(self, score: float, negatives: list[str]) -> CandidateStatus:
        if negatives and score < 0.60:
            return CandidateStatus.REJECTED

        if score >= 0.75:
            return CandidateStatus.CONFIRMED

        if score >= 0.55:
            return CandidateStatus.PROBABLE

        if score >= 0.35:
            return CandidateStatus.CANDIDATE

        return CandidateStatus.REJECTED

    def _reason(
        self,
        status: CandidateStatus,
        positives: list[str],
        negatives: list[str],
    ) -> str:
        if status == CandidateStatus.REJECTED:
            if negatives:
                return f"Rejected: negative evidence detected ({', '.join(negatives)})."
            return "Rejected: not enough identity evidence."

        return f"{status.value}: matched {', '.join(positives)}."

    def _profile_text(self, profile: PublicProfile, profile_evidence) -> str:
        parts = [
            profile.url,
            profile.username,
        ]

        for ev in profile_evidence:
            parts.extend([
                ev.value,
                ev.title,
                ev.description,
            ])

        return " ".join(str(part) for part in parts if part).lower()

    def _name_matches(self, target: TargetInput, profile: PublicProfile, text: str) -> bool:
        return target.full_name.lower() in text or self._username_matches_name(target, profile.username)

    def _username_matches_name(self, target: TargetInput, username: str | None) -> bool:
        if not username:
            return False

        normalized_username = self._normalize(username)
        name_parts = [
            self._normalize(part)
            for part in target.full_name.split()
            if len(part) > 2
        ]

        return bool(name_parts) and all(part in normalized_username for part in name_parts)

    def _is_seeded_link(self, profile: PublicProfile, profile_evidence) -> bool:
        profile_key = self._canonical_url(profile.url)

        return any(
            str(ev.source) == "input"
            and ev.url
            and self._canonical_url(ev.url) == profile_key
            and str(ev.evidence_type) == EvidenceType.PUBLIC_LINK.value
            for ev in profile_evidence
        )

    def _location_terms(self, target: TargetInput) -> list[str]:
        terms = list(target.cities)

        if target.location:
            terms.append(target.location)

        return self._unique(terms)

    def _organization_terms(self, target: TargetInput) -> list[str]:
        terms = []

        if target.company:
            terms.append(target.company)

        if target.department:
            terms.append(target.department)

        return self._unique(terms)

    def _education_terms(self, target: TargetInput) -> list[str]:
        return self._unique(list(target.education))

    def _role_terms(self, target: TargetInput) -> list[str]:
        return self._unique([target.role] if target.role else [])

    def _contact_terms(self, target: TargetInput) -> list[str]:
        terms = list(target.contacts)

        if target.email:
            terms.append(target.email)

        if target.email_domain:
            terms.append(target.email_domain)

        return self._unique(terms)

    def _contains_any(self, text: str, terms: list[str]) -> bool:
        return any(term and term.lower() in text for term in terms)

    def _any_overlap(self, values_a: list[str], values_b: list[str]) -> bool:
        normalized_a = [self._normalize(value) for value in values_a]
        normalized_b = [self._normalize(value) for value in values_b]

        for left in normalized_a:
            for right in normalized_b:
                if left and right and (left in right or right in left):
                    return True

        return False

    def _canonical_url(self, url: str | None) -> str:
        if not url:
            return ""

        parsed = urlparse(url.strip())

        scheme = parsed.scheme or "https"
        netloc = parsed.netloc.lower()

        if netloc.startswith("www."):
            netloc = netloc[4:]

        path = parsed.path

        if path != "/" and path.endswith("/"):
            path = path[:-1]

        query = parsed.query if path.lower().endswith("profile.php") else ""

        return urlunparse((scheme, netloc, path, "", query, ""))

    def _normalize(self, value: str) -> str:
        return "".join(ch.lower() for ch in str(value) if ch.isalnum())

    def _unique(self, values: list[str]) -> list[str]:
        seen = set()
        output = []

        for value in values:
            if not value:
                continue

            cleaned = str(value).strip()

            if not cleaned:
                continue

            key = cleaned.lower()

            if key in seen:
                continue

            seen.add(key)
            output.append(cleaned)

        return output

    def _get(self, obj, field: str, default=None):
        if isinstance(obj, dict):
            return obj.get(field, default)

        return getattr(obj, field, default)