import base64
import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp
from Crypto.Cipher import DES

app = FastAPI(title="Unified Music Stream API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# --- Base64 URL Safe Encoders ---
def encode_safe(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode('utf-8')).decode('utf-8')

def decode_safe(text: str) -> str:
    return base64.urlsafe_b64decode(text.encode('utf-8')).decode('utf-8')

# --- JioSaavn Decryption Logic ---
def decrypt_saavn_url(encrypted_url: str) -> str:
    if not encrypted_url:
        return ""
    try:
        key = b"38586bea"
        cipher = DES.new(key, DES.MODE_ECB)
        decrypted_bytes = cipher.decrypt(base64.b64decode(encrypted_url))
        padding_len = decrypted_bytes[-1]
        clean_bytes = decrypted_bytes[:-padding_len]
        stream_url = clean_bytes.decode('utf-8')
        return stream_url.replace("_96.mp4", "_320.mp4").replace("_96.aac", "_320.mp4")
    except Exception:
        return ""

# 1. JioSaavn Fetcher (Instant Base64 Token Creation)
def fetch_jiosaavn(query: str, limit: int = 7):
    results = []
    url = f"https://www.jiosaavn.com/api.php?__call=search.getResults&_format=json&_marker=0&cc=in&includeMetaTags=1&q={query}&p=1&n={limit}"
    try:
        res = requests.get(url, headers=HEADERS, timeout=5)
        if res.status_code == 200:
            data = res.json().get("results", [])
            for item in data:
                enc_url = item.get("encrypted_media_url", "")
                direct_cdn_url = decrypt_saavn_url(enc_url)
                title = item.get("song", "").replace("&quot;", '"').replace("&#039;", "'")
                artist = item.get("singers", "Unknown Artist")
                
                if direct_cdn_url:
                    token = encode_safe(direct_cdn_url)
                    play_url = f"http://127.0.0.1:8000/play?source=jiosaavn&token={token}"
                else:
                    # Fallback to YouTube Search if JioSaavn link is empty
                    search_term = requests.utils.quote(f"{title} {artist}")
                    play_url = f"http://127.0.0.1:8000/play?source=spotify&query={search_term}"
                
                results.append({
                    "id": item.get("id"),
                    "title": title,
                    "artist": artist,
                    "source": "JioSaavn",
                    "play_url": play_url
                })
    except Exception:
        pass
    return results

# 2. YouTube Music Fetcher
def fetch_ytmusic(query: str, limit: int = 7):
    results = []
    ydl_opts = {'quiet': True, 'extract_flat': True, 'skip_download': True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch{limit}:{query} official audio", download=False)
            entries = info.get('entries', [])
            for entry in entries:
                if not entry:
                    continue
                v_id = entry.get('id')
                results.append({
                    "id": v_id,
                    "title": entry.get('title'),
                    "artist": entry.get('uploader', 'YouTube Music'),
                    "source": "YouTube Music",
                    "play_url": f"http://127.0.0.1:8000/play?source=youtube&id={v_id}"
                })
    except Exception:
        pass
    return results

# 3. Spotify Fetcher
def fetch_spotify(query: str, limit: int = 6):
    results = []
    try:
        token_res = requests.get("https://open.spotify.com/get_access_token", headers=HEADERS, timeout=5)
        if token_res.status_code == 200:
            token = token_res.json().get("accessToken")
            headers = {"Authorization": f"Bearer {token}", **HEADERS}
            res = requests.get(f"https://api.spotify.com/v1/search?q={query}&type=track&limit={limit}", headers=headers, timeout=5)
            if res.status_code == 200:
                tracks = res.json().get("tracks", {}).get("items", [])
                for track in tracks:
                    artists = ", ".join([a.get("name") for a in track.get("artists", [])])
                    title = track.get("name")
                    search_term = requests.utils.quote(f"{title} {artists}")
                    results.append({
                        "id": track.get("id"),
                        "title": title,
                        "artist": artists,
                        "source": "Spotify",
                        "play_url": f"http://127.0.0.1:8000/play?source=spotify&query={search_term}"
                    })
    except Exception:
        pass
    return results

# --- Aggregated Search Endpoint (20 Combined Results) ---
@app.get("/search")
def search_all_sources(name: str):
    saavn_results = fetch_jiosaavn(name, limit=7)
    yt_results = fetch_ytmusic(name, limit=7)
    spotify_results = fetch_spotify(name, limit=6)
    
    all_songs = saavn_results + yt_results + spotify_results
    return {
        "status": True,
        "query": name,
        "total_results": len(all_songs),
        "breakdown": {
            "jiosaavn": len(saavn_results),
            "youtube_music": len(yt_results),
            "spotify": len(spotify_results)
        },
        "songs": all_songs
    }

# --- Bulletproof Streaming Proxy Endpoint ---
@app.get("/play")
def play_audio(source: str, id: str = None, token: str = None, query: str = None):
    # 1. JioSaavn Direct Proxy Stream via Token
    if source == "jiosaavn" and token:
        try:
            stream_cdn_url = decode_safe(token)
            req = requests.get(stream_cdn_url, headers=HEADERS, stream=True)
            if req.status_code == 200:
                return StreamingResponse(
                    req.iter_content(chunk_size=1024 * 64),
                    media_type="audio/mp4"
                )
        except Exception:
            pass
        raise HTTPException(status_code=400, detail="JioSaavn stream failed to decode")

    # 2. YouTube Music / Spotify Stream Proxy
    ydl_opts = {'format': 'bestaudio/best', 'quiet': True, 'skip_download': True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            if source == "youtube" and id:
                target = f"https://www.youtube.com/watch?v={id}"
            elif query:
                target = f"ytsearch1:{query} official audio"
            else:
                raise HTTPException(status_code=400, detail="Missing parameter")
                
            info = ydl.extract_info(target, download=False)
            if 'entries' in info and len(info['entries']) > 0:
                info = info['entries'][0]
                
            stream_url = info.get('url')
            if stream_url:
                req = requests.get(stream_url, headers=HEADERS, stream=True)
                return StreamingResponse(
                    req.iter_content(chunk_size=1024 * 64),
                    media_type="audio/webm"
                )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    raise HTTPException(status_code=404, detail="Audio stream not found")
