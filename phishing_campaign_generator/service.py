import json
import os

from .models import CampaignPayload, CampaignSpec, CampaignTarget, TargetProfile


class CampaignGeneratorService:
    FALLBACK_TEMPLATE_ID = "SIM-GEN-01"

    def __init__(self):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.templates_path = os.path.join(self.base_dir, "templates.json")
        self.data_dir = os.path.join(self.base_dir, "data")

        os.makedirs(self.data_dir, exist_ok=True)

        with open(self.templates_path, "r", encoding="utf-8") as file:
            self.templates = json.load(file)

    def generate_payload(self, target: TargetProfile) -> CampaignPayload:
        template = self._select_template(target)

        payload = CampaignPayload(
            target=self._build_target(target),
            campaign=CampaignSpec(
                template_id=template["template_id"],
                scenario_type=template["scenario_type"],
                category=template.get("category", "unknown"),
                subject_template=template["base_subject"],
                body_template=template["base_body"],
                safety_constraints=template.get("safety_constraints", []),
            ),
        )

        self._save_payload(payload)

        return payload

    def _select_template(self, target: TargetProfile) -> dict:
        keywords = self._profile_keywords(target)

        best_template = None
        best_score = -1

        for template in self.templates:
            if not self._required_data_available(target, template):
                continue

            score = template.get("base_score", 0)

            for trigger in template.get("trigger_keywords", []):
                trigger = trigger.lower()

                if any(trigger in keyword for keyword in keywords):
                    score += 15

            if score > best_score:
                best_score = score
                best_template = template

        return best_template or self._fallback_template()

    def _required_data_available(self, target: TargetProfile, template: dict) -> bool:
        for field in template.get("required_placeholders", []):
            if field == "name" and not target.name:
                return False

            if field == "organization" and not target.organization:
                return False

            if field == "position" and not target.position:
                return False

            if field == "city" and not target.cities:
                return False

            if field == "email" and not target.contacts:
                return False

            if field == "tech_stack" and not target.tech_stack:
                return False

            if field == "platform" and not target.public_links:
                return False

        return True

    def _build_target(self, target: TargetProfile) -> CampaignTarget:
        return CampaignTarget(
            name=target.name or "Utente",
            organization=target.organization,
            position=target.position,
            city=self._first(target.cities),
            email=self._first(target.contacts),
            tech_stack=self._unique(target.tech_stack),
            platforms=self._confirmed_platforms(target),
        )

    def _profile_keywords(self, target: TargetProfile) -> list[str]:
        values = []

        values.extend(target.tech_stack)
        values.extend(target.cities)
        values.extend(target.contacts)

        if target.name:
            values.append(target.name)

        if target.organization:
            values.append(target.organization)

        if target.position:
            values.append(target.position)

        for link in target.public_links:
            values.extend([
                link.url or "",
                link.platform or "",
                link.status or "",
                link.context or "",
            ])
            values.extend(link.matched_context)

        return self._unique([value.lower() for value in values if str(value).strip()])

    def _confirmed_platforms(self, target: TargetProfile) -> list[str]:
        platforms = [
            link.platform
            for link in target.public_links
            if link.platform and link.status == "confirmed"
        ]

        return self._unique(platforms)

    def _fallback_template(self) -> dict:
        for template in self.templates:
            if template.get("template_id") == self.FALLBACK_TEMPLATE_ID:
                return template

        return self.templates[-1]

    def _save_payload(self, payload: CampaignPayload) -> None:
        safe_name = payload.target.name.replace(" ", "_")
        path = os.path.join(self.data_dir, f"payload_{safe_name}.json")

        with open(path, "w", encoding="utf-8") as file:
            json.dump(payload.model_dump(), file, indent=4, ensure_ascii=False)

    def _first(self, values: list[str]) -> str | None:
        for value in values:
            cleaned = str(value).strip()

            if cleaned:
                return cleaned

        return None

    def _unique(self, values: list[str]) -> list[str]:
        seen = set()
        output = []

        for value in values:
            cleaned = str(value).strip()

            if not cleaned:
                continue

            key = cleaned.lower()

            if key in seen:
                continue

            seen.add(key)
            output.append(cleaned)

        return output