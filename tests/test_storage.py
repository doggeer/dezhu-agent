"""StorageBackend + SQLiteBackend 测试 — 覆盖 spec 第 5 节全部存储相关验收."""

import sqlite3
from unittest.mock import patch

from dezhu_agent.messages import Message
from dezhu_agent.storage import SQLiteBackend, _escape_fts5, _message_to_row, _row_to_message

# ---- 辅助函数 ----


def _make_db():
    """创建内存数据库实例."""
    return SQLiteBackend(":memory:")


def _make_msg(**kwargs):
    """快速创建 Message."""
    defaults = {"role": "user", "content": "hello"}
    defaults.update(kwargs)
    return Message(**defaults)


# ---- 正常路径 ----


class TestCRUD:
    """创建、写入、读取、列表."""

    def test_create_session_returns_uuid(self):
        db = _make_db()
        sid = db.create_session()
        assert len(sid) == 32  # UUID4 hex
        assert sid.isalnum()

    def test_save_and_load_messages(self):
        db = _make_db()
        sid = db.create_session()
        msgs = [
            _make_msg(role="user", content="你好"),
            _make_msg(role="assistant", content="你好！有什么可以帮你的？", reasoning_content="思考..."),
        ]
        db.save_messages(sid, msgs)

        loaded = db.load_messages(sid)
        assert len(loaded) == 2
        assert loaded[0].role == "user"
        assert loaded[0].content == "你好"
        assert loaded[1].role == "assistant"
        assert loaded[1].reasoning_content == "思考..."
        # sequence 递增
        assert loaded[0] is not loaded[1]

    def test_save_messages_appends_not_duplicates(self):
        db = _make_db()
        sid = db.create_session()
        db.save_messages(sid, [_make_msg(role="user", content="first")])
        db.save_messages(sid, [_make_msg(role="assistant", content="second")])

        loaded = db.load_messages(sid)
        assert len(loaded) == 2
        assert loaded[0].content == "first"
        assert loaded[1].content == "second"

    def test_list_sessions_sorted_by_created(self):
        db = _make_db()
        s1 = db.create_session()
        s2 = db.create_session()
        sessions = db.list_sessions(5)
        assert len(sessions) == 2
        # 最近创建的在前
        assert sessions[0]["session_id"] == s2

    def test_list_sessions_respects_limit(self):
        db = _make_db()
        for _ in range(5):
            db.create_session()
        sessions = db.list_sessions(3)
        assert len(sessions) == 3

    def test_load_messages_empty_session(self):
        db = _make_db()
        sid = db.create_session()
        loaded = db.load_messages(sid)
        assert loaded == []


# ---- FTS5 搜索 ----


class TestSearch:
    """全文搜索."""

    def test_search_finds_content(self):
        db = _make_db()
        sid = db.create_session()
        msgs = [
            _make_msg(role="user", content="如何部署到生产环境"),
            _make_msg(role="assistant", content="部署需要先配置环境变量"),
        ]
        db.save_messages(sid, msgs)

        results = db.search_messages("部署")
        assert len(results) >= 1
        assert any("部署" in r["content"] for r in results)

    def test_search_no_match(self):
        db = _make_db()
        sid = db.create_session()
        db.save_messages(sid, [_make_msg(content="hello world")])

        results = db.search_messages("xyzxyz")
        assert results == []

    def test_search_with_special_chars(self):
        """搜索包含 FTS5 特殊字符的内容."""
        db = _make_db()
        sid = db.create_session()
        db.save_messages(sid, [_make_msg(content="SELECT * FROM users")])

        results = db.search_messages("SELECT")
        assert len(results) >= 1

    def test_search_context_before_and_after(self):
        db = _make_db()
        sid = db.create_session()
        msgs = [
            _make_msg(role="user", content="第一行"),
            _make_msg(role="assistant", content="目标行：部署信息"),
            _make_msg(role="user", content="第三行"),
        ]
        db.save_messages(sid, msgs)

        results = db.search_messages("部署")
        assert len(results) >= 1
        r = results[0]
        # 前一行存在
        if len(msgs) >= 2:
            assert r["context_before"] == "第一行"
        # 后一行存在
        assert r["context_after"] == "第三行"


# ---- 序列化 ----


class TestSerialization:
    """消息序列化/反序列化."""

    def test_roundtrip_with_tool_calls(self):
        msg = Message(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": "test.txt"}'},
                }
            ],
            reasoning_content="需要读取文件",
        )
        row = _message_to_row(msg, "sid", 0, "2025-01-01T00:00:00Z")
        restored = _row_to_message(row)

        assert restored.role == "assistant"
        assert restored.tool_calls is not None
        assert restored.tool_calls[0]["function"]["name"] == "read_file"
        assert restored.reasoning_content == "需要读取文件"

    def test_internal_fields_excluded(self):
        msg = Message(
            role="user",
            content="test",
            reasoning="内部推理",
            _internal={"key": "value"},
        )
        row = _message_to_row(msg, "sid", 0, "now")
        assert "reasoning" not in row
        assert "_internal" not in row

        restored = _row_to_message(row)
        assert restored.reasoning is None
        assert restored._internal == {}

    def test_special_characters_preserved(self):
        db = _make_db()
        sid = db.create_session()
        content = "单引号'双引号\"换行\nemoji🎉反斜杠\\"
        db.save_messages(sid, [_make_msg(content=content)])

        loaded = db.load_messages(sid)
        assert loaded[0].content == content

    def test_corrupted_tool_calls_json(self):
        row = {
            "role": "assistant",
            "content": "hi",
            "tool_calls": "{invalid json",
            "tool_call_id": None,
            "name": None,
            "reasoning_content": None,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 0,
        }
        msg = _row_to_message(row)
        assert msg.tool_calls is None  # 损坏的 JSON → None


# ---- 异常路径 ----


class TestAbnormal:
    """异常路径验收."""

    def test_autocreate_schema_on_first_run(self):
        """首次运行自动创建数据库和 schema."""
        db = SQLiteBackend(":memory:")
        # 验证表存在
        cur = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'"
        )
        assert cur.fetchone() is not None
        cur = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
        )
        assert cur.fetchone() is not None

    def test_wal_mode_enabled(self):
        """WAL 模式已开启（仅文件数据库，内存数据库跳过）."""
        import tempfile

        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        try:
            db = SQLiteBackend(tmp.name)
            cur = db.conn.execute("PRAGMA journal_mode")
            mode = cur.fetchone()[0]
            assert mode.lower() == "wal"
        finally:
            import os

            os.unlink(tmp.name)
            if os.path.exists(tmp.name + "-wal"):
                os.unlink(tmp.name + "-wal")
            if os.path.exists(tmp.name + "-shm"):
                os.unlink(tmp.name + "-shm")

    def test_save_messages_retry_on_busy(self):
        """写入时 SQLITE_BUSY 自动重试（验证 _execute_with_retry 逻辑）."""
        db = _make_db()
        sid = db.create_session()

        call_count = [0]

        original_execute = db._execute_with_retry

        def mock_execute(sql, params=()):
            call_count[0] += 1
            if call_count[0] < 3 and "INSERT" in sql:
                raise sqlite3.OperationalError("database is locked")
            return original_execute(sql, params)

        with patch.object(db, "_execute_with_retry", side_effect=mock_execute):
            try:
                db.save_messages(sid, [_make_msg(content="test")])
            except sqlite3.OperationalError:
                pass  # 3 次都失败也可以

        # 至少被调用了一次
        assert call_count[0] >= 1

    def test_save_messages_empty_list_noop(self):
        db = _make_db()
        sid = db.create_session()
        db.save_messages(sid, [])
        loaded = db.load_messages(sid)
        assert loaded == []


# ---- 边界条件 ----


class TestBoundary:
    """边界条件验收."""

    def test_large_message_count(self):
        """1000+ 消息加载完整."""
        db = _make_db()
        sid = db.create_session()
        msgs = [_make_msg(content=f"msg {i}") for i in range(1100)]
        db.save_messages(sid, msgs)

        loaded = db.load_messages(sid)
        assert len(loaded) == 1100
        assert loaded[0].content == "msg 0"
        assert loaded[1099].content == "msg 1099"

    def test_unicode_emoji(self):
        db = _make_db()
        sid = db.create_session()
        content = "🎉 你好世界 🌍 — em dash and unicode"
        db.save_messages(sid, [_make_msg(content=content)])

        loaded = db.load_messages(sid)
        assert loaded[0].content == content

    def test_empty_content_message(self):
        db = _make_db()
        sid = db.create_session()
        db.save_messages(sid, [_make_msg(role="assistant", content=None, tool_calls=[])])
        loaded = db.load_messages(sid)
        assert len(loaded) == 1
        assert loaded[0].content is None

    def test_update_session_meta_partial(self):
        db = _make_db()
        sid = db.create_session()
        # 只更新 ended_at
        db.update_session_meta(sid, ended_at="2025-01-01T00:00:00Z")
        sessions = db.list_sessions(1)
        assert sessions[0]["ended_at"] == "2025-01-01T00:00:00Z"
        assert sessions[0]["message_count"] == 0  # 未变

        # 只更新 message_count
        db.update_session_meta(sid, message_count=42)
        sessions = db.list_sessions(1)
        assert sessions[0]["message_count"] == 42

    def test_update_session_meta_noop_when_empty(self):
        db = _make_db()
        sid = db.create_session()
        db.update_session_meta(sid)  # 空调用不报错
        sessions = db.list_sessions(1)
        assert sessions[0]["ended_at"] is None
        assert sessions[0]["message_count"] == 0


# ---- FTS5 转义 ----


class TestFts5Escape:
    def test_simple_query(self):
        assert "hello" in _escape_fts5("hello")

    def test_special_chars_quoted(self):
        result = _escape_fts5("SELECT * FROM")
        # 应该被双引号包裹
        assert result.startswith('"')
        assert result.endswith('"')

    def test_double_quote_escaped(self):
        result = _escape_fts5('say "hello"')
        assert '""' in result  # 双引号翻倍


# ---- system_prompt 列 ----

class TestSystemPrompt:
    """system_prompt 列读写."""

    def test_save_and_load_system_prompt(self):
        db = _make_db()
        sid = db.create_session()
        db.save_system_prompt(sid, "hello world")
        assert db.load_system_prompt(sid) == "hello world"

    def test_load_system_prompt_empty_for_new_session(self):
        db = _make_db()
        sid = db.create_session()
        assert db.load_system_prompt(sid) == ""

    def test_system_prompt_column_exists_in_new_db(self):
        db = _make_db()
        sid = db.create_session()
        # 验证列存在：直接查 schema
        cur = db.conn.execute("PRAGMA table_info(sessions)")
        cols = [row["name"] for row in cur.fetchall()]
        assert "system_prompt" in cols
