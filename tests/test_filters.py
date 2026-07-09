"""Table-driven exclusion engine tests."""

import pytest

from browser_history_refindery.config import ExclusionsConfig
from browser_history_refindery.filters import ExclusionEngine, SkipKind


@pytest.fixture
def engine():
    return ExclusionEngine(ExclusionsConfig())


@pytest.mark.parametrize(
    "url",
    [
        "chrome://settings/",
        "about:blank",
        "file:///Users/x/doc.pdf",
        "chrome-extension://abcdef/page.html",
        "moz-extension://abcdef/page.html",
        "javascript:void(0)",
        "data:text/html,hi",
        "arc://boost/",
    ],
)
def test_scheme_exclusions(engine, url):
    reason = engine.check(url)
    assert reason is not None
    assert reason.kind is SkipKind.SCHEME


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:3000/dev",
        "http://127.0.0.1:8000/admin",
        "http://[::1]/x",
        "http://10.0.0.5/router",
        "http://192.168.1.1/",
        "http://172.16.4.2/x",
        "http://mymachine.local/share",
    ],
)
def test_private_host_exclusions(engine, url):
    reason = engine.check(url)
    assert reason is not None
    assert reason.kind is SkipKind.PRIVATE_HOST


@pytest.mark.parametrize(
    ("url", "expected_rule_part"),
    [
        ("https://www.chase.com/checking", "banking"),
        ("https://mychart.example-hospital.org/mychart/billing", "health"),
        ("https://mail.google.com/mail/u/0/", "auth_webmail"),
        ("https://github.com/login", "auth_webmail"),
        ("https://app.example.com/oauth/callback?code=x", "auth_webmail"),
    ],
)
def test_category_exclusions(engine, url, expected_rule_part):
    reason = engine.check(url)
    assert reason is not None
    assert reason.kind is SkipKind.CATEGORY
    assert expected_rule_part in reason.rule


def test_adult_category_off_by_default(engine):
    assert engine.check("https://pornhub.com/x") is None


def test_adult_category_can_be_enabled():
    engine = ExclusionEngine(ExclusionsConfig(adult=True))
    reason = engine.check("https://pornhub.com/x")
    assert reason is not None
    assert "adult" in reason.rule


def test_categories_can_be_disabled():
    engine = ExclusionEngine(
        ExclusionsConfig(banking=False, health=False, auth_webmail=False)
    )
    assert engine.check("https://www.chase.com/checking") is None


def test_user_skip_domains():
    engine = ExclusionEngine(ExclusionsConfig(skip_domains=["*.corp.example", "x.io"]))
    reason = engine.check("https://dash.corp.example/reports")
    assert reason is not None
    assert reason.kind is SkipKind.USER_PATTERN
    assert engine.check("https://x.io/page") is not None
    assert engine.check("https://notx.io/page") is None


def test_user_skip_patterns():
    engine = ExclusionEngine(
        ExclusionsConfig(skip_patterns=["https://*.slack.com/archives/*"])
    )
    assert engine.check("https://team.slack.com/archives/C123/p456") is not None
    assert engine.check("https://team.slack.com/home") is None


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/article",
        "https://news.ycombinator.com/item?id=1",
        "https://en.wikipedia.org/wiki/Python",
    ],
)
def test_normal_urls_pass(engine, url):
    assert engine.check(url) is None
