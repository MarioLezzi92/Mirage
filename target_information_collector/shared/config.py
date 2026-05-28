from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    github_token: str | None = None

    apify_token: str | None = None
    apify_actor_id: str | None = None

    apify_linkedin_profile_actor_id: str | None = None
    apify_instagram_profile_actor_id: str | None = None
    apify_facebook_profile_actor_id: str | None = None


    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()