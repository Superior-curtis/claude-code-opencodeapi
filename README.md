# Claude Code ↔ OmniRoute AI Gateway

A lightweight proxy that translates Anthropic's `/v1/messages` format to OpenAI's `/v1/chat/completions` format, letting **Claude Code CLI** use **OmniRoute** (265+ AI providers, auto-fallback, RTK compression).

## Architecture

```
Claude Code ──ANTHROPIC──▶ zen-proxy.py ──OPENAI──▶ OmniRoute ──▶ 265+ Providers
     ◀────ANTHROPIC───────────────◀──────────────────────◀─────────────────
```

## Quick Start

### 1. Install OmniRoute

```bash
npm install -g omniroute
omniroute
```

Dashboard at `http://localhost:20128` — connect providers (Kiro AI, OpenCode Free, DuckDuckGo, etc.)

### 2. Run the proxy

```bash
python3 zen-proxy.py
```

The proxy starts on `127.0.0.1:3456`.

### 3. Point Claude Code at it

```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:3456 ANTHROPIC_MODEL=oc/deepseek-v4-flash-free claude
```

Or use OmniRoute's auto-routing model for best results:

```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:3456 ANTHROPIC_MODEL=auto/best-coding-fast claude
```

## Configuration

| Env Var | Default | Description |
|---|---|---|
| `ZEN_PROXY_PORT` | `3456` | Proxy listen port |
| `ZEN_PROXY_URL` | `http://localhost:20128/v1/chat/completions` | Upstream OmniRoute endpoint |

### Model naming (OmniRoute format)

OmniRoute uses `<provider>/<model>` or `auto/<category>` naming:

| Model ID | Description |
|---|---|
| `oc/deepseek-v4-flash-free` | OpenCode free DeepSeek V4 (no API key needed) |
| `oc/qwen3.6-plus-free` | OpenCode free Qwen 3.6 |
| `ddgw/gpt-4o-mini` | DuckDuckGo free GPT-4o Mini |
| `tllm/CLAUDE_4_6_OPUS` | The Old LLM free Claude 4.6 Opus |
| `auto/best-coding-fast` | Auto-route to the best fast coding model |
| `auto/best-free` | Auto-route to the best available free model |

### Auto-routing

OmniRoute smart-routes your requests across 265+ providers with automatic fallback. Use `auto/<category>` to let it pick the best model for the job.

## Provider Connections

Open the OmniRoute dashboard at `http://localhost:20128` and connect providers:
- **Kiro AI** — free Claude (~50 credits/month)
- **OpenCode Free** — no auth needed (oc/* models)
- **DuckDuckGo** — free GPT-4o Mini, Claude 3.5 Haiku
- **The Old LLM** — free Claude 4.6 Opus, GPT-5.4
- **Auggie** — free Claude Sonnet 4.6, GPT-5.5
- **MiMo Code** — free coding model
