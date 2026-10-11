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


@app.before_request
def check_auth():
    if request.path == "/ping":
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
<title>Spotify-klipp</title>
<style>
 body{font-family:-apple-system,system-ui,sans-serif;max-width:420px;margin:0 auto;padding:20px;
      background:#121212;color:#eee}
 h1{font-size:1.4rem} .card{background:#1e1e1e;border-radius:12px;padding:16px;margin:14px 0}
 button,input{font-size:1.1rem;padding:14px;border-radius:10px;border:0;width:100%;box-sizing:border-box;margin-top:8px}
 .go{background:#1db954;color:#000;font-weight:600} .stop{background:#e5484d;color:#fff;font-weight:600}
 .alt{background:#333;color:#eee} a{color:#1db954} .small{font-size:.9rem;color:#bbb}
</style></head><body>
<h1>Spotify-klipp</h1>
<div class="card">
 Status: <b id="run">...</b><br>Senaste: <span id="last">...</span><br>Hoppade över: <span id="n">0</span>
</div>
<div class="card small">
 Ser just nu: <span id="seen">...</span><br>Inloggad som: <span id="user">-</span><br>
 Bakgrundsjobb: <span id="job">-</span>
</div>
<div class="card">
 <form method="post" action="/start">
  <label>Sekunder per låt</label>
  <input type="number" name="seconds" min="5" max="600" value="{seconds}">
  <button class="go">Starta</button>
 </form>
 <form method="post" action="/stop"><button class="stop">Stoppa</button></form>
</div>
<div class="card">Starta musik i Spotify-appen först. <a href="/login">Logga in mot Spotify</a></div>
<script>
async function upd(){try{const r=await (await fetch('/status')).json();
 run.textContent=r.running?'Igång ('+r.seconds+' s)':'Stoppad';
 last.textContent=r.last;n.textContent=r.skipped;
 document.getElementById('seen').textContent=r.seen;
 document.getElementById('user').textContent=r.user||'-';
 document.getElementById('job').textContent=r.stage+' (serverns tid '+(r.beat||'-')+')';}catch(e){}}
upd();setInterval(upd,3000);
</script></body></html>"""


@app.route("/")
def index():
    return PAGE.replace("{seconds}", str(state["seconds"]))


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
