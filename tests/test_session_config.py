from app.telegram.session_config import (
    SessionCredentials,
    credential_candidates,
    load_session_credentials,
)


def test_session_credentials_can_override_env(tmp_path):
    config_path = tmp_path / "accounts.json"
    config_path.write_text(
        '{"account_a": {"api_id": 222, "api_hash": "json-hash"}}',
        encoding="utf-8",
    )

    configured = load_session_credentials(str(config_path))
    env = SessionCredentials(api_id=111, api_hash="env-hash")

    candidates = credential_candidates(
        "account_a",
        configured,
        env,
    )

    assert [(item.api_id, item.api_hash) for item in candidates] == [
        (111, "env-hash"),
        (222, "json-hash"),
    ]


def test_missing_session_file_uses_empty_credentials(tmp_path):
    assert load_session_credentials(str(tmp_path / "missing.json")) == {}
