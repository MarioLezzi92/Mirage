import json
import os
from typing import Any

from .models import CampaignPayload, CampaignSection, CampaignTarget, ConfirmedSource, TargetProfile


class CampaignGeneratorService:
    DEFAULT_TEMPLATE_ID = "SIM-GEN-01"

    def __init__(self):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.templates_path = os.path.join(self.base_dir, "templates.json")
        self.data_dir = os.path.join(self.base_dir, "data")

        os.makedirs(self.data_dir, exist_ok=True)

        with open(self.templates_path, "r", encoding="utf-8") as file:
            self.templates = json.load(file)

    def generate_payload(self, target: TargetProfile) -> CampaignPayload:
        best_template = self._select_template(target)
        campaign_target = self._build_campaign_target(target)

        payload = CampaignPayload(
            target=campaign_target,
            campaign=CampaignSection(
                template_id=best_template.get("template_id", self.DEFAULT_TEMPLATE_ID),
                scenario_type=best_template.get("scenario_type", "generic"),
                category=best_template.get("category", "generic"),
                subject_template=self._template_subject(best_template),
                body_template=self._template_body(best_template),
                safety_constraints=best_template.get("safety_constraints", []),
            ),
        )

        self._save_payload(payload)
        return payload

    def _select_template(self, target: TargetProfile) -> dict[str, Any]:
        keywords = self._target_keywords(target)

        best_template = None
        best_score = -1

        for template in self.templates:
            if self._missing_required_fields(target, template):
                continue

            score = template.get("base_score", 0)

            for trigger in template.get("trigger_keywords", []):
                trigger = str(trigger).lower().strip()

                if trigger and any(trigger in keyword for keyword in keywords):
                    score += 15

            if score > best_score:
                best_score = score
                best_template = template

        if best_template:
            return best_template

        return next(
            (template for template in self.templates if template.get("template_id") == self.DEFAULT_TEMPLATE_ID),
            self.templates[-1],
        )

    def _build_campaign_target(self, target: TargetProfile) -> CampaignTarget:
        confirmed_sources = self._confirmed_sources(target)
        institutional_sources = [
            source
            for source in confirmed_sources
            if source.platform == "institutional"
        ]

        return CampaignTarget(
            name=target.name or "Utente",
            organization=target.organization,
            position=target.position,
            city=target.cities[0] if target.cities else None,
            cities=target.cities,
            email=self._first_email(target.contacts),
            tech_stack=target.tech_stack,
            platforms=self._platforms(confirmed_sources),
            confirmed_sources=confirmed_sources,
            institutional_sources=institutional_sources,
        )

    def _confirmed_sources(self, target: TargetProfile) -> list[ConfirmedSource]:
        sources = []

        for link in target.public_links:
            url = link.get("url")
            platform = link.get("platform")
            status = link.get("status", "confirmed")

            if not url or not platform:
                continue

            if status != "confirmed":
                continue

            sources.append(
                ConfirmedSource(
                    platform=str(platform),
                    url=str(url),
                    status="confirmed",
                    context=link.get("context"),
                )
            )

        return sources

    def _platforms(self, sources: list[ConfirmedSource]) -> list[str]:
        return self._unique([source.platform for source in sources])

    def _first_email(self, contacts: list[str]) -> str | None:
        for contact in contacts:
            if "@" in contact:
                return contact

        return None

    def _target_keywords(self, target: TargetProfile) -> list[str]:
        values = []

        values.extend(target.tech_stack)
        values.extend(target.cities)
        values.extend(target.education)

        if target.name:
            values.append(target.name)

        if target.organization:
            values.append(target.organization)

        if target.position:
            values.append(target.position)

        for link in target.public_links:
            values.append(link.get("platform"))
            values.append(link.get("url"))
            values.append(link.get("context"))

            for item in link.get("matched_context", []):
                values.append(item)

        return [
            str(value).lower().strip()
            for value in values
            if value
        ]

    def _missing_required_fields(self, target: TargetProfile, template: dict[str, Any]) -> bool:
        target_dict = target.model_dump()

        for field in template.get("required_placeholders", []):
            if not target_dict.get(field):
                return True

        return False

    def _template_subject(self, template: dict[str, Any]) -> str:
        return (
            template.get("subject_template")
            or template.get("base_subject")
            or "Comunicazione di sicurezza"
        )

    def _template_body(self, template: dict[str, Any]) -> str:
        return (
            template.get("body_template")
            or template.get("base_body")
            or "Ciao {name},\n\nQuesta è una comunicazione relativa a una simulazione autorizzata."
        )

    def _save_payload(self, payload: CampaignPayload) -> None:
        filename = f"payload_{payload.target.name.replace(' ', '_')}.json"
        path = os.path.join(self.data_dir, filename)

        with open(path, "w", encoding="utf-8") as file:
            json.dump(
                payload.model_dump(mode="json"),
                file,
                indent=4,
                ensure_ascii=False,
            )

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