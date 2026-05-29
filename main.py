from fastapi import FastAPI, HTTPException, UploadFile, File    
import json
from target_information_collector.core.target_information_service import TargetInformationService
from target_information_collector.shared.models import TargetInput
from phishing_campaign_generator.models import TargetProfile
from phishing_campaign_generator.service import CampaignGeneratorService

app = FastAPI(title="SocialEng", version="0.5.0")

service = TargetInformationService(
    use_mock_raw=False,  # Metti False per raccolta live
    mock_raw_filename="mario-lezzi-raw-1.json",
)


@app.on_event("startup")
def startup_event():
    print("\n" + "=" * 60)
    print("🚀  SocialEng API pronta all'uso")
    print("🔗  Server locale:      http://127.0.0.1:8000")
    print("📖  Interactive Docs:   http://127.0.0.1:8000/docs")
    print("=" * 60 + "\n")


@app.post("/target-information-collector/collect")
def collect_target_information(target: TargetInput):
    try:
        return service.collect(target)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))



campaign_service = CampaignGeneratorService()

@app.post("/phishing-campaign-generator/generate")
async def generate_campaign(file: UploadFile = File(...)):
    try:
        content = await file.read()
        data = json.loads(content)
        target = TargetProfile(**data)
        
        # Ora il service si occupa sia di generare che di salvare il file!
        return campaign_service.generate_payload(target)
        
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Il file caricato non è un JSON valido.")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))