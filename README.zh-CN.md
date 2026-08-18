# inferenceport-proxy

[English](./README.md) | **简体中文**

OpenAI 兼容的 [InferencePort AI](https://inferenceport.ai) 免费多模态 API 反向代理。

免注册、免 API Key，开箱即用，兼容任何 OpenAI SDK —— 文本对话、图片生成、视频生成（同步 + 异步）全部走一个标准接口。

> **亮点：** 上游后端暴露了无需认证的公开 HTTP API，且当前无内容审查（等同于 App 的 Studio 模式）。本项目将其包装成标准的 OpenAI 兼容接口，可直接接入你现有的工具链。

## 功能特性

- **文本对话** — OpenAI 兼容 `chat/completions`，支持 SSE 流式输出（含推理链 reasoning）
- **图片生成** — 25+ 模型（Flux、GPT-Image、Seedream、Qwen-Image、Ideogram、Imagen、Wan 等），返回 `b64_json`
- **视频生成** — 19+ 模型（Wan、Kling、Vidu、Seedance、PixVerse、Flux、Hailuo 等）
  - 异步任务: `POST /v1/videos` → 轮询 → 下载 MP4
  - 同步接口: `POST /v1/videos/generations`
- **音频 / 3D 透传**（依赖上游可用性）
- **106 个模型**，`/v1/models` 缓存列表（5 分钟 TTL）
- 可选 `PROXY_TOKEN` Bearer 认证，适合公网部署
- 对上游瞬时故障（429/5xx/网络）做有界重试

## 快速开始

```bash
git clone https://github.com/b3b41020/free-multimodal-proxy.git
cd free-multimodal-proxy
docker compose up -d --build
```

完成。代理监听 `http://localhost:8080`。

```bash
# 健康检查
curl http://localhost:8080/v1/healthz

# 模型列表
curl http://localhost:8080/v1/models

# 对话
curl http://localhost:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"lightning","messages":[{"role":"user","content":"你好"}]}'

# 生图
curl http://localhost:8080/v1/images/generations \
  -H 'Content-Type: application/json' \
  -d '{"model":"zimage","prompt":"一只可爱的猫"}' \
  | python3 -c "import json,sys,base64; d=json.load(sys.stdin); open('img.jpg','wb').write(base64.b64decode(d['data'][0]['b64_json']))"

# 异步生视频
JOB=$(curl -s http://localhost:8080/v1/videos \
  -H 'Content-Type: application/json' \
  -d '{"model":"wan-fast","prompt":"海豚跃出水面"}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
curl -s http://localhost:8080/v1/videos/$JOB                # 轮询: pending -> processing -> completed
curl -s -o video.mp4 http://localhost:8080/v1/videos/$JOB/content   # 下载
```

任意 OpenAI SDK 设置 `base_url` 为 `http://localhost:8080/v1` 即可使用。

## API 接口

| 端点 | 说明 |
|------|------|
| `GET /v1/healthz` | 健康检查 + 上游可达性 |
| `GET /v1/models` | 上游模型列表（5 分钟缓存） |
| `POST /v1/chat/completions` | 对话，支持 `stream=true`（SSE） |
| `POST /v1/images/generations` | 生图 → `data[0].b64_json` |
| `POST /v1/videos` | 创建异步视频任务 → `{id, status}` |
| `GET /v1/videos/{id}` | 轮询视频任务状态 |
| `GET /v1/videos/{id}/content` | 下载视频文件（MP4） |
| `POST /v1/videos/generations` | 同步视频生成（可能耗时数分钟） |
| `POST /v1/audio/generations` | 音频生成（依赖上游） |
| `POST /v1/audio/speech` | TTS 语音合成（依赖上游） |
| `POST /v1/3d/generations` | 3D 生成，需 `image_urls`（依赖上游） |

## 配置

全部通过环境变量配置：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `UPSTREAM_URL` | `https://sharktide-lightning.hf.space` | 上游 API 地址 |
| `RETRIES` | `3` | 上游瞬时故障重试次数 |
| `CONNECT_TIMEOUT` | `8` | 连接超时（秒） |
| `READ_TIMEOUT` | `600` | 读取超时（秒）——同步视频可能耗时数分钟 |
| `CHAT_READ_TIMEOUT` | `120` | 对话读取超时（秒） |
| `MODEL_CACHE_TTL` | `300` | 模型列表缓存时间（秒） |
| `PROXY_TOKEN` | *（空）* | 可选 Bearer Token；为空则开放访问 |

## 热门模型

- **对话:** `lightning`（路由）、`grok-4.5`、`claude-haiku-4.5`、`gemini-3.7-flash`、`gpt-5.6-sol`、`deepseek-v4-pro`、`glm-5.2`、`kimi-k3`
- **生图:** `zimage`、`lightning-image-turbo`、`flux-2-pro`、`gptimage-1.5`、`seedream-5.0-lite`、`qwen-image-2.0-pro`、`ideogram-v4-turbo`、`imagen-4.0-fast`
- **生视频:** `wan-fast`、`kling-2.1-pro`、`vidu-q3`、`seedance-2.5-720p`、`pixverse-v5`、`flux-3`、`hailuo-02`

运行 `GET /v1/models` 查看全部 106 个模型。

## 架构

```
OpenAI SDK / curl
      │  OpenAI 兼容 /v1/* (JSON 或 SSE)
      ▼
inferenceport-proxy  (FastAPI, Docker)
      │  HTTP /gen/*  (无需认证)
      ▼
sharktide-lightning.hf.space  (InferencePort AI 免费额度)
```

本项目是轻量转换层：`/v1/*` 路径与上游 `/gen/*` API 一一对应，附带模型缓存、超时处理与可选认证。不存储任何用户状态。

## 其他部署方式

- **Docker** — `docker compose up -d --build`（推荐）
- **纯 Python** — `pip install fastapi uvicorn "httpx[socks]" && uvicorn app:app --host 0.0.0.0 --port 8080`
- **改端口** — 修改 `docker-compose.yml` 中的 `ports:` 映射即可（如 `8098:8080` 对外暴露 8098）

如果 Docker 主机上 compose 项目过多报 "all predefined address pools have been fully subnetted"，给 compose 加私有子网：

```yaml
networks:
  default:
    ipam:
      config:
        - subnet: 10.99.8.0/24
```

## 免责声明

- 本项目使用**免费额度**。上游按匿名身份限额（网页端约 每天 50 次对话 / 10 张图 / 3 个视频）；当前 HTTP 直连层较为宽松，但可能随时收紧。
- 音频 / 3D 端点依赖上游可用性，可能返回错误。
- 请合理使用，遵守上游服务条款。
- 免责声明                                                                                                                                                                                                                                    
                                                                                                                                                                                                                                             
 本项目是一个独立开源实现，与 InferencePort AI 及其母公司、子公司或合作伙伴没有任何关联、背书或赞助关系。"InferencePort" 为其各自所有者的商标，此处仅用于标识目的。                                                                          
                                                                                                                                                                                                                                             
 使用本软件即表示您知悉并同意：                                                                                                                                                                                                              
                                                                                                                                                                                                                                             
 1. 免费额度限制。 上游服务对免费层设有配额（例如按匿名身份的每日对话 / 生图 / 生视频次数限制）。这些配额完全由上游决定，可能随时收紧、取消或变更，恕不另行通知。本项目无法也不承诺保证可用性、可靠性或额度水平。                            
                                                                                                                                                                                                                                             
 2. 无内容审查。 本软件代理的上游 API 可能不进行内容审核。您对使用本软件生成、传输或存储的内容负全部责任，并须确保其符合所有适用法律、法规及上游服务商的服务条款。                                                                           
                                                                                                                                                                                                                                             
 3. 风险自担。 本软件按"现状"和"按可用状态"提供，不附带任何明示或暗示的担保，包括但不限于适销性、特定用途适用性或不侵权的担保。在任何情况下，作者或贡献者均不对因使用本软件而产生或与之相关的任何索赔、损害或其他责任负责。                  
                                                                                                                                                                                                                                             
 4. 合规责任。 您有责任遵守上游服务商的服务条款及所有适用的当地法律。请勿将本软件用于任何非法、滥用或未经授权的用途。上游可能随时封禁您的访问或限制速率，本项目对此造成的任何损失概不负责。                                                  
                                                                                                                                                                                                                                             
 5. 依赖上游。 本项目完全依赖我们无法控制的第三方公共服务。上游可能随时停止服务、变更 API 或无法访问，这将导致本软件无法正常工作。我们不承诺本项目的长期可用性。                                                                             
                                                                                                                                                                                                                                             
 6. 赔偿条款。 使用本软件即表示您同意，因使用本软件或违反上述条款而产生的任何索赔、责任、损害赔偿、损失或费用，由您向作者和贡献者进行赔偿并使其免受损害。                                                                                    
                                                                                                                                                                                                                                             
 生产环境或商业用途，请使用 InferencePort AI 官方服务并遵守其授权条款。

## 致谢

- https://linux.do - LINUX DO
- [InferencePort AI](https://inferenceport.ai) — 被代理的免费多模态服务
- [sharktide-lightning](https://huggingface.co/spaces/sharktide/lightning) — 上游 HF Space API

## 开源协议

[MIT](LICENSE)
