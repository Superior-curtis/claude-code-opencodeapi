#!/usr/bin/env python3
"""Lightweight Anthropic → OpenAI proxy for OmniRoute AI Gateway.
Chains Claude Code → OmniRoute (265+ providers, auto-fallback, RTK compression)."""

import json, os, sys, time, uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ZEN_URL = os.environ.get("ZEN_PROXY_URL", "http://localhost:20128/v1/chat/completions")
API_KEY = os.environ.get("OPENCODE_PROXY_API_KEY") or os.environ.get("ROUTATIC_PROXY_API_KEY", "")
LISTEN_PORT = int(os.environ.get("ZEN_PROXY_PORT", "3456"))


def anthropic_to_openai(body: dict) -> dict:
    """Convert Anthropic /v1/messages request to OpenAI /v1/chat/completions."""
    messages = []
    system_prompt = None
    
    for msg in body.get("messages", []):
        role = msg.get("role", "user")
        content = msg.get("content", "")
        
        if isinstance(content, str):
            if role == "system":
                system_prompt = content
            else:
                openai_role = "assistant" if role == "assistant" else "user"
                messages.append({"role": openai_role, "content": content})
            continue

        # Content is a list of blocks
        text_parts = []
        tool_calls = []
        has_tool_results = False
        
        for block in content:
            if not isinstance(block, dict):
                if isinstance(block, str):
                    text_parts.append(block)
                continue
            
            btype = block.get("type", "")
            if btype == "text":
                text_parts.append(block.get("text", ""))
            elif btype == "thinking":
                pass
            elif btype == "tool_use":
                tool_calls.append({
                    "id": block.get("id", f"call_{uuid.uuid4().hex[:12]}"),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                    }
                })
            elif btype == "tool_result":
                has_tool_results = True
                result_content = block.get("content", "")
                if isinstance(result_content, list):
                    result_parts = []
                    for rc in result_content:
                        if isinstance(rc, dict) and rc.get("type") == "text":
                            result_parts.append(rc.get("text", ""))
                        elif isinstance(rc, str):
                            result_parts.append(rc)
                    result_content = "\n".join(result_parts)
                messages.append({
                    "role": "tool",
                    "tool_call_id": block.get("tool_use_id", ""),
                    "content": str(result_content),
                })
        
        if role == "system":
            if text_parts:
                system_prompt = "\n".join(text_parts)
            continue

        if role == "user" and system_prompt is None and not messages and text_parts and not has_tool_results:
            system_prompt = "\n".join(text_parts)
            continue

        if text_parts:
            openai_role = "assistant" if role == "assistant" else "user"
            messages.append({"role": openai_role, "content": "\n".join(text_parts)})

        if tool_calls:
            target = messages[-1] if messages and messages[-1]["role"] == "assistant" else None
            if target:
                target["tool_calls"] = tool_calls
            else:
                messages.append({"role": "assistant", "content": "", "tool_calls": tool_calls})

    tools = None
    if body.get("tools"):
        tools = []
        for tool in body["tools"]:
            tools.append({
                "type": "function",
                "function": {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {}),
                },
            })

    openai_body = {
        "model": body.get("model", "auto/best-coding-fast"),
        "messages": messages,
        "max_tokens": body.get("max_tokens", 4096),
        "temperature": body.get("temperature", 0.7),
        "stream": body.get("stream", False),
    }
    if system_prompt:
        openai_body["messages"].insert(0, {"role": "system", "content": system_prompt})
    if tools:
        openai_body["tools"] = tools
        openai_body["tool_choice"] = "auto"
    openai_body["stream"] = False  # force non-streaming for correct tool_call IDs

    return openai_body


def openai_to_anthropic(data: dict, body: dict) -> dict:
    """Convert OpenAI response to Anthropic format."""
    choice = data.get("choices", [{}])[0]
    msg = choice.get("message", {})
    content = msg.get("content", "") or msg.get("reasoning_content", "")

    anthropic_content = []
    if content:
        anthropic_content.append({"type": "text", "text": content})

    tool_calls = msg.get("tool_calls", [])

    if not tool_calls and content:
        import re
        tc_pattern = re.compile(r'\{\s*"tool"\s*:\s*"(\w+)"\s*,\s*"(?:input|arguments)"\s*:\s*(\{.*?\})\s*\}', re.DOTALL)
        for m in tc_pattern.finditer(content):
            try:
                parsed = json.loads(m.group(0))
                tool_calls.append({
                    "id": f"toolu_{uuid.uuid4().hex[:8]}",
                    "function": {
                        "name": parsed.get("tool", "unknown"),
                        "arguments": json.dumps(parsed.get("input") or parsed.get("arguments") or {}),
                    },
                })
            except json.JSONDecodeError:
                pass

    for tc in tool_calls:
        func = tc.get("function", {})
        name = func.get("name", "")
        try:
            arguments = json.loads(func.get("arguments", "{}"))
        except json.JSONDecodeError:
            arguments = {}
        anthropic_content.append({
            "type": "tool_use",
            "id": tc.get("id", f"toolu_{uuid.uuid4().hex[:8]}"),
            "name": name,
            "input": arguments,
        })

    usage = data.get("usage", {})
    return {
        "id": f"msg_{uuid.uuid4().hex[:16]}",
        "type": "message",
        "role": "assistant",
        "content": anthropic_content,
        "model": body.get("model", data.get("model", "")),
        "stop_reason": "tool_use" if tool_calls else ("end_turn" if choice.get("finish_reason") == "stop" else "max_tokens"),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


def sse_event(data: str) -> str:
    return f"data: {data}\n\n"


# Track streaming state across chunks
_stream_state = {}

def openai_stream_to_anthropic_sse(chunk: dict, body: dict, msg_id: str) -> str:
    """Convert OpenAI streaming chunk to Anthropic SSE format."""
    choice = (chunk.get("choices") or [{}])[0]
    delta = choice.get("delta", {})
    finish = choice.get("finish_reason")
    tc_delta = delta.get("tool_calls")

    events = []

    text = delta.get("content") or ""
    reasoning = delta.get("reasoning_content") or ""
    combined = reasoning + text

    if combined:
        events.append(json.dumps({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": combined}}))
        _stream_state.setdefault(msg_id, {"tool_block": 1, "tool_args": {}, "tool_id": {}, "text_buf": ""})["text_buf"] += combined

    if tc_delta:
        state = _stream_state.setdefault(msg_id, {"tool_block": 1, "tool_args": {}, "tool_id": {}})
        if not state.get("closed_text_block"):
            events.append(json.dumps({"type": "content_block_stop", "index": 0}))
            state["closed_text_block"] = True
        for tc in tc_delta:
            idx = tc.get("index", 0)
            func = tc.get("function", {})
            if idx not in state["tool_args"]:
                state["tool_args"][idx] = ""
                state["tool_id"][idx] = f"toolu_{uuid.uuid4().hex[:8]}"
                name = func.get("name", "")
                name = name if name else state.get("tool_names", {}).get(idx, "unknown")
            if func.get("name"):
                state.setdefault("tool_names", {})[idx] = func["name"]
            if func.get("arguments"):
                args = func["arguments"]
                was_empty = not state["tool_args"][idx]
                state["tool_args"][idx] += args
                if was_empty:
                    tool_name = state.get("tool_names", {}).get(idx, "unknown")
                    events.append(json.dumps({
                        "type": "content_block_start", "index": state["tool_block"],
                        "content_block": {"type": "tool_use", "id": state["tool_id"][idx], "name": tool_name, "input": {}}
                    }))
                    state["tool_block"] += 1
                events.append(json.dumps({
                    "type": "content_block_delta", "index": state["tool_block"] - 1,
                    "delta": {"type": "input_json_delta", "partial_json": args}
                }))

    if finish:
        state = _stream_state.pop(msg_id, {})
        text_buf = state.get("text_buf", "")
        if not state.get("tool_args") and finish in ("stop", "end_turn") and text_buf:
            import re
            tc_pattern = re.compile(r'\{\s*"tool"\s*:\s*"(\w+)"\s*,\s*"(?:input|arguments)"\s*:\s*(\{.*?\})\s*\}', re.DOTALL)
            parsed_tools = []
            for m in tc_pattern.finditer(text_buf):
                try:
                    parsed = json.loads(m.group(0))
                    idx = len(parsed_tools)
                    tool_name = parsed.get("tool", "unknown")
                    tool_args = json.dumps(parsed.get("input") or parsed.get("arguments") or {})
                    parsed_tools.append((idx, tool_name, tool_args))
                except json.JSONDecodeError:
                    pass
            if parsed_tools:
                if not state.get("closed_text_block"):
                    events.append(json.dumps({"type": "content_block_stop", "index": 0}))
                    state["closed_text_block"] = True
                for idx, name, args in reversed(parsed_tools):
                    block_idx = state.get("tool_block", 1) + idx
                    tid = f"toolu_{uuid.uuid4().hex[:8]}"
                    events.insert(0, json.dumps({"type": "content_block_stop", "index": block_idx}))
                    events.insert(0, json.dumps({"type": "content_block_delta", "index": block_idx, "delta": {"type": "input_json_delta", "partial_json": args}}))
                    events.insert(0, json.dumps({"type": "content_block_start", "index": block_idx, "content_block": {"type": "tool_use", "id": tid, "name": name, "input": {}}}))
                if not state.get("closed_text_block"):
                    events.append(json.dumps({"type": "content_block_stop", "index": 0}))
                    state["closed_text_block"] = True
                finish = "tool_calls"

        if not state.get("closed_text_block") and state:
            events.append(json.dumps({"type": "content_block_stop", "index": 0}))
        sb = max(state.get("tool_block", 1) - 1, 0)
        if state.get("tool_args"):
            for idx in sorted(state["tool_args"].keys(), reverse=True):
                sb = idx + 1
                events.append(json.dumps({"type": "content_block_stop", "index": sb}))
        usage = chunk.get("usage", {})
        stop_reason = "tool_use" if finish == "tool_calls" else ("end_turn" if finish == "stop" else "max_tokens")
        events.append(json.dumps({
            "type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": usage.get("completion_tokens", 0)},
        }))
        events.append(json.dumps({"type": "message_stop"}))

    if events:
        return "".join(sse_event(e) for e in events)
    return ""


class ProxyHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress default logging

    def _send_error(self, code, message):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        error = json.dumps({"error": {"message": message, "type": "api_error"}, "type": "error"})
        self.wfile.write(error.encode())

    def _forward(self, body: dict):
        openai_body = anthropic_to_openai(body)
        stream = openai_body.get("stream", False)

        req = Request(
            ZEN_URL,
            data=json.dumps(openai_body).encode(),
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "application/json",
                "Origin": "https://opencode.ai",
            },
        )
        try:
            resp = urlopen(req, timeout=300)
        except HTTPError as e:
            err_body = e.read().decode()
            print(f"[zen-proxy] upstream error {e.code}: {err_body}", file=sys.stderr)
            self._send_error(502, f"upstream error: {err_body[:200]}")
            return

        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            msg_id = f"msg_{uuid.uuid4().hex[:16]}"
            # Send start event
            start = json.dumps({
                "type": "message_start",
                "message": {"id": msg_id, "type": "message", "role": "assistant", "content": [], "model": body.get("model", ""), "usage": {"input_tokens": 0, "output_tokens": 0}},
            })
            self.wfile.write(sse_event(start).encode())
            # Send content block start
            block_start = json.dumps({
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            })
            self.wfile.write(sse_event(block_start).encode())
            self.wfile.flush()

            buf = ""
            for line_bytes in resp:
                line = line_bytes.decode()
                buf += line
                while "\n" in buf:
                    full_line, buf = buf.split("\n", 1)
                    full_line = full_line.strip()
                    if full_line.startswith("data: "):
                        data_str = full_line[6:]
                        if data_str == "[DONE]":
                            if msg_id in _stream_state:
                                done_state = _stream_state.pop(msg_id)
                                if not done_state.get("closed_text_block"):
                                    self.wfile.write(sse_event(json.dumps({"type": "content_block_stop", "index": 0})).encode())
                                self.wfile.write(sse_event(json.dumps({"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 0}})).encode())
                                self.wfile.write(sse_event(json.dumps({"type": "message_stop"})).encode())
                                self.wfile.flush()
                            continue
                        try:
                            chunk = json.loads(data_str)
                            sse = openai_stream_to_anthropic_sse(chunk, body, msg_id)
                            if sse:
                                self.wfile.write(sse.encode())
                                self.wfile.flush()
                        except json.JSONDecodeError:
                            pass
        else:
            raw = resp.read().decode()
            try:
                oai_resp = json.loads(raw)
                anthropic_resp = openai_to_anthropic(oai_resp, body)
            except json.JSONDecodeError:
                print(f"[zen-proxy] failed to parse response: {raw[:500]}", file=sys.stderr)
                self._send_error(502, "failed to parse upstream response")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(anthropic_resp, ensure_ascii=False).encode())

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            self._send_error(400, "invalid json")
            return
        req_model = body.get("model", "unknown")
        stream = body.get("stream", False)
        print(f"[zen-proxy] POST {self.path} model={req_model} stream={stream} msgs={len(body.get('messages', []))}", file=sys.stderr, flush=True)
        if not self.path.startswith("/v1/messages"):
            print(f"[zen-proxy] REJECT path {self.path}", file=sys.stderr, flush=True)
            self._send_error(404, "not found")
            return
        self._forward(body)

    def do_GET(self):
        print(f"[zen-proxy] GET {self.path}", file=sys.stderr, flush=True)
        if self.path in ("/v1/models", "/v1/models/"):
            # Proxy to OmniRoute for live model list
            try:
                req = Request(
                    ZEN_URL.replace("/chat/completions", "/models"),
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                resp = urlopen(req, timeout=10)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(resp.read())
            except Exception as e:
                print(f"[zen-proxy] models fetch error: {e}", file=sys.stderr)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                models = {"data": [{"id": "auto/best-coding-fast", "object": "model", "created": 1}]}
                self.wfile.write(json.dumps(models).encode())
        else:
            self._send_error(404, "not found")


def main():
    print(f"[zen-proxy] starting on 127.0.0.1:{LISTEN_PORT} → {ZEN_URL}", file=sys.stderr)
    server = HTTPServer(("127.0.0.1", LISTEN_PORT), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[zen-proxy] shutting down", file=sys.stderr)
        server.shutdown()


if __name__ == "__main__":
    main()
