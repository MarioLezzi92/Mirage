from fastapi import FastAPI, HTTPException

from target_information_collector.core.target_information_service import TargetInformationService
from target_information_collector.shared.models import TargetInput


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