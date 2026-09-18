from app.telegram.accounts import AccountManager


def test_session_discovery_finds_all_session_files(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.telegram.accounts.TG_SESSION_DIR",
        str(tmp_path),
    )
    (tmp_path / "account_b.session").touch()
    (tmp_path / "account_a.session").touch()

    manager = AccountManager()
    assert [p.name for p in manager._session_paths()] == [
        "account_a.session",
        "account_b.session",
    ]


def test_session_discovery_falls_back_to_default_name(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.telegram.accounts.TG_SESSION_DIR",
        str(tmp_path),
    )
    monkeypatch.setattr(
        "app.telegram.accounts.TG_SESSION",
        "giveaway_auto_join",
    )

    manager = AccountManager()
    assert [p.name for p in manager._session_paths()] == [
        "giveaway_auto_join",
    ]
