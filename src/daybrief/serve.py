"""daybrief serve: 바탕화면 '저장' 버튼을 받는 로컬 전용 HTTP 서버 (127.0.0.1만).

바탕화면 HTML은 파일을 쓸 수 없으므로(Lively는 https://<hash>.localhost/ 로 연다) 이 서버가 대신 받는다.
- 127.0.0.1에만 열고, Host 헤더도 확인한다 (DNS 리바인딩 방지).
- 모든 동작에 기기별 토큰이 필요하다 (shelf.token, 대시보드 HTML에만 들어 있음).
- 저장 대상은 digest에 있는 논문 id뿐이다. 임의 URL은 받지 않는다.

GET /ping                              → {"ok": true}
GET /saved?token=                      → {id: [분류...]}
GET /save?id=&cat=&token=&fmt=json     → 저장 결과 JSON (바탕화면 스크립트용)
GET /save?id=&cat=&token=              → 결과 HTML (스크립트가 막혀 링크가 브라우저로 열렸을 때)
"""
from __future__ import annotations

import html
import json
import logging
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from daybrief import shelf

log = logging.getLogger("daybrief.serve")


def make_server(cfg: dict[str, Any], port: int | None = None) -> ThreadingHTTPServer:
    s = shelf.settings(cfg)
    if s is None:
        raise ValueError("profile.yaml에 save.dir이 없습니다 (README '논문 저장' 참고)")
    shelf.token(cfg)  # 없으면 만들어 둔다 (render 전에 서버가 먼저 떠도 같은 값을 쓰게)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            # 요청 줄에 토큰이 들어 있으므로 경로만 남긴다
            log.info("%s %s", self.command, urlparse(self.path).path)

        def _send(self, code: int, body: str, ctype: str) -> None:
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            # 쿠키를 쓰지 않고 토큰으로만 인증하므로 * 로 충분 (바탕화면 origin은 기기마다 다른 *.localhost)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _json(self, code: int, obj: Any) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False), "application/json")

        def _page(self, code: int, msg: str, *, go: str | None = None) -> None:
            refresh = f'<meta http-equiv="refresh" content="1;url={html.escape(go, quote=True)}">' if go else ""
            self._send(code, f'<!doctype html><meta charset="utf-8">{refresh}<title>daybrief</title>'
                       f'<body style="font:15px \'Malgun Gothic\',sans-serif;padding:2em">{html.escape(msg)}</body>',
                       "text/html")

        def do_OPTIONS(self) -> None:  # noqa: N802 (http.server 규칙)
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            port = self.server.server_address[1]
            if self.headers.get("Host", "") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
                return self._json(403, {"error": "host"})
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path == "/ping":
                return self._json(200, {"ok": True})
            if not secrets.compare_digest(q.get("token", ""), shelf.token(cfg)):
                return self._json(403, {"error": "token"})
            if u.path == "/saved":
                return self._json(200, shelf.saved_map(cfg))
            if u.path != "/save":
                return self._json(404, {"error": "not found"})
            as_json = q.get("fmt") == "json"
            item = shelf.find_item(cfg, q.get("id", ""))
            if item is None or item.get("kind") != "paper":
                miss = "digest에 없는 논문"
                return self._json(404, {"error": miss}) if as_json else self._page(404, miss)
            try:
                # JSON(바탕화면 스크립트)일 때만 서버가 브라우저를 연다.
                # HTML이면 이미 브라우저 안이므로 결과 페이지가 그 주소로 이동한다
                res = shelf.save(cfg, item, q.get("cat", ""), browser=as_json)
            except Exception as e:  # noqa: BLE001 — 버튼 하나 실패로 서버가 죽지 않게
                log.exception("save failed")
                msg = f"저장 실패: {type(e).__name__}: {str(e)[:200]}"
                return self._json(500, {"error": msg}) if as_json else self._page(500, msg)
            log.info("saved %s → %s (%s)", item["id"], res["path"], res["status"])
            if as_json:
                return self._json(200, res)
            return self._page(200, f"{res['message']} · {res['folder']}", go=res.get("open"))

    return ThreadingHTTPServer(("127.0.0.1", s["port"] if port is None else port), Handler)
