import json
import re
from pathlib import Path
from typing import Any, Dict
import gender_guesser.detector as gender

from target_information_collector.shared.models import (
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
        confirmed_urls = self._confirmed_profile_urls(raw_profile)

        return StructuredProfile(
            name=raw_profile.target.full_name,
            gender=self._infer_gender(raw_profile),
            birth_date=raw_profile.target.birth_date,
            position=self._position(raw_profile, confirmed_urls),
            organization=self._organization(raw_profile, confirmed_urls),
            cities=self._cities(raw_profile, confirmed_urls),
            education=self._education(raw_profile, confirmed_urls),
            contacts=self._contacts(raw_profile),
            public_links=self._public_links(raw_profile, confirmed_urls), # Passiamo i confermati qui
            tech_stack=self._tech_stack(raw_profile.tech_stack),
        )

    def _confirmed_profile_urls(self, raw_profile: TargetProfile) -> set[str]:
        urls = set()

        # Sfruttiamo il lavoro dell'IdentityResolver guardando gli identity_candidates
        for candidate in raw_profile.identity_candidates:
            # SOGLIA DI CONFERMA GENERALE: Se il resolver è sicuro al 70% o più, il profilo è confermato
            if candidate.confidence >= 0.70:
                urls.add(candidate.profile_url)
            # Teniamo una tolleranza più elastica per GitHub se storicamente affidabile
            elif candidate.platform == "github" and candidate.confidence >= 0.55:
                urls.add(candidate.profile_url)

        return urls

    def _position(self, raw_profile: TargetProfile, confirmed_urls: set[str]) -> str | None:
        for candidate in raw_profile.identity_candidates:
            if candidate.profile_url in confirmed_urls and candidate.role:
                return candidate.role.strip()

        return raw_profile.target.role

    def _organization(self, raw_profile: TargetProfile, confirmed_urls: set[str]) -> str | None:
        for candidate in raw_profile.identity_candidates:
            if candidate.profile_url in confirmed_urls and candidate.company:
                return candidate.company.strip()

        return raw_profile.target.company

    def _cities(self, raw_profile: TargetProfile, confirmed_urls: set[str]) -> list[str]:
        values = []

        if raw_profile.target.location:
            values.append(raw_profile.target.location)

        values.extend(raw_profile.target.cities)

        for candidate in raw_profile.identity_candidates:
            if candidate.profile_url in confirmed_urls and candidate.location:
                values.append(candidate.location)

        for ev in raw_profile.evidence:
            if ev.url not in confirmed_urls:
                continue

            if str(ev.evidence_type) == "location" and ev.value:
                values.append(ev.value)

        return self._unique(values)

    def _education(self, raw_profile: TargetProfile, confirmed_urls: set[str]) -> list[str]:
        values = []

        values.extend(raw_profile.target.education)

        for ev in raw_profile.evidence:
            if ev.url not in confirmed_urls:
                continue

            if str(ev.evidence_type) == "education" and ev.value:
                values.append(ev.value)

        return self._unique(values)

    def _contacts(self, raw_profile: TargetProfile) -> list[str]:
        values = []

        values.extend(raw_profile.target.contacts)

        if raw_profile.target.email:
            values.append(raw_profile.target.email)

        if raw_profile.contact and raw_profile.contact.email:
            values.append(raw_profile.contact.email)

        return self._unique(values)

    def _public_links(self, raw_profile: TargetProfile, confirmed_urls: set[str]) -> list[StructuredPublicLink]:
        links = []

        for profile in raw_profile.public_profiles:
            if self._is_bad_social_url(profile.url):
                continue

            # Lo status ora dipende dinamicamente dal fatto che l'URL sia tra i confermati
            status = "confirmed" if profile.url in confirmed_urls else "candidate"

            links.append(
                StructuredPublicLink(
                    url=profile.url,
                    platform=profile.platform,
                    status=status,
                    matched_context=[],
                )
            )

        for ev in raw_profile.evidence:
            if str(ev.evidence_type) != "web_mention":
                continue

            if ev.confidence <= 0.55:
                continue

            platform = "web"

            if ev.raw_data and ev.raw_data.get("platform"):
                platform = ev.raw_data.get("platform")

            links.append(
                StructuredPublicLink(
                    url=ev.url,
                    platform=platform,
                    status="mention",
                    context=ev.value,
                    matched_context=[],
                )
            )

        return self._deduplicate_links(links)

    def _tech_stack(self, raw_stack: list[str]) -> list[str]:
        values = []

        for item in raw_stack:
            if not item:
                continue

            cleaned = item.strip()
            values.append(cleaned)

        return self._unique(values)

    def _is_bad_social_url(self, url: str) -> bool:
        if not url:
            return False
            
        lower = url.lower()

        bad_fragments = [
            "/photos/",
            "/photo/",
            "/posts/",
            "/videos/",
            "/watch/",
            "/reel/",
            "/reels/",
            "/p/",
            "/stories/",
        ]

        return any(fragment in lower for fragment in bad_fragments)

    def _deduplicate_links(self, links: list[StructuredPublicLink]) -> list[StructuredPublicLink]:
        seen = set()
        output = []

        for link in links:
            key = (link.url, link.platform)

            if key in seen:
                continue

            seen.add(key)
            output.append(link)

        return output

    def _infer_gender(self, raw_profile: TargetProfile) -> str | None:
        # Se c'è già nel raw input, usiamo quello
        if raw_profile.target.gender:
            return raw_profile.target.gender
            
        # Estraiamo il primo nome (es. "Mario" da "Mario Lezzi")
        first_name = raw_profile.target.full_name.split()[0]
        
        # Chiediamo alla libreria di indovinare
        guessed = self.gender_detector.get_gender(first_name)
        
        # Mappiamo i risultati della libreria in un formato pulito
        mapping = {
            "male": "Male",
            "mostly_male": "Male",
            "female": "Female",
            "mostly_female": "Female"
        }
        
        return mapping.get(guessed, None)

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