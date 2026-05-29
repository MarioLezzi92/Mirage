import json
import os
from .models import TargetProfile, CampaignPayload

class CampaignGeneratorService:
    def __init__(self):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.templates_path = os.path.join(self.base_dir, "templates.json")
        self.data_dir = os.path.join(self.base_dir, "data")
        
        os.makedirs(self.data_dir, exist_ok=True)
        
        with open(self.templates_path, "r", encoding="utf-8") as f:
            self.templates = json.load(f)

    def generate_payload(self, target: TargetProfile):
        target_dict = target.model_dump() 
        
        # 1. Creazione pool di parole chiave esteso
        user_keywords = []
        if target.tech_stack:
            user_keywords.extend([k.lower() for k in target.tech_stack])
        if target.position:
            user_keywords.append(target.position.lower())
        if target.organization:
            user_keywords.append(target.organization.lower())
        if target.cities:
            user_keywords.extend([c.lower() for c in target.cities])
            
        for link in target.public_links:
            for val in link.values():
                if isinstance(val, str):
                    user_keywords.append(val.lower())

        best_template = None
        highest_score = -1

        # 2. Logica di selezione
        for template in self.templates:
            missing_required = False
            for placeholder in template.get("required_placeholders", []):
                if not target_dict.get(placeholder):
                    missing_required = True
                    break
            
            if missing_required:
                continue
            
            current_score = template.get("base_score", 0)
            matches = 0
            
            for keyword in template.get("trigger_keywords", []):
                if any(keyword.lower() in u_kw for u_kw in user_keywords):
                    matches += 1
            
            current_score += (matches * 15)

            if current_score > highest_score:
                highest_score = current_score
                best_template = template

        if not best_template:
            best_template = next((t for t in self.templates if t["template_id"] == "SIM-GEN-01"), self.templates[-1])
            highest_score = best_template.get("base_score", 10)

        name_val = target.name if target.name else "Utente"

        # 3. Manteniamo i template grezzi con i {placeholder} originali
        draft_subject = best_template["base_subject"]
        draft_body = best_template["base_body"]

        payload = CampaignPayload(
            target_name=name_val,
            template_id=best_template["template_id"],
            scenario_type=best_template["scenario_type"],
            score_achieved=highest_score,
            safety_constraints=best_template.get("safety_constraints", []),
            subject=draft_subject,
            body=draft_body
        )

        nome_file = f"payload_{name_val.replace(' ', '_')}.json"
        percorso_file = os.path.join(self.data_dir, nome_file)
        
        with open(percorso_file, "w", encoding="utf-8") as f:
            json.dump(payload.model_dump(), f, indent=4, ensure_ascii=False)

        return payload