from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    github_token: str = ""
    github_owner: str = ""
    github_repo: str = ""
    # Preserve the raw token so github_issue_service.resolve_dry_run can treat
    # invalid values as dry-run instead of failing during settings parsing.
    github_dry_run: str | bool = "true"
    gemini_api_key: str = ""
    # Optional agent layer. Default OFF: the deterministic investigation service
    # stays the source of truth and default behavior. When enabled, agent output
    # is still re-validated and grounded, and falls back to deterministic if it
    # is invalid or weaker. This flag never enables any external write action.
    agent_mode: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
