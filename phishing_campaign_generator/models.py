from pydantic import BaseModel, Field


class PublicLink(BaseModel):
    url: str | None = None
    platform: str | None = None
    status: str | None = None
    context: str | None = None
    matched_context: list[str] = Field(default_factory=list)


class TargetProfile(BaseModel):
    name: str | None = None
    gender: str | None = None
    birth_date: str | None = None
    position: str | None = None
    organization: str | None = None
    cities: list[str] = Field(default_factory=list)
    education: list[dict | str] = Field(default_factory=list)
    contacts: list[str] = Field(default_factory=list)
    public_links: list[PublicLink] = Field(default_factory=list)
    tech_stack: list[str] = Field(default_factory=list)


class CampaignTarget(BaseModel):
    name: str
    organization: str | None = None
    position: str | None = None
    city: str | None = None
    email: str | None = None
    tech_stack: list[str] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)


class CampaignSpec(BaseModel):
    template_id: str
    scenario_type: str
    category: str
    subject_template: str
    body_template: str
    safety_constraints: list[str] = Field(default_factory=list)


class CampaignPayload(BaseModel):
    target: CampaignTarget
    campaign: CampaignSpec