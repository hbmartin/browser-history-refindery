"""Discovery over fake macOS and Linux home trees."""

from browser_history_refindery.browsers.base import BrowserFamily
from browser_history_refindery.browsers.discovery import Platform, discover_all


def test_discover_all(fake_home):
    profiles = discover_all(fake_home, platform_name="darwin")
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
    assert discover_all(tmp_path / "nonexistent", platform_name="darwin") == []


def test_unsupported_platform_returns_empty(fake_home):
    assert discover_all(fake_home, platform_name="win32") == []


def test_discover_linux(fake_linux_home):
    profiles = discover_all(fake_linux_home, platform_name="linux")
    by_key = {profile.key: profile for profile in profiles}

    assert by_key["chrome:Default"].profile_name == "Harold"
    assert by_key["chrome:Profile 1"].profile_name == "Work"
    assert by_key["brave:Default"].browser_label == "Brave"
    assert by_key["chromium:Default"].browser_label == "Chromium"

    # No Safari on Linux.
    assert not [p for p in profiles if p.family is BrowserFamily.SAFARI]
    # Electron app under ~/.config is not mistaken for a browser.
    assert not [p for p in profiles if "electron" in p.browser_id.lower()]


def test_discover_linux_finds_both_firefox_installs(fake_linux_home):
    profiles = discover_all(fake_linux_home, platform_name="linux")
    firefox = [p for p in profiles if p.family is BrowserFamily.FIREFOX]
    # Both the ~/.mozilla and the Snap Firefox installs are discovered.
    paths = {str(p.history_path) for p in firefox}
    assert len(paths) == 2
    assert any(".mozilla" in path and "snap" not in path for path in paths)
    assert any("snap" in path for path in paths)


def test_discover_linux_missing_home(tmp_path):
    assert discover_all(tmp_path / "nonexistent", platform_name="linux") == []


def test_platform_detection_via_discovery(fake_linux_home):
    # A macOS scan of a Linux tree finds nothing (no ~/Library).
    assert discover_all(fake_linux_home, platform_name="darwin") == []
    assert discover_all(fake_linux_home, platform_name="linux2") != []


def test_known_chromium_specs_cover_linux():
    from browser_history_refindery.browsers.discovery import KNOWN_CHROMIUM

    chrome = next(spec for spec in KNOWN_CHROMIUM if spec.browser_id == "chrome")
    assert chrome.data_dir(Platform.LINUX) == "google-chrome"
    assert chrome.data_dir(Platform.MACOS) == "Google/Chrome"
    # Browsers with no Linux build report None for Linux.
    dia = next(spec for spec in KNOWN_CHROMIUM if spec.browser_id == "dia")
    assert dia.data_dir(Platform.LINUX) is None
