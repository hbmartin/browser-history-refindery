"""Discovery over a fake home tree."""

from browser_history_refindery.browsers.discovery import discover_all


def test_discover_all(fake_home):
    profiles = discover_all(fake_home)
    by_key = {profile.key: profile for profile in profiles}

    assert by_key["chrome:Default"].profile_name == "Harold"
    assert by_key["chrome:Profile 1"].profile_name == "Work"
    assert by_key["dia:Default"].browser_label == "Dia"
    assert by_key["safari:Safari"].history_path.name == "History.db"

    firefox = [profile for profile in profiles if profile.browser_id == "firefox"]
    assert len(firefox) == 1  # the profile without places.sqlite is excluded
    assert firefox[0].profile_name == "default-release"

    assert not [p for p in profiles if "electron" in p.browser_id.lower()]


def test_discover_missing_home(tmp_path):
    assert discover_all(tmp_path / "nonexistent") == []
