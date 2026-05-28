import json
from fastapi import FastAPI, HTTPException
from target_information_collector.profiling.profile_builder import ProfileBuilder
from target_information_collector.profiling.structured_profile_builder import StructuredProfileBuilder
from target_information_collector.output.json_writer import JsonProfileWriter
from target_information_collector.resolution.identity_resolver import IdentityResolver
from target_information_collector.shared.models import TargetInput, PublicProfile, Evidence
from pathlib import Path

app = FastAPI(title="SocialEng", version="0.4.0")

@app.on_event("startup")
def startup_event():
    print("\n" + "="*60)
    print("🚀  SocialEng API scaricata e pronta all'uso!")
    print("🔗  Server locale:      http://127.0.0.1:8000")
    print("📖  Interactive Docs:   http://127.0.0.1:8000/docs")
    print("="*60 + "\n")
    
pb = ProfileBuilder()
jw = JsonProfileWriter()
sb = StructuredProfileBuilder()
ir = IdentityResolver()

@app.post("/target-information-collector/collect")
def collect_target_information(target: TargetInput):
    try:
        # ========================================================
        # 🛑 MOCK ATTIVO: COMMENTIAMO IL CRAWLER LIVE PER I TEST
        # ========================================================
        # raw_data = pb.collect_raw(target)
        # raw_filename = jw.save(target.full_name, raw_data)
        
        # Forziamo il caricamento dal file locale congelato
        raw_filename = "mario-lezzi-raw-1.json"
        print(f"🛠️ MODALITÀ TEST: Caricamento dati locali da {raw_filename}...")
        
        file_path = Path(r"C:\Users\mario\OneDrive\Desktop\SocialEng\target_information_collector\data\raw") / raw_filename

        with open(file_path, "r", encoding="utf-8") as f:
            saved_raw_data = json.load(f)
        # ========================================================

        raw_candidates_list = saved_raw_data.get("candidates", [])
        raw_evidence_list = saved_raw_data.get("evidence", [])
        
        # ==========================================
        # FASE 3: RISOLUZIONE GENERICA & STRUTTURAZIONE
        # ==========================================
        evidence_objects = [Evidence(**ev) for ev in raw_evidence_list]
        profiles = [PublicProfile(**c) for c in raw_candidates_list]
        
        # Il resolver screma i dati grezzi applicando l'algoritmo di esclusione delle omonimie
        resolved = ir.resolve(
            target=target,
            candidates=[],
            profiles=profiles, 
            evidence=evidence_objects
        )
        
        tech_stack = list(set([
            ev.value for ev in evidence_objects 
            if str(ev.evidence_type) == "tech_stack" and ev.value
        ]))
        
        contacts = list(set([
            ev.value for ev in evidence_objects 
            if str(ev.evidence_type) == "email" and ev.value
        ]))
        
        target_profile_dict = {
            "target": target.model_dump(mode="json"),
            "identity_candidates": [c.model_dump(mode="json") for c in resolved.get("identity_candidates", [])],
            "public_profiles": [p.model_dump(mode="json") for p in resolved.get("public_profiles", [])],
            "evidence": raw_evidence_list,
            "tech_stack": tech_stack,
            "contact": {
                "email": contacts[0] if contacts else None,
                "status": "PUBLIC_CONFIRMED" if contacts else "NOT_FOUND",
                "confidence": 0.85 if contacts else 0.0,
                "campaign_eligible": True if contacts else False,
                "reason": "Found during web scraping" if contacts else "No emails discovered",
                "evidence": []
            } if contacts else None
        }
        
        structured_filename = sb.build_from_raw(target_profile_dict)
        
        return {
            "status": "success",
            "raw_file": raw_filename,
            "structured_file": structured_filename
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))