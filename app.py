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
import os
import threading
import time

import spotipy
from flask import Flask, Response, jsonify, redirect, request
from spotipy.cache_handler import MemoryCacheHandler
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth

SCOPE = "user-read-playback-state user-modify-playback-state"
PASSWORD = os.environ["APP_PASSWORD"]

app = Flask(__name__)

state = {
    "running": False,
    "seconds": int(os.getenv("CLIP_SECONDS", "60")),
    "last": "Ingen aktivitet än",
    "skipped": 0,
}


def make_oauth():
    refresh = os.getenv("SPOTIFY_REFRESH_TOKEN")
    token = None
    if refresh:
        # expires_at=0 gör att en ny access token hämtas direkt
        token = {
            "access_token": "",
            "refresh_token": refresh,
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
    )


oauth = make_oauth()


def current_token():
    cached = oauth.cache_handler.get_cached_token()
    if not cached:
        return None
    return oauth.validate_token(cached)


def worker():
    while True:
        time.sleep(1)
        if not state["running"]:
            continue
        try:
            token = current_token()
            if not token:
                state["last"] = "Inte inloggad mot Spotify"
                time.sleep(5)
                continue
            sp = spotipy.Spotify(auth=token["access_token"])
            pb = sp.current_playback()
            if (
                pb
                and pb.get("is_playing")
                and pb.get("item")
                and pb["progress_ms"] >= state["seconds"] * 1000
            ):
                name = pb["item"]["name"]
                sp.next_track()
                state["skipped"] += 1
                state["last"] = f"Hoppade över: {name}"
                time.sleep(3)  # undvik dubbelhopp
        except SpotifyException as e:
            wait = 5
            if e.http_status == 429 and e.headers:
                wait = int(e.headers.get("Retry-After", 5)) + 1
            state["last"] = f"Spotify-fel {e.http_status}, försöker igen"
            time.sleep(wait)
        except Exception as e:
            state["last"] = f"Fel: {e}"
            time.sleep(5)


threading.Thread(target=worker, daemon=True).start()


@app.route("/ping")
def ping():
    # Utan lösenord, så en gratis övervakningstjänst kan hålla appen vaken
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
 .alt{background:#333;color:#eee} a{color:#1db954}
</style></head><body>
<h1>Spotify-klipp</h1>
<div class="card">
 Status: <b id="run">...</b><br>Senaste: <span id="last">...</span><br>Hoppade över: <span id="n">0</span>
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
 last.textContent=r.last;n.textContent=r.skipped;}catch(e){}}
upd();setInterval(upd,10000);
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
    return redirect("/")


@app.route("/stop", methods=["POST"])
def stop():
    state["running"] = False
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
    refresh = oauth.cache_handler.get_cached_token()["refresh_token"]
    return (
        "<body style='font-family:system-ui;padding:20px'>"
        "<h3>Inloggad!</h3>"
        "<p>Vill du slippa logga in igen när appen startar om? Lägg detta som miljövariabeln "
        "<b>SPOTIFY_REFRESH_TOKEN</b> i Render (håll det hemligt):</p>"
        f"<textarea readonly rows=4 style='width:100%'>{refresh}</textarea>"
        "<p><a href='/'>Till startsidan</a></p></body>"
    )
