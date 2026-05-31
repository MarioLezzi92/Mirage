import json
import re
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse, urlunparse

import gender_guesser.detector as gender

from target_information_collector.shared.models import (
    StructuredProfile,
    StructuredPublicLink,
    StructuredPublicMention,
    TargetProfile,
)


class StructuredProfileBuilder:
    MIN_DATA_CONFIDENCE = 0.55
    MIN_PUBLIC_MENTION_CONFIDENCE = 0.70

    SOCIAL_PLATFORMS = {"linkedin", "github", "facebook", "instagram"}

    BAD_SOCIAL_PARTS = {
        "photos", "photo", "videos", "video", "watch", "reel", "reels",
        "p", "stories", "story", "groups", "events", "share", "permalink",
    }

    BAD_WEB_DOMAINS = {
        "corriere.it",
        "baritoday.it",
        "rainews.it",
        "telenorba.it",
        "lagazzettadelmezzogiorno.it",
        "ledicola.it",
        "polaris.irpi.cnr.it",
        "vatican.va",
        "x.com",
        "twitter.com",
    }

    NEGATIVE_VALUES = {
        "no schools/universities to show",
        "no workplaces to show",
        "no places to show",
        "none",
        "null",
    }

    DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}

    CONTEXT_STOPWORDS = {
        "di", "del", "della", "delle", "degli", "dei",
        "of", "the", "and", "for", "at", "in",
        "studi", "studies", "universita", "università", "university",
    }

    TECHNICAL_CONTEXT_TERMS = {
        "machine learning", "cloud", "informatica", "computer science",
        "github", "repository", "software", "cyber", "cybersecurity",
        "data science", "dipartimento", "unisa",
    }

    SOCIAL_IDENTITY_SIGNALS = {"username", "seeded_link", "contact", "alias"}

    def __init__(self):
        self.profile_dir = Path(__file__).resolve().parent.parent / "data" / "profiles"
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.gender_detector = gender.Detector()

    def build_from_raw(self, raw_data_dict: Dict[str, Any]) -> str:
        raw_profile = TargetProfile(**raw_data_dict)
        structured = self.build(raw_profile)
        filename = f"{raw_profile.target.full_name.lower().replace(' ', '-')}.json"

        with open(self.profile_dir / filename, "w", encoding="utf-8") as file:
            json.dump(structured.model_dump(mode="json"), file, indent=2, ensure_ascii=False)

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
            public_mentions=self._public_mentions(raw_profile),
            tech_stack=self._tech_stack(raw_profile.tech_stack),
        )

    def _confirmed_urls(self, raw_profile: TargetProfile) -> set[str]:
        return {
            self._canonical_url(candidate.profile_url)
            for candidate in raw_profile.identity_candidates
            if self._status(candidate.status) == "confirmed"
        }

    def _confirmed_profile_urls(self, raw_profile: TargetProfile) -> set[str]:
        urls = set()

        for candidate in raw_profile.identity_candidates:
            if self._status(candidate.status) != "confirmed":
                continue

            platform = candidate.platform or self._platform_from_url(candidate.profile_url)
            context = candidate.positive_evidence or []

            if self._valid_confirmed_profile_url(candidate.profile_url, platform, context):
                urls.add(self._canonical_url(candidate.profile_url))

        return urls

    def _candidate_context_by_url(self, raw_profile: TargetProfile) -> dict[str, list[str]]:
        return {
            self._canonical_url(candidate.profile_url): candidate.positive_evidence
            for candidate in raw_profile.identity_candidates
        }

    def _candidate_status_by_url(self, raw_profile: TargetProfile) -> dict[str, str]:
        return {
            self._canonical_url(candidate.profile_url): self._status(candidate.status)
            for candidate in raw_profile.identity_candidates
        }

    def _position(self, raw_profile: TargetProfile, confirmed_urls: set[str]) -> str | None:
        values = [raw_profile.target.role]

        for candidate in raw_profile.identity_candidates:
            if self._status(candidate.status) == "confirmed" and candidate.role:
                values.append(candidate.role)

        for ev in raw_profile.evidence:
            if not self._evidence_from_confirmed_source(ev, confirmed_urls):
                continue

            etype = self._etype(ev.evidence_type)

            if etype == "role" and ev.value and ev.confidence >= self.MIN_DATA_CONFIDENCE:
                values.append(ev.value)
            elif etype == "profile" and ev.confidence >= self.MIN_DATA_CONFIDENCE:
                values.append(self._position_from_profile_text(raw_profile, ev))

        return self._first(values)

    def _organization(self, raw_profile: TargetProfile, confirmed_urls: set[str]) -> str | None:
        values = [raw_profile.target.company]

        for candidate in raw_profile.identity_candidates:
            if self._status(candidate.status) == "confirmed" and candidate.company:
                values.append(candidate.company)

        for ev in raw_profile.evidence:
            if (
                self._evidence_from_confirmed_source(ev, confirmed_urls)
                and self._etype(ev.evidence_type) == "organization"
                and ev.value
                and ev.confidence >= self.MIN_DATA_CONFIDENCE
            ):
                values.append(ev.value)

        return self._first(values)

    def _cities(self, raw_profile: TargetProfile, confirmed_urls: set[str]) -> list[str]:
        values = [raw_profile.target.location, *raw_profile.target.cities]

        for candidate in raw_profile.identity_candidates:
            if self._status(candidate.status) == "confirmed" and candidate.location:
                values.append(candidate.location)

        for ev in raw_profile.evidence:
            if not self._evidence_from_confirmed_source(ev, confirmed_urls):
                continue

            etype = self._etype(ev.evidence_type)

            if etype == "location" and ev.value and ev.confidence >= self.MIN_DATA_CONFIDENCE:
                values.append(ev.value)
            elif etype == "profile" and ev.confidence >= self.MIN_DATA_CONFIDENCE:
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

            if (
                self._etype(ev.evidence_type) == "education"
                and ev.value
                and ev.confidence >= self.MIN_DATA_CONFIDENCE
            ):
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
            if (
                self._evidence_from_confirmed_source(ev, confirmed_urls)
                and self._etype(ev.evidence_type) == "email"
                and ev.value
                and ev.confidence >= self.MIN_DATA_CONFIDENCE
            ):
                values.append(ev.value)

        return self._unique(values)

    def _public_links(self, raw_profile: TargetProfile) -> list[StructuredPublicLink]:
        links = []
        status_by_url = self._candidate_status_by_url(raw_profile)
        context_by_url = self._candidate_context_by_url(raw_profile)

        def add_link(url: str | None, platform: str | None):
            key = self._canonical_url(url)
            context = context_by_url.get(key, [])

            if status_by_url.get(key) != "confirmed":
                return
            if not self._valid_confirmed_profile_url(url, platform, context):
                return

            links.append(
                StructuredPublicLink(
                    url=url,
                    platform=platform or self._platform_from_url(url),
                    status="confirmed",
                    context=None,
                    matched_context=context,
                )
            )

        for profile in raw_profile.public_profiles:
            add_link(profile.url, profile.platform)

        for ev in raw_profile.evidence:
            if self._etype(ev.evidence_type) not in {"profile", "public_link"}:
                continue

            platform = ev.platform or (ev.raw_data or {}).get("platform") or self._platform_from_url(ev.url)
            add_link(ev.url, platform)

        return self._deduplicate_links(links)

    def _public_mentions(self, raw_profile: TargetProfile) -> list[StructuredPublicMention]:
        confirmed_profile_urls = self._confirmed_profile_urls(raw_profile)
        mentions = []

        for ev in raw_profile.evidence:
            if self._etype(ev.evidence_type) not in {"web_mention", "public_link"}:
                continue
            if not self._is_useful_public_mention(raw_profile, ev, confirmed_profile_urls):
                continue

            mentions.append(
                StructuredPublicMention(
                    url=ev.url,
                    platform=ev.platform or self._platform_from_url(ev.url),
                    mention_type=self._mention_type(ev),
                    confidence=round(float(ev.confidence or 0.0), 2),
                    context=self._mention_context(ev),
                    reason=self._mention_reason(raw_profile, ev),
                )
            )

        return self._deduplicate_mentions(mentions)

    def _is_useful_public_mention(self, raw_profile: TargetProfile, ev, confirmed_profile_urls: set[str]) -> bool:
        if not ev.url:
            return False
        if self._canonical_url(ev.url) in confirmed_profile_urls:
            return False
        if ev.confidence < self.MIN_PUBLIC_MENTION_CONFIDENCE:
            return False
        if self._is_document_url(ev.url) or self._is_bad_web_domain(ev.url):
            return False

        platform = (ev.platform or self._platform_from_url(ev.url)).lower().strip()

        if platform in {"instagram", "facebook"} and self._is_noisy_social_mention(ev.url):
            return False

        text = self._join_text(ev.value, ev.title, ev.description, ev.url).lower()
        return self._contains_target_name(raw_profile, text) and self._has_relevant_context(raw_profile, text)

    def _has_relevant_context(self, raw_profile: TargetProfile, text: str) -> bool:
        if self._contains_company(raw_profile, text):
            return True

        matched_terms = [term for term in self._institutional_context_terms(raw_profile) if term in text]
        return len(matched_terms) >= 2 or any(term in text for term in self.TECHNICAL_CONTEXT_TERMS)

    def _contains_target_name(self, raw_profile: TargetProfile, text: str) -> bool:
        full_name = raw_profile.target.full_name.lower()

        if full_name in text:
            return True

        parts = [part.lower() for part in raw_profile.target.full_name.split() if len(part) > 2]
        return bool(parts) and all(part in text for part in parts)

    def _mention_type(self, ev) -> str:
        url = ev.url or ""
        platform = (ev.platform or self._platform_from_url(url)).lower().strip()
        parts = [part for part in urlparse(url.lower()).path.split("/") if part]

        if platform == "linkedin" and parts and parts[0] == "posts":
            return "linkedin_post"
        if platform == "github" and len(parts) >= 2:
            return "github_repository_reference"
        if platform == "facebook":
            if any(part in {"posts", "photos", "videos"} for part in parts):
                return "facebook_post"
            return "facebook_page_or_context"
        if platform == "institutional":
            return "institutional_reference"

        return "public_mention"

    def _mention_context(self, ev) -> str | None:
        return ev.value or ev.title or ev.description

    def _mention_reason(self, raw_profile: TargetProfile, ev) -> str:
        text = self._join_text(ev.value, ev.title, ev.description, ev.url).lower()
        reasons = []

        if self._contains_target_name(raw_profile, text):
            reasons.append("contains_target_name")
        if self._contains_company(raw_profile, text):
            reasons.append("contains_target_organization")
        if any(term in text for term in {"machine learning", "data science", "cloud"}):
            reasons.append("contains_technical_context")
        if ev.platform:
            reasons.append(f"source_platform:{ev.platform}")

        return ", ".join(reasons) if reasons else "useful_public_mention"

    def _valid_confirmed_profile_url(self, url: str | None, platform: str | None, context: list[str]) -> bool:
        if not url:
            return False

        platform = (platform or self._platform_from_url(url)).lower().strip()

        if platform == "linkedin":
            return self._is_linkedin_profile(url)
        if platform == "github":
            return self._is_github_user_profile(url)
        if platform == "instagram":
            return self._is_instagram_profile(url) and self._has_social_identity_signal(context)
        if platform == "facebook":
            return self._is_facebook_person_profile(url) and self._has_social_identity_signal(context)

        return False

    def _has_social_identity_signal(self, context: list[str]) -> bool:
        return any(item in context for item in self.SOCIAL_IDENTITY_SIGNALS)

    def _institutional_context_terms(self, raw_profile: TargetProfile) -> list[str]:
        values = [
            raw_profile.target.company,
            raw_profile.target.location,
            *raw_profile.target.cities,
            *raw_profile.target.education,
            *raw_profile.target.aliases,
        ]
        terms = []

        for value in values:
            terms.extend(self._significant_tokens(value))

        return self._unique(terms)

    def _contains_company(self, raw_profile: TargetProfile, text: str) -> bool:
        company = raw_profile.target.company
        return bool(company) and (company.lower() in text or "unisa" in text)

    def _evidence_from_confirmed_source(self, ev, confirmed_urls: set[str]) -> bool:
        return (
            bool(ev.url)
            and self._canonical_url(ev.url) in confirmed_urls
            and (ev.platform or "").lower().strip() in self.SOCIAL_PLATFORMS
        )

    def _is_linkedin_profile(self, url: str) -> bool:
        parsed = urlparse(url.lower())
        parts = [part for part in parsed.path.split("/") if part]
        return "linkedin.com" in parsed.netloc and len(parts) >= 2 and parts[0] == "in"

    def _is_github_user_profile(self, url: str) -> bool:
        parsed = urlparse(url.lower())
        parts = [part for part in parsed.path.split("/") if part]
        return "github.com" in parsed.netloc and len(parts) == 1

    def _is_instagram_profile(self, url: str) -> bool:
        parsed = urlparse(url.lower())
        parts = [part for part in parsed.path.split("/") if part]
        return "instagram.com" in parsed.netloc and len(parts) == 1 and parts[0] not in self.BAD_SOCIAL_PARTS

    def _is_facebook_person_profile(self, url: str) -> bool:
        parsed = urlparse(url.lower())
        parts = [part for part in parsed.path.split("/") if part]

        if "facebook.com" not in parsed.netloc or not parts:
            return False
        if any(part in self.BAD_SOCIAL_PARTS for part in parts):
            return False
        if parts[0] == "people":
            return len(parts) >= 3
        if parts[0] == "profile.php":
            return "id=" in parsed.query

        return len(parts) == 1

    def _is_noisy_social_mention(self, url: str) -> bool:
        parsed = urlparse(url.lower())
        parts = [part for part in parsed.path.split("/") if part]

        if not parts:
            return True
        if "instagram.com" in parsed.netloc:
            return parts[0] in {"p", "reel", "reels", "stories", "explore", "tags"}
        if "facebook.com" in parsed.netloc:
            return any(part in {"groups", "posts", "photos", "videos", "watch", "reel", "reels"} for part in parts)

        return False

    def _is_bad_web_domain(self, url: str) -> bool:
        domain = urlparse(url.lower()).netloc.removeprefix("www.")
        return any(domain == bad or domain.endswith(f".{bad}") for bad in self.BAD_WEB_DOMAINS)

    def _tech_stack(self, raw_stack: list[str]) -> list[str]:
        return self._unique([item.strip() for item in raw_stack if item])

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
            return pieces[-1] if len(pieces) >= 2 else None

        return None

    def _locations_from_text(self, text: str | None) -> list[str]:
        if not text:
            return []

        values = []

        for sentence in [part.strip() for part in re.split(r"[.;]", text) if part.strip()]:
            lowered = sentence.lower()

            if "università" in lowered or "university" in lowered:
                continue
            if any(term in lowered for term in ["italia", "italy"]):
                values.append(sentence)
                values.extend(piece.strip() for piece in sentence.split(",") if piece.strip())

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
            elif isinstance(node, str) and key in {"education", "college", "school", "university", "secondary_school"}:
                values.append(node)

        visit(raw_data)
        return values

    def _clean_locations(self, values: list[Any]) -> list[str]:
        output = []

        for value in values:
            cleaned = str(value or "").strip()
            lowered = cleaned.lower()

            if not cleaned or len(cleaned) > 80:
                continue
            if any(bad in lowered for bad in ["profile ·", "digital creator", "direzione nazionale"]):
                continue

            output.append(cleaned)

        return self._unique(output)

    def _clean_education(self, values: list[str]) -> list[str]:
        return self._unique([
            str(value).strip()
            for value in values
            if value and str(value).strip().lower() not in self.NEGATIVE_VALUES
        ])

    def _looks_like_school(self, value: str | None) -> bool:
        return bool(value) and any(
            term in value.lower()
            for term in ["università", "university", "school", "college", "istituto"]
        )

    def _is_document_url(self, url: str | None) -> bool:
        lowered = (url or "").lower()
        return any(ext in lowered for ext in self.DOCUMENT_EXTENSIONS)

    def _deduplicate_links(self, links: list[StructuredPublicLink]) -> list[StructuredPublicLink]:
        by_key = {}

        for link in links:
            key = (self._canonical_url(link.url), link.platform)
            by_key.setdefault(key, link)

        return list(by_key.values())

    def _deduplicate_mentions(self, mentions: list[StructuredPublicMention]) -> list[StructuredPublicMention]:
        by_key = {}

        for mention in mentions:
            key = (self._canonical_url(mention.url), mention.mention_type)

            if key not in by_key or mention.confidence > by_key[key].confidence:
                by_key[key] = mention

        return list(by_key.values())

    def _infer_gender(self, raw_profile: TargetProfile) -> str | None:
        if raw_profile.target.gender:
            return raw_profile.target.gender

        guessed = self.gender_detector.get_gender(raw_profile.target.full_name.split()[0])
        return {
            "male": "Male",
            "mostly_male": "Male",
            "female": "Female",
            "mostly_female": "Female",
        }.get(guessed)

    def _platform_from_url(self, url: str | None) -> str:
        domain = urlparse((url or "").lower()).netloc

        for platform in self.SOCIAL_PLATFORMS:
            if f"{platform}.com" in domain:
                return platform

        return "web" if domain else "unknown"

    def _canonical_url(self, url: str | None) -> str:
        if not url:
            return ""

        parsed = urlparse(url.strip())
        scheme = parsed.scheme or "https"
        netloc = parsed.netloc.lower()

        if netloc in {"www.facebook.com", "web.facebook.com", "m.facebook.com", "mbasic.facebook.com"}:
            netloc = "facebook.com"
        elif netloc.startswith("www."):
            netloc = netloc[4:]

        path = parsed.path[:-1] if parsed.path != "/" and parsed.path.endswith("/") else parsed.path
        query = parsed.query if path.lower().endswith("profile.php") else ""

        return urlunparse((scheme, netloc, path, "", query, ""))

    def _etype(self, evidence_type) -> str:
        if evidence_type is None:
            return ""

        return str(evidence_type.value if hasattr(evidence_type, "value") else evidence_type).lower().strip()

    def _status(self, status) -> str:
        if status is None:
            return ""

        return str(status.value if hasattr(status, "value") else status).lower().strip()

    def _first(self, values: list[Any]) -> str | None:
        values = self._unique(values)
        return values[0] if values else None

    def _unique(self, values: list[Any]) -> list[str]:
        seen = set()
        output = []

        for value in values:
            cleaned = str(value or "").strip()
            key = cleaned.lower()

            if cleaned and key not in seen:
                seen.add(key)
                output.append(cleaned)

        return output

    def _significant_tokens(self, value: str | None) -> list[str]:
        if not value:
            return []

        tokens = re.split(r"[^a-z0-9]+", self._normalize_text(value))
        return [token for token in tokens if len(token) > 2 and token not in self.CONTEXT_STOPWORDS]

    def _normalize_text(self, value: str) -> str:
        normalized = str(value).lower()

        for old, new in {"à": "a", "è": "e", "é": "e", "ì": "i", "ò": "o", "ù": "u"}.items():
            normalized = normalized.replace(old, new)

        return normalized

    def _join_text(self, *values: Any) -> str:
        return " ".join(str(value) for value in values if value).strip()
