import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field


class CampaignTemplate(BaseModel):
    scenario: str
    channel: str = "email"
    tone: str
    sender_template: str
    subject_template: str
    body_template: str
    required_fields: list[str] = Field(default_factory=list)
    preferred_fields: list[str] = Field(default_factory=list)
    selection_terms: list[str] = Field(default_factory=list)


class Campaign(BaseModel):
    target: str
    scenario: str
    channel: str
    tone: str
    sender_template: str
    subject_template: str
    body_template: str
    variables: dict[str, str]


class CampaignGenerator:
    _placeholder = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")

    def __init__(
        self,
        template_file: str | Path | None = None,
        output_dir: str | Path | None = None,
        profile_dir: str | Path | None = None,
    ) -> None:
        module_dir = Path(__file__).resolve().parent
        self.template_file = Path(template_file or module_dir / "templates.json")
        self.output_dir = Path(output_dir or module_dir / "data" / "campaigns")
        self.profile_dir = Path(
            profile_dir
            or module_dir.parent
            / "target_information_collector"
            / "data"
            / "profiles"
        )
        self.templates = self._load_templates()

    def generate(self, profiles: Iterable[Any] | None = None) -> list[Campaign]:
        campaigns: list[Campaign] = []
        for profile in profiles if profiles is not None else self._load_profiles():
            data = self._profile_data(profile)
            campaign = self._build_campaign(data)
            self._save(campaign)
            campaigns.append(campaign)
        return campaigns

    def _load_profiles(self) -> list[dict[str, Any]]:
        paths = sorted(self.profile_dir.glob("*.json"))
        if not paths:
            raise FileNotFoundError(
                f"Nessun profilo strutturato in {self.profile_dir}"
            )

        latest: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}
        for path in paths:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not str(data.get("name") or "").strip():
                raise ValueError(f"Profilo strutturato non valido: {path}")

            match = re.search(r"-structured-(\d+)\.json$", path.name)
            rank = (
                int(match.group(1)) if match else 0,
                path.stat().st_mtime_ns,
            )
            key = str(data["name"]).casefold()
            if key not in latest or rank > latest[key][0]:
                latest[key] = (rank, data)

        return [latest[key][1] for key in sorted(latest)]

    def _load_templates(self) -> list[CampaignTemplate]:
        data = json.loads(self.template_file.read_text(encoding="utf-8"))
        templates = [CampaignTemplate.model_validate(item) for item in data]
        if not templates:
            raise ValueError("templates.json non contiene template")
        return templates

    def _build_campaign(self, profile: dict[str, Any]) -> Campaign:
        target = str(profile.get("name") or "").strip()
        if not target:
            raise ValueError("Il profilo non contiene il nome del target")

        template = self._select_template(profile)
        values = self._variable_values(profile)
        placeholders = self._placeholders(template)
        missing = placeholders - values.keys() - {"simulation_url"}
        if missing:
            raise ValueError(
                f"Template {template.scenario}: variabili mancanti {sorted(missing)}"
            )

        return Campaign(
            target=target,
            scenario=template.scenario,
            channel=template.channel,
            tone=template.tone,
            sender_template=template.sender_template,
            subject_template=template.subject_template,
            body_template=template.body_template,
            variables={
                name: values[name]
                for name in sorted(placeholders)
                if name in values
            },
        )

    def _select_template(self, profile: dict[str, Any]) -> CampaignTemplate:
        compatible = [
            template
            for template in self.templates
            if all(
                self._has_value(profile, field)
                for field in template.required_fields
            )
        ]
        if not compatible:
            raise ValueError("Nessun template compatibile con il profilo")

        context = self._normalize(
            json.dumps(profile, ensure_ascii=False, sort_keys=True, default=str)
        )
        return max(
            compatible,
            key=lambda template: (
                sum(
                    self._has_value(profile, field)
                    for field in template.preferred_fields
                )
                + 4
                * sum(
                    self._contains(context, term)
                    for term in template.selection_terms
                ),
                hashlib.sha256(
                    f"{context}|{template.scenario}".encode()
                ).hexdigest(),
            ),
        )

    @classmethod
    def _contains(cls, context: str, term: str) -> bool:
        normalized = cls._normalize(term)
        return bool(normalized) and f" {normalized} " in f" {context} "

    @staticmethod
    def _normalize(value: str) -> str:
        ascii_value = unicodedata.normalize("NFKD", value).encode(
            "ascii", "ignore"
        ).decode("ascii")
        return re.sub(r"[^a-z0-9]+", " ", ascii_value.casefold()).strip()

    @classmethod
    def _placeholders(cls, template: CampaignTemplate) -> set[str]:
        text = " ".join(
            (
                template.sender_template,
                template.subject_template,
                template.body_template,
            )
        )
        return set(cls._placeholder.findall(text))

    @staticmethod
    def _variable_values(profile: dict[str, Any]) -> dict[str, str]:
        def first(field: str) -> str:
            value = profile.get(field)
            if isinstance(value, list) and value:
                item = value[0]
                if isinstance(item, Mapping):
                    return str(item.get("title") or item.get("url") or "").strip()
                return str(item).strip()
            return ""

        def social_service(repository_only: bool = False) -> str:
            links = profile.get("social_links")
            if not isinstance(links, list):
                links = []
            repositories = {"github", "gitlab", "bitbucket"}
            candidates = [
                item
                for item in links
                if isinstance(item, Mapping)
                and str(item.get("platform") or "").strip()
                and (
                    not repository_only
                    or str(item.get("platform") or "").casefold() in repositories
                )
            ]
            if not candidates:
                return "" if repository_only else email_service()

            def confidence(item: Mapping[str, Any]) -> float:
                try:
                    return float(item.get("confidence") or 0)
                except (TypeError, ValueError):
                    return 0.0

            platform = str(max(candidates, key=confidence)["platform"]).casefold()
            return {
                "github": "GitHub",
                "gitlab": "GitLab",
                "bitbucket": "Bitbucket",
                "linkedin": "LinkedIn",
                "instagram": "Instagram",
                "facebook": "Facebook",
            }.get(platform, platform.replace("-", " ").title())

        def email_service() -> str:
            email = first("emails").casefold()
            domain = email.rsplit("@", 1)[-1] if "@" in email else ""
            known = {
                "gmail.com": "Google",
                "outlook.com": "Microsoft",
                "hotmail.com": "Microsoft",
                "live.com": "Microsoft",
                "icloud.com": "Apple",
                "yahoo.com": "Yahoo",
                "proton.me": "Proton Mail",
                "protonmail.com": "Proton Mail",
                "libero.it": "Libero Mail",
                "virgilio.it": "Virgilio Mail",
            }
            if domain in known:
                return known[domain]
            labels = [part for part in domain.split(".") if part]
            return labels[-2].replace("-", " ").title() if len(labels) >= 2 else ""

        def mention_source() -> str:
            mentions = profile.get("mentions")
            if not isinstance(mentions, list) or not mentions:
                return ""
            item = mentions[0]
            if not isinstance(item, Mapping):
                return ""
            host = (urlparse(str(item.get("url") or "")).hostname or "").casefold()
            return host.removeprefix("www.")

        return {
            key: value
            for key, value in {
                "name": str(profile.get("name") or "").strip(),
                "organization": str(profile.get("organization") or "").strip(),
                "summary": str(profile.get("summary") or "").strip(),
                "education": first("education"),
                "email": first("emails"),
                "technology": first("tech_stack"),
                "web_mention": first("mentions"),
                "mention_source": mention_source(),
                "account_service": social_service(),
                "repository_service": social_service(repository_only=True),
            }.items()
            if value
        }

    @staticmethod
    def _profile_data(profile: Any) -> dict[str, Any]:
        if isinstance(profile, Mapping):
            return dict(profile)
        model_dump = getattr(profile, "model_dump", None)
        if callable(model_dump):
            return model_dump(mode="json")
        raise TypeError("Ogni profilo deve essere un mapping o un modello Pydantic")

    @staticmethod
    def _has_value(profile: dict[str, Any], field: str) -> bool:
        if field == "mention_source":
            mentions = profile.get("mentions")
            if not isinstance(mentions, list) or not mentions:
                return False
            item = mentions[0]
            return bool(
                isinstance(item, Mapping)
                and urlparse(str(item.get("url") or "")).hostname
            )
        if field in {"account_service", "repository_service"}:
            return bool(CampaignGenerator._variable_values(profile).get(field))
        return bool(profile.get(field))

    def _save(self, campaign: Campaign) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        slug = self._slug(campaign.target)
        pattern = re.compile(rf"^{re.escape(slug)}-campaign-(\d+)\.json$")
        path = self.output_dir / f"{slug}-campaign-1.json"
        path.write_text(
            json.dumps(campaign.model_dump(mode="json"), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        for old_path in self.output_dir.glob(f"{slug}-campaign-*.json"):
            if old_path != path and pattern.fullmatch(old_path.name):
                old_path.unlink()
        return path

    @staticmethod
    def _slug(value: str) -> str:
        ascii_value = unicodedata.normalize("NFKD", value).encode(
            "ascii", "ignore"
        ).decode("ascii")
        slug = re.sub(r"[^a-z0-9]+", "-", ascii_value.casefold()).strip("-")
        return slug or "target"


def generate(
    profiles: Iterable[Any] | None = None,
    template_file: str | Path | None = None,
    output_dir: str | Path | None = None,
    profile_dir: str | Path | None = None,
) -> list[Campaign]:
    return CampaignGenerator(template_file, output_dir, profile_dir).generate(profiles)
