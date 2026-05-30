import json
import re
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse, urlunparse

import gender_guesser.detector as gender

from target_information_collector.shared.models import (
    StructuredProfile,
    StructuredPublicLink,
    TargetProfile,
)


class StructuredProfileBuilder:
    MIN_DATA_CONFIDENCE = 0.55
    MIN_INSTITUTIONAL_CONFIDENCE = 0.55

    GOOD_STATUSES = {"confirmed"}

    BAD_SOCIAL_PARTS = {
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

    NEGATIVE_VALUES = {
        "no schools/universities to show",
        "no workplaces to show",
        "no places to show",
        "none",
        "null",
    }

    DOCUMENT_EXTENSIONS = {
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
    }

    CONTEXT_STOPWORDS = {
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
        "universita",
        "università",
        "university",
    }

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
        confirmed_urls = self._confirmed_urls(raw_profile)

        return StructuredProfile(
            name=raw_profile.target.full_name,
            gender=self._infer_gender(raw_profile),
            birth_date=raw_profile.target.birth_date,
            position=self._position(raw_profile, confirmed_urls),
            organization=self._organization(raw_profile, confirmed_urls),
            cities=self._cities(raw_profile, confirmed_urls),
            education=self._education(raw_profile, confirmed_urls),
            contacts=self._contacts(raw_profile, confirmed_urls),
            public_links=self._public_links(raw_profile),
            tech_stack=self._tech_stack(raw_profile.tech_stack),
        )

    def _confirmed_urls(self, raw_profile: TargetProfile) -> set[str]:
        return {
            self._canonical_url(candidate.profile_url)
            for candidate in raw_profile.identity_candidates
            if self._status(candidate.status) == "confirmed"
        }

    def _position(self, raw_profile: TargetProfile, confirmed_urls: set[str]) -> str | None:
        values = [raw_profile.target.role]

        for candidate in raw_profile.identity_candidates:
            if self._candidate_usable(candidate) and candidate.role:
                values.append(candidate.role)

        for ev in raw_profile.evidence:
            if not self._evidence_from_confirmed_source(ev, confirmed_urls):
                continue

            ev_type = self._etype(ev.evidence_type)

            if ev_type == "role" and ev.value and ev.confidence >= self.MIN_DATA_CONFIDENCE:
                values.append(ev.value)

            if ev_type == "profile" and ev.confidence >= self.MIN_DATA_CONFIDENCE:
                values.append(self._position_from_profile_text(raw_profile, ev))

        return self._first(values)

    def _organization(self, raw_profile: TargetProfile, confirmed_urls: set[str]) -> str | None:
        values = [raw_profile.target.company]

        for candidate in raw_profile.identity_candidates:
            if self._candidate_usable(candidate) and candidate.company:
                values.append(candidate.company)

        for ev in raw_profile.evidence:
            if not self._evidence_from_confirmed_source(ev, confirmed_urls):
                continue

            if self._etype(ev.evidence_type) == "organization":
                if ev.value and ev.confidence >= self.MIN_DATA_CONFIDENCE:
                    values.append(ev.value)

        return self._first(values)

    def _cities(self, raw_profile: TargetProfile, confirmed_urls: set[str]) -> list[str]:
        values = [raw_profile.target.location]
        values.extend(raw_profile.target.cities)

        for candidate in raw_profile.identity_candidates:
            if self._candidate_usable(candidate) and candidate.location:
                values.append(candidate.location)

        for ev in raw_profile.evidence:
            if not self._evidence_from_confirmed_source(ev, confirmed_urls):
                continue

            if self._etype(ev.evidence_type) == "location":
                if ev.value and ev.confidence >= self.MIN_DATA_CONFIDENCE:
                    values.append(ev.value)

            if self._etype(ev.evidence_type) == "profile" and ev.confidence >= self.MIN_DATA_CONFIDENCE:
                values.extend(self._locations_from_text(ev.description))
                values.extend(self._locations_from_text(ev.value))

        return self._clean_locations(values)

    def _education(self, raw_profile: TargetProfile, confirmed_urls: set[str]) -> list[str]:
        values = list(raw_profile.target.education)

        if self._looks_like_school(raw_profile.target.company):
            values.append(raw_profile.target.company)

        for ev in raw_profile.evidence:
            if not self._evidence_from_confirmed_source(ev, confirmed_urls):
                continue

            if self._etype(ev.evidence_type) == "education":
                if ev.value and ev.confidence >= self.MIN_DATA_CONFIDENCE:
                    values.append(ev.value)

            if ev.confidence >= self.MIN_DATA_CONFIDENCE:
                values.extend(self._education_from_raw(ev.raw_data))

        return self._clean_education(values)

    def _contacts(self, raw_profile: TargetProfile, confirmed_urls: set[str]) -> list[str]:
        values = list(raw_profile.target.contacts)

        if raw_profile.target.email:
            values.append(raw_profile.target.email)

        if raw_profile.contact and raw_profile.contact.email:
            values.append(raw_profile.contact.email)

        for ev in raw_profile.evidence:
            if not self._evidence_from_confirmed_source(ev, confirmed_urls):
                continue

            if self._etype(ev.evidence_type) == "email":
                if ev.value and ev.confidence >= self.MIN_DATA_CONFIDENCE:
                    values.append(ev.value)

        return self._unique(values)

    def _public_links(self, raw_profile: TargetProfile) -> list[StructuredPublicLink]:
        links = []

        status_by_url = {
            self._canonical_url(candidate.profile_url): self._status(candidate.status)
            for candidate in raw_profile.identity_candidates
        }

        context_by_url = {
            self._canonical_url(candidate.profile_url): candidate.positive_evidence
            for candidate in raw_profile.identity_candidates
        }

        for profile in raw_profile.public_profiles:
            if not self._valid_profile_url(profile.url):
                continue

            key = self._canonical_url(profile.url)

            if status_by_url.get(key) != "confirmed":
                continue

            links.append(
                StructuredPublicLink(
                    url=profile.url,
                    platform=profile.platform,
                    status="confirmed",
                    matched_context=context_by_url.get(key, []),
                )
            )

        for ev in raw_profile.evidence:
            ev_type = self._etype(ev.evidence_type)

            if ev_type not in {"profile", "public_link", "web_mention"}:
                continue

            if not self._valid_profile_url(ev.url):
                continue

            key = self._canonical_url(ev.url)
            resolver_status = status_by_url.get(key)

            if resolver_status == "confirmed":
                links.append(
                    StructuredPublicLink(
                        url=ev.url,
                        platform=ev.platform or (ev.raw_data or {}).get("platform") or self._platform_from_url(ev.url),
                        status="confirmed",
                        context=None,
                        matched_context=context_by_url.get(key, []),
                    )
                )
                continue

            if self._is_confirmed_institutional_mention(raw_profile, ev):
                links.append(
                    StructuredPublicLink(
                        url=ev.url,
                        platform=ev.platform or "institutional",
                        status="confirmed",
                        context=ev.value or ev.title or ev.description,
                        matched_context=["institutional_reference"],
                    )
                )

        return self._deduplicate_links(links)

    def _is_confirmed_institutional_mention(self, raw_profile: TargetProfile, ev) -> bool:
        if self._etype(ev.evidence_type) != "web_mention":
            return False

        if ev.confidence < self.MIN_INSTITUTIONAL_CONFIDENCE:
            return False

        if not ev.url:
            return False

        if self._is_document_url(ev.url):
            return False

        platform = (ev.platform or "").lower().strip()
        result_class = str((ev.raw_data or {}).get("result_class") or "").lower().strip()

        if platform not in {"institutional", "web"} and result_class != "institutional_reference":
            return False

        text = self._join_text(ev.value, ev.title, ev.description, ev.url).lower()
        target_name = raw_profile.target.full_name.lower()

        if target_name not in text:
            return False

        context_terms = self._institutional_context_terms(raw_profile)

        if not context_terms:
            return True

        return any(term in text for term in context_terms)

    def _institutional_context_terms(self, raw_profile: TargetProfile) -> list[str]:
        values = []

        values.append(raw_profile.target.company)
        values.append(raw_profile.target.location)
        values.extend(raw_profile.target.cities)
        values.extend(raw_profile.target.education)
        values.extend(raw_profile.target.aliases)

        terms = []

        for value in values:
            terms.extend(self._significant_tokens(value))

        return self._unique(terms)

    def _evidence_from_confirmed_source(self, ev, confirmed_urls: set[str]) -> bool:
        if not ev.url:
            return False

        key = self._canonical_url(ev.url)

        if key not in confirmed_urls:
            return False

        platform = (ev.platform or "").lower().strip()

        return platform in {"github", "linkedin", "instagram", "facebook"}

    def _tech_stack(self, raw_stack: list[str]) -> list[str]:
        return self._unique([item.strip() for item in raw_stack if item])

    def _candidate_usable(self, candidate) -> bool:
        return self._status(candidate.status) == "confirmed"

    def _position_from_profile_text(self, raw_profile: TargetProfile, ev) -> str | None:
        title = ev.title or ""

        if " - " in title:
            candidate = title.split(" - ", 1)[1].strip(" .…")
            if candidate:
                return candidate

        description = ev.description or ""
        organization = raw_profile.target.company

        if organization and organization.lower() in description.lower():
            before_org = description.split(organization, 1)[0]
            pieces = [part.strip(" .·") for part in before_org.split(".") if part.strip()]

            if len(pieces) >= 2:
                return pieces[-1]

        return None

    def _locations_from_text(self, text: str | None) -> list[str]:
        if not text:
            return []

        values = []
        sentences = [part.strip() for part in re.split(r"[.;]", text) if part.strip()]

        for sentence in sentences:
            lowered = sentence.lower()

            if not any(term in lowered for term in ["italia", "italy"]):
                continue

            if "università" in lowered or "university" in lowered:
                continue

            values.append(sentence)

            for piece in sentence.split(","):
                cleaned = piece.strip()
                if cleaned:
                    values.append(cleaned)

        return self._unique(values)

    def _education_from_raw(self, raw_data: Any) -> list[str]:
        values = []

        def visit(node: Any, key: str = ""):
            if isinstance(node, dict):
                for child_key, child_value in node.items():
                    visit(child_value, child_key)
            elif isinstance(node, list):
                for item in node:
                    visit(item, key)
            elif isinstance(node, str) and key in {
                "education",
                "college",
                "school",
                "university",
                "secondary_school",
            }:
                values.append(node)

        visit(raw_data)
        return values

    def _clean_locations(self, values: list[Any]) -> list[str]:
        output = []

        for value in values:
            if not value:
                continue

            cleaned = str(value).strip()

            if not cleaned:
                continue

            lowered = cleaned.lower()

            if len(cleaned) > 80:
                continue

            if any(bad in lowered for bad in ["profile ·", "digital creator", "direzione nazionale"]):
                continue

            output.append(cleaned)

        return self._unique(output)

    def _clean_education(self, values: list[str]) -> list[str]:
        output = []

        for value in values:
            if not value:
                continue

            cleaned = str(value).strip()

            if not cleaned:
                continue

            if cleaned.lower() in self.NEGATIVE_VALUES:
                continue

            output.append(cleaned)

        return self._unique(output)

    def _looks_like_school(self, value: str | None) -> bool:
        if not value:
            return False

        lowered = value.lower()

        return any(
            term in lowered
            for term in ["università", "university", "school", "college", "istituto"]
        )

    def _valid_profile_url(self, url: str | None) -> bool:
        if not url:
            return False

        if self._is_bad_social_url(url):
            return False

        return True

    def _is_document_url(self, url: str | None) -> bool:
        if not url:
            return False

        lowered = url.lower()
        return any(ext in lowered for ext in self.DOCUMENT_EXTENSIONS)

    def _is_bad_social_url(self, url: str | None) -> bool:
        if not url:
            return False

        parsed = urlparse(url.lower())
        domain = parsed.netloc.lower()
        parts = [part for part in parsed.path.split("/") if part]

        if "facebook.com" in domain or "instagram.com" in domain:
            return any(part in self.BAD_SOCIAL_PARTS for part in parts)

        return False

    def _deduplicate_links(self, links: list[StructuredPublicLink]) -> list[StructuredPublicLink]:
        by_key = {}

        for link in links:
            key = (self._canonical_url(link.url), link.platform)

            if key not in by_key:
                by_key[key] = link
                continue

            if self._rank(link.status) > self._rank(by_key[key].status):
                by_key[key] = link

        return list(by_key.values())

    def _rank(self, status) -> int:
        ranks = {
            "confirmed": 4,
            "probable": 3,
            "candidate": 2,
            "mention": 1,
        }

        return ranks.get(self._status(status), 0)

    def _infer_gender(self, raw_profile: TargetProfile) -> str | None:
        if raw_profile.target.gender:
            return raw_profile.target.gender

        first_name = raw_profile.target.full_name.split()[0]
        guessed = self.gender_detector.get_gender(first_name)

        return {
            "male": "Male",
            "mostly_male": "Male",
            "female": "Female",
            "mostly_female": "Female",
        }.get(guessed)

    def _platform_from_url(self, url: str | None) -> str:
        if not url:
            return "web"

        domain = urlparse(url.lower()).netloc

        if "linkedin.com" in domain:
            return "linkedin"

        if "facebook.com" in domain:
            return "facebook"

        if "instagram.com" in domain:
            return "instagram"

        if "github.com" in domain:
            return "github"

        return "web"

    def _canonical_url(self, url: str | None) -> str:
        if not url:
            return ""

        parsed = urlparse(url.strip())

        scheme = parsed.scheme or "https"
        netloc = parsed.netloc.lower()

        if netloc in {
            "www.facebook.com",
            "web.facebook.com",
            "m.facebook.com",
            "mbasic.facebook.com",
        }:
            netloc = "facebook.com"
        elif netloc.startswith("www."):
            netloc = netloc[4:]

        path = parsed.path

        if path != "/" and path.endswith("/"):
            path = path[:-1]

        query = parsed.query if path.lower().endswith("profile.php") else ""

        return urlunparse((scheme, netloc, path, "", query, ""))

    def _etype(self, evidence_type) -> str:
        if evidence_type is None:
            return ""

        if hasattr(evidence_type, "value"):
            return str(evidence_type.value).lower().strip()

        return str(evidence_type).lower().strip()

    def _status(self, status) -> str:
        if status is None:
            return ""

        if hasattr(status, "value"):
            return str(status.value).lower().strip()

        return str(status).lower().strip()

    def _first(self, values: list[Any]) -> str | None:
        values = self._unique(values)
        return values[0] if values else None

    def _unique(self, values: list[Any]) -> list[str]:
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

    def _significant_tokens(self, value: str | None) -> list[str]:
        if not value:
            return []

        normalized = self._normalize_text(value)
        tokens = re.split(r"[^a-z0-9]+", normalized)

        output = []

        for token in tokens:
            if len(token) <= 2:
                continue

            if token in self.CONTEXT_STOPWORDS:
                continue

            output.append(token)

        return output

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

    def _join_text(self, *values: Any) -> str:
        return " ".join(str(value) for value in values if value).strip()