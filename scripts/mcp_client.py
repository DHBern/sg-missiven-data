"""Minimal client for the fleet's MCP servers (streamable HTTP, JSON-RPC).

Used by authority_links.py to call our own servers (ssrq, humanlineage, hls,
hbls, kf) from a script. The servers sit behind tei.dh.unibe.ch and need the
UniBE VPN. Headers (the hls key) are read from the user's Claude config and
never printed.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

FLEET = "https://tei.dh.unibe.ch/mcp/{name}/mcp"


def _headers_for(name: str) -> dict[str, str]:
    cfg = Path(os.path.expanduser("~/.claude.json"))
    if not cfg.exists():
        return {}
    try:
        srv = json.loads(cfg.read_text()).get("mcpServers", {}).get(name, {})
    except ValueError:
        return {}
    return dict(srv.get("headers") or {})


class MCP:
    def __init__(self, name: str, url: str | None = None, timeout: float = 60):
        self.name = name
        self.url = url or FLEET.format(name=name)
        self.timeout = timeout
        self.session: str | None = None
        self._id = 0
        self.extra = _headers_for(name)

    def _post(self, payload: dict) -> dict | None:
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": "2025-06-18", **self.extra}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode(), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            sid = r.headers.get("Mcp-Session-Id")
            if sid:
                self.session = sid
            body = r.read().decode("utf-8", "replace")
            ctype = r.headers.get("Content-Type", "")
        if not body.strip():
            return None
        if "text/event-stream" in ctype:
            # the last data: line that carries a JSON-RPC result
            out = None
            for line in body.splitlines():
                if line.startswith("data:"):
                    try:
                        msg = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    if "result" in msg or "error" in msg:
                        out = msg
            return out
        return json.loads(body)

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        msg = self._post({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}})
        if msg is None:
            raise RuntimeError(f"{self.name}: empty answer to {method}")
        if "error" in msg:
            raise RuntimeError(f"{self.name}: {msg['error']}")
        return msg["result"]

    def open(self) -> "MCP":
        self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "sg-missiven-authority", "version": "1"}})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self

    def call(self, tool: str, **args) -> object:
        """A tool's result as data: structuredContent, else the JSON in its text items."""
        if self.session is None:
            self.open()
        res = self._rpc("tools/call", {"name": tool, "arguments": args})
        if res.get("isError"):
            text = " ".join(c.get("text", "") for c in res.get("content", []))
            raise RuntimeError(f"{self.name}.{tool}: {text[:300]}")
        sc = res.get("structuredContent")
        if sc is not None:
            # FastMCP wraps a list result as {"result": [...]}
            return sc["result"] if isinstance(sc, dict) and set(sc) == {"result"} else sc
        items = []
        for c in res.get("content", []):
            if c.get("type") == "text":
                try:
                    items.append(json.loads(c["text"]))
                except ValueError:
                    items.append(c["text"])
        return items[0] if len(items) == 1 else items

    def tools(self) -> list[str]:
        if self.session is None:
            self.open()
        return [t["name"] for t in self._rpc("tools/list").get("tools", [])]
