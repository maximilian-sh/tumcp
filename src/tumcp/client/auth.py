"""TU Wien IdP login (SimpleSAMLphp + SAML2) without a browser.

Flow, as a browser performs it:
  1. GET a protected TISS page -> redirects to idp.zid.tuwien.ac.at
  2. POST username / password / TOTP to the credential form
  3. optionally acknowledge the "password quality" interstitial
  4. post the SAMLResponse form back to the service -> session cookie

Lessons that cost real time (keep them):
- Identify pages by CONTENT, never by URL: after posting credentials the IdP serves
  the SAML consent page from the very same `loginuserpass` URL.
- Never take the first <form>: TISS and the IdP render a site-search form first.
- TOTP codes are single-use; a code reused inside its 30 s window is rejected like a
  wrong password. Sessions are therefore persisted (see session.py).

Credentials are passed in by the caller and never logged.
"""
from __future__ import annotations

import logging
from urllib.parse import urljoin

import httpx2
from selectolax.lexbor import LexborHTMLParser

from . import dom
from .totp import totp

log = logging.getLogger(__name__)

IDP_HOST = "idp.zid.tuwien.ac.at"
TISS_LOGIN = "https://tiss.tuwien.ac.at/admin/authentifizierung"
TISS_PROBE = "https://tiss.tuwien.ac.at/education/favorites.xhtml"
MAX_HOPS = 12


class AuthError(RuntimeError):
    """Login failed (wrong credentials, TOTP reuse, or an unexpected page)."""


def form_with(tree: LexborHTMLParser, *field_names: str):
    """The <form> that actually carries one of these input names (or a password field)."""
    wanted = {n.lower() for n in field_names}
    for node in tree.css("form"):
        names = {(i.attributes.get("name") or "").lower() for i in node.css("input")}
        types = {(i.attributes.get("type") or "").lower() for i in node.css("input")}
        if wanted & names or ("password" in wanted and "password" in types):
            return node
    return None


def form_payload(form) -> dict[str, str]:
    """All named inputs of a form except submit buttons, plus the first submit button."""
    body: dict[str, str] = {}
    submit = None
    for inp in form.css("input"):
        name = inp.attributes.get("name")
        if not name:
            continue
        if (inp.attributes.get("type") or "").lower() in ("submit", "button", "image"):
            submit = submit or (name, inp.attributes.get("value") or "")
            continue
        body[name] = inp.attributes.get("value") or ""
    if submit:
        body.setdefault(*submit)
    return body


def looks_logged_in(html: str) -> bool:
    low = html.lower()
    return "logout" in low or "abmelden vom system" in low


async def post_saml(client: httpx2.AsyncClient, resp: httpx2.Response) -> httpx2.Response | None:
    """If `resp` is a SAML auto-submit page, post it and return the result."""
    form = form_with(dom.parse(resp.text), "SAMLResponse", "SAMLRequest")
    if form is None:
        return None
    body = {i.attributes["name"]: (i.attributes.get("value") or "")
            for i in form.css("input") if i.attributes.get("name")}
    action = urljoin(str(resp.url), form.attributes.get("action") or str(resp.url))
    return await client.post(action, data=body)


async def login(client: httpx2.AsyncClient, wurl, username: str, password: str,
                totp_secret: str | None) -> None:
    """Authenticate `client` against TISS (which also establishes the IdP session)."""
    if not username or not password:
        raise AuthError("username/password missing — run `tumcp login`")

    resp = await client.get(wurl(TISS_LOGIN))
    posted_credentials = False

    for hop in range(MAX_HOPS):
        url = str(resp.url)
        html = resp.text
        tree = dom.parse(html)

        if url.startswith("https://tiss.tuwien.ac.at/") and looks_logged_in(html):
            log.info("logged in after %d hop(s)", hop)
            return

        cred_form = form_with(tree, "username", "password")
        saml_form = form_with(tree, "SAMLResponse", "SAMLRequest")

        if cred_form is not None and saml_form is None:
            if posted_credentials:
                raise AuthError("the IdP rejected the login (wrong password, or the "
                                "current TOTP code was already used)")
            body: dict[str, str] = {}
            has_totp = False
            for inp in cred_form.css("input"):
                name = inp.attributes.get("name")
                if not name or (inp.attributes.get("type") or "").lower() == "submit":
                    continue
                body[name] = inp.attributes.get("value") or ""
                has_totp = has_totp or name.lower() == "totp"
            for key in list(body):
                low = key.lower()
                if low in ("username", "user", "j_username"):
                    body[key] = username
                elif low in ("password", "pass", "j_password"):
                    body[key] = password
                elif low == "totp":
                    if not totp_secret:
                        raise AuthError("the IdP asks for a TOTP code but no TOTP secret "
                                        "is configured — run `tumcp login`")
                    body[key] = totp(totp_secret)
            action = urljoin(url, cred_form.attributes.get("action") or url)
            resp = await client.post(action, data=body)
            posted_credentials = True
            continue

        if IDP_HOST in url and "tupwquality" in url:          # password-quality notice
            form = tree.css_first("form")
            if form is None:
                raise AuthError("password-quality page has no form")
            action = urljoin(url, form.attributes.get("action") or url)
            resp = await client.post(action, data=form_payload(form))
            continue

        if saml_form is not None:
            nxt = await post_saml(client, resp)
            if nxt is not None:
                resp = nxt
                continue

        probe = await client.get(wurl(TISS_PROBE))
        if looks_logged_in(probe.text):
            log.info("logged in (confirmed via probe)")
            return
        raise AuthError(f"login stuck at {url.split('?')[0]}")

    raise AuthError("too many redirects during login")
