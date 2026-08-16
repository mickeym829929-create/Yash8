from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import requests
import base64
from Crypto.Cipher import DES
import os
import json
from urllib.parse import quote
import time
from typing import Optional
import yt_dlp

app = FastAPI(title="Music Stream API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, Like Gecko) Chrome/120.0.0.0 Safari/537.36"
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

# 1. JioSaavn Fetcher
def fetch_jiosaavn(query: str, limit: int = 7):
    results = []
    url = f"https://www.jiosaavn.com/api.php?__call=search.getResults&_format=json&_marker=0&cc=in&includeMetaTags=1&q={query}&p=1&n={limit}"
    try:
        res = requests.get(url, headers=HEADERS, timeout=8)
        if res.status_code == 200:
            data = res.json().get("results", [])
            for item in data:
                enc_url = item.get("encrypted_media_url", "")
                direct_cdn_url = decrypt_saavn_url(enc_url)
                title = item.get("song", "").replace("&quot;", '"').replace("&#039;", "'")
                artist = item.get("singers", "Unknown Artist")
                
                if direct_cdn_url:
                    token = encode_safe(direct_cdn_url)
                    # Use Vercel URL if available
                    base_url = os.getenv("VERCEL_URL", "https://your-domain.vercel.app")
                    play_url = f"https://{base_url}/play?source=jiosaavn&token={token}"
                else:
                    search_term = quote(f"{title} {artist}")
                    base_url = os.getenv("VERCEL_URL", "https://your-domain.vercel.app")
                    play_url = f"https://{base_url}/play?source=spotify&query={search_term}"
                
                results.append({
                    "id": item.get("id"),
                    "title": title,
                    "artist": artist,
                    "source": "JioSaavn",
                    "play_url": play_url,
                    "thumbnail": item.get("image", ""),
                    "duration": item.get("duration", "")
                })
    except Exception as e:
        print(f"JioSaavn error: {e}")
    return results

# 2. Spotify Fetcher
def fetch_spotify(query: str, limit: int = 6):
    results = []
    try:
        token_res = requests.get("https://open.spotify.com/get_access_token", headers=HEADERS, timeout=5)
        if token_res.status_code == 200:
            token_data = token_res.json()
            token = token_data.get("accessToken")
            if token:
                headers = {"Authorization": f"Bearer {token}", **HEADERS}
                res = requests.get(
                    f"https://api.spotify.com/v1/search?q={query}&type=track&limit={limit}",
                    headers=headers,
                    timeout=5
                )
                if res.status_code == 200:
                    tracks = res.json().get("tracks", {}).get("items", [])
                    for track in tracks:
                        artists = ", ".join([a.get("name") for a in track.get("artists", [])])
                        title = track.get("name")
                        search_term = quote(f"{title} {artists}")
                        base_url = os.getenv("VERCEL_URL", "https://your-domain.vercel.app")
                        
                        results.append({
                            "id": track.get("id"),
                            "title": title,
                            "artist": artists,
                            "source": "Spotify",
                            "play_url": f"https://{base_url}/play?source=spotify&query={search_term}",
                            "thumbnail": track.get("album", {}).get("images", [{}])[0].get("url", ""),
                            "duration": track.get("duration_ms", 0)
                        })
    except Exception as e:
        print(f"Spotify error: {e}")
    return results

# 3. YouTube Music Fetcher
def fetch_ytmusic(query: str, limit: int = 7):
    results = []
    try:
        ydl_opts = {
            'quiet': True,
            'extract_flat': True,
            'skip_download': True,
            'no_warnings': True
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch{limit}:{query} official audio", download=False)
            entries = info.get('entries', [])
            for entry in entries:
                if not entry:
                    continue
                v_id = entry.get('id')
                base_url = os.getenv("VERCEL_URL", "https://your-domain.vercel.app")
                results.append({
                    "id": v_id,
                    "title": entry.get('title', 'Unknown'),
                    "artist": entry.get('uploader', 'YouTube Music'),
                    "source": "YouTube Music",
                    "play_url": f"https://{base_url}/play?source=youtube&id={v_id}",
                    "thumbnail": f"https://img.youtube.com/vi/{v_id}/hqdefault.jpg",
                    "duration": entry.get('duration', 0)
                })
    except Exception as e:
        print(f"YouTube error: {e}")
        # Fallback to Invidious API if yt-dlp fails
        try:
            invidious_instances = [
                "https://invidious.io.lol",
                "https://invidious.fdn.fr",
                "https://inv.riverside.rocks"
            ]
            
            for instance in invidious_instances:
                try:
                    res = requests.get(
                        f"{instance}/api/v1/search?q={query}&type=video&limit={limit}",
                        headers=HEADERS,
                        timeout=5
                    )
                    if res.status_code == 200:
                        videos = res.json()
                        for video in videos[:limit]:
                            if video.get("lengthSeconds", 0) > 0:
                                video_id = video.get("videoId")
                                base_url = os.getenv("VERCEL_URL", "https://your-domain.vercel.app")
                                results.append({
                                    "id": video_id,
                                    "title": video.get("title", "Unknown"),
                                    "artist": video.get("author", "YouTube Music"),
                                    "source": "YouTube Music",
                                    "play_url": f"https://{base_url}/play?source=youtube&id={video_id}",
                                    "thumbnail": f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
                                    "duration": video.get("lengthSeconds", 0)
                                })
                        break
                except:
                    continue
        except:
            pass
    return results

# --- Aggregated Search Endpoint ---
@app.get("/search")
def search_all_sources(name: str):
    # Limit results to avoid timeout on Vercel
    saavn_results = fetch_jiosaavn(name, limit=5)
    yt_results = fetch_ytmusic(name, limit=5)
    spotify_results = fetch_spotify(name, limit=5)
    
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

# --- Streaming Proxy Endpoint ---
@app.get("/play")
def play_audio(
    source: str,
    id: Optional[str] = None,
    token: Optional[str] = None,
    query: Optional[str] = None
):
    try:
        # 1. JioSaavn Direct Proxy Stream via Token
        if source == "jiosaavn" and token:
            try:
                stream_cdn_url = decode_safe(token)
                req = requests.get(stream_cdn_url, headers=HEADERS, stream=True, timeout=10)
                if req.status_code == 200:
                    return StreamingResponse(
                        req.iter_content(chunk_size=1024 * 64),
                        media_type=req.headers.get("content-type", "audio/mp4")
                    )
            except Exception as e:
                print(f"JioSaavn stream error: {e}")
                raise HTTPException(status_code=400, detail="JioSaavn stream failed")
        
        # 2. YouTube Music Stream
        elif source == "youtube" and id:
            try:
                ydl_opts = {
                    'format': 'bestaudio/best',
                    'quiet': True,
                    'no_warnings': True,
                    'extract_flat': False,
                    'skip_download': True,
                }
                
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(f"https://www.youtube.com/watch?v={id}", download=False)
                    stream_url = info.get('url')
                    
                    if stream_url:
                        req = requests.get(stream_url, headers=HEADERS, stream=True, timeout=10)
                        if req.status_code == 200:
                            return StreamingResponse(
                                req.iter_content(chunk_size=1024 * 64),
                                media_type=req.headers.get("content-type", "audio/webm")
                            )
            except Exception as e:
                print(f"YouTube stream error: {e}")
                # Fallback to alternative source
                try:
                    alt_url = f"https://invidious.io.lol/api/v1/videos/{id}"
                    alt_res = requests.get(alt_url, headers=HEADERS, timeout=5)
                    if alt_res.status_code == 200:
                        formats = alt_res.json().get("adaptiveFormats", [])
                        for fmt in formats:
                            if fmt.get("type", "").startswith("audio/"):
                                stream_url = fmt.get("url")
                                if stream_url:
                                    req = requests.get(stream_url, headers=HEADERS, stream=True, timeout=10)
                                    if req.status_code == 200:
                                        return StreamingResponse(
                                            req.iter_content(chunk_size=1024 * 64),
                                            media_type=req.headers.get("content-type", "audio/webm")
                                        )
                except:
                    pass
                
                raise HTTPException(status_code=404, detail="YouTube stream not found")
        
        # 3. Spotify Search Fallback
        elif source == "spotify" and query:
            try:
                ydl_opts = {
                    'format': 'bestaudio/best',
                    'quiet': True,
                    'no_warnings': True,
                    'extract_flat': False,
                    'skip_download': True,
                }
                
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(f"ytsearch1:{query}", download=False)
                    if 'entries' in info and len(info['entries']) > 0:
                        video = info['entries'][0]
                        stream_url = video.get('url')
                        if stream_url:
                            req = requests.get(stream_url, headers=HEADERS, stream=True, timeout=10)
                            if req.status_code == 200:
                                return StreamingResponse(
                                    req.iter_content(chunk_size=1024 * 64),
                                    media_type=req.headers.get("content-type", "audio/webm")
                                )
            except Exception as e:
                print(f"Spotify stream error: {e}")
                raise HTTPException(status_code=404, detail="Stream not found")
        
        raise HTTPException(status_code=400, detail="Invalid parameters")
    
    except Exception as e:
        print(f"Play error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Health check endpoint
@app.get("/health")
def health_check():
    return {"status": "healthy", "timestamp": time.time()}

# Root endpoint
@app.get("/")
def root():
    return {
        "message": "Music Stream API",
        "endpoints": {
            "search": "/search?name=query",
            "play": "/play?source=jiosaavn&token=...",
            "health": "/health"
        },
        "usage": {
            "search": "GET /search?name=song_name",
            "play_jiosaavn": "GET /play?source=jiosaavn&token=encoded_token",
            "play_youtube": "GET /play?source=youtube&id=video_id",
            "play_spotify": "GET /play?source=spotify&query=song_name"
        }
    }