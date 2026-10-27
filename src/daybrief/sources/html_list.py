"""범용 게시판 목록 스크래퍼 (항목 1, 2).

사이트마다 코드를 짜지 않고 CSS 셀렉터 설정으로 처리한다. 설정 예:
  sources:
    html_list:
      boards:
        - id: knu-notice
          kind: notice
          url: https://...            # 게시판 목록 페이지
          row: "table tbody tr"       # 게시글 한 줄
          title: "td.subject a"       # 제목 (텍스트)
          link: "td.subject a"        # 링크 (href 속성). "self"면 행 자체가 <a> (카드형 목록)
          date: "td.date"             # 날짜 (선택)
          date_attr: "datetime"       # 선택. 날짜를 텍스트 대신 이 속성에서 읽음 (<time datetime=...>)
          date_format: "%Y.%m.%d"     # 선택. 없으면 자동 파싱
          id_param: "doc_no"          # 선택. 링크 쿼리에서 글 번호 파라미터. 고정글/일반행처럼 같은 글이
                                      #   다른 URL로 두 번 나올 때 하나로 합침. 없으면 URL 전체로 ID 생성
          fetch_body: false           # true면 상세 페이지 본문도 가져옴 (느림)
          body: "div.view-content"    # fetch_body일 때 본문 셀렉터
JS로 렌더링되는 게시판이면 이 소스로 안 된다 → 별도 playwright 소스 필요.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

from dateutil import parser as dparser
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from daybrief import http
from daybrief.models import Item
from daybrief.sources.base import register


def parse_date(text: str, fmt: str | None) -> datetime | None:
    text = text.strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, fmt) if fmt else dparser.parse(text, fuzzy=True)
    except (ValueError, OverflowError):
        return None


def parse_board(html: str, board: dict[str, Any]) -> list[Item]:
    tree = HTMLParser(html)
    items: list[Item] = []
    for row in tree.css(board["row"]):
        t = row.css_first(board["title"])
        # link: "self" → 행 자체가 <a> (카드형 목록)
        a = row if board.get("link") == "self" else row.css_first(board.get("link", board["title"]))
        if t is None or a is None or not a.attributes.get("href"):
            continue
        url = urljoin(board["url"], a.attributes["href"])
        d = row.css_first(board["date"]) if board.get("date") else None
        # date_attr: 화면 텍스트(예: "2026.10")보다 정확한 날짜가 속성(예: <time datetime="2026-10-23">)에 있을 때
        if d is None:
            date_text = ""
        elif board.get("date_attr"):
            date_text = d.attributes.get(board["date_attr"]) or ""
        else:
            date_text = d.text()
        # 게시판 URL은 쿼리스트링이 바뀌는 경우가 많아 해시로 안정 ID 생성. id_param이 있으면 글 번호만 쓴다
        key = url
        if board.get("id_param"):
            vals = parse_qs(urlparse(url).query).get(board["id_param"])
            if vals and vals[0]:
                key = f"{board['id_param']}={vals[0]}"
        sid = hashlib.sha1(key.encode()).hexdigest()[:16]
        items.append(
            Item(
                id=f"html_list:{board['id']}:{sid}",
                source="html_list",
                kind=board.get("kind", "notice"),
                title=t.text(strip=True),
                url=url,
                published_at=parse_date(date_text, board.get("date_format")) if date_text else None,
                meta={"board": board["id"]},
            )
        )
    return items


@register("html_list")
class HtmlListSource:
    name = "html_list"
    run_on = "cloud"

    def fetch(self, since: datetime, cfg: dict[str, Any]) -> list[Item]:
        out: list[Item] = []
        for board in cfg.get("boards", []):
            html = http.get(board["url"]).text
            for it in parse_board(html, board):
                if it.published_at and it.published_at.date() < since.date():
                    continue
                if board.get("fetch_body") and board.get("body"):
                    node = HTMLParser(http.get(it.url).text).css_first(board["body"])
                    it.body = node.text(separator="\n", strip=True) if node else ""
                out.append(it)
        return out
