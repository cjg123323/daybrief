"""SQLite 상태 저장: 이미 본 항목, 판정, 요약, 사용자 피드백. git에는 올리지 않는다."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from daybrief.models import Item, Judgement, Summary

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY, source TEXT, kind TEXT, title TEXT, url TEXT,
  published_at TEXT, meta TEXT, first_seen TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS judgements (
  item_id TEXT PRIMARY KEY, relevant INTEGER, score REAL, reason TEXT, model TEXT,
  created TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS summaries (
  item_id TEXT PRIMARY KEY, text TEXT, basis TEXT, model TEXT,
  created TEXT DEFAULT (datetime('now'))
);
-- 9번 피드백: 사용자가 판정을 뒤집은 기록. 다음 판정 프롬프트에 few-shot으로 들어감
CREATE TABLE IF NOT EXISTS feedback (
  item_id TEXT, correct_relevant INTEGER, note TEXT, created TEXT DEFAULT (datetime('now'))
);
"""


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)

    def new_only(self, items: list[Item], *, record: bool = True) -> list[Item]:
        """처음 보는 항목만 반환하고 기록한다. 같은 실행 안의 중복도 제거.
        record=False(dry-run)면 기록하지 않아 다음 실제 실행에서 다시 처리된다."""
        out, seen = [], set()
        for it in items:
            if it.id in seen:
                continue
            seen.add(it.id)
            if self.db.execute("SELECT 1 FROM items WHERE id=?", (it.id,)).fetchone():
                continue
            if not record:
                out.append(it)
                continue
            self.db.execute(
                "INSERT INTO items(id,source,kind,title,url,published_at,meta) VALUES (?,?,?,?,?,?,?)",
                (it.id, it.source, it.kind, it.title, it.url,
                 it.published_at.isoformat() if it.published_at else None,
                 json.dumps(it.meta, ensure_ascii=False)),
            )
            out.append(it)
        self.db.commit()
        return out

    def forget_unjudged(self, ids: list[str]) -> int:
        """new_only로 기록했지만 판정을 못 받은 항목을 '본 적 없음'으로 되돌린다.
        LLM 호출이 실패한 날(크레딧 소진, 네트워크) 항목이 영영 묻히지 않게 다음 실행에서 다시 처리."""
        n = 0
        for i in ids:
            n += self.db.execute("DELETE FROM items WHERE id=? AND id NOT IN (SELECT item_id FROM judgements)",
                                 (i,)).rowcount
        self.db.commit()
        return n

    def save_judgement(self, j: Judgement) -> None:
        self.db.execute("INSERT OR REPLACE INTO judgements(item_id,relevant,score,reason,model) "
                        "VALUES (?,?,?,?,?)", (j.item_id, int(j.relevant), j.score, j.reason, j.model))
        self.db.commit()

    def save_summary(self, s: Summary) -> None:
        self.db.execute("INSERT OR REPLACE INTO summaries(item_id,text,basis,model) VALUES (?,?,?,?)",
                        (s.item_id, s.text, s.basis, s.model))
        self.db.commit()

    def feedback_examples(self, limit: int = 10) -> list[dict]:
        rows = self.db.execute(
            "SELECT i.title, f.correct_relevant, f.note FROM feedback f JOIN items i ON i.id=f.item_id "
            "ORDER BY f.created DESC LIMIT ?", (limit,)).fetchall()
        return [{"title": t, "relevant": bool(r), "note": n} for t, r, n in rows]
