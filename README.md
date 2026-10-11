# Spotify-klipp i molnet (iPhone-vänlig)

Spelar bara första N sekunderna av varje låt, byter sedan. Allt görs från Safari på iPhone.
Kräver Spotify Premium.

## Installation (ca 15 min)

1. **GitHub:** skapa ett konto och ett nytt repo (github.com/new). Välj "Add file > Upload files"
   och ladda upp `app.py`, `requirements.txt` och `render.yaml`.
2. **Spotify:** gå till developer.spotify.com/dashboard, skapa en app. Notera Client ID och Client Secret.
   Redirect URI lägger du till i steg 4.
3. **Render:** skapa konto på render.com > New > Blueprint > välj ditt repo. Fyll i:
   - `SPOTIPY_CLIENT_ID` och `SPOTIPY_CLIENT_SECRET` från steg 2
   - `APP_PASSWORD`: valfritt lösenord till webbsidan
   - `SPOTIPY_REDIRECT_URI`: `https://DITT-APPNAMN.onrender.com/callback`
     (appnamnet ser du i Render; om det är upptaget får du ett annat, kolla URL:en)
4. **Tillbaka i Spotify-dashboarden:** lägg till exakt samma Redirect URI under appens Settings.
5. Öppna `https://DITT-APPNAMN.onrender.com`, logga in med valfritt användarnamn och ditt lösenord,
   tryck "Logga in mot Spotify" och godkänn.
6. Starta musik i Spotify-appen, tryck **Starta** på sidan.

## Hålla appen vaken

Gratisplaner på molntjänster brukar somna efter en stund utan besök, och då stannar
bakgrundstråden. Lägg `https://DITT-APPNAMN.onrender.com/ping` som en HTTP-monitor
i en gratis övervakningstjänst (t.ex. UptimeRobot, 5 minuters intervall) så hålls den igång.

## Spara inloggningen

Efter inloggning visas en refresh token. Lägg den som `SPOTIFY_REFRESH_TOKEN` i Render,
så slipper du logga in på nytt varje gång appen startar om.

## Säkerhet

Webbsidan styr ditt Spotify-konto, så välj ett långt `APP_PASSWORD` och dela inte adressen.
