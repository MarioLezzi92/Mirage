from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, HttpUrl, field_validator


class EvidenceType(str, Enum):
    PROFILE = "profile"
    EMAIL = "email"
    ROLE = "role"
    BIO = "bio"
    COMPANY = "company"
    LOCATION = "location"
    EDUCATION = "education"
    TECH_STACK = "tech_stack"
    WEB_MENTION = "web_mention"


class TargetInput(BaseModel):
    full_name: str = Field(min_length=1)
    company: str | None = None
    role: str | None = None
    department: str | None = None
    email: str | None = None
    github_username: str | None = None
    cities: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    public_links: list[HttpUrl] = Field(default_factory=list)
    # Contesto ricavato internamente da fonti indipendenti. Non fa parte
    # dell'input utente e non viene scritto nei JSON.
    corroboration: list[str] = Field(default_factory=list, exclude=True)
    # Profili ancora ambigui usati soltanto per cercare collegamenti pubblici
    # tra piattaforme. Non sono considerati automaticamente verificati.
    profile_hypotheses: dict[str, str] = Field(
        default_factory=dict,
        exclude=True,
    )

    @field_validator("full_name", "company", "role", "department", "email", "github_username")
    @classmethod
    def clean_text(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None


class SearchResult(BaseModel):
    url: HttpUrl
    title: str = ""
    snippet: str = ""
    query: str = ""


class CandidateProfile(BaseModel):
    platform: str
    url: HttpUrl
    username: str | None = None
    discovered_by: str
    title: str = ""
    snippet: str = ""
    context: str = ""
    explicit: bool = False
    related_profiles: list[str] = Field(default_factory=list)


class ProfileData(BaseModel):
    platform: str
    url: HttpUrl
    full_name: str | None = None
    username: str | None = None
    role: str | None = None
    bio: str | None = None
    company: str | None = None
    locations: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    tech_stack: list[str] = Field(default_factory=list)
    crosslinks: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class Evidence(BaseModel):
    source: str
    platform: str
    evidence_type: EvidenceType
    value: str
    url: HttpUrl | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DiscoveryOutput(BaseModel):
    candidates: list[CandidateProfile] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


class CollectionResult(BaseModel):
    target: TargetInput
    candidates: list[CandidateProfile] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    active_profile_agents: list[str] = Field(default_factory=list)


class ProfileLink(BaseModel):
    platform: str
    url: HttpUrl
    confidence: float = Field(ge=0.0, le=1.0)


class WebMention(BaseModel):
    title: str
    url: HttpUrl
    confidence: float = Field(ge=0.0, le=1.0)


class TargetProfile(BaseModel):
    name: str
    summary: str | None = None
    organization: str | None = None
    cities: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    social_links: list[ProfileLink] = Field(default_factory=list)
    mentions: list[WebMention] = Field(default_factory=list)
    tech_stack: list[str] = Field(default_factory=list)
