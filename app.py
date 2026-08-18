"""
inferenceport-proxy — OpenAI-compatible reverse proxy for InferencePort AI
(free multimodal API from sharktide-lightning.hf.space)

No registration, no API key required upstream. Supports:
  - Text chat (OpenAI-compatible, SSE streaming)
  - Image generation (b64_json)
  - Video generation (sync + async job + MP4 download)
  - Audio / 3D (passthrough)

Design: direct connection to the upstream with bounded retries.
Optional Bearer token auth for your own deployment.
"""
import asyncio, json, os, time
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
import httpx

UPSTREAM = os.environ.get("UPSTREAM_URL", "https://sharktide-lightning.hf.space").rstrip("/")
RETRIES = int(os.environ.get("RETRIES", "3"))                       # retry count for transient errors
CONNECT_TIMEOUT = float(os.environ.get("CONNECT_TIMEOUT", "8"))
READ_TIMEOUT = float(os.environ.get("READ_TIMEOUT", "600"))         # sync video can take minutes
CHAT_READ_TIMEOUT = float(os.environ.get("CHAT_READ_TIMEOUT", "120"))
MODEL_CACHE_TTL = int(os.environ.get("MODEL_CACHE_TTL", "300"))     # seconds
TOKEN = os.environ.get("PROXY_TOKEN", "")                           # optional; empty = open access

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

app = FastAPI(title="inferenceport-proxy")

# ---------- model cache ----------
model_cache = {"ts": 0.0, "data": None}
models_lock = asyncio.Lock()


def auth_ok(request: Request) -> bool:
    if not TOKEN:
        return True
    h = request.headers.get("Authorization", "")
    return h == f"Bearer {TOKEN}" or h == TOKEN


async def fetch_models():
    """Fetch & cache the upstream model list (TTL). Returns None on failure (uses stale cache)."""
    async with models_lock:
        if model_cache["data"] and (time.time() - model_cache["ts"]) < MODEL_CACHE_TTL:
            return model_cache["data"]
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=CONNECT_TIMEOUT), trust_env=False) as c:
                r = await c.get(f"{UPSTREAM}/gen/models", headers={"User-Agent": UA})
                if r.status_code == 200:
                    model_cache["data"] = r.json()
                    model_cache["ts"] = time.time()
                    return model_cache["data"]
        except Exception as e:
            print(f"[models] fetch error: {e}", flush=True)
        return model_cache["data"]


async def upstream_call(path: str, method: str, body: bytes = None, params=None, read_timeout=READ_TIMEOUT):
    """Call upstream with bounded retries on transient failures (429/5xx/network)."""
    url = f"{UPSTREAM}{path}"
    headers = {"User-Agent": UA, "Content-Type": "application/json", "Accept": "*/*"}
    last = None
    for attempt in range(RETRIES):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(read_timeout, connect=CONNECT_TIMEOUT), trust_env=False) as c:
                r = await c.request(method, url, content=body, params=params, headers=headers)
                if r.status_code in (200, 201, 202):
                    return r.status_code, r.headers, r.content
                if r.status_code not in (429, 500, 502, 503, 504):
                    return r.status_code, r.headers, r.content
                last = r
        except Exception as e:
            last = None
            print(f"[upstream] {path} attempt {attempt}: {type(e).__name__} {str(e)[:100]}", flush=True)
        if attempt < RETRIES - 1:
            await asyncio.sleep(0.8 * (attempt + 1))
    if last is not None:
        return last.status_code, last.headers, last.content
    return 502, None, json.dumps({"error": {"message": "upstream unreachable", "type": "upstream_error"}}).encode()


def filter_headers(h) -> dict:
    """Passthrough only safe response headers."""
    out = {}
    if not h:
        return out
    for k in ("content-type", "content-length", "cache-control", "expires", "etag", "last-modified"):
        if k in h:
            out[k] = h[k]
    return out


# ---------- routes ----------

@app.get("/v1/healthz")
async def healthz():
    ok = False
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5), trust_env=False) as c:
            r = await c.get(f"{UPSTREAM}/gen/models", headers={"User-Agent": UA})
            ok = r.status_code == 200
    except Exception:
        pass
    return {"status": "ok" if ok else "degraded", "upstream": UPSTREAM, "ts": time.time()}


@app.get("/v1/models")
async def models(request: Request):
    if not auth_ok(request):
        return JSONResponse({"error": {"message": "unauthorized"}}, status_code=401)
    data = await fetch_models()
    if not data:
        return JSONResponse({"error": {"message": "upstream model fetch failed"}}, status_code=502)
    return data


@app.post("/v1/chat/completions")
async def chat(request: Request):
    if not auth_ok(request):
        return JSONResponse({"error": {"message": "unauthorized"}}, status_code=401)
    body = await request.body()
    try:
        payload = json.loads(body)
    except Exception:
        return JSONResponse({"error": {"message": "invalid json"}}, status_code=400)
    stream = bool(payload.get("stream", False))
    url = f"{UPSTREAM}/gen/chat/completions"
    headers = {"User-Agent": UA, "Content-Type": "application/json",
               "Accept": "text/event-stream" if stream else "application/json"}

    last = None
    for attempt in range(RETRIES):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(CHAT_READ_TIMEOUT, connect=CONNECT_TIMEOUT), trust_env=False) as c:
                r = await c.post(url, content=body, headers=headers)
                if r.status_code not in (429, 500, 502, 503, 504):
                    resp_headers = filter_headers(r.headers)
                    resp_headers["X-Route"] = "direct"
                    if stream:
                        async def gen():
                            async with httpx.AsyncClient(timeout=httpx.Timeout(CHAT_READ_TIMEOUT, connect=CONNECT_TIMEOUT), trust_env=False) as c2:
                                async with c2.stream("POST", url, content=body, headers=headers) as sr:
                                    async for chunk in sr.aiter_bytes():
                                        yield chunk
                        return StreamingResponse(gen(), media_type="text/event-stream", headers=resp_headers)
                    return JSONResponse(json.loads(r.content), headers=resp_headers)
                last = r
        except Exception as e:
            print(f"[chat] attempt {attempt}: {type(e).__name__} {str(e)[:100]}", flush=True)
        if attempt < RETRIES - 1:
            await asyncio.sleep(0.8 * (attempt + 1))
    if last is not None:
        return JSONResponse(json.loads(last.content), status_code=last.status_code)
    return JSONResponse({"error": {"message": "upstream unreachable"}}, status_code=502)


async def generic_json_endpoint(request: Request, path: str, read_timeout=READ_TIMEOUT):
    """JSON passthrough for images / audio / 3d / sync video."""
    if not auth_ok(request):
        return JSONResponse({"error": {"message": "unauthorized"}}, status_code=401)
    body = await request.body()
    st, h, content = await upstream_call(path, "POST", body=body, read_timeout=read_timeout)
    rh = filter_headers(h)
    rh["X-Route"] = "direct"
    try:
        data = json.loads(content)
    except Exception:
        data = {"error": {"message": content.decode(errors="replace")[:500]}}
    return JSONResponse(data, status_code=st, headers=rh)


@app.post("/v1/images/generations")
async def images(request: Request):
    return await generic_json_endpoint(request, "/gen/images/generations", read_timeout=180)


@app.post("/v1/videos/generations")
async def videos_sync(request: Request):
    return await generic_json_endpoint(request, "/gen/videos/generations", read_timeout=READ_TIMEOUT)


@app.post("/v1/videos")
async def videos_async_create(request: Request):
    return await generic_json_endpoint(request, "/gen/videos", read_timeout=60)


@app.get("/v1/videos/{video_id}")
async def videos_async_status(request: Request, video_id: str):
    if not auth_ok(request):
        return JSONResponse({"error": {"message": "unauthorized"}}, status_code=401)
    st, h, content = await upstream_call(f"/gen/videos/{video_id}", "GET", read_timeout=30)
    rh = filter_headers(h)
    rh["X-Route"] = "direct"
    try:
        data = json.loads(content)
    except Exception:
        data = {"error": {"message": content.decode(errors="replace")[:500]}}
    return JSONResponse(data, status_code=st, headers=rh)


@app.get("/v1/videos/{video_id}/content")
async def videos_async_content(request: Request, video_id: str):
    """Stream the video file (follows upstream 303 -> /asset-cdn/...)."""
    if not auth_ok(request):
        return JSONResponse({"error": {"message": "unauthorized"}}, status_code=401)
    url = f"{UPSTREAM}/gen/videos/{video_id}/content"
    headers = {"User-Agent": UA}

    for attempt in range(RETRIES):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=CONNECT_TIMEOUT), trust_env=False) as c:
                r = await c.get(url, headers=headers)
                if r.status_code == 303:
                    loc = r.headers.get("location", "")
                    if loc.startswith("/"):
                        loc = f"{UPSTREAM}{loc}"
                    return await stream_video_file(loc)
                if r.status_code == 200:
                    mt = r.headers.get("content-type", "video/mp4")
                    return StreamingResponse(iter([r.content]), media_type=mt,
                                             headers={"X-Route": "direct", "Content-Disposition": f'attachment; filename="{video_id}.mp4"'})
                if r.status_code == 202:
                    return JSONResponse({"status": "processing", "message": "video not ready yet"}, status_code=202)
                if r.status_code not in (429, 500, 502, 503, 504):
                    return JSONResponse({"error": {"message": r.text[:300]}}, status_code=r.status_code)
        except Exception as e:
            print(f"[video-content] attempt {attempt}: {type(e).__name__} {str(e)[:100]}", flush=True)
        if attempt < RETRIES - 1:
            await asyncio.sleep(0.8 * (attempt + 1))
    return JSONResponse({"error": {"message": "upstream unreachable"}}, status_code=502)


async def stream_video_file(loc: str):
    """Fetch the actual file from the asset URL and stream it back."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=CONNECT_TIMEOUT), trust_env=False) as c:
            r = await c.get(loc, headers={"User-Agent": UA})
            if r.status_code == 200:
                mt = r.headers.get("content-type", "video/mp4")
                return StreamingResponse(iter([r.content]), media_type=mt,
                                         headers={"X-Route": "direct", "Content-Disposition": 'attachment; filename="video.mp4"'})
            return JSONResponse({"error": {"message": f"asset fetch failed: {r.status_code}"}}, status_code=r.status_code)
    except Exception as e:
        print(f"[video-asset] {type(e).__name__} {str(e)[:100]}", flush=True)
        return JSONResponse({"error": {"message": "asset fetch failed"}}, status_code=502)


@app.post("/v1/audio/generations")
async def audio(request: Request):
    return await generic_json_endpoint(request, "/gen/audio/generations", read_timeout=120)


@app.post("/v1/audio/speech")
async def audio_speech(request: Request):
    return await generic_json_endpoint(request, "/gen/audio/speech", read_timeout=120)


@app.post("/v1/3d/generations")
async def gen_3d(request: Request):
    return await generic_json_endpoint(request, "/gen/3d/generations", read_timeout=300)


@app.get("/")
async def root():
    return {"service": "inferenceport-proxy", "docs": "/docs", "models": "/v1/models", "healthz": "/v1/healthz"}
