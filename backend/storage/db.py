"""
SQLite 数据层（M2）：建表 + CRUD

设计要点：
- sqlite3 标准库，免额外依赖
- 每次操作独立连接（本地文件，开销极小），避免 FastAPI 线程池下
  共享连接的 check_same_thread 问题；WAL 模式提升并发读写表现
- CRUD 全部为同步函数；路由层如需 async，直接用 def 路由由
  FastAPI 自动放线程池，不要在 async def 里直接调用
- 测试通过 monkeypatch 本模块的 DB_PATH 变量切换到临时库

表结构（含 M2 评审时确定的预留决策）：
- conversations.meta：JSON 字符串，存上下文（选区问答的 file/selection/节点 id），
  M9 选区交互需要，避免日后迁移
- flashcards 用 id 自增主键 + concept 索引：同一概念允许多张卡
  （概念卡 + 诊断式坑卡），不再用 concept 做主键
- annotations.offset 语义：Markdown 源文件中的字符偏移量（0-based），
  不是渲染后 DOM 的偏移；配合 text 字段双重定位，M9 高亮时按
  源文本匹配映射到渲染节点
- reviews 以 concept（wiki 文件 kebab-case slug）为主键：产品定义为
  每概念一个复习状态；wiki 文件重命名时需同步更新此表
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from backend.config import DB_PATH, logger

# 模块级变量，测试可 monkeypatch 覆盖
DB_PATH = str(DB_PATH)

# ===== 建表语句 =====

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    concept TEXT PRIMARY KEY,
    due TEXT NOT NULL,
    interval_days REAL NOT NULL DEFAULT 1,
    ease REAL NOT NULL DEFAULT 2.5,
    last_rated TEXT,
    times_seen INTEGER NOT NULL DEFAULT 0,
    total_reviews INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    meta TEXT
);

CREATE TABLE IF NOT EXISTS flashcards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    concept TEXT NOT NULL,
    front TEXT NOT NULL,
    back TEXT NOT NULL,
    created TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_flashcards_concept ON flashcards(concept);

CREATE TABLE IF NOT EXISTS sources (
    path TEXT PRIMARY KEY,
    sha1 TEXT NOT NULL,
    mtime REAL NOT NULL,
    wiki_file TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS annotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file TEXT NOT NULL,
    offset INTEGER NOT NULL,
    text TEXT NOT NULL,
    note TEXT NOT NULL,
    created TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_annotations_file ON annotations(file);
"""

ALL_TABLES = ("reviews", "conversations", "flashcards", "sources", "annotations")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def _conn():
    """独立连接上下文：自动建表（幂等，IF NOT EXISTS 开销微秒级）+ commit / rollback / close

    每次连接都执行 _SCHEMA：服务启动、CLI、agent 工具任何路径进来都无需
    先显式 init_db，天然规避"库文件被删后首操作报 no such table"；
    对测试 monkeypatch DB_PATH 同样安全（新库新连接自动建表）。
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """显式初始化（保留 API 兼容；_conn 已自动建表，通常无需调用）"""
    with _conn():
        pass
    logger.info(f"SQLite 初始化完成: {DB_PATH}")


# ===== reviews（FSRS 复习状态，每概念一条）=====

def upsert_review(concept: str, due: str, interval_days: float, ease: float,
                  last_rated: str | None = None) -> None:
    """插入或更新概念的复习状态（编译生成卡片时首次登记）"""
    with _conn() as conn:
        conn.execute(
            """INSERT INTO reviews (concept, due, interval_days, ease, last_rated)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(concept) DO UPDATE SET
                   due=excluded.due, interval_days=excluded.interval_days,
                   ease=excluded.ease, last_rated=excluded.last_rated""",
            (concept, due, interval_days, ease, last_rated or _now()),
        )


def get_review(concept: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM reviews WHERE concept = ?", (concept,)
        ).fetchone()
        return dict(row) if row else None


def get_due_reviews(today: str) -> list[dict]:
    """获取今天及之前到期的复习卡（due <= today）"""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM reviews WHERE due <= ? ORDER BY due", (today,)
        ).fetchall()
        return [dict(r) for r in rows]


def delete_review(concept: str) -> bool:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM reviews WHERE concept = ?", (concept,))
        return cur.rowcount > 0


# ===== conversations（对话历史，meta 存上下文 JSON）=====

def add_conversation(role: str, content: str, meta: dict | None = None) -> int:
    """写入一条对话记录，meta 可携带 file/selection/node_id 等上下文，返回记录 id"""
    import json
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO conversations (ts, role, content, meta) VALUES (?, ?, ?, ?)",
            (_now(), role, content, json.dumps(meta, ensure_ascii=False) if meta else None),
        )
        return cur.lastrowid


def list_conversations(limit: int = 100, offset: int = 0) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM conversations ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]


# ===== flashcards（复习卡，一概念多卡）=====

def add_flashcard(concept: str, front: str, back: str) -> int:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO flashcards (concept, front, back, created) VALUES (?, ?, ?, ?)",
            (concept, front, back, _now()),
        )
        return cur.lastrowid


def get_flashcard(card_id: int) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM flashcards WHERE id = ?", (card_id,)
        ).fetchone()
        return dict(row) if row else None


def get_flashcards_by_concept(concept: str) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM flashcards WHERE concept = ? ORDER BY id", (concept,)
        ).fetchall()
        return [dict(r) for r in rows]


def list_flashcards(limit: int = 100, offset: int = 0) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM flashcards ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]


def delete_flashcard(card_id: int) -> bool:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM flashcards WHERE id = ?", (card_id,))
        return cur.rowcount > 0


# ===== sources（raw 文件收录状态，编译管道增量依据）=====

def upsert_source(path: str, sha1: str, mtime: float,
                  wiki_file: str | None = None, status: str = "pending") -> None:
    """插入或更新 raw 文件的处理状态（按 path 主键）"""
    with _conn() as conn:
        conn.execute(
            """INSERT INTO sources (path, sha1, mtime, wiki_file, status)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET
                   sha1=excluded.sha1, mtime=excluded.mtime,
                   wiki_file=excluded.wiki_file, status=excluded.status""",
            (path, sha1, mtime, wiki_file, status),
        )


def get_source(path: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM sources WHERE path = ?", (path,)).fetchone()
        return dict(row) if row else None


def list_sources(status: str | None = None) -> list[dict]:
    with _conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM sources WHERE status = ? ORDER BY path", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM sources ORDER BY path").fetchall()
        return [dict(r) for r in rows]


def delete_source(path: str) -> bool:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM sources WHERE path = ?", (path,))
        return cur.rowcount > 0


# ===== annotations（文本批注，offset 为 Markdown 源字符偏移）=====

def add_annotation(file: str, offset: int, text: str, note: str) -> int:
    """新增批注；offset 为该批注在 Markdown 源文件中的字符偏移量（0-based）"""
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO annotations (file, offset, text, note, created) VALUES (?, ?, ?, ?, ?)",
            (file, offset, text, note, _now()),
        )
        return cur.lastrowid


def get_annotation(annotation_id: int) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM annotations WHERE id = ?", (annotation_id,)
        ).fetchone()
        return dict(row) if row else None


def list_annotations_by_file(file: str) -> list[dict]:
    """按文件取批注，按 offset 升序（渲染时顺序定位高亮）"""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM annotations WHERE file = ? ORDER BY offset", (file,)
        ).fetchall()
        return [dict(r) for r in rows]


def delete_annotation(annotation_id: int) -> bool:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM annotations WHERE id = ?", (annotation_id,))
        return cur.rowcount > 0
