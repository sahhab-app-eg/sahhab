#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================
 سحّاب (Sahhab) — نسخة الموقع
 موقع تحميل فيديوهات بشكل احترافي، بيشتغل على Pydroid 3
 من غير ولا مكتبة خارجية (بايثون صافي)

 التشغيل:  شغّل الملف، وبعدين افتح اللينك اللي هيظهر
           (غالبا http://127.0.0.1:8765) في كروم.
 الاختبار: python sahhab_web.py --test
=============================================================
"""

import json
import os
import re
import socket
import ssl
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "2.1.0"
PORT = 8765

# ---------------------------------------------------------------
# رسايل مصري
# ---------------------------------------------------------------
MSG = {
    "empty_url": "يا عم انت كده مش لاعب.. حط لينك الاول 😅",
    "bad_url": "ده مش لينك ده كلام فارغ! لينك يبدأ بـ http يا نجم 🔗",
    "no_youtube_id": "مش لاقي رقم الفيديو في اللينك ده.. اتاكد منه كده 🤔",
    "unsupported": "الموقع ده لسه مش متدعوم عندنا يا برنس 😔 الشغال دلوقتي: يوتيوب + فيدوبا/انافاست + لينكات ملفات مباشرة + تيك توك (على مزاجه)",
    "not_playable": "الفيديو ده مش متاح يا صاحبي. السبب: %s",
    "login_required": "الفيديو ده محتاج تسجيل دخول في يوتيوب.. دي خصوصية زيادة عن اللزوم 🔒",
    "no_formats": "يوتيوب بيلعب بيها النهاردة.. مفيش لينكات تحميل واضحة 😤",
    "fail_final": "خلاص استسلم.. جرب تاني بعد شوية يا معلم 🙏",
    "tt_blocked": "تيك توك رافض يدينا الفيديو من غير تسجيل دخول.. جرب لينك تاني 🚫",
    "hls_no_video": "مش لاقي فيديو في الصفحة دي.. يمكن اتمسح أو اللينك غلط 🕵️",
    "hls_fail": "السيرفر قطع علينا النص.. الملف اللي نزل محفوظ وهتكمل منه لو جربت تاني 🧩",
    "cancelled": "وقفنا التحميل زي ما انت عايز 🛑",
    "size_unknown": "حجم غير معروف",
}

UA_PHONE = ("Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36")

_ssl_ctx = ssl.create_default_context()


class SahhabError(Exception):
    pass


class LoginRequired(SahhabError):
    pass


class NotPlayable(SahhabError):
    pass


class Cancelled(Exception):
    pass


# ---------------------------------------------------------------
# ادوات عامة
# ---------------------------------------------------------------
def http_get(url, headers=None, timeout=30, data=None, max_bytes=None):
    hdrs = {"User-Agent": UA_PHONE}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx) as r:
        if max_bytes:
            return r.read(max_bytes)
        return r.read()


def human_size(n):
    if n is None or n <= 0:
        return MSG["size_unknown"]
    for unit in ("بايت", "كيلو", "ميجا", "جيجا"):
        if n < 1024:
            return "%.1f %s" % (n, unit)
        n /= 1024.0
    return "%.1f تيرا" % n


def human_time(sec):
    sec = int(sec)
    if sec < 60:
        return "%d ثانية" % sec
    if sec < 3600:
        return "%d دقيقة و %d ثانية" % (sec // 60, sec % 60)
    return "%d ساعة و %d دقيقة" % (sec // 3600, (sec % 3600) // 60)


def clock(sec):
    sec = int(sec)
    if sec >= 3600:
        return "%d:%02d:%02d" % (sec // 3600, (sec % 3600) // 60, sec % 60)
    return "%d:%02d" % (sec // 60, sec % 60)


def modify_title(title):
    if not title:
        return ""
    t = re.sub(r"(\\u0027|\\u0022|\")", "'", title)
    t = t.replace("<", "(").replace(">", ")")
    t = re.sub(r"\\u0028", "(", t, flags=re.I)
    t = re.sub(r"\\u0029", ")", t, flags=re.I)
    t = re.sub(r'[/\\:|]', "-", t)
    t = re.sub(r'[*?]', "", t)
    t = re.sub(r"\s+", " ", t).strip(" .")
    return t or "video"


def sanitize_filename(name, ext, max_len=120):
    name = unicodedata.normalize("NFC", name)
    name = modify_title(name)
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip(" .")
    if not name:
        name = "video"
    if len(name) > max_len:
        name = name[:max_len].rstrip(" .")
    return "%s.%s" % (name, ext.lstrip("."))


def unique_path(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 1
    while os.path.exists("%s (%d)%s" % (base, i, ext)):
        i += 1
    return "%s (%d)%s" % (base, i, ext)


def get_extension_from_url(url):
    path = urllib.parse.urlsplit(url).path
    if "." in path:
        return path.rsplit(".", 1)[1].lower()
    return ""


def pick_outdir():
    """مكان التحميل: مجلد Download في الموبايل لو متاح، وإلا جنب الملف."""
    cands = []
    env = os.environ.get("SAHHAB_DIR")
    if env:
        cands.append(env)
    cands += ["/storage/emulated/0/Download/Sahhab",
              os.path.join(os.path.expanduser("~"), "Sahhab"),
              os.path.join(os.getcwd(), "Sahhab")]
    for d in cands:
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".probe")
            with open(probe, "w") as fh:
                fh.write("1")
            os.remove(probe)
            return d
        except OSError:
            continue
    return os.getcwd()


# ---------------------------------------------------------------
# محرك يوتيوب (Innertube)
# ---------------------------------------------------------------
INNERTUBE_CLIENTS = {
    "visionos": {
        "clientName": "VISIONOS", "clientVersion": "1.02",
        "deviceMake": "Apple", "deviceModel": "RealityDevice17,1",
        "userAgent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_7_3) "
                     "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                     "Version/26.0 Safari/605.1.15",
        "osName": "visionOS", "osVersion": "26.5.23O471",
        "clientNameId": "101",
    },
    "android": {
        "clientName": "ANDROID", "clientVersion": "20.10.38",
        "androidSdkVersion": 30,
        "userAgent": "com.google.android.youtube/20.10.38 (Linux; U; Android 11) gzip",
        "osName": "Android", "osVersion": "11",
        "clientNameId": "3",
    },
    "ios": {
        "clientName": "IOS", "clientVersion": "20.10.4",
        "deviceMake": "Apple", "deviceModel": "iPhone16,2",
        "userAgent": "com.google.ios.youtube/20.10.4 "
                     "(iPhone16,2; U; CPU iOS 18_3_2 like Mac OS X;)",
        "osName": "iPhone", "osVersion": "18.3.2.22D82",
        "clientNameId": "5",
    },
}


def extract_youtube_id(url):
    patterns = [
        r"(?:youtube\.com/watch\?[^#]*\bv=)([\w-]{11})",
        r"(?:youtu\.be/)([\w-]{11})",
        r"(?:youtube\.com/(?:shorts|embed|live|v)/)([\w-]{11})",
        r"(?:music\.youtube\.com/watch\?[^#]*\bv=)([\w-]{11})",
        r"(?:youtube\.com/watch\?v=)([\w-]{11})",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def get_visitor_data(video_id):
    html = http_get(
        "https://www.youtube.com/embed/%s" % urllib.parse.quote(video_id),
        headers={"User-Agent": INNERTUBE_CLIENTS["visionos"]["userAgent"]},
    ).decode("utf-8", "replace")
    m = re.search(r'"VISITOR_DATA":\s*"([^"]+)"', html)
    if not m:
        raise SahhabError("مش قادر اجيب VISITOR_DATA")
    return m.group(1)


def innertube_player(video_id, client_key, visitor_data):
    c = INNERTUBE_CLIENTS[client_key]
    client = {k: v for k, v in c.items() if k != "clientNameId"}
    client.update({"hl": "en", "timeZone": "UTC", "utcOffsetMinutes": 0})
    body = {
        "playbackContext": {
            "contentPlaybackContext": {"html5Preference": "HTML5_PREF_WANTS"}
        },
        "contentCheckOk": True,
        "racyCheckOk": True,
        "videoId": video_id,
        "context": {"client": client},
    }
    headers = {
        "content-type": "application/json",
        "x-youtube-client-name": c["clientNameId"],
        "x-youtube-client-version": c["clientVersion"],
        "x-goog-visitor-id": visitor_data,
        "origin": "https://www.youtube.com",
    }
    if c.get("userAgent"):
        headers["user-agent"] = c["userAgent"]
    raw = http_get(
        "https://www.youtube.com/youtubei/v1/player?prettyPrint=false",
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        timeout=30,
    )
    return json.loads(raw.decode("utf-8", "replace"))


def has_only_sabr_formats(resp):
    af = ((resp or {}).get("streamingData") or {}).get("adaptiveFormats")
    if not isinstance(af, list) or not af:
        return False
    return all(not f.get("url") or re.search(r"[?&]sabr=1(?:&|$)", f.get("url", ""))
               for f in af)


def youtube_formats(video_id):
    visitor = get_visitor_data(video_id)
    last_err = None
    resp = None
    for ck in ("visionos", "android", "ios"):
        try:
            resp = innertube_player(video_id, ck, visitor)
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue
        ps = resp.get("playabilityStatus") or {}
        status = ps.get("status")
        if status == "LOGIN_REQUIRED":
            raise LoginRequired(MSG["login_required"])
        if status and status != "OK":
            last_err = NotPlayable(ps.get("reason") or status)
            resp = None
            continue
        if has_only_sabr_formats(resp):
            last_err = NotPlayable("sabr-only")
            resp = None
            continue
        if ck != "android":
            try:
                ar = innertube_player(video_id, "android", visitor)
                if (ar.get("playabilityStatus") or {}).get("status") == "OK":
                    aprog = [f for f in (ar.get("streamingData") or {}).get("formats", [])
                             if f.get("url") and not re.search(r"[?&]sabr=1(?:&|$)", f["url"])]
                    if aprog:
                        resp.setdefault("streamingData", {})
                        resp["streamingData"]["formats"] = aprog
            except Exception:  # noqa: BLE001
                pass
        break
    if resp is None:
        if isinstance(last_err, NotPlayable) and last_err.args and last_err.args[0] not in ("sabr-only",):
            raise NotPlayable(MSG["not_playable"] % last_err.args[0])
        raise SahhabError(MSG["no_formats"] + ("" if not last_err else " (%s)" % last_err))

    sd = resp.get("streamingData") or {}
    vd = resp.get("videoDetails") or {}
    meta = {
        "id": video_id,
        "title": vd.get("title") or "video",
        "author": vd.get("author") or "",
        "lengthSeconds": int(vd.get("lengthSeconds") or 0),
        "thumb": "https://i.ytimg.com/vi/%s/hqdefault.jpg" % video_id,
    }
    progressive, adaptive, audio = [], [], []
    for f in (sd.get("formats") or []):
        if f.get("url"):
            progressive.append(f)
    for f in (sd.get("adaptiveFormats") or []):
        if not f.get("url"):
            continue
        mime = f.get("mimeType") or ""
        if mime.startswith("video/"):
            adaptive.append(f)
        elif mime.startswith("audio/"):
            audio.append(f)
    key_q = lambda f: int(f.get("height") or 0)  # noqa: E731
    progressive.sort(key=key_q, reverse=True)
    adaptive.sort(key=key_q, reverse=True)
    audio.sort(key=lambda f: int(f.get("bitrate") or 0), reverse=True)
    if not progressive and not adaptive and not audio:
        raise SahhabError(MSG["no_formats"])
    return meta, progressive, adaptive, audio


# ---------------------------------------------------------------
# التحميل: resume + progress + retries + الغاء
# ---------------------------------------------------------------
def download_file(url, dest_path, headers=None, retries=3, on_progress=None, cancel=None):
    done = 0
    mode = "wb"
    if os.path.exists(dest_path):
        done = os.path.getsize(dest_path)
        if done > 0:
            mode = "ab"
    attempt = 0
    while True:
        attempt += 1
        try:
            hdrs = dict(headers or {})
            if done:
                hdrs["Range"] = "bytes=%d-" % done
            req = urllib.request.Request(url, headers=hdrs)
            if "user-agent" not in {k.lower() for k in hdrs}:
                req.add_header("User-Agent", UA_PHONE)
            with urllib.request.urlopen(req, timeout=60, context=_ssl_ctx) as r:
                status = getattr(r, "status", 200)
                if done and status == 200:
                    done = 0
                    mode = "wb"
                total_hdr = r.headers.get("Content-Length")
                total = (int(total_hdr) + done) if total_hdr and total_hdr.isdigit() else None
                started = time.time()
                got = 0
                with open(dest_path, mode) as fh:
                    while True:
                        if cancel is not None and cancel.is_set():
                            raise Cancelled()
                        buf = r.read(256 * 1024)
                        if not buf:
                            break
                        fh.write(buf)
                        done += len(buf)
                        got += len(buf)
                        if on_progress:
                            el = max(time.time() - started, 0.001)
                            spd = got / el
                            eta = ((total - done) / spd) if (total and spd > 0) else None
                            on_progress(done, total, spd, eta)
            return dest_path, done
        except Cancelled:
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            if attempt >= retries:
                raise SahhabError(MSG["fail_final"] + " (%s)" % e)
            time.sleep(2 * attempt)
            if os.path.exists(dest_path):
                done = os.path.getsize(dest_path)
                mode = "ab"


# ---------------------------------------------------------------
# فيدوبا / انافاست: فك تشفير الصفحة + HLS
# ---------------------------------------------------------------
def unpack_packer(src):
    m = re.search(
        r"eval\(function\(p,a,c,k,e,d\)\{.*?\}\('(.*?)',(\d+),(\d+),'(.*?)'\.split\('\|'\)",
        src, re.S)
    if not m:
        return None
    payload, a, c = m.group(1), int(m.group(2)), int(m.group(3))
    k = m.group(4).split('|')

    def base36ish(n):
        digits = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
        if n == 0:
            return '0'
        s = ''
        while n:
            s = digits[n % 36] + s
            n //= 36
        return s

    def e(cc):
        r = e(cc // a) if cc >= a else ''
        cc %= a
        return r + (chr(cc + 29) if cc > 35 else base36ish(cc))

    out = payload
    for i in range(c - 1, -1, -1):
        if i < len(k) and k[i]:
            out = re.sub(r'\b%s\b' % re.escape(e(i)), lambda _m, v=k[i]: v, out)
    return out


def extract_hls_urls(html):
    urls = []
    for block in re.findall(r"<script[^>]*>(.*?)</script>", html, re.S | re.I):
        if 'eval(function(p,a,c,k,e,d)' in block:
            decoded = unpack_packer(block)
            if decoded:
                urls.extend(re.findall(r'https?://[^"\'\\\s<>]+\.m3u8[^"\'\\\s<>]*', decoded))
    urls.extend(re.findall(r'https?://[^"\'\\\s<>]+\.m3u8[^"\'\\\s<>]*', html))
    seen = []
    for u in urls:
        u = u.replace('\\/', '/')
        if u not in seen:
            seen.append(u)
    return seen


def parse_master_m3u8(text):
    variants = []
    lines = [ln.strip() for ln in text.splitlines()]
    for i, ln in enumerate(lines):
        if ln.startswith('#EXT-X-STREAM-INF'):
            bw = re.search(r'BANDWIDTH=(\d+)', ln)
            res = re.search(r'RESOLUTION=(\d+)x(\d+)', ln)
            j = i + 1
            while j < len(lines) and (not lines[j] or lines[j].startswith('#')):
                j += 1
            if j < len(lines):
                variants.append({
                    'url': lines[j],
                    'width': int(res.group(1)) if res else 0,
                    'height': int(res.group(2)) if res else 0,
                    'bandwidth': int(bw.group(1)) if bw else 0,
                })
    variants.sort(key=lambda v: (v['height'], v['bandwidth']), reverse=True)
    return variants or None


def parse_media_playlist(text, base_url):
    segs = []
    for ln in text.splitlines():
        ln = ln.strip()
        if ln and not ln.startswith('#'):
            segs.append(urllib.parse.urljoin(base_url, ln))
    return segs


def hls_download(playlist_url, dest_path, headers=None, retries=3, on_progress=None, cancel=None):
    text = http_get(playlist_url, headers=headers, timeout=30).decode('utf-8', 'replace')
    segs = parse_media_playlist(text, playlist_url)
    if not segs:
        raise SahhabError(MSG["hls_no_video"])
    sidecar = dest_path + '.seg'
    done = 0
    if os.path.exists(sidecar) and os.path.exists(dest_path):
        try:
            saved = json.loads(open(sidecar, encoding='utf-8').read())
            if saved.get('url') == playlist_url and 0 < int(saved.get('done', 0)) < len(segs):
                done = int(saved['done'])
        except Exception:  # noqa: BLE001
            done = 0
    if done == 0 and os.path.exists(dest_path):
        os.remove(dest_path)
    total = len(segs)
    t0 = time.time()
    with open(dest_path, 'ab') as fh:
        for idx in range(done, total):
            if cancel is not None and cancel.is_set():
                fh.flush()
                raise Cancelled()
            attempt = 0
            while True:
                attempt += 1
                try:
                    data = http_get(segs[idx], headers=headers, timeout=60)
                    if not data:
                        raise SahhabError('empty segment')
                    fh.write(data)
                    break
                except Exception as e:  # noqa: BLE001
                    if attempt >= retries:
                        fh.flush()
                        with open(sidecar, 'w', encoding='utf-8') as sc:
                            json.dump({'url': playlist_url, 'done': idx}, sc)
                        raise SahhabError(MSG["hls_fail"] + ' (%s)' % e)
                    time.sleep(2 * attempt)
            fh.flush()
            with open(sidecar, 'w', encoding='utf-8') as sc:
                json.dump({'url': playlist_url, 'done': idx + 1}, sc)
            if on_progress:
                el = max(time.time() - t0, 0.001)
                sps = (idx + 1 - done) / el
                eta = (total - idx - 1) / sps if sps > 0 else None
                # نخلي التقدم بالنسبة للقطع: done/total بالقطع
                on_progress(idx + 1, total, None, eta, segments=True)
    if os.path.exists(sidecar):
        os.remove(sidecar)
    return dest_path, os.path.getsize(dest_path)


# ---------------------------------------------------------------
# تيك توك
# ---------------------------------------------------------------
def tiktok_extract(url):
    html = http_get(url, headers={"User-Agent": UA_PHONE}, timeout=30).decode("utf-8", "replace")
    candidates = []
    for pat in (r'"downloadAddr":"([^"]+)"', r'"playAddr":"([^"]+)"'):
        for m in re.finditer(pat, html):
            try:
                u = json.loads('"' + m.group(1) + '"')
            except Exception:  # noqa: BLE001
                continue
            if u.startswith("http") and u not in candidates:
                candidates.append(u)
    title = None
    m = re.search(r'"desc":"([^"]{0,200})"', html)
    if m:
        try:
            title = json.loads('"' + m.group(1) + '"')
        except Exception:  # noqa: BLE001
            pass
    return candidates, (title or "tiktok")


# ---------------------------------------------------------------
# توجيه اللينكات
# ---------------------------------------------------------------
def normalize_url(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw):
        raw = "https://" + raw
    try:
        parts = urllib.parse.urlsplit(raw)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    if not parts.netloc or "." not in parts.netloc:
        return None
    return raw


def route(url):
    host = urllib.parse.urlsplit(url).netloc.lower()
    host = host.split("@")[-1].split(":")[0]
    if re.search(r"(^|\.)(youtube\.com|youtu\.be|youtube-nocookie\.com)$", host):
        return "youtube"
    if re.search(r"(^|\.)(vidoba\.org|anafast\.org)$", host):
        return "vidoba"
    if re.search(r"(^|\.)tiktok\.com$", host):
        return "tiktok"
    if re.search(r"\.(mp4|webm|mkv|mp3|m4a|mov|avi)([?#].*)?$", url, re.I):
        return "direct"
    return "unknown"


# ---------------------------------------------------------------
# تحليل اللينك -> قايمة اختيارات (بتتخزن في السيرفر)
# ---------------------------------------------------------------
INFOS = {}          # token -> {"meta":..., "options":[internal]}
INFOS_LOCK = threading.Lock()


def _csize(f):
    s = str(f.get("contentLength", ""))
    return int(s) if s.isdigit() else None


def analyze(url):
    """بيرجع (public_info, internal_info). بيرمي SahhabError لو فشل."""
    kind = route(url)
    if kind == "unknown":
        raise SahhabError(MSG["unsupported"])
    options = []   # internal: dict(label, group, quality, ext, size, type, url, headers)
    meta = {"title": "", "author": "", "duration": "", "thumb": "", "source": kind}

    if kind == "youtube":
        vid = extract_youtube_id(url)
        if not vid:
            raise SahhabError(MSG["no_youtube_id"])
        m, progressive, adaptive, audio = youtube_formats(vid)
        meta.update(title=m["title"], author=m["author"], thumb=m["thumb"],
                    duration=clock(m["lengthSeconds"]) if m["lengthSeconds"] else "")
        for f in progressive:
            ext = "mp4" if "mp4" in (f.get("mimeType") or "") else "webm"
            options.append(dict(group="ready", quality=f.get("qualityLabel") or "?", ext=ext,
                                size=_csize(f), type="file", url=f["url"], headers={}))
        for f in adaptive[:4]:
            ext = "mp4" if "mp4" in (f.get("mimeType") or "") else "webm"
            options.append(dict(group="video", quality=f.get("qualityLabel") or "?", ext=ext,
                                size=_csize(f), type="file", url=f["url"], headers={}))
        if audio:
            f = audio[0]
            options.append(dict(group="audio", quality="m4a", ext="m4a",
                                size=_csize(f), type="file", url=f["url"], headers={}))

    elif kind == "vidoba":
        host = urllib.parse.urlsplit(url).netloc.lower()
        headers = {"Referer": "https://%s/" % host, "User-Agent": UA_PHONE}
        html = http_get(url, headers={"User-Agent": UA_PHONE}, timeout=30).decode("utf-8", "replace")
        m3u8s = extract_hls_urls(html)
        if not m3u8s:
            raise SahhabError(MSG["hls_no_video"])
        mt = re.search(r"<title>\s*(?:Watch\s+)?(.*?)</title>", html, re.S | re.I)
        meta["title"] = (mt.group(1).strip() if mt else "") or host.split(".")[0]
        master = http_get(m3u8s[0], headers=headers, timeout=30).decode("utf-8", "replace")
        variants = parse_master_m3u8(master)
        if variants:
            for v in variants:
                options.append(dict(group="ready", quality="%dp" % v["height"] if v["height"] else "HLS",
                                    ext="ts", size=None, type="hls",
                                    url=urllib.parse.urljoin(m3u8s[0], v["url"]), headers=headers,
                                    note="%.1f ميجابت/ث" % (v["bandwidth"] * 8 / 1e6) if v["bandwidth"] else ""))
        else:
            options.append(dict(group="ready", quality="HLS", ext="ts", size=None,
                                type="hls", url=m3u8s[0], headers=headers))

    elif kind == "tiktok":
        links, title = tiktok_extract(url)
        if not links:
            raise SahhabError(MSG["tt_blocked"])
        meta["title"] = title
        options.append(dict(group="ready", quality="تيك توك", ext="mp4", size=None, type="tiktok",
                            url=links, headers={"Referer": "https://www.tiktok.com/", "User-Agent": UA_PHONE}))

    elif kind == "direct":
        ext = get_extension_from_url(url) or "mp4"
        name = os.path.basename(urllib.parse.urlsplit(url).path) or "file"
        meta["title"] = re.sub(r"\.%s$" % re.escape(ext), "", name, flags=re.I) or "file"
        size = None
        try:
            req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA_PHONE})
            with urllib.request.urlopen(req, timeout=15, context=_ssl_ctx) as r:
                cl = r.headers.get("Content-Length")
                size = int(cl) if cl and cl.isdigit() else None
        except Exception:  # noqa: BLE001
            pass
        audio_ext = ext in ("mp3", "m4a")
        options.append(dict(group="audio" if audio_ext else "ready", quality=ext.upper(), ext=ext,
                            size=size, type="file", url=url, headers={}))

    if not options:
        raise SahhabError(MSG["no_formats"])

    token = uuid.uuid4().hex[:12]
    pub = {"token": token, "meta": meta, "options": []}
    for i, o in enumerate(options):
        pub["options"].append({
            "id": i, "group": o["group"], "quality": o["quality"], "ext": o["ext"],
            "size": human_size(o["size"]) if o["size"] else "", "note": o.get("note", ""),
        })
    with INFOS_LOCK:
        INFOS[token] = {"meta": meta, "options": options, "ts": time.time()}
        if len(INFOS) > 60:
            for k in sorted(INFOS, key=lambda k: INFOS[k]["ts"])[:20]:
                INFOS.pop(k, None)
    return pub


# ---------------------------------------------------------------
# المهام (التحميلات)
# ---------------------------------------------------------------
JOBS = {}
JOBS_LOCK = threading.Lock()
OUTDIR = None


def _run_job(job, opt, title):
    cancel = job["_cancel"]

    def prog(done, total, spd, eta, segments=False):
        job["done"], job["total"] = done, total
        job["segments"] = bool(segments)
        job["speed"] = human_size(spd) + "/ث" if spd else ""
        job["eta"] = human_time(eta) if eta is not None else ""
        job["pct"] = round(done * 100.0 / total, 1) if total else 0

    try:
        fname = sanitize_filename("%s [%s]" % (title, opt["quality"]) if opt["type"] == "hls" else title,
                                  opt["ext"])
        dest = unique_path(os.path.join(OUTDIR, fname))
        job["file"] = os.path.basename(dest)
        job["path"] = dest
        job["state"] = "running"
        if opt["type"] == "hls":
            path, size = hls_download(opt["url"], dest, headers=opt["headers"], on_progress=prog, cancel=cancel)
        elif opt["type"] == "tiktok":
            err = None
            path = None
            for u in opt["url"]:
                try:
                    path, size = download_file(u, dest, headers=opt["headers"], on_progress=prog, cancel=cancel)
                    break
                except SahhabError as e:
                    err = e
            if path is None:
                raise err or SahhabError(MSG["tt_blocked"])
        else:
            path, size = download_file(opt["url"], dest, headers=opt["headers"], on_progress=prog, cancel=cancel)
        job.update(state="done", pct=100, size=human_size(size), speed="", eta="", path=path)
    except Cancelled:
        job.update(state="cancelled", error=MSG["cancelled"], speed="", eta="")
        try:
            if job.get("path") and os.path.exists(job["path"]):
                os.remove(job["path"])
        except OSError:
            pass
    except Exception as e:  # noqa: BLE001
        job.update(state="error", error=str(e) if isinstance(e, SahhabError) else MSG["fail_final"] + " (%s)" % e,
                   speed="", eta="")


def start_job(token, opt_id):
    with INFOS_LOCK:
        info = INFOS.get(token)
    if not info:
        raise SahhabError("الجلسة انتهت.. اعمل تحليل للينك تاني 🔄")
    try:
        opt = info["options"][int(opt_id)]
    except (ValueError, IndexError):
        raise SahhabError("الاختيار ده مش موجود")
    jid = uuid.uuid4().hex[:10]
    job = {
        "id": jid, "state": "queued", "title": info["meta"]["title"], "thumb": info["meta"].get("thumb", ""),
        "quality": opt["quality"], "ext": opt["ext"], "pct": 0, "done": 0, "total": None,
        "speed": "", "eta": "", "file": "", "path": "", "error": "", "size": "", "segments": False,
        "ts": time.time(), "_cancel": threading.Event(),
    }
    with JOBS_LOCK:
        JOBS[jid] = job
    threading.Thread(target=_run_job, args=(job, opt, info["meta"]["title"]), daemon=True).start()
    return jid


def public_job(j):
    d = {k: v for k, v in j.items() if not k.startswith("_") and k != "path"}
    d["can_get"] = j["state"] == "done" and os.path.exists(j.get("path") or "")
    if j.get("segments") and j.get("total"):
        d["detail"] = "قطعة %d من %d" % (j["done"], j["total"])
    elif j.get("total"):
        d["detail"] = "%s / %s" % (human_size(j["done"]), human_size(j["total"]))
    elif j.get("done"):
        d["detail"] = human_size(j["done"])
    else:
        d["detail"] = ""
    return d


# ---------------------------------------------------------------
# الواجهة (HTML + CSS + JS في ملف واحد)
# ---------------------------------------------------------------
PAGE = r"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#05060f">
<title>سحّاب — أسرع طريقة لتحميل الفيديوهات</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cairo:wght@300;400;600;700;800;900&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#05060f; --ink:#f4f6ff; --mut:#8e97b8; --line:rgba(255,255,255,.09);
  --c1:#22d3ee; --c2:#7c5cff; --c3:#ff4fa3; --c4:#ffb454;
  --ok:#2ee59d; --err:#ff6b8a; --warn:#ffc65a;
  --grad:linear-gradient(120deg,var(--c1),var(--c2) 45%,var(--c3));
}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
html{scroll-behavior:smooth}
body{font-family:'Cairo',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--ink);line-height:1.7;
  overflow-x:hidden;min-height:100vh;-webkit-font-smoothing:antialiased}
::selection{background:var(--c2);color:#fff}

/* ===== الخلفية ===== */
.aurora{position:fixed;inset:0;z-index:-3;overflow:hidden;background:
  radial-gradient(1200px 700px at 85% -10%,rgba(124,92,255,.35),transparent 60%),
  radial-gradient(900px 600px at 5% 10%,rgba(34,211,238,.22),transparent 60%),
  radial-gradient(900px 700px at 50% 120%,rgba(255,79,163,.22),transparent 60%),var(--bg)}
.orb{position:absolute;border-radius:50%;filter:blur(90px);mix-blend-mode:screen;animation:drift 22s ease-in-out infinite}
.o1{width:520px;height:520px;background:rgba(124,92,255,.35);top:8%;right:-8%}
.o2{width:440px;height:440px;background:rgba(34,211,238,.25);top:46%;left:-10%;animation-delay:-7s}
.o3{width:380px;height:380px;background:rgba(255,79,163,.22);bottom:-8%;right:25%;animation-delay:-13s}
@keyframes drift{0%,100%{transform:translate(0,0) scale(1)}33%{transform:translate(60px,-40px) scale(1.1)}66%{transform:translate(-40px,50px) scale(.95)}}
.grid{position:fixed;inset:0;z-index:-2;opacity:.5;pointer-events:none;
  background-image:linear-gradient(var(--line) 1px,transparent 1px),linear-gradient(90deg,var(--line) 1px,transparent 1px);
  background-size:64px 64px;-webkit-mask-image:radial-gradient(ellipse 70% 55% at 50% 0%,#000 30%,transparent 75%);mask-image:radial-gradient(ellipse 70% 55% at 50% 0%,#000 30%,transparent 75%)}
.grain{position:fixed;inset:0;z-index:-1;pointer-events:none;opacity:.07;mix-blend-mode:overlay;
  background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='2'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>")}
.spot{position:fixed;width:520px;height:520px;border-radius:50%;pointer-events:none;z-index:-1;left:0;top:0;
  background:radial-gradient(circle,rgba(124,92,255,.16),transparent 65%);transform:translate(-50%,-50%);transition:opacity .3s}

.wrap{max-width:1120px;margin:0 auto;padding:0 20px}

/* ===== شريط التنقل ===== */
header{position:sticky;top:0;z-index:50;padding:12px 0;backdrop-filter:blur(18px) saturate(1.4);-webkit-backdrop-filter:blur(18px) saturate(1.4);
  background:linear-gradient(rgba(5,6,15,.72),rgba(5,6,15,.35));border-bottom:1px solid var(--line)}
nav{display:flex;align-items:center;justify-content:space-between;gap:12px}
.logo{display:flex;align-items:center;gap:11px;font-weight:900;font-size:1.55rem;letter-spacing:-.5px;text-decoration:none;color:var(--ink)}
.mark{width:42px;height:42px;border-radius:13px;display:grid;place-items:center;background:var(--grad);position:relative;
  box-shadow:0 10px 30px rgba(124,92,255,.5),inset 0 1px 0 rgba(255,255,255,.4)}
.mark svg{width:22px;height:22px}
.links{display:flex;gap:6px;align-items:center}
.links a{color:var(--mut);text-decoration:none;font-weight:600;font-size:.95rem;padding:7px 14px;border-radius:99px;transition:.2s}
.links a:hover{color:var(--ink);background:rgba(255,255,255,.07)}
.pill{font-size:.78rem;font-weight:700;color:var(--c1);border:1px solid rgba(34,211,238,.35);background:rgba(34,211,238,.08);padding:4px 13px;border-radius:99px}

/* ===== الهيرو ===== */
.hero{text-align:center;padding:78px 0 24px;position:relative}
.badge{display:inline-flex;align-items:center;gap:9px;padding:6px 8px 6px 16px;border-radius:99px;background:rgba(255,255,255,.06);
  border:1px solid var(--line);font-size:.88rem;color:#cfd6f5;margin-bottom:26px;backdrop-filter:blur(10px)}
.badge b{background:var(--grad);color:#fff;padding:2px 12px;border-radius:99px;font-size:.76rem}
.dot{width:8px;height:8px;border-radius:50%;background:var(--ok);box-shadow:0 0 0 0 rgba(46,229,157,.7);animation:ping 1.8s infinite}
@keyframes ping{70%{box-shadow:0 0 0 10px rgba(46,229,157,0)}100%{box-shadow:0 0 0 0 rgba(46,229,157,0)}}
h1{font-size:clamp(2.5rem,8.5vw,5.4rem);font-weight:900;line-height:1.12;letter-spacing:-1.5px}
.grad{background:linear-gradient(100deg,var(--c1),var(--c2),var(--c3),var(--c4),var(--c1));background-size:250% 100%;
  -webkit-background-clip:text;background-clip:text;color:transparent;animation:flow 7s linear infinite}
@keyframes flow{to{background-position:-250% 0}}
.sub{color:var(--mut);font-size:clamp(1rem,2.4vw,1.28rem);max-width:640px;margin:22px auto 0;font-weight:400}

/* ===== شريط اللينك ===== */
.bar-wrap{max-width:760px;margin:40px auto 0;position:relative}
.bar-wrap::before{content:'';position:absolute;inset:-2px;border-radius:26px;background:conic-gradient(from var(--a,0deg),var(--c1),var(--c2),var(--c3),var(--c4),var(--c1));
  filter:blur(14px);opacity:.55;animation:spin 6s linear infinite;z-index:-1}
.bar-wrap::after{content:'';position:absolute;inset:-1.5px;border-radius:25px;background:conic-gradient(from var(--a,0deg),var(--c1),var(--c2),var(--c3),var(--c4),var(--c1));
  animation:spin 6s linear infinite;z-index:-1}
@property --a{syntax:'<angle>';initial-value:0deg;inherits:false}
@keyframes spin{to{--a:360deg}}
.bar{display:flex;align-items:center;gap:10px;padding:9px;border-radius:24px;background:#0a0c1c;position:relative}
.bar .ic{padding:0 12px 0 4px;font-size:1.3rem;opacity:.8}
.bar input{flex:1;min-width:0;background:none;border:0;outline:0;color:var(--ink);font:inherit;font-size:1.05rem;padding:14px 4px;direction:ltr;text-align:left}
.bar input::placeholder{color:#59627f;direction:rtl;text-align:right}
.paste{background:rgba(255,255,255,.07);border:1px solid var(--line);color:var(--ink);font:inherit;font-weight:700;font-size:.9rem;padding:9px 16px;border-radius:14px;cursor:pointer;transition:.2s;white-space:nowrap}
.paste:hover{background:rgba(255,255,255,.14)}
.go{border:0;cursor:pointer;font:inherit;font-weight:800;font-size:1.05rem;color:#fff;padding:0 30px;min-height:56px;border-radius:16px;white-space:nowrap;
  background:var(--grad);background-size:200% 100%;box-shadow:0 10px 30px rgba(124,92,255,.5),inset 0 1px 0 rgba(255,255,255,.35);transition:.25s;position:relative;overflow:hidden}
.go:hover{transform:translateY(-2px);background-position:100% 0;box-shadow:0 16px 40px rgba(124,92,255,.6),inset 0 1px 0 rgba(255,255,255,.35)}
.go:active{transform:scale(.97)}
.go[disabled]{opacity:.7;cursor:wait;transform:none}
.go::after{content:'';position:absolute;top:0;left:-60%;width:40%;height:100%;background:linear-gradient(90deg,transparent,rgba(255,255,255,.35),transparent);transform:skewX(-20deg);animation:shine 3.2s infinite}
@keyframes shine{to{left:140%}}
.hint{margin-top:16px;color:#6e7799;font-size:.9rem;display:flex;gap:18px;justify-content:center;flex-wrap:wrap}
.hint span::before{content:'✓ ';color:var(--ok);font-weight:900}
.err{display:none;max-width:760px;margin:16px auto 0;padding:13px 18px;border-radius:16px;background:rgba(255,107,138,.1);border:1px solid rgba(255,107,138,.4);color:#ffd0da;text-align:right}
.err.show{display:block;animation:pop .35s cubic-bezier(.2,.9,.3,1.3)}
@keyframes pop{from{opacity:0;transform:translateY(10px) scale(.97)}to{opacity:1;transform:none}}

/* ===== ماركي المواقع ===== */
.marq{margin:54px 0 0;overflow:hidden;-webkit-mask-image:linear-gradient(90deg,transparent,#000 15%,#000 85%,transparent);mask-image:linear-gradient(90deg,transparent,#000 15%,#000 85%,transparent)}
.mt{display:flex;gap:14px;width:max-content;animation:mq 30s linear infinite}
.marq:hover .mt{animation-play-state:paused}
@keyframes mq{to{transform:translateX(50%)}}
.src{display:flex;align-items:center;gap:10px;padding:10px 22px;border-radius:99px;background:rgba(255,255,255,.04);border:1px solid var(--line);color:#c3cbe8;font-weight:700;white-space:nowrap}
.src i{font-style:normal;font-size:1.2rem}

/* ===== التحميل (loader) ===== */
.loader{display:none;margin:40px auto 0;max-width:760px;padding:26px;border-radius:24px;border:1px solid var(--line);background:rgba(255,255,255,.04);text-align:center}
.loader.show{display:block;animation:pop .3s}
.sk{display:flex;gap:16px;align-items:center}
.sk .a{width:200px;aspect-ratio:16/9;border-radius:14px}
.sk .b{flex:1}.sk .b div{height:14px;border-radius:8px;margin:10px 0}
.sh{background:linear-gradient(100deg,rgba(255,255,255,.05) 30%,rgba(255,255,255,.14) 50%,rgba(255,255,255,.05) 70%);background-size:300% 100%;animation:sk 1.4s infinite}
@keyframes sk{to{background-position:-300% 0}}
.ltxt{margin-top:14px;color:var(--mut);font-weight:600}

/* ===== كارت النتيجة ===== */
#result{display:none;margin:40px auto 0;max-width:900px;border-radius:30px;overflow:hidden;position:relative;
  background:linear-gradient(160deg,rgba(255,255,255,.09),rgba(255,255,255,.03));border:1px solid rgba(255,255,255,.14);
  box-shadow:0 40px 100px rgba(0,0,0,.6),0 0 0 1px rgba(255,255,255,.03) inset;backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px)}
#result.show{display:block;animation:pop .5s cubic-bezier(.2,.9,.3,1.2)}
.vtop{display:grid;grid-template-columns:minmax(0,340px) 1fr;gap:26px;padding:24px;align-items:center}
.poster{position:relative;border-radius:20px;overflow:hidden;aspect-ratio:16/9;background:#0e1126;box-shadow:0 20px 50px rgba(0,0,0,.5)}
.poster img{width:100%;height:100%;object-fit:cover;display:block;transition:transform .6s}
#result:hover .poster img{transform:scale(1.06)}
.poster::after{content:'';position:absolute;inset:0;background:linear-gradient(180deg,transparent 50%,rgba(0,0,0,.6))}
.play{position:absolute;inset:0;display:grid;place-items:center;z-index:2}
.play span{width:58px;height:58px;border-radius:50%;background:rgba(255,255,255,.18);backdrop-filter:blur(8px);display:grid;place-items:center;border:1px solid rgba(255,255,255,.4);font-size:1.3rem;padding-right:4px}
.dur{position:absolute;bottom:10px;left:10px;z-index:3;background:rgba(0,0,0,.7);padding:1px 10px;border-radius:8px;font-size:.82rem;font-weight:700}
.vi h2{font-size:clamp(1.15rem,3vw,1.6rem);line-height:1.45;font-weight:800;margin-bottom:12px;word-break:break-word}
.tags{display:flex;gap:8px;flex-wrap:wrap}
.tag{font-size:.82rem;font-weight:600;padding:3px 13px;border-radius:99px;background:rgba(255,255,255,.07);border:1px solid var(--line);color:#c9d0ee}
.opts{padding:4px 24px 26px}
.gt{display:flex;align-items:baseline;gap:10px;margin:20px 0 10px;font-weight:800;font-size:1.05rem}
.gt small{font-weight:400;color:var(--mut);font-size:.82rem}
.chips{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px}
.opt{display:flex;align-items:center;gap:12px;padding:13px 14px;border-radius:16px;background:rgba(255,255,255,.045);border:1px solid var(--line);cursor:pointer;
  transition:.25s;position:relative;overflow:hidden;font:inherit;color:inherit;text-align:right;width:100%}
.opt::before{content:'';position:absolute;inset:0;background:var(--grad);opacity:0;transition:.25s}
.opt:hover{transform:translateY(-3px);border-color:rgba(255,255,255,.3);box-shadow:0 14px 34px rgba(124,92,255,.3)}
.opt:hover::before{opacity:.16}
.opt>*{position:relative}
.opt .q{font-weight:900;font-size:1.25rem;min-width:56px}
.opt .m{flex:1;display:flex;flex-direction:column;line-height:1.35}
.opt .m b{font-size:.74rem;text-transform:uppercase;letter-spacing:1px;color:#b9a8ff}
.opt .m span{font-size:.84rem;color:var(--mut)}
.opt .arrow{width:36px;height:36px;border-radius:12px;background:var(--grad);display:grid;place-items:center;font-size:1rem;box-shadow:0 6px 18px rgba(124,92,255,.5)}
.opt[disabled]{opacity:.6}
.note{font-size:.83rem;color:var(--warn);background:rgba(255,198,90,.07);border:1px solid rgba(255,198,90,.22);padding:8px 14px;border-radius:12px;margin-bottom:10px}

/* ===== التحميلات ===== */
.sec{margin-top:70px}
.sec h2{font-size:clamp(1.5rem,4vw,2.2rem);font-weight:900;letter-spacing:-.5px;display:flex;align-items:center;gap:12px;margin-bottom:6px}
.sec .lead{color:var(--mut);margin-bottom:20px}
.cnt{font-size:.8rem;background:var(--grad);padding:2px 12px;border-radius:99px;font-weight:800;color:#fff}
#jobs:empty{display:none}
#jobs{display:grid;gap:12px;margin-bottom:12px}
.empty{padding:34px;border-radius:24px;border:1.5px dashed rgba(255,255,255,.14);text-align:center;color:var(--mut)}
.empty big{display:block;font-size:2.4rem;margin-bottom:4px}
.job{display:flex;gap:16px;align-items:center;padding:14px 16px;border-radius:22px;background:linear-gradient(160deg,rgba(255,255,255,.07),rgba(255,255,255,.025));
  border:1px solid var(--line);animation:pop .4s;transition:.3s}
.job:hover{border-color:rgba(255,255,255,.2)}
.ring{position:relative;width:64px;height:64px;flex:none}
.ring svg{width:64px;height:64px;transform:rotate(-90deg)}
.ring circle{fill:none;stroke-width:6;stroke-linecap:round}
.ring .bg{stroke:rgba(255,255,255,.1)}
.ring .fg{stroke:url(#rg);transition:stroke-dashoffset .5s;stroke-dasharray:163.4;stroke-dashoffset:163.4}
.job.done .fg{stroke:var(--ok)}
.job.error .fg,.job.cancelled .fg{stroke:var(--err)}
.ring b{position:absolute;inset:0;display:grid;place-items:center;font-size:.82rem;font-weight:800}
.jb{flex:1;min-width:0}
.jt{font-weight:800;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.jt small{color:var(--c1);font-weight:700;margin-right:6px}
.js{font-size:.84rem;color:var(--mut);display:flex;gap:6px 14px;flex-wrap:wrap;margin-top:3px}
.js .ok{color:var(--ok);font-weight:700}.js .er{color:var(--err);font-weight:600}
.act{display:flex;gap:8px;flex:none}
.sm{border:1px solid var(--line);background:rgba(255,255,255,.07);color:var(--ink);font:inherit;font-size:.88rem;font-weight:700;padding:8px 16px;border-radius:12px;cursor:pointer;text-decoration:none;transition:.2s;white-space:nowrap}
.sm:hover{background:rgba(255,255,255,.15)}
.sm.pri{background:linear-gradient(135deg,var(--ok),#14b8a6);color:#022b1c;border:0;box-shadow:0 8px 22px rgba(46,229,157,.35)}
.saved{margin-top:14px;text-align:center;color:var(--mut);font-size:.86rem}
.saved code{direction:ltr;display:inline-block;background:rgba(255,255,255,.06);padding:2px 12px;border-radius:9px;color:#a5f3fc;font-family:ui-monospace,monospace}

/* ===== مميزات (bento) ===== */
.bento{display:grid;grid-template-columns:repeat(6,1fr);gap:14px;margin-top:22px}
.card{position:relative;padding:26px;border-radius:26px;background:linear-gradient(160deg,rgba(255,255,255,.07),rgba(255,255,255,.02));border:1px solid var(--line);overflow:hidden;transition:.35s}
.card::before{content:'';position:absolute;inset:0;background:radial-gradient(420px circle at var(--mx,50%) var(--my,0%),rgba(124,92,255,.22),transparent 45%);opacity:0;transition:.3s}
.card:hover{transform:translateY(-4px);border-color:rgba(255,255,255,.22)}
.card:hover::before{opacity:1}
.card>*{position:relative}
.card .ico{width:52px;height:52px;border-radius:16px;display:grid;place-items:center;font-size:1.6rem;margin-bottom:14px;background:rgba(255,255,255,.08);border:1px solid var(--line)}
.card h3{font-size:1.25rem;font-weight:800;margin-bottom:4px}
.card p{color:var(--mut);font-size:.95rem}
.c-a{grid-column:span 3}.c-b{grid-column:span 3}.c-c{grid-column:span 2}.c-d{grid-column:span 2}.c-e{grid-column:span 2}
.big{font-size:3.2rem;font-weight:900;line-height:1.1;background:var(--grad);-webkit-background-clip:text;background-clip:text;color:transparent}

/* ===== الخطوات ===== */
.steps{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:22px;counter-reset:s}
.step{padding:26px;border-radius:26px;border:1px solid var(--line);background:rgba(255,255,255,.03);position:relative;counter-increment:s}
.step::before{content:counter(s);position:absolute;top:18px;left:22px;font-size:4.2rem;font-weight:900;line-height:1;color:transparent;-webkit-text-stroke:1.5px rgba(255,255,255,.12)}
.step h3{font-size:1.2rem;font-weight:800;margin:30px 0 4px}
.step p{color:var(--mut);font-size:.95rem}

/* ===== أسئلة ===== */
.faq{display:grid;gap:10px;margin-top:22px;max-width:820px}
details{border-radius:18px;border:1px solid var(--line);background:rgba(255,255,255,.035);padding:2px 20px;transition:.25s}
details[open]{background:rgba(255,255,255,.07);border-color:rgba(255,255,255,.18)}
summary{cursor:pointer;font-weight:800;padding:15px 0;list-style:none;display:flex;justify-content:space-between;gap:10px;align-items:center}
summary::-webkit-details-marker{display:none}
summary::after{content:'+';font-size:1.5rem;color:var(--c1);transition:.3s;font-weight:400}
details[open] summary::after{transform:rotate(45deg)}
details p{color:var(--mut);padding-bottom:16px}

footer{margin-top:90px;padding:34px 0 46px;border-top:1px solid var(--line);text-align:center;color:#6e7799;font-size:.88rem}
footer .logo{justify-content:center;margin-bottom:8px;font-size:1.2rem}
footer .mark{width:32px;height:32px;border-radius:10px}.footer .mark svg{width:16px}

.rv{opacity:0;transform:translateY(26px);transition:opacity .8s,transform .8s cubic-bezier(.2,.8,.2,1)}
.rv.in{opacity:1;transform:none}
.toast{position:fixed;bottom:22px;left:50%;transform:translate(-50%,120px);background:#12152d;border:1px solid rgba(255,255,255,.18);padding:12px 22px;border-radius:99px;
  font-weight:700;z-index:99;transition:.4s cubic-bezier(.2,.9,.3,1.3);box-shadow:0 20px 50px rgba(0,0,0,.6)}
.toast.show{transform:translate(-50%,0)}

@media(max-width:820px){
  .links a:not(.pill){display:none}
  .hero{padding-top:48px}
  .vtop{grid-template-columns:1fr;padding:16px}
  .opts{padding:2px 16px 20px}
  .bento,.steps{grid-template-columns:1fr}
  .c-a,.c-b,.c-c,.c-d,.c-e{grid-column:auto}
  .bar{flex-wrap:wrap}.bar input{flex:1 1 0}
  .go{width:100%;order:3}
  .job{flex-wrap:wrap}.act{width:100%}.act .sm{flex:1;text-align:center}
  .sk .a{width:110px}
}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}.rv{opacity:1;transform:none}}
</style>
</head>
<body>
<div class="aurora"><div class="orb o1"></div><div class="orb o2"></div><div class="orb o3"></div></div>
<div class="grid"></div><div class="grain"></div><div class="spot" id="spot"></div>
<svg width="0" height="0" style="position:absolute"><defs><linearGradient id="rg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#22d3ee"/><stop offset=".5" stop-color="#7c5cff"/><stop offset="1" stop-color="#ff4fa3"/></linearGradient></defs></svg>

<header><div class="wrap"><nav>
  <a class="logo" href="#top"><span class="mark"><svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12"/><path d="M6.5 10.5 12 16l5.5-5.5"/><path d="M4 20h16"/></svg></span>سحّاب</a>
  <div class="links"><a href="#how">إزاي بيشتغل</a><a href="#features">المميزات</a><a href="#faq">أسئلة</a><span class="pill" id="ver">v2</span></div>
</nav></div></header>

<main class="wrap" id="top">
  <section class="hero">
    <div class="badge rv"><b>جديد</b><span class="dot"></span> النسخة الجديدة شغالة دلوقتي</div>
    <h1 class="rv">حمّل أي فيديو<br><span class="grad">في ثواني معدودة</span></h1>
    <p class="sub rv">الصق اللينك، اختار الجودة، وخلاص. من غير تسجيل ومن غير إعلانات ومن غير وجع دماغ.</p>

    <div class="bar-wrap rv"><div class="bar">
      <span class="ic">🔗</span>
      <input id="url" type="url" inputmode="url" autocomplete="off" spellcheck="false" placeholder="الصق لينك الفيديو هنا..." />
      <button class="paste" id="paste" type="button">📋 لصق</button>
      <button class="go" id="go" type="button">حلّل ✨</button>
    </div></div>
    <div class="hint rv"><span>مجاني بالكامل</span><span>بيكمل لو النت قطع</span><span>بيشتغل على جهازك</span></div>
    <div class="err" id="err"></div>

    <div class="marq rv"><div class="mt">
      <div class="src"><i>▶️</i>يوتيوب</div><div class="src"><i>🎵</i>تيك توك</div><div class="src"><i>📺</i>فيدوبا</div><div class="src"><i>⚡</i>انافاست</div><div class="src"><i>📁</i>لينكات مباشرة</div><div class="src"><i>🎧</i>صوت m4a</div><div class="src"><i>🎞️</i>MP4 / WEBM</div><div class="src"><i>📡</i>بث HLS</div>
      <div class="src"><i>▶️</i>يوتيوب</div><div class="src"><i>🎵</i>تيك توك</div><div class="src"><i>📺</i>فيدوبا</div><div class="src"><i>⚡</i>انافاست</div><div class="src"><i>📁</i>لينكات مباشرة</div><div class="src"><i>🎧</i>صوت m4a</div><div class="src"><i>🎞️</i>MP4 / WEBM</div><div class="src"><i>📡</i>بث HLS</div>
    </div></div>
  </section>

  <div class="loader" id="loader">
    <div class="sk"><div class="a sh"></div><div class="b"><div class="sh" style="width:90%"></div><div class="sh" style="width:65%"></div><div class="sh" style="width:40%"></div></div></div>
    <div class="ltxt">بنقرا الفيديو ونجيب أحسن الجودات...</div>
  </div>

  <section id="result">
    <div class="vtop">
      <div class="poster"><img id="rThumb" alt="" referrerpolicy="no-referrer"><div class="play"><span>▶</span></div><span class="dur" id="rDur"></span></div>
      <div class="vi"><h2 id="rTitle"></h2><div class="tags" id="rMeta"></div></div>
    </div>
    <div class="opts" id="groups"></div>
  </section>

  <section class="sec" id="dl">
    <h2>📥 تحميلاتك <span class="cnt" id="cnt" style="display:none"></span></h2>
    <p class="lead">كل حاجة بتنزل قدامك لحظة بلحظة.</p>
    <div id="jobs"></div>
    <div class="empty" id="empty"><big>🚀</big>مفيش تحميلات لسه.. الصق لينك وابدأ</div>
    <div class="saved" id="saved"></div>
  </section>

  <section class="sec" id="how">
    <h2 class="rv">3 خطوات وخلصت</h2><p class="lead rv">أبسط من كده مفيش.</p>
    <div class="steps">
      <div class="step rv"><h3>الصق اللينك</h3><p>انسخ لينك الفيديو من أي تطبيق والصقه في الخانة فوق.</p></div>
      <div class="step rv"><h3>اختار الجودة</h3><p>هنوريك كل الجودات المتاحة وأحجامها، فيديو أو صوت بس.</p></div>
      <div class="step rv"><h3>حمّل واحفظ</h3><p>التحميل بيتابع قدامك، ولما يخلص تحفظ الملف بضغطة.</p></div>
    </div>
  </section>

  <section class="sec" id="features">
    <h2 class="rv">ليه سحّاب؟</h2><p class="lead rv">اتبنى عشان يبقى سريع وبسيط وعلى هواك.</p>
    <div class="bento">
      <div class="card c-a rv"><div class="ico">⚡</div><h3>سرعة من غير تحكم</h3><p>بيحمل على أقصى سرعة النت عندك ويعرض السرعة والوقت المتبقي لحظيا.</p></div>
      <div class="card c-b rv"><div class="ico">🔁</div><h3>استكمال ذكي</h3><p>النت فصل؟ ولا يهمك، بيكمل من نفس النقطة بدل ما يبدأ من الأول.</p></div>
      <div class="card c-c rv"><div class="big">0 ج</div><p>مجاني بالكامل، ومفيش اشتراكات.</p></div>
      <div class="card c-d rv"><div class="ico">🎧</div><h3>فيديو أو صوت</h3><p>اسحب الفيديو كامل أو الصوت بس.</p></div>
      <div class="card c-e rv"><div class="ico">🔒</div><h3>خصوصية تامة</h3><p>بيشتغل على جهازك. مفيش حساب ولا تتبع.</p></div>
    </div>
  </section>

  <section class="sec" id="faq">
    <h2 class="rv">أسئلة بتتكرر</h2>
    <div class="faq">
      <details class="rv"><summary>الملفات بتتحفظ فين؟</summary><p>في مجلد Sahhab جوه مجلد التحميلات في موبايلك لو متاح، وتقدر كمان تضغط «حفظ» عشان المتصفح ينزلها.</p></details>
      <details class="rv"><summary>ليه بعض الجودات العالية من غير صوت؟</summary><p>يوتيوب بيفصل الصوت عن الفيديو في الجودات العالية. لو عايزهم مع بعض اختار من «جاهز للتشغيل».</p></details>
      <details class="rv"><summary>الموقع مش مدعوم، أعمل إيه؟</summary><p>الشغال حاليا: يوتيوب، تيك توك، فيدوبا، انافاست، وأي لينك مباشر لملف فيديو أو صوت.</p></details>
      <details class="rv"><summary>مسموح أحمل أي حاجة؟</summary><p>حمّل المحتوى اللي ليك حق فيه أو المسموح تحميله بس، والمسؤولية على المستخدم.</p></details>
    </div>
  </section>
</main>

<footer><div class="wrap"><a class="logo" href="#top"><span class="mark"><svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12"/><path d="M6.5 10.5 12 16l5.5-5.5"/><path d="M4 20h16"/></svg></span>سحّاب</a>
اتعمل بحب 💙 — حمّل المحتوى اللي ليك حق فيه بس</div></footer>
<div class="toast" id="toast"></div>

<script>
const $ = id => document.getElementById(id);
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let token = null, pollTimer = null;
const DEMO = location.search.indexOf('demo') > -1;

function toast(m){ const t=$('toast'); t.textContent=m; t.classList.add('show'); clearTimeout(t._h); t._h=setTimeout(()=>t.classList.remove('show'),2600); }
function showErr(m){ const e=$('err'); e.textContent=m; e.classList.remove('show'); void e.offsetWidth; e.classList.add('show'); }
function hideErr(){ $('err').classList.remove('show'); }

async function api(path, body){
  const r = await fetch(path, body ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)} : undefined);
  const j = await r.json().catch(()=>({error:'حصلت مشكلة غريبة'}));
  if(!r.ok || j.error) throw new Error(j.error || 'حصلت مشكلة');
  return j;
}

$('paste').onclick = async () => {
  try { $('url').value = await navigator.clipboard.readText(); $('url').focus(); toast('تم اللصق ✅'); }
  catch(e){ $('url').focus(); showErr('المتصفح مانع اللصق التلقائي.. اضغط مطولا على الخانة والصق بإيدك 📋'); }
};
$('url').addEventListener('keydown', e => { if(e.key==='Enter') analyze(); });
$('go').onclick = analyze;

async function analyze(){
  hideErr();
  const url = $('url').value.trim();
  if(!url){ showErr('يا عم انت كده مش لاعب.. حط لينك الاول 😅'); return; }
  $('go').disabled = true; $('go').textContent='⏳ بنحلل...';
  $('result').classList.remove('show'); $('loader').classList.add('show');
  try{ const d = await api('/api/info', {url}); token = d.token; renderResult(d); }
  catch(e){ showErr(e.message); }
  finally{ $('go').disabled = false; $('go').textContent='حلّل ✨'; $('loader').classList.remove('show'); }
}

const GROUPS = {
  ready:['📹 جاهز للتشغيل','صوت وصورة في ملف واحد'],
  video:['🎞️ فيديو بس','جودة عالية من غير صوت'],
  audio:['🎧 صوت بس','للأغاني والبودكاست']
};
const SRC = {youtube:'يوتيوب',vidoba:'فيدوبا',tiktok:'تيك توك',direct:'لينك مباشر'};

function renderResult(d){
  const m = d.meta, th = $('rThumb');
  const poster = th.parentElement;
  if(m.thumb){ th.src = m.thumb; poster.style.display=''; } else { th.removeAttribute('src'); poster.style.display='none'; }
  $('rDur').textContent = m.duration || ''; $('rDur').style.display = m.duration ? '' : 'none';
  $('rTitle').textContent = m.title || 'فيديو';
  const tags = [];
  if(m.author) tags.push('👤 '+m.author);
  tags.push('🌐 '+(SRC[m.source]||m.source));
  tags.push('🎯 '+d.options.length+' اختيار');
  $('rMeta').innerHTML = tags.map(t=>'<span class="tag">'+esc(t)+'</span>').join('');
  let html='';
  for(const g of ['ready','video','audio']){
    const list = d.options.filter(o=>o.group===g);
    if(!list.length) continue;
    html += '<div class="gt">'+GROUPS[g][0]+' <small>'+GROUPS[g][1]+'</small></div>';
    if(g==='video') html += '<div class="note">ℹ️ الجودات العالية من يوتيوب بتيجي فيديو لوحده من غير صوت. للصوت والصورة مع بعض اختار من «جاهز للتشغيل».</div>';
    html += '<div class="chips">';
    for(const o of list){
      html += '<button class="opt" data-id="'+o.id+'"><span class="q">'+esc(o.quality)+'</span>'
        +'<span class="m"><b>'+esc(o.ext)+'</b><span>'+esc(o.size||o.note||'—')+'</span></span><span class="arrow">⬇</span></button>';
    }
    html += '</div>';
  }
  $('groups').innerHTML = html;
  $('groups').querySelectorAll('.opt').forEach(b=> b.onclick = ()=> startDl(b.dataset.id, b));
  $('result').classList.add('show');
  if(!DEMO) $('result').scrollIntoView({behavior:'smooth', block:'center'});
}

async function startDl(id, btn){
  hideErr(); btn.disabled = true;
  try{
    if(DEMO){ toast('ده وضع التجربة'); return; }
    await api('/api/download', {token, option:id}); toast('بدأ التحميل 🚀'); poll();
    setTimeout(()=>$('dl').scrollIntoView({behavior:'smooth'}),250);
  }catch(e){ showErr(e.message); window.scrollTo({top:0,behavior:'smooth'}); }
  finally{ btn.disabled=false; }
}

function jobHtml(j){
  const pct = j.state==='done' ? 100 : Math.min(100, j.pct||0);
  const off = (163.4 * (1 - pct/100)).toFixed(1);
  let status='';
  if(j.state==='running'||j.state==='queued'){
    status = (j.detail?'<span>'+esc(j.detail)+'</span>':'')+(j.speed?'<span>⚡ '+esc(j.speed)+'</span>':'')+(j.eta?'<span>⏳ فاضل '+esc(j.eta)+'</span>':'<span>جاري البدء...</span>');
  } else if(j.state==='done'){ status='<span class="ok">✅ خلصنا</span><span>'+esc(j.size)+'</span><span>'+esc(j.file)+'</span>'; }
  else { status='<span class="er">'+esc(j.error||'فشل')+'</span>'; }
  let act='';
  if(j.state==='running'||j.state==='queued') act='<button class="sm" data-cancel="'+j.id+'">إيقاف</button>';
  if(j.can_get) act='<a class="sm pri" href="/file/'+j.id+'" download>💾 حفظ الملف</a>';
  const label = j.state==='done' ? '✓' : (j.state==='error'||j.state==='cancelled') ? '✕' : Math.round(pct)+'%';
  return '<div class="job '+j.state+'"><div class="ring"><svg viewBox="0 0 64 64"><circle class="bg" cx="32" cy="32" r="26"/><circle class="fg" cx="32" cy="32" r="26" style="stroke-dashoffset:'+off+'"/></svg><b>'+label+'</b></div>'
    +'<div class="jb"><div class="jt">'+esc(j.title)+'<small>'+esc(j.quality)+'</small></div><div class="js">'+status+'</div></div><div class="act">'+act+'</div></div>';
}

function paintJobs(jobs, dir){
  $('jobs').innerHTML = jobs.map(jobHtml).join('');
  $('empty').style.display = jobs.length ? 'none' : '';
  $('cnt').style.display = jobs.length ? '' : 'none'; $('cnt').textContent = jobs.length;
  $('jobs').querySelectorAll('[data-cancel]').forEach(b=> b.onclick = ()=> api('/api/cancel',{id:b.dataset.cancel}).then(refresh));
  if(dir) $('saved').innerHTML = '📁 الملفات بتتحفظ في: <code>'+esc(dir)+'</code>';
}
async function refresh(){
  try{ const d = await api('/api/jobs'); paintJobs(d.jobs, d.dir); return d.jobs.some(j=>j.state==='running'||j.state==='queued'); }
  catch(e){ return false; }
}
function poll(){
  if(pollTimer) return;
  const tick = async()=>{ const a = await refresh(); if(!a){ clearInterval(pollTimer); pollTimer=null; } };
  pollTimer = setInterval(tick, 800); tick();
}

// تأثيرات: ضوء يتبع الماوس + ظهور تدريجي + ضوء الكروت
const spot=$('spot');
addEventListener('pointermove',e=>{ spot.style.transform='translate('+e.clientX+'px,'+e.clientY+'px) translate(-50%,-50%)'; },{passive:true});
document.querySelectorAll('.card').forEach(c=>c.addEventListener('pointermove',e=>{
  const r=c.getBoundingClientRect(); c.style.setProperty('--mx',(e.clientX-r.left)+'px'); c.style.setProperty('--my',(e.clientY-r.top)+'px'); }));
const io = new IntersectionObserver(es=>es.forEach(x=>{ if(x.isIntersecting){ x.target.classList.add('in'); io.unobserve(x.target);} }),{threshold:.12});
document.querySelectorAll('.rv').forEach((el,i)=>{ el.style.transitionDelay=(i%4)*70+'ms'; io.observe(el); });

fetch('/api/ping').then(r=>r.json()).then(d=>{ $('ver').textContent='v'+d.version; }).catch(()=>{});
if(DEMO){
  document.querySelectorAll('.rv').forEach(e=>e.classList.add('in'));
  renderResult({token:'x',meta:{title:'فيديو تجريبي: رحلة حول العالم بجودة عالية 4K',author:'قناة سحّاب',duration:'12:34',thumb:'',source:'youtube'},
    options:[{id:0,group:'ready',quality:'720p',ext:'mp4',size:'48.2 ميجا'},{id:1,group:'ready',quality:'360p',ext:'mp4',size:'19.6 ميجا'},
    {id:2,group:'video',quality:'1080p',ext:'mp4',size:'96.1 ميجا'},{id:3,group:'audio',quality:'m4a',ext:'m4a',size:'9.4 ميجا'}]});
  paintJobs([{id:'a',state:'running',title:'رحلة حول العالم',quality:'720p',pct:64,detail:'31 ميجا / 48 ميجا',speed:'3.2 ميجا/ث',eta:'5 ثانية'},
    {id:'b',state:'done',title:'أغنية جميلة',quality:'m4a',pct:100,size:'9.4 ميجا',file:'song.m4a',can_get:true},
    {id:'c',state:'error',title:'فيديو فاشل',quality:'1080p',pct:10,error:'النت عمل حجرة ورقة مقص وخسرنا'}],'/storage/emulated/0/Download/Sahhab');
} else { refresh().then(a=>{ if(a) poll(); }); }
</script>
</body>
</html>
"""


# ---------------------------------------------------------------
# السيرفر
# ---------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "Sahhab/" + VERSION
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # هدوء
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path in ("/", "/index.html"):
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if path == "/api/ping":
            return self._json({"ok": True, "version": VERSION})
        if path == "/api/jobs":
            with JOBS_LOCK:
                jobs = sorted(JOBS.values(), key=lambda j: j["ts"], reverse=True)
                data = [public_job(j) for j in jobs[:30]]
            return self._json({"jobs": data, "dir": OUTDIR})
        if path.startswith("/file/"):
            return self._file(path[6:])
        if path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")
        self._json({"error": "مش موجود"}, 404)

    def _file(self, jid):
        with JOBS_LOCK:
            job = JOBS.get(jid)
        if not job or job["state"] != "done" or not os.path.exists(job.get("path") or ""):
            return self._json({"error": "الملف مش موجود"}, 404)
        p = job["path"]
        size = os.path.getsize(p)
        name = os.path.basename(p)
        ctype = {"mp4": "video/mp4", "webm": "video/webm", "m4a": "audio/mp4", "mp3": "audio/mpeg",
                 "ts": "video/mp2t"}.get(name.rsplit(".", 1)[-1].lower(), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition",
                         "attachment; filename*=UTF-8''" + urllib.parse.quote(name))
        self.end_headers()
        try:
            with open(p, "rb") as fh:
                while True:
                    buf = fh.read(256 * 1024)
                    if not buf:
                        break
                    self.wfile.write(buf)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}
        except Exception:  # noqa: BLE001
            return self._json({"error": "الطلب مش مفهوم"}, 400)
        try:
            if path == "/api/info":
                url = normalize_url(body.get("url"))
                if url is None:
                    return self._json({"error": MSG["empty_url"] if not (body.get("url") or "").strip() else MSG["bad_url"]}, 400)
                return self._json(analyze(url))
            if path == "/api/download":
                jid = start_job(body.get("token"), body.get("option"))
                return self._json({"id": jid})
            if path == "/api/cancel":
                with JOBS_LOCK:
                    j = JOBS.get(body.get("id"))
                if j:
                    j["_cancel"].set()
                return self._json({"ok": True})
        except SahhabError as e:
            return self._json({"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            return self._json({"error": MSG["fail_final"] + " (%s)" % e}, 500)
        self._json({"error": "مش موجود"}, 404)


def make_server(port=None):
    port = int(os.environ.get("PORT", PORT)) if port is None else port
    host = os.environ.get("SAHHAB_HOST", "127.0.0.1")
    last = None
    for p in range(port, port + 20):
        try:
            return ThreadingHTTPServer((host, p), Handler)
        except OSError as e:
            last = e
    raise last


def main():
    global OUTDIR
    OUTDIR = pick_outdir()
    srv = make_server()
    url = "http://127.0.0.1:%d" % srv.server_address[1]
    print("=" * 52)
    print(" سحّاب v%s شغال ✅" % VERSION)
    print(" افتح اللينك ده في كروم:  %s" % url)
    print(" التحميلات في: %s" % OUTDIR)
    print(" لإيقاف السيرفر: اضغط Stop في Pydroid")
    print("=" * 52)
    try:
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    except Exception:  # noqa: BLE001
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nيلا سلام يا وحش 👋")
    finally:
        srv.server_close()


# ---------------------------------------------------------------
# اختبارات ذاتية
# ---------------------------------------------------------------
def _self_test():
    import tempfile
    failures = []

    def check(name, cond):
        print("  [%s] %s" % ("PASS" if cond else "FAIL", name))
        if not cond:
            failures.append(name)

    print("== المحرك ==")
    check("watch", extract_youtube_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ")
    check("youtu.be", extract_youtube_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ")
    check("shorts", extract_youtube_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ")
    check("not youtube", extract_youtube_id("https://example.com/watch?v=dQw4w9WgXcQ") is None)
    check("route yt", route("https://m.youtube.com/watch?v=dQw4w9WgXcQ") == "youtube")
    check("notyoutube.com", route("https://notyoutube.com/watch?v=dQw4w9WgXcQ") != "youtube")
    check("route tiktok", route("https://www.tiktok.com/@a/video/1") == "tiktok")
    check("route direct", route("https://cdn.example.com/v/x.mp4?token=1") == "direct")
    check("route unknown", route("https://www.facebook.com/watch/?v=1") == "unknown")
    check("sanitize", sanitize_filename('a/b\\c:d|e*f?g"h<i>j', "mp4") == "a-b-c-d-efg'h(i)j.mp4")
    check("sanitize empty", sanitize_filename("", "mp4") == "video.mp4")
    check("ext query", get_extension_from_url("https://x.com/a/b.mp4?token=zzz") == "mp4")
    check("normalize ok", normalize_url("youtube.com/watch?v=dQw4w9WgXcQ") == "https://youtube.com/watch?v=dQw4w9WgXcQ")
    check("normalize empty", normalize_url("") is None)
    check("normalize no domain", normalize_url("hello world") is None)
    check("normalize ftp", normalize_url("ftp://a.com/x.mp4") is None)
    check("sabr all", has_only_sabr_formats({"streamingData": {"adaptiveFormats": [{"url": "https://x/?sabr=1"}]}}) is True)
    check("sabr none", has_only_sabr_formats({"streamingData": {"adaptiveFormats": [{"url": "https://x/"}]}}) is False)
    packed = ("eval(function(p,a,c,k,e,d){while(c--)if(k[c])p=p.replace("
              "new RegExp('\\\\b'+e(c)+'\\\\b','g'),k[c]);return p}('1 0',10,2,'world|hello'.split('|')))")
    up = unpack_packer(packed)
    check("packer", up is not None and up.strip() == "hello world")
    check("master m3u8", parse_master_m3u8("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100,RESOLUTION=640x360\na.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=200,RESOLUTION=1280x720\nb.m3u8\n")[0]["height"] == 720)

    print("== الواجهة والسيرفر (محلي) ==")
    global OUTDIR
    tmp = tempfile.mkdtemp()
    OUTDIR = os.path.join(tmp, "out")
    os.makedirs(OUTDIR)
    payload = os.urandom(700 * 1024)
    src_dir = os.path.join(tmp, "src")
    os.makedirs(src_dir)
    with open(os.path.join(src_dir, "clip.mp4"), "wb") as fh:
        fh.write(payload)
    import functools
    import http.server
    fs = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(http.server.SimpleHTTPRequestHandler, directory=src_dir))
    threading.Thread(target=fs.serve_forever, daemon=True).start()
    srv = make_server(18765)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % srv.server_address[1]

    def call(path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + path, data=data,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    st, html = call("/")
    check("الصفحة الرئيسية", st == 200 and "سحّاب".encode() in html)
    st, b = call("/api/info", {"url": ""})
    check("لينك فاضي", st == 400 and "error" in json.loads(b))
    st, b = call("/api/info", {"url": "https://www.facebook.com/watch/?v=1"})
    check("موقع مش مدعوم", st == 400)
    st, b = call("/api/info", {"url": "http://127.0.0.1:%d/clip.mp4" % fs.server_address[1]})
    # route بيطلب دومين فيه نقطة، فاللينك المحلي غالبا يترفض: نختبر بـ localhost عبر دالة مباشرة
    if st != 200:
        info = None
        with INFOS_LOCK:
            tok = "t1"
            INFOS[tok] = {"meta": {"title": "clip", "thumb": ""}, "ts": time.time(), "options": [
                dict(group="ready", quality="MP4", ext="mp4", size=len(payload), type="file",
                     url="http://127.0.0.1:%d/clip.mp4" % fs.server_address[1], headers={})]}
    else:
        tok = json.loads(b)["token"]
    st, b = call("/api/download", {"token": tok, "option": 0})
    check("بدء التحميل", st == 200)
    jid = json.loads(b)["id"]
    for _ in range(100):
        time.sleep(0.1)
        st, b = call("/api/jobs")
        js = {j["id"]: j for j in json.loads(b)["jobs"]}
        if js[jid]["state"] in ("done", "error"):
            break
    check("التحميل خلص", js[jid]["state"] == "done")
    st, b = call("/file/" + jid)
    check("الملف مطابق", st == 200 and b == payload)
    st, b = call("/api/download", {"token": "nope", "option": 0})
    check("جلسة منتهية", st == 400)
    srv.shutdown()
    fs.shutdown()
    print()
    if failures:
        print("فشل: %s" % ", ".join(failures))
        return 1
    print("كل الاختبارات نجحت. البرنامج زي الفل!")
    return 0


if __name__ == "__main__":
    if "--test" in sys.argv:
        sys.exit(_self_test())
    main()
