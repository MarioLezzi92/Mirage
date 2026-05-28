from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    github_token: str | None = None

    apify_token: str | None = None
    apify_actor_id: str = "apify~google-search-scraper"

    apify_linkedin_profile_actor_id: str = "supreme_coder~linkedin-profile-scraper"
    apify_instagram_profile_actor_id: str = "apify~instagram-profile-scraper"
    apify_facebook_profile_actor_id: str = "lazyscraper~facebook-profile-scraper"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()