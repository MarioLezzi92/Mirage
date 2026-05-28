import json
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse, urlunparse

import gender_guesser.detector as gender

from target_information_collector.shared.models import (
    CandidateStatus,
    StructuredProfile,
    StructuredPublicLink,
    TargetProfile,
)


class StructuredProfileBuilder:
    def __init__(self):
        self.profile_dir = Path(__file__).resolve().parent.parent / "data" / "profiles"
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.gender_detector = gender.Detector()

    def build_from_raw(self, raw_data_dict: Dict[str, Any]) -> str:
        raw_profile = TargetProfile(**raw_data_dict)
        structured = self.build(raw_profile)

        filename = f"{raw_profile.target.full_name.lower().replace(' ', '-')}.json"

        with open(self.profile_dir / filename, "w", encoding="utf-8") as file:
            json.dump(
                structured.model_dump(mode="json"),
                file,
                indent=2,
                ensure_ascii=False,
            )

        return filename

    def build(self, raw_profile: TargetProfile) -> StructuredProfile:
        usable_urls = self._usable_urls(raw_profile)

        return StructuredProfile(
            name=raw_profile.target.full_name,
            gender=self._infer_gender(raw_profile),
            birth_date=raw_profile.target.birth_date,
            position=self._position(raw_profile, usable_urls),
            organization=self._organization(raw_profile, usable_urls),
            cities=self._cities(raw_profile, usable_urls),
            education=self._education(raw_profile, usable_urls),
            contacts=self._contacts(raw_profile),
            public_links=self._public_links(raw_profile),
            tech_stack=self._tech_stack(raw_profile.tech_stack),
        )

    def _usable_urls(self, raw_profile: TargetProfile) -> set[str]:
        usable_statuses = {
            CandidateStatus.CONFIRMED,
            CandidateStatus.PROBABLE,
            "confirmed",
            "probable",
        }

        return {
            self._canonical_url(candidate.profile_url)
            for candidate in raw_profile.identity_candidates
            if candidate.status in usable_statuses
        }

    def _position(self, raw_profile: TargetProfile, usable_urls: set[str]) -> str | None:
        for candidate in raw_profile.identity_candidates:
            if self._canonical_url(candidate.profile_url) in usable_urls and candidate.role:
                return candidate.role.strip()

        return raw_profile.target.role

    def _organization(self, raw_profile: TargetProfile, usable_urls: set[str]) -> str | None:
        for candidate in raw_profile.identity_candidates:
            if self._canonical_url(candidate.profile_url) in usable_urls and candidate.company:
                return candidate.company.strip()

        return raw_profile.target.company

    def _cities(self, raw_profile: TargetProfile, usable_urls: set[str]) -> list[str]:
        values = []

        if raw_profile.target.location:
            values.append(raw_profile.target.location)

        values.extend(raw_profile.target.cities)

        for candidate in raw_profile.identity_candidates:
            if self._canonical_url(candidate.profile_url) in usable_urls and candidate.location:
                values.append(candidate.location)

        for ev in raw_profile.evidence:
            if not ev.url or self._canonical_url(ev.url) not in usable_urls:
                continue

            if str(ev.evidence_type) == "location" and ev.value:
                values.append(ev.value)

        return self._unique(values)

    def _education(self, raw_profile: TargetProfile, usable_urls: set[str]) -> list[str]:
        values = list(raw_profile.target.education)

        for ev in raw_profile.evidence:
            if not ev.url or self._canonical_url(ev.url) not in usable_urls:
                continue

            if str(ev.evidence_type) == "education" and ev.value:
                values.append(ev.value)

        return self._unique(values)

    def _contacts(self, raw_profile: TargetProfile) -> list[str]:
        values = list(raw_profile.target.contacts)

        if raw_profile.target.email:
            values.append(raw_profile.target.email)

        if raw_profile.contact and raw_profile.contact.email:
            values.append(raw_profile.contact.email)

        return self._unique(values)

    def _public_links(self, raw_profile: TargetProfile) -> list[StructuredPublicLink]:
        links = []

        status_by_url = {
            self._canonical_url(candidate.profile_url): candidate.status
            for candidate in raw_profile.identity_candidates
        }

        context_by_url = {
            self._canonical_url(candidate.profile_url): candidate.positive_evidence
            for candidate in raw_profile.identity_candidates
        }

        for profile in raw_profile.public_profiles:
            if self._is_bad_social_url(profile.url):
                continue

            key = self._canonical_url(profile.url)
            status = status_by_url.get(key, CandidateStatus.CANDIDATE)

            if status in {
                CandidateStatus.REJECTED, "REJECTED",
                CandidateStatus.CANDIDATE, "CANDIDATE",
                "rejected", "candidate",
                }:
                continue

            links.append(
                StructuredPublicLink(
                    url=profile.url,
                    platform=profile.platform,
                    status=status,
                    matched_context=context_by_url.get(key, []),
                )
            )

        for ev in raw_profile.evidence:
            if str(ev.evidence_type) != "web_mention":
                continue

            if ev.confidence <= 0.55 or not ev.url:
                continue

            if self._is_bad_social_url(ev.url):
                continue

            links.append(
                StructuredPublicLink(
                    url=ev.url,
                    platform=(ev.raw_data or {}).get("platform") or ev.platform or "web",
                    status="mention",
                    context=ev.value,
                    matched_context=[],
                )
            )

        return self._deduplicate_links(links)

    def _tech_stack(self, raw_stack: list[str]) -> list[str]:
        return self._unique([item.strip() for item in raw_stack if item])

    def _is_bad_social_url(self, url: str) -> bool:
        if not url:
            return False

        parsed = urlparse(url.lower())
        domain = parsed.netloc.lower()
        parts = [part for part in parsed.path.split("/") if part]

        if not parts:
            return False

        bad_parts = {
            "photos",
            "photo",
            "posts",
            "post",
            "videos",
            "watch",
            "reel",
            "reels",
            "p",
            "stories",
            "story",
            "groups",
            "pages",
            "events",
        }

        if "facebook.com" in domain or "instagram.com" in domain:
            return any(part in bad_parts for part in parts)

        return False

    def _deduplicate_links(self, links: list[StructuredPublicLink]) -> list[StructuredPublicLink]:
        seen = set()
        output = []

        for link in links:
            key = (self._canonical_url(link.url), link.platform)

            if key in seen:
                continue

            seen.add(key)
            output.append(link)

        return output

    def _infer_gender(self, raw_profile: TargetProfile) -> str | None:
        if raw_profile.target.gender:
            return raw_profile.target.gender

        first_name = raw_profile.target.full_name.split()[0]
        guessed = self.gender_detector.get_gender(first_name)

        mapping = {
            "male": "Male",
            "mostly_male": "Male",
            "female": "Female",
            "mostly_female": "Female",
        }

        return mapping.get(guessed)

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