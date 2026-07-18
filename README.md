# Claude Code ↔ OpenCode API Proxy

A lightweight proxy that translates Anthropic's `/v1/messages` format to OpenAI's `/v1/chat/completions` format, letting **Claude Code CLI** use **OpenCode Zen / Go** models.

## How it works

```
Claude Code ──ANTHROPIC──▶ zen-proxy.py ──OPENAI──▶ api.opencode.ai
     ◀────ANTHROPIC───────────────◀──────────────────────
```

## Setup

### 1. Get an API key

- Sign up at [opencode.ai](https://opencode.ai)
- Go to **Settings → API Keys** and create a key
- The same key works for both Zen and Go endpoints

### 2. Run the proxy

```bash
export OPENCODE_PROXY_API_KEY="sk-your-api-key-here"
python3 zen-proxy.py
```

The proxy starts on `127.0.0.1:3456` by default. Set `ZEN_PROXY_PORT` to change the port.

### 3. Point Claude Code at it

```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:3456 ANTHROPIC_MODEL=deepseek-v4-pro claude
```

## Configuration

| Env Var | Default | Description |
|---|---|---|
| `OPENCODE_PROXY_API_KEY` | `""` | Your OpenCode API key |
| `ZEN_PROXY_PORT` | `3456` | Proxy listen port |
| `ZEN_PROXY_URL` | `https://opencode.ai/zen/go/v1/chat/completions` | Upstream OpenAI endpoint |
| `ZEN_PROXY_MODEL` | `deepseek-v4-pro` | Model name to send upstream |

### Endpoints

- **Go** (default): `https://opencode.ai/zen/go/v1/chat/completions` — supports `deepseek-v4-pro` with tool results
- **Zen**: `https://opencode.ai/zen/v1/chat/completions` — supports `deepseek-v4-flash-free` (free, but tool calls may be unreliable)

### Models

| Model | Endpoint | Tool Calls | Notes |
|---|---|---|---|
| `deepseek-v4-pro` | Go | ✅ | Works with tool results |
| `deepseek-v4-flash-free` | Zen | ⚠️ | Free, sometimes outputs tool calls as text |
| `qwen3.7-plus` | Go | ✅ | |
| `minimax-m3` | Go | ✅ | |

## Why not LiteLLM?

Claude Code sends Anthropic-format requests (`/v1/messages`). LiteLLM only accepts OpenAI format (`/v1/chat/completions`), so it can't replace this proxy without an additional translation layer.
