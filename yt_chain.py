"""YouTube-on-datacenter-IP chain for Sahhab.

Bundles everything the yt-dlp fallback needs to pass YouTube's bot wall from a
hosting IP: a TV-client OAuth Bearer token (device-flow refresh token supplied
via env), the PlayStation UA YouTube currently accepts for TVHTML5, the bgutil
PO-token provider, and the patched EJS challenge solver (vendored under
vendor/yt_dlp_ejs - upstream 0.8.0 + PR #80 for ejs#79).

Secrets: only YT_OAUTH_REFRESH_TOKEN (env). The TV client_id/secret below are
the public ones shipped in yt-dlp's own source; they are not user secrets.
Nothing here is logged.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request


def _log(msg):
    # diagnostics only; never log token material
    print("[yt_chain] " + msg, file=sys.stderr, flush=True)

PS4_UA = ("Mozilla/5.0 (PlayStation; PlayStation 4/12.00) "
          "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15")

_TV_CLIENT_ID = ("861556708454-d6dlm3lh05idd8npek18k6be8ba3oc68"
                 ".apps.googleusercontent.com")
_TV_CLIENT_SECRET = "SboVhoG9s0rNafixCSGGKXAT"

_access_token = None
_access_exp = 0.0


def have_youtube_auth():
    return bool(os.environ.get("YT_OAUTH_REFRESH_TOKEN"))


def get_access_token():
    """Refresh the OAuth access token; cache it in memory until near expiry."""
    global _access_token, _access_exp
    now = time.time()
    if _access_token and now < _access_exp - 300:
        return _access_token
    rt = os.environ.get("YT_OAUTH_REFRESH_TOKEN")
    if not rt:
        return None
    body = urllib.parse.urlencode({
        "client_id": _TV_CLIENT_ID,
        "client_secret": _TV_CLIENT_SECRET,
        "refresh_token": rt,
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=body)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    except Exception as e:  # noqa: BLE001
        _log("token refresh FAILED: %s" % type(e).__name__)
        raise
    _access_token = data["access_token"]
    _access_exp = now + float(data.get("expires_in", 3600))
    _log("access token refreshed, expires_in=%s" % data.get("expires_in"))
    return _access_token


_installed = False


def install():
    """Monkeypatch yt-dlp's YouTube extractor for Bearer auth + PS4 UA (once)."""
    global _installed
    if _installed:
        return
    from yt_dlp.extractor.youtube import _base  # type: ignore

    clients = _base.INNERTUBE_CLIENTS.get("tv")
    if clients:
        clients["INNERTUBE_CONTEXT"]["client"]["userAgent"] = PS4_UA

    orig = _base.YoutubeBaseInfoExtractor._call_api

    def patched(self, ep, *args, **kwargs):
        if ep == "player":
            token = get_access_token()
            headers = dict(kwargs.get("headers") or {})
            if token:
                headers["Authorization"] = "Bearer " + token
            headers["User-Agent"] = PS4_UA
            kwargs["headers"] = headers
            resp = orig(self, ep, *args, **kwargs)
            try:
                ps = (resp or {}).get("playabilityStatus") or {}
                _log("player response: status=%s reason=%s" % (
                    ps.get("status"), (ps.get("reason") or "")[:60]))
            except Exception:  # noqa: BLE001
                pass
            return resp
        return orig(self, ep, *args, **kwargs)

    _base.YoutubeBaseInfoExtractor._call_api = patched
    _installed = True
    _log("monkeypatch installed (tv client, PS4 UA, Bearer on player calls)")


def chain_opts():
    """Extra YoutubeDL opts: tv client, POT provider, patched EJS solver."""
    _log("chain active, refresh token present")
    install()
    return {
        "extractor_args": {
            "youtube": {"player_client": ["tv"]},
            "youtubepot-bgutilhttp": {"base_url": ["http://127.0.0.1:4416"]},
            # dev mode: our vendored solver build (upstream 0.8.0 + PR #80)
            # intentionally differs from the pinned release hash.
            "youtube-ejs": {"dev": ["true"]},
        },
        "remote_components": ["ejs:github"],
    }
