from pathlib import Path

from app.tools.redactor import (
    REDACTION_MARKER,
    contains_secret,
    redact_text,
)

# Fake, demo-only secrets. None are real credentials.
GHP_TOKEN = "ghp_fakeTokenForDemoOnly1234567890"
PAT_TOKEN = "github_pat_11ABCDEFG0fakeFineGrainedToken1234567890"
SK_TOKEN = "sk-fakeOpenAIKey1234567890abcdefghij"
DB_URL = "DATABASE_URL=postgres://user:password@example.com:5432/payments"

# The exact fake secrets planted in the secret_in_logs fixture log.
SECRET_IN_LOGS_CI_LOG = (
    Path(__file__).resolve().parents[1]
    / "demo"
    / "incidents"
    / "secret_in_logs"
    / "ci.log"
)
RAW_FAKE_SECRETS = [
    "ghp_fakeTokenForDemoOnly1234567890",
    "fake.jwt.token",
    "postgres://user:password@example.com:5432/payments",
    "fake-api-key-12345",
]


def _types(result):
    return {finding.type for finding in result.findings}


# 1. ghp_ classic token --------------------------------------------------------
def test_redacts_ghp_token():
    result = redact_text(f"Using GitHub token {GHP_TOKEN} now")
    assert GHP_TOKEN not in result.redacted_text
    assert "type=github_token" in result.redacted_text
    assert result.redactions_applied == 1
    assert "github_token" in _types(result)


# 2. github_pat_ fine-grained token -------------------------------------------
def test_redacts_github_pat_token():
    result = redact_text(f"token: {PAT_TOKEN}")
    assert PAT_TOKEN not in result.redacted_text
    assert "type=github_fine_grained_token" in result.redacted_text
    assert result.redactions_applied == 1


# 3. sk- OpenAI-style token ----------------------------------------------------
def test_redacts_sk_token():
    result = redact_text(f"OPENAI_API_KEY {SK_TOKEN}")
    assert SK_TOKEN not in result.redacted_text
    assert "type=openai_token" in result.redacted_text


# 4. Bearer token --------------------------------------------------------------
def test_redacts_bearer_token():
    result = redact_text("Authorization: Bearer abc.def.ghijklmnop")
    assert "abc.def.ghijklmnop" not in result.redacted_text
    assert "type=bearer_token" in result.redacted_text


# 5. api_key key/value (both = and :) -----------------------------------------
def test_redacts_api_key_key_value():
    eq = redact_text("api_key=supersecretvalue123")
    colon = redact_text("api_key: supersecretvalue123")
    assert "supersecretvalue123" not in eq.redacted_text
    assert "supersecretvalue123" not in colon.redacted_text
    assert "type=api_key" in eq.redacted_text
    assert "type=api_key" in colon.redacted_text


# 6. password key/value --------------------------------------------------------
def test_redacts_password_key_value():
    result = redact_text("password=hunter2 password: hunter2")
    assert "hunter2" not in result.redacted_text
    assert result.redactions_applied == 2
    assert _types(result) == {"password"}


# 7. DATABASE_URL --------------------------------------------------------------
def test_redacts_database_url():
    result = redact_text(DB_URL)
    assert "postgres://user:password@example.com:5432/payments" not in result.redacted_text
    assert "type=database_url" in result.redacted_text


# 8. No full secret leaks into findings ---------------------------------------
def test_findings_do_not_leak_full_secret():
    result = redact_text(f"token {GHP_TOKEN} and api_key=supersecretvalue123")
    assert result.findings
    for finding in result.findings:
        assert finding.original_preview.endswith("...")
        assert len(finding.original_preview) <= 7  # 4 chars + "..."
        assert GHP_TOKEN not in finding.original_preview
        assert "supersecretvalue123" not in finding.original_preview
        assert GHP_TOKEN not in finding.replacement


# 9. Idempotency ---------------------------------------------------------------
def test_redaction_is_idempotent():
    text = (
        f"{GHP_TOKEN} {PAT_TOKEN} {SK_TOKEN} {DB_URL} "
        "Bearer abc.def.ghijklmnop api_key=secretvalue123 password=hunter2"
    )
    first = redact_text(text)
    second = redact_text(first.redacted_text)

    assert second.redacted_text == first.redacted_text
    assert second.redactions_applied == 0
    assert not contains_secret(first.redacted_text)


# 10. Clean text is unchanged with zero redactions ----------------------------
def test_clean_text_returns_same_text_and_zero_redactions():
    text = (
        "Deploy succeeded. The login is passwordless and api_key_missing was "
        "logged as a warning, but no credentials appeared."
    )
    result = redact_text(text)

    assert result.redacted_text == text
    assert result.redactions_applied == 0
    assert result.findings == []
    assert REDACTION_MARKER not in result.redacted_text
    assert contains_secret(text) is False


# 11. The secret_in_logs fixture log redacts cleanly with no raw secret leak. ---
def test_secret_in_logs_fixture_ci_log_redacts_without_leak():
    ci_log = SECRET_IN_LOGS_CI_LOG.read_text(encoding="utf-8")
    # Sanity: the fixture really does contain the raw fake secrets up front.
    for secret in RAW_FAKE_SECRETS:
        assert secret in ci_log, f"fixture missing planted secret: {secret}"

    result = redact_text(ci_log)

    # Every planted secret is removed; the typed marker is present instead.
    assert result.redactions_applied >= len(RAW_FAKE_SECRETS)
    assert REDACTION_MARKER in result.redacted_text
    for secret in RAW_FAKE_SECRETS:
        assert secret not in result.redacted_text
    assert not contains_secret(result.redacted_text)
