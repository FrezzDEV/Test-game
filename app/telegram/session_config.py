import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SessionCredentials:
    api_id: int | None = None
    api_hash: str | None = None
    phone: str | None = None
    two_fa_password: str | None = None


def load_session_credentials(path: str) -> dict[str, SessionCredentials]:
    file_path = Path(path)
    if not file_path.exists():
        return {}

    with file_path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)

    if not isinstance(raw, dict):
        raise ValueError("Session accounts config must be a JSON object")

    result: dict[str, SessionCredentials] = {}
    for session_name, value in raw.items():
        if not isinstance(session_name, str) or not isinstance(value, dict):
            raise ValueError(
                "Each session config must be an object keyed by session name"
            )

        api_id_raw = value.get("api_id")
        api_hash_raw = value.get("api_hash")
        api_id = int(api_id_raw) if api_id_raw not in (None, "") else None
        api_hash = str(api_hash_raw).strip() if api_hash_raw else None

        result[session_name] = SessionCredentials(
            api_id=api_id,
            api_hash=api_hash,
            phone=str(value["phone"]).strip() if value.get("phone") else None,
            two_fa_password=(
                str(value["2fa_password"]).strip()
                if value.get("2fa_password")
                else (
                    str(value["two_fa_password"]).strip()
                    if value.get("two_fa_password")
                    else None
                )
            ),
        )
    return result


def credential_candidates(
    session_name: str,
    configured: dict[str, SessionCredentials],
    env_credentials: SessionCredentials,
) -> list[SessionCredentials]:
    override = configured.get(session_name)
    candidates: list[SessionCredentials] = []

    # Try the global .env credentials first. If they fail, fall back to the
    # session-specific credentials from config/accounts.json.
    if env_credentials.api_id and env_credentials.api_hash:
        candidates.append(env_credentials)

    if override and override.api_id and override.api_hash:
        if not candidates or (
            candidates[0].api_id != override.api_id
            or candidates[0].api_hash != override.api_hash
        ):
            candidates.append(override)

    return candidates
