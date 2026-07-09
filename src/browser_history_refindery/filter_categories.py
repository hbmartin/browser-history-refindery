"""Shipped sensitive-category blocklist data.

Domains match exactly or as a parent of the URL's host (``chase.com`` matches
``www.chase.com``). Path keywords are literal substrings checked against the
URL path.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Category:
    """One named sensitive category of URLs to skip."""

    name: str
    domains: frozenset[str]
    path_keywords: tuple[str, ...] = field(default=())


BANKING = Category(
    name="banking",
    domains=frozenset(
        {
            "ally.com",
            "americanexpress.com",
            "bankofamerica.com",
            "capitalone.com",
            "cash.app",
            "chase.com",
            "chime.com",
            "citi.com",
            "citibank.com",
            "coinbase.com",
            "discover.com",
            "etrade.com",
            "fidelity.com",
            "kraken.com",
            "navyfederal.org",
            "paypal.com",
            "pnc.com",
            "robinhood.com",
            "schwab.com",
            "sofi.com",
            "truist.com",
            "usbank.com",
            "vanguard.com",
            "venmo.com",
            "wellsfargo.com",
            "wise.com",
        }
    ),
)

HEALTH = Category(
    name="health",
    domains=frozenset(
        {
            "aetna.com",
            "anthem.com",
            "athenahealth.com",
            "betterhelp.com",
            "bcbs.com",
            "cigna.com",
            "clevelandclinic.org",
            "cvs.com",
            "followmyhealth.com",
            "goodrx.com",
            "kaiserpermanente.org",
            "mayoclinic.org",
            "mychart.com",
            "riteaid.com",
            "talkspace.com",
            "uhc.com",
            "walgreens.com",
            "webmd.com",
            "zocdoc.com",
        }
    ),
    path_keywords=("/mychart",),
)

AUTH_WEBMAIL = Category(
    name="auth_webmail",
    domains=frozenset(
        {
            "accounts.google.com",
            "appleid.apple.com",
            "auth0.com",
            "duosecurity.com",
            "fastmail.com",
            "login.microsoftonline.com",
            "login.yahoo.com",
            "mail.aol.com",
            "mail.google.com",
            "mail.proton.me",
            "mail.yahoo.com",
            "okta.com",
            "onelogin.com",
            "outlook.live.com",
            "outlook.office.com",
        }
    ),
    path_keywords=(
        "/login",
        "/logout",
        "/signin",
        "/sign-in",
        "/sign_in",
        "/signout",
        "/sign-out",
        "/oauth",
        "/sso",
        "/auth/",
        "/authorize",
        "/password",
        "/2fa",
        "/mfa",
    ),
)

ADULT = Category(
    name="adult",
    domains=frozenset(
        {
            "adultfriendfinder.com",
            "brazzers.com",
            "cam4.com",
            "chaturbate.com",
            "fansly.com",
            "onlyfans.com",
            "pornhub.com",
            "redtube.com",
            "stripchat.com",
            "xhamster.com",
            "xnxx.com",
            "xvideos.com",
            "youporn.com",
        }
    ),
)

ALL_CATEGORIES: tuple[Category, ...] = (BANKING, HEALTH, AUTH_WEBMAIL, ADULT)
