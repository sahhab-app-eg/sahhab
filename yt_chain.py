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
            try:
                resp = orig(self, ep, *args, **kwargs)
            except Exception as e:  # noqa: BLE001
                detail = ""
                code = getattr(e, "code", None)
                if code:
                    try:
                        detail = (e.read() or b"")[:120].decode("utf-8", "replace")
                    except Exception:  # noqa: BLE001
                        detail = ""
                _log("player call RAISED: %s code=%s body=%s" % (
                    type(e).__name__, code, detail))
                raise
            try:
                ps = (resp or {}).get("playabilityStatus") or {}
                _log("player response: status=%s reason=%s" % (
                    ps.get("status"), (ps.get("reason") or "")[:60]))
            except Exception:  # noqa: BLE001
                pass
            return resp
        return orig(self, ep, *args, **kwargs)

    _base.YoutubeBaseInfoExtractor._call_api = patched
    install_url_proxy()
    _installed = True
    _log("monkeypatch installed (tv client, PS4 UA, Bearer on player calls)")


_PROXY_HOSTS = ("googlevideo.com", "youtube.com", "youtubei.googleapis.com")


def _proxy_target(url):
    import urllib.parse as _up
    h = _up.urlparse(url).hostname or ""
    if any(h == p or h.endswith("." + p) for p in _PROXY_HOSTS):
        base = os.environ.get("YT_PROXY_URL", "").rstrip("/")
        if base:
            return base + "/?u=" + _up.quote(url, safe="")
    return None


def install_url_proxy():
    """Route all YouTube/googlevideo traffic through the YT_PROXY_URL reverse proxy.

    The proxy (a Cloudflare Worker) supplies clean egress IPs; the request keeps
    its headers/body verbatim, plus the X-Sahhab-Key shared-key header.
    """
    key = os.environ.get("YT_PROXY_KEY", "")
    if not (os.environ.get("YT_PROXY_URL") and key):
        return
    from yt_dlp import YoutubeDL
    from yt_dlp.networking import Request

    orig = YoutubeDL.urlopen

    def patched(self, req):
        nu = _proxy_target(req.url)
        if nu:
            headers = dict(req.headers)
            headers["X-Sahhab-Key"] = key
            req = Request(nu, data=req.data, headers=headers,
                          method=getattr(req, "method", None))
        return orig(self, req)

    YoutubeDL.urlopen = patched
    _log("url proxy active (YT_PROXY_URL)")


def chain_opts():
    """Extra YoutubeDL opts: tv client, POT provider, patched EJS solver."""
    _log("chain active, refresh token present")
    install()
    return {
        "extractor_args": {
            "youtube": {"player_client": ["tv_embedded"], "player_skip": ["webpage", "configs"]},
            "youtubepot-bgutilhttp": {"base_url": ["http://127.0.0.1:4416"]},
            # dev mode: our vendored solver build (upstream 0.8.0 + PR #80)
            # intentionally differs from the pinned release hash.
            "youtube-ejs": {"dev": ["true"]},
        },
        "remote_components": ["ejs:github"],
    }


def download_merged(url, dest, height, on_progress=None, cancel=None, base_opts=None):
    """Download video(avc1, <=height) + m4a audio with yt-dlp, merge to mp4 via ffmpeg.

    Same chain (Bearer + proxy + n-solver) as extraction. Returns (path, size).
    """
    import glob
    import yt_dlp  # type: ignore

    class _Cancel(Exception):
        pass

    base = os.path.splitext(dest)[0]
    state = {"t0": time.time(), "n": 0, "total": 0, "done": 0}

    def hook(d):
        if cancel is not None and cancel.is_set():
            raise _Cancel()
        if d.get("status") == "downloading" and on_progress:
            tot = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            got = d.get("downloaded_bytes") or 0
            on_progress(got, tot or None, d.get("speed") or 0, d.get("eta"))

    opts = dict(base_opts or {})
    opts.update(chain_opts())
    opts.update({
        "outtmpl": base + ".%(ext)s",
        "format": ("bv*[height<=%d][vcodec^=avc1][ext=mp4]+ba[ext=m4a]/b[ext=mp4]" % int(height)),
        "merge_output_format": "mp4",
        "progress_hooks": [hook],
        "noprogress": True,
        "concurrent_fragment_downloads": 1,
    })
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except _Cancel:
        for f in glob.glob(base + ".*"):
            try:
                os.remove(f)
            except OSError:
                pass
        from_app = sys.modules.get("__main__") or sys.modules.get("app")
        exc = getattr(from_app, "Cancelled", Exception)
        raise exc()
    except Exception as e:  # noqa: BLE001
        for f in glob.glob(base + ".*"):
            try:
                os.remove(f)
            except OSError:
                pass
        if "cancel" in type(e).__name__.lower():
            raise
        raise RuntimeError(str(e).split("\n")[0][:140])
    out = base + ".mp4"
    if not os.path.exists(out):
        cands = [f for f in glob.glob(base + ".*") if not f.endswith((".part", ".ytdl"))]
        if not cands:
            raise RuntimeError("no output")
        out = cands[0]
    return out, os.path.getsize(out)
