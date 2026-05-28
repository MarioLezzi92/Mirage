from target_information_collector.agents.facebook_agent import FacebookAgent
from target_information_collector.agents.github_agent import GitHubAgent
from target_information_collector.agents.instagram_agent import InstagramAgent
from target_information_collector.agents.linkedin_agent import LinkedInAgent
from target_information_collector.agents.web_agent import WebAgent
from target_information_collector.evidence.evidence_store import EvidenceStore
from target_information_collector.shared.models import TargetInput


class CollectorPipeline:
    """
    Orchestratore della raccolta raw.

    Gli agenti raccolgono evidenze pubbliche.
    La risoluzione identitaria viene fatta dopo da IdentityResolver.
    """

    def __init__(self):
        self.web_agent = WebAgent()
        self.github_agent = GitHubAgent()
        self.linkedin_agent = LinkedInAgent()
        self.facebook_agent = FacebookAgent()
        self.instagram_agent = InstagramAgent()

    def collect_raw(self, target: TargetInput) -> dict:
        store = EvidenceStore(target)

        self.web_agent.collect_base(store)

        self.github_agent.collect(store)
        self.linkedin_agent.collect(store)

        self.web_agent.collect_social_contextual(store)

        self.facebook_agent.collect(store)
        self.instagram_agent.collect(store)

        return store.as_raw_dict()