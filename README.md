# inferenceport-proxy

**English** | [简体中文](./README.zh-CN.md)

OpenAI-compatible reverse proxy for [InferencePort AI](https://inferenceport.ai)'s free multimodal API.

No registration. No API key. Works out of the box with any OpenAI SDK — text chat, image generation, and video generation (sync + async) through one simple endpoint.

> **What makes this interesting:** the upstream backend exposes a public HTTP API that requires no auth token and currently applies no content moderation (equivalent to the app's "studio" mode). This project wraps it in a standard OpenAI-compatible surface so you can use it with your existing tools.

## Features

- **Text chat** — OpenAI-compatible `chat/completions`, including SSE streaming with reasoning chunks
- **Image generation** — 25+ models (Flux, GPT-Image, Seedream, Qwen-Image, Ideogram, Imagen, Wan, ...), returns `b64_json`
- **Video generation** — 19+ models (Wan, Kling, Vidu, Seedance, PixVerse, Flux, Hailuo, ...)
  - Async job API: `POST /v1/videos` → poll → download MP4
  - Sync API: `POST /v1/videos/generations`
- **Audio / 3D passthrough** (upstream status dependent)
- **106 models** served from a cached `/v1/models` listing (5 min TTL)
- Optional `PROXY_TOKEN` Bearer auth for public deployments
- Bounded retries on transient upstream failures (429/5xx/network)

## Quick start

```bash
git clone https://github.com/<your-name>/inferenceport-proxy.git
cd inferenceport-proxy
docker compose up -d --build
```

That's it. The proxy listens on `http://localhost:8080`.

```bash
# health
curl http://localhost:8080/v1/healthz

# models
curl http://localhost:8080/v1/models

# chat
curl http://localhost:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"lightning","messages":[{"role":"user","content":"hello"}]}'

# image
curl http://localhost:8080/v1/images/generations \
  -H 'Content-Type: application/json' \
  -d '{"model":"zimage","prompt":"a cute cat"}' \
  | python3 -c "import json,sys,base64; d=json.load(sys.stdin); open('img.jpg','wb').write(base64.b64decode(d['data'][0]['b64_json']))"

# async video
JOB=$(curl -s http://localhost:8080/v1/videos \
  -H 'Content-Type: application/json' \
  -d '{"model":"wan-fast","prompt":"a dolphin jumping over waves"}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
curl -s http://localhost:8080/v1/videos/$JOB                # poll: pending -> processing -> completed
curl -s -o video.mp4 http://localhost:8080/v1/videos/$JOB/content   # download
```

Use with any OpenAI SDK by pointing `base_url` at `http://localhost:8080/v1`.

## API

| Endpoint | Description |
|----------|-------------|
| `GET /v1/healthz` | Health check + upstream reachability |
| `GET /v1/models` | Upstream model list (cached, 5 min) |
| `POST /v1/chat/completions` | Chat, `stream=true` supported (SSE) |
| `POST /v1/images/generations` | Image generation → `data[0].b64_json` |
| `POST /v1/videos` | Create async video job → `{id, status}` |
| `GET /v1/videos/{id}` | Poll video job status |
| `GET /v1/videos/{id}/content` | Download video file (MP4) |
| `POST /v1/videos/generations` | Sync video generation (can take minutes) |
| `POST /v1/audio/generations` | Audio generation (upstream dependent) |
| `POST /v1/audio/speech` | TTS (upstream dependent) |
| `POST /v1/3d/generations` | 3D generation, requires `image_urls` (upstream dependent) |

## Configuration

All settings are environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `UPSTREAM_URL` | `https://sharktide-lightning.hf.space` | Upstream API base |
| `RETRIES` | `3` | Retries for transient upstream failures |
| `CONNECT_TIMEOUT` | `8` | Connect timeout (s) |
| `READ_TIMEOUT` | `600` | Read timeout (s) — sync video can take minutes |
| `CHAT_READ_TIMEOUT` | `120` | Chat read timeout (s) |
| `MODEL_CACHE_TTL` | `300` | Model list cache TTL (s) |
| `PROXY_TOKEN` | *(empty)* | Optional Bearer token; empty = open access |

## Popular models

- **Chat:** `lightning` (router), `grok-4.5`, `claude-haiku-4.5`, `gemini-3.7-flash`, `gpt-5.6-sol`, `deepseek-v4-pro`, `glm-5.2`, `kimi-k3`
- **Image:** `zimage`, `lightning-image-turbo`, `flux-2-pro`, `gptimage-1.5`, `seedream-5.0-lite`, `qwen-image-2.0-pro`, `ideogram-v4-turbo`, `imagen-4.0-fast`
- **Video:** `wan-fast`, `kling-2.1-pro`, `vidu-q3`, `seedance-2.5-720p`, `pixverse-v5`, `flux-3`, `hailuo-02`

Run `GET /v1/models` for the full 106-model listing.

## Architecture

```
OpenAI SDK / curl
      │  OpenAI-compatible /v1/* (JSON or SSE)
      ▼
inferenceport-proxy  (FastAPI, Docker)
      │  HTTP /gen/*  (plain, no auth)
      ▼
sharktide-lightning.hf.space  (InferencePort AI free tier)
```

The proxy is a thin translation layer: `/v1/*` paths map 1:1 onto the upstream `/gen/*` API, with model-list caching, timeout handling, and optional auth. No user state is stored.

## Deploying elsewhere

- **Docker** — `docker compose up -d --build` (recommended)
- **Bare Python** — `pip install fastapi uvicorn "httpx[socks]" && uvicorn app:app --host 0.0.0.0 --port 8080`
- **Port** — change the `ports:` mapping in `docker-compose.yml` (e.g. `8098:8080` to expose on 8098)

If your Docker host runs many compose projects and hits "all predefined address pools have been fully subnetted", add a private subnet to the compose file:

```yaml
networks:
  default:
    ipam:
      config:
        - subnet: 10.99.8.0/24
```

## Disclaimer

- This is a **free tier** proxy. The upstream enforces per-identity quotas (chat ~50/day, images ~10/day, videos ~3/day per anonymous identity on the web app); the direct HTTP layer is currently more lenient but may tighten at any time.
- Audio and 3D endpoints depend on upstream availability and may return errors.
- Use responsibly and respect the upstream service's terms.

## Credits

- [InferencePort AI](https://inferenceport.ai) — the free multimodal service this proxies
- [sharktide-lightning](https://huggingface.co/spaces/sharktide/lightning) — upstream HF Space API

## License

[MIT](LICENSE)
