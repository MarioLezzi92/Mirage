from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

class TargetProfile(BaseModel):
    name: Optional[str] = None
    organization: Optional[str] = None
    tech_stack: List[str] = Field(default_factory=list)
    cities: List[str] = Field(default_factory=list)
    contacts: List[str] = Field(default_factory=list)
    public_links: List[Dict[str, Any]] = Field(default_factory=list)
    gender: Optional[str] = None
    birth_date: Optional[str] = None
    position: Optional[str] = None
    education: List[Any] = Field(default_factory=list)

class CampaignPayload(BaseModel):
    target_name: str
    template_id: str
    scenario_type: str
    score_achieved: int
    safety_constraints: List[str]
    subject: str
    body: str