import re

from target_information_collector.evidence.evidence_normalizer import EvidenceNormalizer
from target_information_collector.shared.models import (
    CandidateStatus,
    EvidenceType,
    IdentityCandidate,
    PublicProfile,
    TargetInput,
)


class IdentityResolver:
    STRONG_SIGNALS = {
        "organization",
        "education",
        "location",
        "contact",
        "alias",
        "seeded_link",
    }

    ORG_STOPWORDS = {
        "di",
        "del",
        "della",
        "delle",
        "degli",
        "dei",
        "of",
        "the",
        "and",
        "for",
        "at",
        "in",
        "studi",
        "studies",
    }

    ORG_TRANSLATIONS = {
        "universita": "university",
        "università": "university",
        "univ": "university",
        "university": "university",
        "istituto": "institute",
        "institute": "institute",
        "politecnico": "polytechnic",
        "polytechnic": "polytechnic",
        "college": "college",
        "school": "school",
    }

    def __init__(self):
        self.normalizer = EvidenceNormalizer()

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
            status = self._status(score, positives, negatives)

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
                role=self._first_evidence_value(profile_evidence, EvidenceType.ROLE),
                company=self._first_evidence_value(profile_evidence, EvidenceType.ORGANIZATION),
                location=self._first_evidence_value(profile_evidence, EvidenceType.LOCATION),
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
            if profile.url:
                merged[self._canonical_url(profile.url)] = profile

        for candidate in candidates:
            url = self._get(candidate, "url")
            platform = self._get(candidate, "platform")

            if not url or not platform:
                continue

            key = self._canonical_url(url)
            confidence = float(self._get(candidate, "confidence", 0.0) or 0.0)
            existing = merged.get(key)

            if existing:
                if confidence > existing.confidence:
                    existing.confidence = confidence

                if not existing.username and self._get(candidate, "username"):
                    existing.username = self._get(candidate, "username")

                continue

            merged[key] = PublicProfile(
                platform=platform,
                url=url,
                username=self._get(candidate, "username"),
                confidence=confidence,
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

        if self._name_matches(target, profile, text, profile_evidence):
            positives.append("name")

        if self._username_matches_name(target, profile.username):
            positives.append("username")

        if self._organization_matches(target, text):
            positives.append("organization")

        if self._education_matches(target, text):
            positives.append("education")

        if self._contains_any(text, self._location_terms(target)):
            positives.append("location")

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

        if "name" not in positives and "username" not in positives and "seeded_link" not in positives:
            negatives.append("missing_identity_signal")

        if "organization" in positives or "education" in positives:
            return self._unique(negatives)

        candidate_orgs = [
            str(ev.value).strip()
            for ev in profile_evidence
            if self._etype(ev.evidence_type) in {
                EvidenceType.ORGANIZATION.value,
                EvidenceType.EDUCATION.value,
            }
            and ev.value
        ]

        target_orgs = self._organization_terms(target) + self._education_terms(target)

        if candidate_orgs and target_orgs:
            if not self._any_overlap(candidate_orgs, target_orgs):
                negatives.append("organization_conflict")

        candidate_locations = [
            str(ev.value).strip()
            for ev in profile_evidence
            if self._etype(ev.evidence_type) == EvidenceType.LOCATION.value and ev.value
        ]

        target_locations = self._location_terms(target)

        if candidate_locations and target_locations:
            if not self._any_overlap(candidate_locations, target_locations):
                negatives.append("location_conflict")

        return self._unique(negatives)

    def _score(self, positives: list[str], negatives: list[str]) -> float:
        weights = {
            "name": 0.30,
            "username": 0.12,
            "organization": 0.30,
            "education": 0.24,
            "location": 0.16,
            "role": 0.08,
            "contact": 0.35,
            "alias": 0.20,
            "seeded_link": 0.45,
        }

        score = sum(weights.get(item, 0.0) for item in positives)
        score -= 0.35 * len(negatives)

        if self._only_weak_identity(positives):
            score = min(score, 0.49)

        if "name" not in positives and "username" not in positives and "seeded_link" not in positives:
            score = min(score, 0.30)

        return round(max(0.0, min(score, 0.99)), 2)

    def _status(
        self,
        score: float,
        positives: list[str],
        negatives: list[str],
    ) -> CandidateStatus:
        if "missing_identity_signal" in negatives:
            return CandidateStatus.REJECTED

        if negatives and score < 0.70:
            return CandidateStatus.REJECTED

        has_identity_signal = any(
            item in positives
            for item in {"name", "username", "contact", "alias", "seeded_link"}
        )

        has_strong_signal = self._has_strong_signal(positives)

        if score >= 0.75 and has_identity_signal and has_strong_signal:
            return CandidateStatus.CONFIRMED

        if score >= 0.55 and has_identity_signal and has_strong_signal:
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

            raw_data = ev.raw_data or {}

            if isinstance(raw_data, dict):
                parts.extend(self._safe_raw_profile_strings(raw_data))

        return " ".join(str(part) for part in parts if part).lower()

    def _safe_raw_profile_strings(self, raw_data: dict) -> list[str]:
        values = []

        allowed_keys = {
            "name",
            "fullName",
            "fullname",
            "headline",
            "bio",
            "biography",
            "company",
            "current_company",
            "current_title",
            "location",
            "current_city",
            "hometown",
            "email",
            "intro",
            "work",
            "college",
            "school",
            "secondary_school",
            "title",
            "description",
        }

        ignored_keys = {
            "query",
            "phase",
            "source_url",
            "source_evidence",
            "result_class",
            "platform",
            "username",
            "url",
            "public_search_url",
            "derived_from",
            "field",
        }

        def visit(node, key: str = ""):
            if key in ignored_keys:
                return

            if isinstance(node, dict):
                for child_key, child_value in node.items():
                    visit(child_value, child_key)
            elif isinstance(node, list):
                for item in node:
                    visit(item, key)
            elif isinstance(node, str):
                if key in allowed_keys:
                    values.append(node)

        visit(raw_data)
        return values

    def _name_matches(
        self,
        target: TargetInput,
        profile: PublicProfile,
        text: str,
        profile_evidence,
    ) -> bool:
        target_name = target.full_name.lower()

        if self._username_matches_name(target, profile.username):
            return True

        for ev in profile_evidence:
            ev_text = self._join_text(ev.value, ev.title, ev.description).lower()

            if target_name in ev_text:
                return True

        return target_name in text

    def _username_matches_name(self, target: TargetInput, username: str | None) -> bool:
        if not username:
            return False

        if username.lower() in {"people", "profile.php", "public"}:
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
            self._value(ev.source) == "input"
            and ev.url
            and self._canonical_url(ev.url) == profile_key
            and self._etype(ev.evidence_type) == EvidenceType.PUBLIC_LINK.value
            for ev in profile_evidence
        )

    def _organization_matches(self, target: TargetInput, text: str) -> bool:
        if self._contains_any(text, self._organization_terms(target)):
            return True

        if not target.company:
            return False

        target_tokens = self._organization_tokens(target.company)
        text_tokens = self._organization_tokens(text)

        if not target_tokens or not text_tokens:
            return False

        overlap = target_tokens.intersection(text_tokens)

        if len(overlap) / len(target_tokens) >= 0.60:
            return True

        important_tokens = {
            token
            for token in target_tokens
            if token not in {"university", "institute", "college", "school", "polytechnic"}
        }

        return bool(important_tokens) and important_tokens.issubset(text_tokens)

    def _education_matches(self, target: TargetInput, text: str) -> bool:
        if self._contains_any(text, self._education_terms(target)):
            return True

        if target.company and self._looks_like_education_org(target.company):
            return self._organization_matches(target, text)

        return False

    def _organization_tokens(self, text: str) -> set[str]:
        normalized = self._normalize_text(text)
        raw_tokens = re.split(r"[^a-z0-9]+", normalized)
        tokens = set()

        for token in raw_tokens:
            if not token:
                continue

            if len(token) <= 2:
                continue

            if token in self.ORG_STOPWORDS:
                continue

            token = self.ORG_TRANSLATIONS.get(token, token)

            if token in self.ORG_STOPWORDS:
                continue

            tokens.add(token)

        return tokens

    def _location_terms(self, target: TargetInput) -> list[str]:
        terms = list(target.cities)

        if target.location:
            terms.append(target.location)

        return self._unique(terms)

    def _organization_terms(self, target: TargetInput) -> list[str]:
        terms = []

        if target.company:
            terms.append(target.company)
            terms.extend(self._organization_aliases(target.company))

        if target.department:
            terms.append(target.department)

        return self._unique(terms)

    def _education_terms(self, target: TargetInput) -> list[str]:
        terms = list(target.education)

        if target.company and self._looks_like_education_org(target.company):
            terms.append(target.company)
            terms.extend(self._organization_aliases(target.company))

        return self._unique(terms)

    def _role_terms(self, target: TargetInput) -> list[str]:
        return self._unique([target.role] if target.role else [])

    def _contact_terms(self, target: TargetInput) -> list[str]:
        terms = list(target.contacts)

        if target.email:
            terms.append(target.email)

        if target.email_domain:
            terms.append(target.email_domain)

        return self._unique(terms)

    def _organization_aliases(self, organization: str) -> list[str]:
        normalized = self._normalize_text(organization)
        tokens = [
            token
            for token in re.split(r"[^a-z0-9]+", normalized)
            if len(token) > 2 and token not in self.ORG_STOPWORDS
        ]

        aliases = []

        for token in tokens:
            translated = self.ORG_TRANSLATIONS.get(token, token)

            if translated not in self.ORG_STOPWORDS:
                aliases.append(translated)

        if "university" in aliases:
            important_tokens = [
                token
                for token in aliases
                if token not in {"university", "institute", "college", "school", "polytechnic"}
            ]

            if important_tokens:
                aliases.append(f"uni{important_tokens[-1]}")

        return self._unique(aliases)

    def _looks_like_education_org(self, value: str | None) -> bool:
        if not value:
            return False

        lowered = value.lower()

        return any(
            term in lowered
            for term in ["università", "universita", "university", "school", "college", "istituto"]
        )

    def _has_strong_signal(self, positives: list[str]) -> bool:
        return any(item in self.STRONG_SIGNALS for item in positives)

    def _only_weak_identity(self, positives: list[str]) -> bool:
        return bool(positives) and set(positives).issubset({"name", "username", "role"})

    def _first_evidence_value(self, profile_evidence, evidence_type: EvidenceType) -> str | None:
        for ev in profile_evidence:
            if self._etype(ev.evidence_type) == evidence_type.value and ev.value:
                return str(ev.value).strip()

        return None

    def _contains_any(self, text: str, terms: list[str]) -> bool:
        lower = text.lower()
        return any(term and term.lower() in lower for term in terms)

    def _any_overlap(self, values_a: list[str], values_b: list[str]) -> bool:
        normalized_a = [self._normalize(value) for value in values_a]
        normalized_b = [self._normalize(value) for value in values_b]

        for left in normalized_a:
            for right in normalized_b:
                if left and right and (left in right or right in left):
                    return True

        return False

    def _canonical_url(self, url: str | None) -> str:
        return self.normalizer.normalize_url(url) or ""

    def _normalize(self, value: str) -> str:
        return "".join(ch.lower() for ch in str(value) if ch.isalnum())

    def _normalize_text(self, value: str) -> str:
        normalized = str(value).lower()
        replacements = {
            "à": "a",
            "è": "e",
            "é": "e",
            "ì": "i",
            "ò": "o",
            "ù": "u",
        }

        for old, new in replacements.items():
            normalized = normalized.replace(old, new)

        return normalized

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

    def _etype(self, value) -> str:
        if value is None:
            return ""

        if hasattr(value, "value"):
            return str(value.value).lower().strip()

        return str(value).lower().strip()

    def _value(self, value) -> str:
        if value is None:
            return ""

        if hasattr(value, "value"):
            return str(value.value).lower().strip()

        return str(value).lower().strip()

    def _get(self, obj, field: str, default=None):
        if isinstance(obj, dict):
            return obj.get(field, default)

        return getattr(obj, field, default)

    def _join_text(self, *values) -> str:
        return " ".join(str(value) for value in values if value).strip()