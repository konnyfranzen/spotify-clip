"""
Spotify-klipp i molnet: spelar bara första N sekunderna av varje låt, byter sedan.
Styrs från en enkel webbsida (funkar bra i Safari på iPhone).

Miljövariabler:
    SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET   från developer.spotify.com/dashboard
    SPOTIPY_REDIRECT_URI                       https://DIN-APP.onrender.com/callback
    APP_PASSWORD                               lösenord till webbsidan (obligatoriskt)
    SPOTIFY_REFRESH_TOKEN                      valfritt, sparar inloggningen mellan omstarter
    CLIP_SECONDS                               valfritt, standard 60
"""
import base64
import hmac
import logging
import os
import sys
import threading
import time

import spotipy
from flask import Flask, Response, jsonify, redirect, request
from spotipy.cache_handler import MemoryCacheHandler
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth

logging.basicConfig(
    level=logging.INFO, stream=sys.stdout, format="%(asctime)s %(message)s"
)
log = logging.getLogger("clip")

SCOPE = "user-read-playback-state user-modify-playback-state"
TIMEOUT = 10  # sekunder, så att inget anrop kan hänga för evigt
PASSWORD = os.environ["APP_PASSWORD"]

app = Flask(__name__)

state = {
    "running": False,
    "seconds": int(os.getenv("CLIP_SECONDS", "60")),
    "last": "Ingen aktivitet än",
    "seen": "Ingen kontroll gjord än (tryck Starta)",
    "user": "",
    "skipped": 0,
    "stage": "startar",
    "beat": "",
}


def set_seen(msg, log_it=True):
    if log_it and state["seen"] != msg:
        log.info(msg)
    state["seen"] = msg


def make_oauth():
    refresh = os.getenv("SPOTIFY_REFRESH_TOKEN")
    token = None
    if refresh:
        # expires_at=0 gör att en ny access token hämtas direkt
        token = {
            "access_token": "",
            "refresh_token": refresh.strip(),
            "expires_at": 0,
            "expires_in": 0,
            "token_type": "Bearer",
            "scope": SCOPE,
        }
    return SpotifyOAuth(
        scope=SCOPE,
        cache_handler=MemoryCacheHandler(token_info=token),
        open_browser=False,
        show_dialog=True,
        requests_timeout=TIMEOUT,
    )


oauth = make_oauth()


def current_token():
    cached = oauth.cache_handler.get_cached_token()
    if not cached:
        return None
    return oauth.validate_token(cached)


def devices_text(sp):
    """Kort text om vilka Spotify-enheter som syns, för felsökning."""
    try:
        devs = sp.devices().get("devices", [])
        if not devs:
            return "Inga enheter syns. Öppna Spotify-appen och spela en låt."
        return "Enheter: " + ", ".join(
            f"{d.get('name')}{' (aktiv)' if d.get('is_active') else ' (inte aktiv)'}"
            for d in devs
        )
    except SpotifyException as e:
        return f"Kunde inte läsa enheter ({e.http_status})."
    except Exception as e:
        return f"Kunde inte läsa enheter: {e}"


def worker():
    log.info("Bakgrundsjobbet startade")
    while True:
        time.sleep(1)
        state["beat"] = time.strftime("%H:%M:%S")
        if not state["running"]:
            state["stage"] = "vilar (inte startad)"
            continue
        try:
            state["stage"] = "hämtar token"
            token = current_token()
            if not token:
                set_seen("Inte inloggad mot Spotify (koden saknas eller är ogiltig)")
                state["last"] = "Inte inloggad mot Spotify"
                time.sleep(5)
                continue
            sp = spotipy.Spotify(
                auth=token["access_token"], requests_timeout=TIMEOUT, retries=1
            )

            if not state["user"]:
                state["stage"] = "hämtar kontonamn"
                try:
                    me = sp.current_user()
                    state["user"] = me.get("display_name") or me.get("id") or "okänt"
                    log.info("Inloggad som %s", state["user"])
                except Exception as e:
                    log.info("Kunde inte läsa kontonamn: %s", e)
                    state["user"] = "okänt"

            state["stage"] = "läser uppspelning"
            pb = sp.current_playback()
            state["stage"] = "klar med kontroll"
            if not pb:
                set_seen(
                    "Spotify rapporterar ingen aktiv uppspelning. " + devices_text(sp)
                )
            elif not pb.get("item"):
                set_seen("Uppspelning finns men ingen låt hittades (reklam eller podd?)")
            elif not pb.get("is_playing"):
                set_seen(f"Pausad: {pb['item']['name']}")
            else:
                name = pb["item"]["name"]
                progress = pb["progress_ms"] or 0
                set_seen(
                    f"Spelar: {name} ({progress // 1000} s av {state['seconds']} s)",
                    log_it=False,
                )
                if progress >= state["seconds"] * 1000:
                    state["stage"] = "hoppar över"
                    sp.next_track()
                    state["skipped"] += 1
                    state["last"] = f"Hoppade över: {name}"
                    log.info("Hoppade över: %s", name)
                    time.sleep(3)  # undvik dubbelhopp
        except SpotifyException as e:
            wait = 5
            if e.http_status == 429 and e.headers:
                wait = int(e.headers.get("Retry-After", 5)) + 1
            msg = f"Spotify-fel {e.http_status}: {str(e.msg)[:150]}"
            state["last"] = msg
            set_seen(msg)
            time.sleep(wait)
        except Exception as e:
            msg = f"Fel: {type(e).__name__}: {str(e)[:150]}"
            state["last"] = msg
            set_seen(msg)
            time.sleep(5)


worker_thread = None
_lock = threading.Lock()


def ensure_worker():
    """Starta bakgrundsjobbet, och starta om det om tråden skulle ha dött."""
    global worker_thread
    with _lock:
        if worker_thread is None or not worker_thread.is_alive():
            worker_thread = threading.Thread(target=worker, daemon=True)
            worker_thread.start()


ensure_worker()


@app.route("/ping")
def ping():
    # Utan lösenord, så en gratis övervakningstjänst kan hålla appen vaken
    ensure_worker()
    return "ok"


LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120" role="img" aria-label="Låtväxlarn">
<defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#3df0a8"/><stop offset="1" stop-color="#0e9f5a"/></linearGradient></defs>
<rect width="120" height="120" rx="28" fill="url(#lg)"/>
<g fill="#08170f" transform="translate(-6 0)">
<rect x="24" y="49" width="9" height="22" rx="4.5" opacity=".45"/>
<rect x="38" y="35" width="9" height="50" rx="4.5" opacity=".7"/>
<rect x="52" y="43" width="9" height="34" rx="4.5"/>
<path d="M72 41 L90 60 L72 79 Z" stroke="#08170f" stroke-width="8" stroke-linejoin="round"/>
<rect x="100" y="38" width="9" height="44" rx="4.5"/>
</g></svg>"""

# Dessa sidor är öppna utan lösenord (ikoner som webbläsaren hämtar själv)
PUBLIC_PATHS = {"/ping", "/logo.svg", "/apple-touch-icon.png"}


ICON_PNG_B64 = (
    "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"
)


@app.route("/apple-touch-icon.png")
def apple_touch_icon():
    return Response(base64.b64decode(ICON_PNG_B64), mimetype="image/png")


@app.route("/logo.svg")
def logo_svg():
    return Response(LOGO_SVG, mimetype="image/svg+xml")


@app.before_request
def check_auth():
    if request.path in PUBLIC_PATHS:
        return None
    auth = request.authorization
    if not auth or not hmac.compare_digest(auth.password or "", PASSWORD):
        return Response(
            "Logga in", 401, {"WWW-Authenticate": 'Basic realm="Spotify clip"'}
        )
    ensure_worker()
    return None


PAGE = """<!doctype html>
<html lang="sv"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Låtväxlarn</title>
<meta name="apple-mobile-web-app-title" content="Låtväxlarn">
<meta name="theme-color" content="#121212">
<link rel="icon" href="/logo.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<style>
 body{font-family:-apple-system,system-ui,sans-serif;max-width:420px;margin:0 auto;padding:12px 16px;
      background:#121212;color:#eee}
 .hero{text-align:center;padding:2px 0 0}
 .hero svg{width:56px;height:56px;filter:drop-shadow(0 4px 12px rgba(29,185,84,.35))}
 h1{font-size:1.5rem;margin:6px 0 0;letter-spacing:.3px;text-align:center}
 .card{background:#1e1e1e;border-radius:14px;padding:10px 14px;margin:8px 0}
 label{display:block;text-align:center;color:#bbb;font-size:.9rem}
 input{font-size:1.1rem;padding:8px;border-radius:12px;border:1px solid #3a3a3a;width:100%;
       box-sizing:border-box;margin-top:6px;background:#2a2a2a;color:#fff;text-align:center}
 .row{display:flex;justify-content:center;gap:32px;margin-top:10px;margin-bottom:0}
 .round{width:auto;margin:0;padding:0;border:0;background:none;display:flex;flex-direction:column;
        align-items:center;gap:5px;color:#eee;font-size:.85rem;font-weight:600;cursor:pointer;
        -webkit-tap-highlight-color:transparent}
 .round .circle{width:72px;height:72px;border-radius:50%;display:grid;place-items:center;
        box-shadow:0 4px 12px rgba(0,0,0,.45);transition:transform .12s}
 .round:active .circle{transform:scale(.93)}
 .go .circle{background:#1db954;color:#06210f} .stop .circle{background:#e5484d;color:#fff}
 a{color:#1db954} .small{font-size:.85rem;color:#bbb}
</style></head><body>
<header class="hero">{logo}<h1>Låtväxlarn</h1></header>
<div class="card">
 Status: <b id="run">...</b><br>Senaste: <span id="last">...</span><br>Hoppade över: <span id="n">0</span>
</div>
<div class="card small">
 Ser just nu: <span id="seen">...</span>
</div>
<div class="card">
 <form method="post" action="/start">
  <label for="secs">Sekunder per låt</label>
  <input id="secs" type="number" name="seconds" min="5" max="600" value="{seconds}" inputmode="numeric">
  <div class="row">
   <button class="round go" type="submit" formaction="/start" aria-label="Starta">
    <span class="circle"><svg viewBox="0 0 24 24" width="32" height="32" aria-hidden="true"><path d="M8 5.5v13l11-6.5z" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg></span>
    <span>Starta</span>
   </button>
   <button class="round stop" type="submit" formaction="/stop" formnovalidate aria-label="Stoppa">
    <span class="circle"><svg viewBox="0 0 24 24" width="29" height="29" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2.5" fill="currentColor"/></svg></span>
    <span>Stoppa</span>
   </button>
  </div>
 </form>
</div>
<div class="card">Starta musik i Spotify-appen först. <a href="/login">Logga in mot Spotify</a><br>
 <span class="small">Inloggad som: <span id="user">-</span></span></div>
<script>
async function upd(){try{const r=await (await fetch('/status')).json();
 run.textContent=r.running?'Igång ('+r.seconds+' s)':'Stoppad';
 last.textContent=r.last;n.textContent=r.skipped;
 document.getElementById('seen').textContent=r.seen;
 document.getElementById('user').textContent=r.user||'-';}catch(e){}}
upd();setInterval(upd,3000);
</script></body></html>"""


@app.route("/")
def index():
    return PAGE.replace("{seconds}", str(state["seconds"])).replace("{logo}", LOGO_SVG)


@app.route("/status")
def status():
    return jsonify(state)


@app.route("/start", methods=["POST"])
def start():
    try:
        state["seconds"] = max(5, min(600, int(request.form.get("seconds", 60))))
    except ValueError:
        pass
    state["running"] = True
    log.info("Startad, %s s per låt", state["seconds"])
    return redirect("/")


@app.route("/stop", methods=["POST"])
def stop():
    state["running"] = False
    log.info("Stoppad")
    return redirect("/")


@app.route("/login")
def login():
    return redirect(oauth.get_authorize_url())


@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "Inloggningen misslyckades", 400
    oauth.get_access_token(code, as_dict=False, check_cache=False)
    state["user"] = ""  # läses om vid nästa kontroll
    refresh = oauth.cache_handler.get_cached_token()["refresh_token"]
    return (
        "<body style='font-family:system-ui;padding:20px'>"
        "<h3>Inloggad!</h3>"
        "<p>Vill du slippa logga in igen när appen startar om? Lägg detta som miljövariabeln "
        "<b>SPOTIFY_REFRESH_TOKEN</b> i Render (håll det hemligt):</p>"
        f"<textarea readonly rows=4 style='width:100%'>{refresh}</textarea>"
        "<p><a href='/'>Till startsidan</a></p></body>"
    )
