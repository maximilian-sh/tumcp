"""TUWEL login when Moodle bounces it to the Moodle-app link (simulated TUWEL, no network).

A Moodle session that remembers admin/tool/mobile/launch.php as the page to return to
redirects the finished login to moodlemobile://token=…, which no HTTP client can open."""
import asyncio
import time

import httpx2

from tumcp.client.credentials import Credentials
from tumcp.client.session import TUWEL, TuClient

SAML_PAGE = (f'<form method="post" action="{TUWEL}/auth/saml2/sp/saml2-acs.php">'
             '<input type="hidden" name="SAMLResponse" value="x"></form>')
MY_PAGE = '<script>M.cfg = {"sesskey":"abc123"};</script><div data-userid="42"></div>'


def handler(request: httpx2.Request) -> httpx2.Response:
    url = str(request.url)
    if request.url.scheme == "moodlemobile":
        raise httpx2.UnsupportedProtocol("Request URL has an unsupported protocol")
    if url.startswith(f"{TUWEL}/auth/saml2/login.php"):
        return httpx2.Response(200, text=SAML_PAGE)
    if url.startswith(f"{TUWEL}/auth/saml2/sp/saml2-acs.php"):
        return httpx2.Response(303, headers={"location": "moodlemobile://token=abc"})
    if url.startswith(f"{TUWEL}/my/"):
        return httpx2.Response(200, text=MY_PAGE)
    return httpx2.Response(404)


def test_login_survives_redirect_to_the_app_link(tmp_path):
    async def run():
        tu = TuClient(Credentials("u", "p", None), data=tmp_path)
        await tu.http.aclose()
        tu.http = httpx2.AsyncClient(transport=httpx2.MockTransport(handler),
                                     follow_redirects=True)
        tu._tiss_checked = time.time()               # IdP session counts as fresh
        try:
            await tu._tuwel_login()
            return tu._sesskey, tu._userid
        finally:
            await tu.aclose()

    assert asyncio.run(run()) == ("abc123", 42)
