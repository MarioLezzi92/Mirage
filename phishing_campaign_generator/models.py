from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class TargetProfile(BaseModel):
    name: Optional[str] = None
    gender: Optional[str] = None
    birth_date: Optional[str] = None
    position: Optional[str] = None
    organization: Optional[str] = None
    cities: List[str] = Field(default_factory=list)
    education: List[Any] = Field(default_factory=list)
    contacts: List[str] = Field(default_factory=list)
    public_links: List[Dict[str, Any]] = Field(default_factory=list)
    tech_stack: List[str] = Field(default_factory=list)


class ConfirmedSource(BaseModel):
    platform: str
    url: str
    status: str = "confirmed"
    context: Optional[str] = None


class CampaignTarget(BaseModel):
    name: str
    organization: Optional[str] = None
    position: Optional[str] = None

    city: Optional[str] = None
    cities: List[str] = Field(default_factory=list)

    email: Optional[str] = None
    tech_stack: List[str] = Field(default_factory=list)
    platforms: List[str] = Field(default_factory=list)

    confirmed_sources: List[ConfirmedSource] = Field(default_factory=list)
    institutional_sources: List[ConfirmedSource] = Field(default_factory=list)


class CampaignSection(BaseModel):
    template_id: str
    scenario_type: str
    category: str
    subject_template: str
    body_template: str
    safety_constraints: List[str] = Field(default_factory=list)


class CampaignPayload(BaseModel):
    target: CampaignTarget
    campaign: CampaignSection