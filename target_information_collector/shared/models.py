from enum import Enum
from typing import Any, Optional, List
from pydantic import BaseModel, Field


class AuthorizationStatus(str, Enum):
    AUTHORIZED = "AUTHORIZED"
    UNAUTHORIZED = "UNAUTHORIZED"


class EvidenceSource(str, Enum):
    INPUT = "input"
    GITHUB = "github"
    WEB = "web"
    LINKEDIN = "linkedin"
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"
    APIFY = "apify"


class EvidenceType(str, Enum):
    IDENTITY = "identity"
    PROFILE = "profile"
    PUBLIC_LINK = "public_link"
    EMAIL = "email"
    LOCATION = "location"
    EDUCATION = "education"
    ORGANIZATION = "organization"
    ROLE = "role"
    TECH_STACK = "tech_stack"
    SOCIAL_HINT = "social_hint"
    WEB_MENTION = "web_mention"
    ERROR = "error"


class CandidateStatus(str, Enum):
    UNVERIFIED = "unverified"
    CANDIDATE = "candidate"
    PROBABLE = "probable"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class TargetInput(BaseModel):
    full_name: str
    birth_date: Optional[str] = None
    gender: Optional[str] = None
    cities: List[str] = Field(default_factory=list)
    education: List[str] = Field(default_factory=list)
    contacts: List[str] = Field(default_factory=list)
    public_links: List[str] = Field(default_factory=list)
    aliases: List[str] = Field(default_factory=list)

    location: Optional[str] = None
    company: Optional[str] = None
    role: Optional[str] = None
    department: Optional[str] = None
    email: Optional[str] = None
    email_domain: Optional[str] = None
    linkedin_url: Optional[str] = None
    github_username: Optional[str] = None


class Evidence(BaseModel):
    source: EvidenceSource | str
    evidence_type: EvidenceType | str
    value: Optional[str] = None
    url: Optional[str] = None
    platform: Optional[str] = None
    username: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    confidence: float = 0.0
    raw_data: dict[str, Any] = Field(default_factory=dict)


class CandidateProfile(BaseModel):
    platform: str
    url: str
    username: Optional[str] = None
    display_name: Optional[str] = None
    confidence: float = 0.0
    matched_context: List[str] = Field(default_factory=list)
    raw_data: dict[str, Any] = Field(default_factory=dict)


class PublicProfile(BaseModel):
    platform: str
    url: str
    username: Optional[str] = None
    confidence: float = 0.0


class IdentityCandidate(BaseModel):
    candidate_id: str
    full_name: Optional[str] = None
    platform: str
    profile_url: str
    username: Optional[str] = None
    company: Optional[str] = None
    role: Optional[str] = None
    location: Optional[str] = None

    matched_fields: List[str] = Field(default_factory=list)
    confidence: float = 0.0
    status: CandidateStatus | str = CandidateStatus.UNVERIFIED
    positive_evidence: List[str] = Field(default_factory=list)
    negative_evidence: List[str] = Field(default_factory=list)
    reason: Optional[str] = None

    evidence: List[Evidence] = Field(default_factory=list)


class Contact(BaseModel):
    email: Optional[str] = None
    status: Optional[str] = None
    confidence: float = 0.0
    campaign_eligible: bool = False
    reason: Optional[str] = None
    evidence: List[Any] = Field(default_factory=list)


class TargetProfile(BaseModel):
    target: TargetInput
    identity_candidates: List[IdentityCandidate] = Field(default_factory=list)
    public_profiles: List[PublicProfile] = Field(default_factory=list)
    evidence: List[Evidence] = Field(default_factory=list)
    tech_stack: List[str] = Field(default_factory=list)
    contact: Optional[Contact] = None


class StructuredPublicLink(BaseModel):
    url: str
    platform: str
    status: CandidateStatus | str = CandidateStatus.CANDIDATE
    context: Optional[str] = None
    matched_context: List[str] = Field(default_factory=list)


class StructuredProfile(BaseModel):
    name: str
    gender: Optional[str] = None
    birth_date: Optional[str] = None
    position: Optional[str] = None
    organization: Optional[str] = None
    cities: List[str] = Field(default_factory=list)
    education: List[str] = Field(default_factory=list)
    contacts: List[str] = Field(default_factory=list)
    public_links: List[StructuredPublicLink] = Field(default_factory=list)
    tech_stack: List[str] = Field(default_factory=list)