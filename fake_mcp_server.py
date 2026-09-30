#!/usr/bin/env python3
"""假 MCP 服务端：完整记录 CLI 发出的每个请求，用于确认请求字段名。

设计要点有三个。第一，服务端对所有 JSON-RPC 方法都返回宽松的成功响应，
因为目的只是让 CLI 走到"发出工具调用"这一步，而不是复刻真实业务响应。
第二，tools/list 返回覆盖全部 28 个工具的宽松 inputSchema，避免客户端
按 schema 校验时剔除我们想观察的参数字段。第三，所有请求的请求头与
原始请求体都追加写入 capture/requests.jsonl，不经过任何加工。
"""

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOOL_NAMES = [
    "agent_search",
    "decor_price_query",
    "decor_store_search",
    "entity_detail",
    "entity_material",
    "entity_resolution",
    "get_house_dynamic",
    "get_my_house_list",
    "house_rank_search",
    "house_search",
    "house_sold_search",
    "land_search",
    "market_trend_search",
    "newhouse_search",
    "plate_search",
    "policy_search",
    "rent_agent_search",
    "rent_house_appointment",
    "rent_house_search",
    "rent_market_search",
    "report_user_intent",
    "resblock_rank_search",
    "resblock_search",
    "resblock_unready_list",
    "school_district_search",
    "school_search",
    "store_search",
    "maps_geo",
    "maps_direction_driving",
    "maps_direction_transit_integrated",
]

CAPTURE_DIR = os.path.join(os.getcwd(), "capture")
_write_lock = threading.Lock()
_sequence = 0


def record(kind, payload):
    """把一条记录追加到 requests.jsonl，序号用于还原请求先后顺序。"""
    global _sequence
    with _write_lock:
        _sequence += 1
        entry = {"seq": _sequence, "ts": time.time(), "kind": kind}
        entry.update(payload)
        os.makedirs(CAPTURE_DIR, exist_ok=True)
        path = os.path.join(CAPTURE_DIR, "requests.jsonl")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def permissive_tool_schema(name):
    """给每个工具一个"什么都接受"的 schema，避免参数被客户端预校验掉。"""
    return {
        "name": name,
        "description": "probe stub for " + name,
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        },
    }


def build_result(method, params):
    """按 JSON-RPC 方法名构造一个尽可能宽松的成功响应。

    响应体里同时填入了 result/data/content/accepted/session_id/isError 等
    多个字段，这是因为 CLI 解析响应的确切形状未知，多给几个字段能让它
    更容易继续往下走；多给的字段对抓包目的没有副作用。
    """
    tool_name = ""
    tool_arguments = {}
    if isinstance(params, dict):
        tool_name = str(params.get("name") or "")
        if isinstance(params.get("arguments"), dict):
            tool_arguments = params["arguments"]

    echo = {
        "probe": True,
        "tool": tool_name,
        "method": method,
        "arguments": tool_arguments,
    }
    text = json.dumps(echo, ensure_ascii=False)

    inner = {
        "accepted": True,
        "session_id": "probe-session-0001",
        "sessionId": "probe-session-0001",
        "isError": False,
        "data": echo,
        "content": [{"type": "text", "text": text}],
        "result": echo,
        "items": [],
        "list": [],
        "message": "probe ok",
    }

    if method == "initialize":
        inner.update(
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "beike-mcp-probe", "version": "0.0.1"},
            }
        )
    elif method == "tools/list":
        inner.update({"tools": [permissive_tool_schema(n) for n in TOOL_NAMES]})
    elif method == "tools/call":
        inner.update(
            {
                "content": [{"type": "text", "text": text}],
                "structuredContent": echo,
            }
        )
    return inner


class ProbeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "beike-probe/0.1"

    def log_message(self, fmt, *args):
        # 默认日志写到 stderr，会与探针输出混杂，这里改为写入独立文件。
        os.makedirs(CAPTURE_DIR, exist_ok=True)
        with open(os.path.join(CAPTURE_DIR, "server.log"), "a", encoding="utf-8") as handle:
            handle.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

    def _send(self, status, body_bytes, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        if body_bytes:
            self.wfile.write(body_bytes)

    def do_GET(self):
        record(
            "http",
            {
                "http_method": "GET",
                "path": self.path,
                "headers": dict(self.headers.items()),
                "body": "",
            },
        )
        if self.path.startswith("/manifest.json"):
            manifest = {
                "latest": "0.2.22",
                "releases": [
                    {
                        "version": "0.2.22",
                        "binaries": [
                            {
                                "os": "darwin",
                                "arch": "arm64",
                                "url": "http://127.0.0.1/manifest.json",
                                "sha256": "0" * 64,
                            }
                        ],
                    }
                ],
            }
            payload = json.dumps(manifest).encode("utf-8")
            self._send(200, payload, "application/json")
            return
        self._send(200, b'{"probe":true}', "application/json")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body_text = raw.decode("utf-8")
        except UnicodeDecodeError:
            body_text = raw.decode("utf-8", "replace")

        try:
            parsed = json.loads(body_text)
        except json.JSONDecodeError:
            parsed = None

        record(
            "http",
            {
                "http_method": "POST",
                "path": self.path,
                "headers": dict(self.headers.items()),
                "body": body_text,
                "parsed": parsed,
            },
        )

        method = ""
        request_id = None
        params = None
        if isinstance(parsed, dict):
            method = str(parsed.get("method") or "")
            request_id = parsed.get("id")
            params = parsed.get("params")

        if method.startswith("notifications/"):
            # JSON-RPC 通知不需要响应，返回 202 空体。
            self._send(202, b"", "application/json")
            return

        response = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": build_result(method, params),
        }
        payload = json.dumps(response, ensure_ascii=False).encode("utf-8")
        self._send(200, payload, "application/json")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8971)
    parser.add_argument("--capture-dir", default="capture")
    args = parser.parse_args()

    global CAPTURE_DIR
    CAPTURE_DIR = os.path.abspath(args.capture_dir)
    os.makedirs(CAPTURE_DIR, exist_ok=True)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), ProbeHandler)
    print("probe server listening on http://127.0.0.1:%d/mcp" % args.port, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
