import re
from urllib.parse import urlparse

from target_information_collector.core.identity_matcher import IdentityMatcher
from target_information_collector.core.profile_normalizer import ProfileNormalizer
from target_information_collector.shared.models import (
    CollectionResult,
    Evidence,
    EvidenceType,
    ProfileLink,
    TargetInput,
    TargetProfile,
    WebMention,
)
from target_information_collector.shared.text import (
    email_owner_matches,
    normalize,
    tokens,
)


class ProfileBuilder:
    MAX_SUMMARY_BIO_TOKENS = 40

    BIO_PLATFORM_PRIORITY = {
        "linkedin": 3,
        "github": 2,
        "facebook": 1,
        "instagram": 0,
    }

    def build(self, result: CollectionResult) -> TargetProfile:
        target = result.target
        social_links = self._verified_links(target, result.evidence)
        evidence = self._verified_evidence(result.evidence, social_links)
        return TargetProfile(
            name=target.full_name,
            summary=self._summary(target.role, evidence),
            organization=target.company or self._best(
                evidence, EvidenceType.COMPANY
            ),
            cities=self._locations(
                [*target.cities, *self._values(evidence, EvidenceType.LOCATION)]
            ),
            education=self._education(
                [
                    *target.education,
                    *self._values(evidence, EvidenceType.EDUCATION),
                ]
            ),
            emails=self._emails(target.full_name, target.email, evidence),
            social_links=social_links,
            mentions=self._mentions(
                target.full_name,
                result.evidence,
                identity_verified=bool(target.email) or any(
                    item.evidence_type == EvidenceType.EMAIL
                    and email_owner_matches(target.full_name, item.value)
                    for item in evidence
                ),
            ),
            tech_stack=self._unique(
                self._values(evidence, EvidenceType.TECH_STACK)
            ),
        )

    @staticmethod
    def _values(evidence: list[Evidence], evidence_type: EvidenceType) -> list[str]:
        return [item.value for item in evidence if item.evidence_type == evidence_type]

    @staticmethod
    def _best(evidence: list[Evidence], evidence_type: EvidenceType) -> str | None:
        candidates = [item for item in evidence if item.evidence_type == evidence_type]
        return max(candidates, key=lambda item: item.confidence).value if candidates else None

    @classmethod
    def _emails(
        cls,
        full_name: str,
        input_email: str | None,
        evidence: list[Evidence],
    ) -> list[str]:
        values = [input_email] if input_email else []
        values.extend(
            item.value
            for item in evidence
            if item.evidence_type == EvidenceType.EMAIL
            and (
                item.platform != "web"
                or email_owner_matches(full_name, item.value)
            )
        )
        return cls._unique(values)

    @classmethod
    def _verified_links(
        cls,
        target: TargetInput,
        evidence: list[Evidence],
    ) -> list[ProfileLink]:
        resolved = IdentityMatcher.resolve_profiles(target, evidence)
        return [
            ProfileLink(
                platform=item.platform,
                url=item.url,
                confidence=item.confidence,
            )
            for item in resolved.values()
            if item.url
        ]

    @staticmethod
    def _verified_evidence(
        evidence: list[Evidence],
        links: list[ProfileLink],
    ) -> list[Evidence]:
        allowed = {
            str(link.url).casefold().rstrip("/")
            for link in links
        }
        allowed.update(
            str(item.url).casefold().rstrip("/")
            for item in evidence
            if item.evidence_type == EvidenceType.WEB_MENTION and item.url
        )
        return [
            item
            for item in evidence
            if item.evidence_type not in {
                EvidenceType.PROFILE,
                EvidenceType.WEB_MENTION,
            }
            and (
                item.url is None
                or str(item.url).casefold().rstrip("/") in allowed
            )
        ]

    @classmethod
    def _summary(
        cls,
        target_role: str | None,
        evidence: list[Evidence],
    ) -> str | None:
        role = cls._clean_description(
            target_role or cls._best(evidence, EvidenceType.ROLE)
        )
        bio = cls._clean_description(cls._best_bio(evidence))
        summary: str | None
        if role and bio:
            normalized_role = normalize(role)
            normalized_bio = normalize(bio)
            if len(tokens(bio)) > cls.MAX_SUMMARY_BIO_TOKENS:
                summary = role
            elif normalized_role in normalized_bio:
                summary = bio
            elif normalized_bio in normalized_role:
                summary = role
            elif len(tokens(role) & tokens(bio)) >= 2:
                summary = bio if len(tokens(bio)) >= len(tokens(role)) else role
            elif cls._descriptions_overlap(role, bio):
                summary = bio if len(tokens(bio)) >= len(tokens(role)) else role
            else:
                summary = f"{role.rstrip('.')}. {bio}"
        else:
            summary = role or bio

        age = cls._age(evidence)
        if age and (not summary or not re.search(rf"\b{age}\b", summary)):
            age_fact = f"{age} years old"
            return f"{summary.rstrip('.')}. {age_fact}." if summary else age_fact
        return summary

    @classmethod
    def _best_bio(cls, evidence: list[Evidence]) -> str | None:
        candidates = [
            item
            for item in evidence
            if item.evidence_type == EvidenceType.BIO
            and cls._bio_information(item.value) > 0
        ]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda item: (
                cls.BIO_PLATFORM_PRIORITY.get(item.platform, 0),
                item.confidence,
                cls._bio_information(item.value),
                len(item.value),
            ),
        ).value

    @staticmethod
    def _clean_description(value: str | None) -> str | None:
        if not value:
            return None
        cleaned = re.sub(r"\s+@\s+.+$", "", value)
        cleaned = re.sub(r"@[\w.-]+", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        cleaned = re.sub(r"^[^\w]+", "", cleaned).strip()
        return cleaned or None

    @staticmethod
    def _bio_information(value: str) -> int:
        if normalize(value) in {
            "current", "in corso", "ongoing", "presente",
        }:
            return 0
        cleaned = re.sub(r"https?://\S+|@[\w.-]+", " ", value)
        cleaned = re.sub(
            r"\b\d{1,3}\s*(?:y/o|yo|years? old|anni)?\b",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
        return len(tokens(cleaned))

    @staticmethod
    def _descriptions_overlap(left: str, right: str) -> bool:
        left_compact = normalize(left).replace(" ", "")
        right_compact = normalize(right).replace(" ", "")
        return any(
            len(token) >= 6 and token in right_compact
            for token in tokens(left)
        ) or any(
            len(token) >= 6 and token in left_compact
            for token in tokens(right)
        )

    @staticmethod
    def _age(evidence: list[Evidence]) -> int | None:
        candidates: list[tuple[float, int]] = []
        pattern = re.compile(
            r"\b(?:age\s*:?[ ]*)?(\d{1,2})\s*(?:y/o|yo|years? old|anni)\b",
            re.IGNORECASE,
        )
        for item in evidence:
            if item.evidence_type != EvidenceType.BIO:
                continue
            match = pattern.search(item.value)
            if match and 13 <= (age := int(match.group(1))) <= 99:
                candidates.append((item.confidence, age))
        return max(candidates)[1] if candidates else None

    @classmethod
    def _mentions(
        cls,
        full_name: str,
        evidence: list[Evidence],
        *,
        identity_verified: bool,
    ) -> list[WebMention]:
        identity_verified = identity_verified or any(
            item.platform == "web"
            and item.evidence_type == EvidenceType.EMAIL
            and email_owner_matches(full_name, item.value)
            for item in evidence
        )
        if not identity_verified:
            return []
        output: dict[str, WebMention] = {}
        for item in evidence:
            if item.evidence_type != EvidenceType.WEB_MENTION or not item.url:
                continue
            if cls._profile_like_mention(str(item.url)):
                continue
            output[str(item.url).casefold().rstrip("/")] = WebMention(
                title=item.value,
                url=item.url,
                confidence=item.confidence,
            )
        return list(output.values())

    @staticmethod
    def _profile_like_mention(url: str) -> bool:
        sections = {
            part.casefold()
            for part in urlparse(url).path.split("/")
            if part
        }
        return bool(sections & {
            "author", "authors", "citations", "people", "person",
            "profile", "profiles",
        })

    @staticmethod
    def _unique(values: list[str]) -> list[str]:
        output: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = value.strip()
            key = cleaned.casefold()
            if cleaned and key not in seen:
                output.append(cleaned)
                seen.add(key)
        return output

    @classmethod
    def _locations(cls, values: list[str]) -> list[str]:
        output: dict[str, tuple[tuple[bool, int, int], str]] = {}
        for value in ProfileNormalizer.valid_locations(cls._unique(values)):
            locality = normalize(value.split(",", 1)[0])
            specific = re.sub(
                r"^greater\s+|\s+metropolitan\s+area$", "", locality
            ).strip()
            is_broad = specific != locality
            quality = (not is_broad, value.count(","), len(value))
            key = specific or locality
            if key not in output or quality > output[key][0]:
                output[key] = (quality, value)
        return [value for _, value in output.values()]

    @classmethod
    def _education(cls, values: list[str]) -> list[str]:
        output: list[str] = []
        for value in cls._unique(values):
            for index, current in enumerate(output):
                if cls._education_redundant(value, current):
                    break
                if cls._education_redundant(current, value):
                    output[index] = value
                    break
            else:
                output.append(value)
        return output

    @staticmethod
    def _education_redundant(left: str, right: str) -> bool:
        normalized = normalize(left)
        if normalized in {
            normalize(part) for part in right.split(" — ")
        }:
            return True
        left_tokens, right_tokens = tokens(left), tokens(right)
        return len(left_tokens) >= 2 and left_tokens < right_tokens
