"""GitHub issue route — approval- and safety-gated, dry-run by default."""

from fastapi import APIRouter, Body, Depends

from app.config import Settings
from app.dependencies import settings_dependency
from app.schemas.approval import GitHubIssueOptions, GitHubIssueResult
from app.services.github_issue_service import create_github_issue as create_issue
from app.services.github_issue_service import github_settings_from_env

router = APIRouter(prefix="/incidents", tags=["github"])


@router.post("/{incident_id}/github/issue", response_model=GitHubIssueResult)
def create_github_issue(
    incident_id: str,
    options: GitHubIssueOptions | None = Body(default=None),
    settings: Settings = Depends(settings_dependency),
) -> GitHubIssueResult:
    """Preview or create the GitHub issue for an approved, grounded report."""
    options = options or GitHubIssueOptions()
    config = github_settings_from_env(
        {
            "GITHUB_TOKEN": settings.github_token,
            "GITHUB_OWNER": settings.github_owner,
            "GITHUB_REPO": settings.github_repo,
            "GITHUB_DRY_RUN": str(settings.github_dry_run),
        }
    )
    if options.dry_run is True:
        config = type(config)(
            token=config.token, owner=config.owner, repo=config.repo, dry_run=True
        )

    outcome = create_issue(
        incident_id,
        config=config,
        labels=options.labels,
    )
    return GitHubIssueResult(
        incident_id=outcome.incident_id,
        created=outcome.created,
        dry_run=outcome.dry_run,
        title=outcome.title,
        body_preview=outcome.body_preview,
        labels=outcome.labels,
        issue_url=outcome.url,
        issue_number=outcome.number,
        message=outcome.message,
    )
