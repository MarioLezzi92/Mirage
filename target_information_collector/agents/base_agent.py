from abc import ABC, abstractmethod

from target_information_collector.shared.models import (
    CandidateProfile,
    DiscoveryOutput,
    Evidence,
    TargetInput,
)


class DiscoveryAgent(ABC):
    name: str

    @abstractmethod
    def discover(self, target: TargetInput) -> DiscoveryOutput:
        raise NotImplementedError


class ProfileAgent(ABC):
    platform: str

    @abstractmethod
    def collect(self, target: TargetInput, candidate: CandidateProfile) -> list[Evidence]:
        raise NotImplementedError
