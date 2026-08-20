import re
from typing import Any

from target_information_collector.shared.models import ProfileData, TargetInput
from target_information_collector.shared.text import (
    canonical_url,
    is_profile_url,
    normalize,
    platform_from_url,
)


class ProfileNormalizer:
    """Porta output social eterogenei nei campi già previsti dal profilo."""

    EMPTY_VALUE_PATTERN = re.compile(
        r"^(?:n/?a|none|null|-|"
        r"no\s+.+\s+(?:to show|available|provided|listed|found)|"
        r"nessun[oa]?\s+.+\s+(?:da mostrare|disponibile|indicat[oa]))$",
        re.IGNORECASE,
    )

    NAME_KEYS = ("fullName", "full_name", "displayName", "name")
    USERNAME_KEYS = ("username", "userName", "user_name", "login", "handle")
    ROLE_KEYS = (
        "position", "positions", "currentPosition", "current_position", "role",
        "roles", "jobTitle", "job_title", "occupation", "headline", "work",
        "workplaces", "experience", "experiences", "workHistory", "currentRole",
        "current_role", "currentJob", "current_job",
    )
    ROLE_ITEMS = ("position", "role", "jobTitle", "occupation", "headline", "title")
    BIO_KEYS = (
        "biography", "bio", "about", "intro", "description", "summary",
        "profileSummary", "profile_summary", "tagline",
    )
    COMPANY_KEYS = (
        "company", "companies", "companyName", "company_name", "organization",
        "organizations", "currentCompany", "current_company", "employer",
        "employers", "work", "workplaces", "experience", "experiences",
        "workHistory", "workplace", "workplaceName", "workplace_name",
    )
    COMPANY_ITEMS = (
        "company", "companyName", "organization", "employer", "workplace", "name",
    )
    LOCATION_KEYS = (
        "location", "locations", "city", "cities", "currentCity", "current_city",
        "hometown", "homeTown", "address", "addressLocality", "locality", "region",
        "country", "placesLived", "places_lived", "addressWithCountry",
        "address_with_country", "addressWithoutCountry", "address_without_country",
        "geoLocationName", "geo_location_name",
    )
    LOCATION_ITEMS = ("location", "city", "locality", "name", "text", "value", "label")
    EDUCATION_KEYS = (
        "education", "educations", "schools", "school", "schoolName", "university",
        "universities", "college", "colleges", "schoolHistory", "school_history",
    )
    EDUCATION_ITEMS = (
        "schoolName", "school", "institution", "university", "college", "name",
        "degreeName", "degree", "fieldOfStudy", "field",
    )
    EMAIL_KEYS = (
        "email", "emails", "emailAddress", "emailAddresses", "publicEmail",
        "contactEmail", "businessEmail", "business_email",
    )
    TECH_KEYS = (
        "techStack", "tech_stack", "technologies", "technology", "tools",
        "programmingLanguages", "programming_languages", "skills", "skill",
        "topSkills", "top_skills", "expertise",
    )
    GENERIC_ITEMS = ("name", "title", "text", "value", "label", "description")
    EMAIL_PATTERN = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
    URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+", re.I)

    def normalize(self, platform: str, url: str, raw: dict[str, Any]) -> ProfileData:
        full_name = self._direct_text(raw, self.NAME_KEYS)
        if not full_name:
            first_name = self._first_text(raw, ("firstName", "first_name"))
            last_name = self._first_text(raw, ("lastName", "last_name"))
            full_name = " ".join(
                value for value in (first_name, last_name) if value
            ) or self._first_text(raw, self.NAME_KEYS)

        strings = self._strings(raw) if platform == "facebook" else []
        role_values = self._enriched_values(
            raw,
            self.ROLE_KEYS,
            self.ROLE_ITEMS,
            strings,
            ("Works as", "Lavora come"),
        )
        company_values = self._enriched_values(
            raw,
            self.COMPANY_KEYS,
            self.COMPANY_ITEMS,
            strings,
            ("Works at", "Worked at", "Lavora presso"),
        )
        locations = self._enriched_values(
            raw,
            self.LOCATION_KEYS,
            self.LOCATION_ITEMS,
            strings,
            ("Lives in", "From", "Vive a", "Originario di"),
        )
        locations = self.valid_locations(locations)
        education = self._enriched_values(
            raw,
            self.EDUCATION_KEYS,
            self.EDUCATION_ITEMS,
            strings,
            ("Studied at", "Studies at", "Went to", "Ha studiato presso"),
        )
        education = self._unique(
            self._clean_education(value) for value in education
        )
        raw_bio = self._first_text(raw, self.BIO_KEYS)
        bio = self._clean_bio(raw_bio)

        return ProfileData(
            platform=platform,
            url=url,
            full_name=full_name,
            username=self._direct_text(raw, self.USERNAME_KEYS)
            or self._first_text(raw, self.USERNAME_KEYS),
            role=role_values[0] if role_values else None,
            bio=bio,
            company=company_values[0] if company_values else None,
            locations=locations,
            education=education,
            emails=self._emails(raw, [raw_bio] if raw_bio else [], strings),
            tech_stack=[
                value
                for value in self._texts(raw, self.TECH_KEYS)
                if not re.search(
                    r"\band\s+\+\d+\s+skills?\b",
                    value,
                    re.IGNORECASE,
                )
            ],
            crosslinks=self._crosslinks(raw, platform, url),
            raw=raw,
        )

    @classmethod
    def _crosslinks(
        cls,
        raw: dict[str, Any],
        own_platform: str,
        own_url: str,
    ) -> list[str]:
        own_key = canonical_url(own_url).casefold()
        output: list[str] = []
        seen: set[str] = set()
        for value in cls._strings(raw):
            for match in cls.URL_PATTERN.findall(value):
                url = match.rstrip(".,;:!?)]}")
                platform = platform_from_url(url)
                if (
                    platform in {"web", own_platform}
                    or not is_profile_url(url, platform)
                ):
                    continue
                canonical = canonical_url(url)
                key = canonical.casefold()
                if key == own_key or key in seen:
                    continue
                output.append(canonical)
                seen.add(key)
        return output

    def enrich_from_discovery(
        self,
        profile: ProfileData,
        target: TargetInput,
        title: str,
        snippet: str,
    ) -> ProfileData:
        """Riusa i dati pubblici della discovery solo dopo la verifica del profilo."""
        context = " ".join(
            value for value in (title, snippet, profile.bio) if value
        )
        if not context:
            return profile

        if (
            not profile.company
            and target.company
            and self._contains(target.company, context)
        ):
            profile.company = target.company

        matched_cities = [
            city
            for city in target.cities
            if self._contains(city.split(",", 1)[0], context)
        ]
        profile.locations = self._merge_locations(
            [*profile.locations, *matched_cities]
        )
        profile.education = self._unique(
            [
                *profile.education,
                *(
                    item
                    for item in target.education
                    if self._contains(item, context)
                ),
            ]
        )
        profile.emails = self._unique(
            [*profile.emails, *self.EMAIL_PATTERN.findall(context)]
        )

        if profile.platform == "linkedin":
            profile.role = profile.role or self._linkedin_role(
                target.full_name,
                target.company,
                title,
                snippet,
            )
            profile.education = self._unique(
                [*profile.education, *self._linkedin_education(snippet)]
            )
            location = self._linkedin_location(
                snippet,
                (target.full_name, target.company or "", profile.role or ""),
            )
            if location:
                profile.locations = self._merge_locations(
                    [*profile.locations, location]
                )
        return profile

    def _enriched_values(
        self,
        data: Any,
        keys: tuple[str, ...],
        item_keys: tuple[str, ...],
        strings: list[str],
        prefixes: tuple[str, ...],
    ) -> list[str]:
        values = self._cleaned(self._texts(data, keys, item_keys), prefixes)
        values = self._unique([*values, *self._prefixed(strings, prefixes)])
        return self._specific(
            [value for value in values if self._meaningful(value)]
        )

    def _first_text(self, data: Any, keys: tuple[str, ...]) -> str | None:
        values = self._texts(data, keys)
        return values[0] if values else None

    def _direct_text(self, data: dict[str, Any], keys: tuple[str, ...]) -> str | None:
        lookup = {self._key(key): value for key, value in data.items()}
        for key in keys:
            value = lookup.get(self._key(key))
            text = self._as_text(value) if value is not None else None
            if text:
                return text
        return None

    def _texts(
        self,
        data: Any,
        keys: tuple[str, ...],
        item_keys: tuple[str, ...] = (),
    ) -> list[str]:
        output: list[str] = []
        for value in self._values(data, keys):
            for item in value if isinstance(value, list) else [value]:
                text = self._as_text(item, item_keys)
                if text:
                    output.append(text)
        return self._unique(output)

    def _emails(
        self,
        data: Any,
        bios: list[str],
        facebook_strings: list[str],
    ) -> list[str]:
        values = [*self._texts(data, self.EMAIL_KEYS), *bios, *facebook_strings]
        return self._unique(
            email for value in values for email in self.EMAIL_PATTERN.findall(value)
        )

    @classmethod
    def _clean_bio(cls, value: str | None) -> str | None:
        if not value or not cls._meaningful(value):
            return None
        cleaned = cls.EMAIL_PATTERN.sub("", value)
        cleaned = re.sub(r"\s+([.,;:])", r"\1", cleaned)
        return cleaned.strip(" .,-") or None

    @classmethod
    def _meaningful(cls, value: str) -> bool:
        return not cls.EMPTY_VALUE_PATTERN.fullmatch(value.strip())

    @classmethod
    def _linkedin_role(
        cls,
        full_name: str,
        company: str | None,
        title: str,
        snippet: str,
    ) -> str | None:
        segments = cls._sentences(snippet)
        expected_name = normalize(full_name)
        for index, segment in enumerate(segments[:-1]):
            if expected_name and expected_name in normalize(segment):
                role = segments[index + 1].strip(" .,-")
                if cls._valid_role(role, company):
                    return role

        role = re.sub(
            rf"^{re.escape(full_name)}\s*[-|:]+\s*",
            "",
            title.strip(),
            flags=re.IGNORECASE,
        )
        role = re.sub(r"\s*[|·]\s*LinkedIn.*$", "", role, flags=re.IGNORECASE)
        role = role.rstrip(" .,-…")
        return role if cls._valid_role(role, company) else None

    @classmethod
    def _valid_role(cls, value: str, company: str | None) -> bool:
        return bool(
            value
            and len(value) <= 180
            and not cls._discovery_noise(value)
            and not (company and cls._contains(company, value))
            and not ("," in value and cls._looks_like_location(value))
        )

    @classmethod
    def _linkedin_location(
        cls,
        value: str,
        excluded: tuple[str, ...],
    ) -> str | None:
        cleaned = re.sub(
            r"(?:\.{2,})?\s*Read more\s*$",
            "",
            value.strip(),
            flags=re.IGNORECASE,
        ).rstrip(" .")
        excluded_values = [normalize(item) for item in excluded if item]
        for segment in reversed(cls._sentences(cleaned)):
            candidate = segment.strip(" .,-…")
            normalized = normalize(candidate)
            if (
                not normalized
                or cls._location_noise(candidate)
                or any(
                    normalized == item or normalized in item or item in normalized
                    for item in excluded_values
                )
            ):
                continue
            if cls._looks_like_location(candidate):
                return candidate
        return None

    @classmethod
    def _linkedin_education(cls, value: str) -> list[str]:
        segments = cls._sentences(value)
        section = next(
            (
                index
                for index, segment in enumerate(segments)
                if re.search(
                    r"\b(?:formazione|istruzione|education)\b",
                    segment,
                    re.IGNORECASE,
                )
            ),
            None,
        )
        if section is None:
            return []

        degree_index = next(
            (
                index
                for index in range(section + 1, len(segments))
                if re.search(
                    r"\b(?:laurea|master|bachelor|degree|phd|dottorato|"
                    r"diploma)\w*\b",
                    segments[index],
                    re.IGNORECASE,
                )
                and not cls._discovery_noise(segments[index])
            ),
            None,
        )
        if degree_index is None:
            return []

        degree = segments[degree_index].strip(" .,-…")
        institution = next(
            (
                institution
                for segment in reversed(segments[section + 1:degree_index])
                if (institution := cls._education_institution(segment))
            ),
            None,
        )
        period = next(
            (
                cls._period(segment)
                for segment in segments[degree_index + 1:]
                if cls._period(segment)
            ),
            None,
        )
        return [
            " — ".join(
                part for part in (institution, degree, period) if part
            )
        ]

    @staticmethod
    def _education_institution(value: str) -> str | None:
        for part in reversed(value.split("·")):
            cleaned = re.sub(
                r"^\s*Grafico\s+",
                "",
                part,
                flags=re.IGNORECASE,
            ).strip(" .,-…")
            if re.search(
                r"\b(?:universit|university|college|school|istituto|liceo|"
                r"academy)\w*\b",
                cleaned,
                re.IGNORECASE,
            ):
                return cleaned
        return None

    @staticmethod
    def _period(value: str) -> str | None:
        match = re.search(
            r"\b(?:19|20)\d{2}\s*[-–—]\s*(?:(?:19|20)\d{2}|"
            r"present|presente|oggi|in corso)\b",
            value,
            re.IGNORECASE,
        )
        return re.sub(r"\s*[-–—]\s*", "-", match.group(0)) if match else None

    @classmethod
    def _clean_education(cls, value: str) -> str:
        patterns = (
            r"^(?:Studied|Studies)\s+(.+?)\s+at\s+(.+)$",
            r"^(?:Ha studiato|Studia)\s+(.+?)\s+presso\s+(.+)$",
        )
        cleaned = value.strip()
        for pattern in patterns:
            match = re.match(pattern, cleaned, re.IGNORECASE)
            if match:
                cleaned = f"{match.group(1).strip()} — {match.group(2).strip()}"
                break

        parts: list[str] = []
        for part in cleaned.split(" — "):
            part = re.sub(r"\s+", " ", part).strip(" ,")
            if "," in part:
                repeated, detail = (item.strip() for item in part.split(",", 1))
                if any(normalize(repeated) == normalize(item) for item in parts):
                    part = detail
            if part and not any(normalize(part) == normalize(item) for item in parts):
                parts.append(part)
        return " — ".join(parts)

    @staticmethod
    def _sentences(value: str) -> list[str]:
        return [
            segment.strip()
            for segment in re.split(r"\.\s+|[\r\n]+", value)
            if segment.strip()
        ]

    @staticmethod
    def _discovery_noise(value: str) -> bool:
        return bool(
            re.search(r"\d", value)
            or re.search(
                r"\b(?:follower|following|collegament|connection|visualizza|"
                r"vedi|follow|message|profilo|profile|linkedin)\w*\b",
                value,
                re.IGNORECASE,
            )
        )

    @classmethod
    def _location_noise(cls, value: str) -> bool:
        return cls._discovery_noise(value) or bool(
            re.search(
                r"\b(?:grafico|formazione|istruzione|education|school|"
                r"universit|laurea|degree|esperienza|experience)\w*\b",
                value,
                re.IGNORECASE,
            )
        )

    @classmethod
    def _looks_like_location(cls, value: str) -> bool:
        normalized = normalize(value)
        if (
            len(normalized) < 2
            or cls._location_noise(value)
            or "@" in value
            or len(value) > 100
        ):
            return False
        parts = [part.strip() for part in value.split(",")]
        if not 1 <= len(parts) <= 4:
            return False
        words = value.replace(",", " ").split()
        if not 1 <= len(words) <= 8:
            return False
        return all(
            part
            and any(character.isalpha() for character in part)
            and part[0].isupper()
            for part in parts
        )

    @classmethod
    def valid_locations(cls, values: list[str]) -> list[str]:
        return cls._merge_locations(
            [value.strip() for value in values if cls._looks_like_location(value)]
        )

    @staticmethod
    def _contains(expected: str, actual: str) -> bool:
        return bool(expected and normalize(expected) in normalize(actual))

    @classmethod
    def _merge_locations(cls, values: list[str]) -> list[str]:
        output: list[str] = []
        seen: set[str] = set()
        for value in values:
            locality = normalize(value.split(",", 1)[0])
            if locality and locality not in seen:
                output.append(value.strip())
                seen.add(locality)
        return output

    @classmethod
    def _values(cls, data: Any, keys: tuple[str, ...]) -> list[Any]:
        wanted = {cls._key(key) for key in keys}
        output: list[Any] = []
        if isinstance(data, dict):
            for key, value in data.items():
                if cls._key(key) in wanted:
                    output.append(value)
                elif isinstance(value, (dict, list)):
                    output.extend(cls._values(value, keys))
        elif isinstance(data, list):
            for value in data:
                output.extend(cls._values(value, keys))
        return output

    @classmethod
    def _as_text(cls, value: Any, item_keys: tuple[str, ...] = ()) -> str | None:
        if isinstance(value, str):
            return value.strip() or None
        if not isinstance(value, dict):
            return None

        lookup = {cls._key(key): item for key, item in value.items()}
        parts: list[str] = []
        for key in item_keys or cls.GENERIC_ITEMS:
            item = lookup.get(cls._key(key))
            text = item.strip() if isinstance(item, str) else None
            if text:
                parts.append(text)
        return " — ".join(cls._unique(parts)) or None

    @classmethod
    def _cleaned(cls, values: list[str], prefixes: tuple[str, ...]) -> list[str]:
        output: list[str] = []
        for value in values:
            cleaned = value.strip()
            for prefix in prefixes:
                cleaned = re.sub(
                    rf"^{re.escape(prefix)}\s*:?[ ]*",
                    "",
                    cleaned,
                    flags=re.IGNORECASE,
                )
            output.append(cleaned)
        return cls._unique(output)

    @classmethod
    def _prefixed(cls, values: list[str], prefixes: tuple[str, ...]) -> list[str]:
        output: list[str] = []
        for value in values:
            for prefix in prefixes:
                match = re.match(
                    rf"^{re.escape(prefix)}\s*:?[ ]*(.+)$",
                    value.strip(),
                    flags=re.IGNORECASE,
                )
                if match:
                    output.append(match.group(1).strip())
        return cls._unique(output)

    @classmethod
    def _strings(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [item for child in value.values() for item in cls._strings(child)]
        if isinstance(value, list):
            return [item for child in value for item in cls._strings(child)]
        return []

    @staticmethod
    def _key(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", value.casefold())

    @staticmethod
    def _unique(values) -> list[str]:
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
    def _specific(cls, values: list[str]) -> list[str]:
        normalized = [" ".join(cls._key(value).split()) for value in values]
        return [
            value
            for index, value in enumerate(values)
            if not any(
                normalized[index] != other and normalized[index] in other
                for other_index, other in enumerate(normalized)
                if other_index != index
            )
        ]
