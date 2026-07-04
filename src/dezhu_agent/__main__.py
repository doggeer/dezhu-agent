"""CLI 入口：交互式对话，每行输入发给 agent.

支持参数：
  dezhu-agent                 新会话
  dezhu-agent --continue [N]  恢复最近 N 个会话
  dezhu-agent --search TEXT   搜索历史消息
"""

from __future__ import annotations

import argparse
import atexit
import signal
import sys
from datetime import datetime, timezone

from dezhu_agent.config import DEZHU_LOG_DIR, DEZHU_LOG_LEVEL, STREAM_MODE, THINKING_ENABLED
from dezhu_agent.logging_config import _log_level_to_int, init_logging, set_session_context
from dezhu_agent.loop import run_conversation
from dezhu_agent.storage import SQLiteBackend


def _format_local_time(iso_string: str) -> str:
    """将 UTC ISO 时间字符串转为本地时间，格式化为 YYYY-MM-DD HH:MM:SS."""
    if not iso_string:
        return ""
    try:
        dt = datetime.fromisoformat(iso_string)
    except ValueError:
        return iso_string[:19]
    local_dt = dt.astimezone()
    return local_dt.strftime("%Y-%m-%d %H:%M:%S")


def _print_cache_stats(hit: int, miss: int):
    """打印缓存命中统计（灰色 dim 文本，不干扰正文）."""
    total = hit + miss
    if total == 0:
        return
    ratio = hit / total * 100
    print(f"\033[2m📊 缓存 {hit}/{total} ({ratio:.0f}%) 命中  |  未命中 {miss} tokens\033[0m\n")


def _make_stream_printer():
    """返回闭包，在 reasoning→content 切换时插入分割线."""
    reasoning_started = False
    thought_ended = False

    def _print(chunk):
        nonlocal reasoning_started, thought_ended

        if chunk.reasoning_delta:
            if not reasoning_started:
                reasoning_started = True
                print(f"\n💭 思考中 {'─' * 44}")
            print(f"\033[2m{chunk.reasoning_delta}\033[0m", end="", flush=True)

        if chunk.content_delta:
            if reasoning_started and not thought_ended:
                thought_ended = True
                print("\033[0m\n" + "─" * 50 + "\n💬 回复：")
            print(chunk.content_delta, end="", flush=True)

        if chunk.finish_reason:
            print()
            _print_cache_stats(chunk.prompt_cache_hit_tokens, chunk.prompt_cache_miss_tokens)

    return _print


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数."""
    parser = argparse.ArgumentParser(
        prog="dezhu-agent",
        description="DeZhu Agent — AI 编程助手 CLI",
    )
    parser.add_argument(
        "--continue",
        dest="continue_n",
        nargs="?",
        const=10,
        type=int,
        default=None,
        metavar="N",
        help="恢复最近的 N 个会话（默认 10）",
    )
    parser.add_argument(
        "--search",
        type=str,
        default=None,
        metavar="TEXT",
        help="搜索历史消息内容",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        metavar="PATH",
        help="数据库文件路径",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="启用 DEBUG 日志级别（详细输出）",
    )
    return parser.parse_args(argv)


def _run_search(storage: SQLiteBackend, query: str) -> None:
    """--search 模式：搜索并格式化输出，然后退出."""
    results = storage.search_messages(query)
    if not results:
        print("未找到匹配的消息。")
        return

    print(f"搜索「{query}」— 找到 {len(results)} 条结果：\n")
    for i, r in enumerate(results, 1):
        sid = r["session_id"][:8]
        created = _format_local_time(r.get("created_at", ""))
        print(f"[{i}] 会话 {sid}  |  {created}")
        if r.get("context_before"):
            print(f"    … {r['context_before'][:80]}")
        content = r.get("content", "") or "(空)"
        print(f"    → {content[:120]}")
        if r.get("context_after"):
            print(f"    … {r['context_after'][:80]}")
        print()


def _run_continue(storage: SQLiteBackend, limit: int) -> tuple[str, list | None]:
    """--continue 模式：列出最近会话，交互式选择，返回 (session_id, history)."""
    sessions = storage.list_sessions(limit)
    if not sessions:
        print("没有历史会话，将创建新会话。")
        return storage.create_session(), None

    print(f"最近 {len(sessions)} 个会话：\n")
    for i, s in enumerate(sessions, 1):
        sid = s["session_id"][:8]
        created = _format_local_time(s.get("created_at", ""))
        count = s.get("message_count", 0)
        ended = s.get("ended_at", "")
        status = "已结束" if ended else "进行中"
        print(f"  {i}. 会话 {sid}  |  {created}  |  {count} 条消息  |  {status}")

    print()
    while True:
        try:
            choice = input(f"选择会话 (1-{len(sessions)}，直接回车创建新会话): ").strip()
        except EOFError:
            choice = ""

        if choice == "":
            return storage.create_session(), None

        try:
            idx = int(choice)
            if 1 <= idx <= len(sessions):
                selected = sessions[idx - 1]
                sid = selected["session_id"]
                history = storage.load_messages(sid)
                print(f"已恢复会话 {sid[:8]}（{len(history)} 条消息）\n")
                return sid, history
        except ValueError:
            pass
        print(f"请输入 1-{len(sessions)} 之间的数字，或直接回车创建新会话。")


def _run_conversation_loop(storage: SQLiteBackend, session_id: str, history: list | None) -> None:
    """主对话循环."""
    current_history = history

    while True:
        try:
            user_message = input("> ")
        except EOFError:
            break
        if not user_message.strip():
            continue
        if user_message.strip() == "/exit":
            break

        if STREAM_MODE:
            final_reply, new_history, session_id = run_conversation(
                user_message,
                history=current_history,
                on_stream_chunk=_make_stream_printer(),
                storage=storage,
                session_id=session_id,
            )
        else:
            final_reply, new_history, session_id = run_conversation(
                user_message,
                history=current_history,
                storage=storage,
                session_id=session_id,
            )

            if THINKING_ENABLED and new_history:
                for m in reversed(new_history):
                    if m.role == "assistant" and m.reasoning_content:
                        print(f"\n💭 思考中 {'─' * 44}")
                        print(f"\033[2m{m.reasoning_content}\033[0m")
                        print("─" * 50)
                        break

            # 取最后一条 assistant 消息的缓存统计
            cache_hit = cache_miss = 0
            for m in reversed(new_history):
                if m.role == "assistant":
                    cache_hit = m.prompt_cache_hit_tokens
                    cache_miss = m.prompt_cache_miss_tokens
                    break

            print(f"💬 {final_reply}")
            _print_cache_stats(cache_hit, cache_miss)

        # 压缩可能导致 session 变更，同步更新日志上下文
        set_session_context(session_id)
        current_history = new_history


def _register_exit_handlers(storage: SQLiteBackend, session_id: str) -> None:
    """注册退出处理器：更新会话元数据."""
    ended = False

    def _cleanup() -> None:
        nonlocal ended
        if ended:
            return
        ended = True
        try:
            now = datetime.now(timezone.utc).isoformat()
            # 使用 COUNT 查询获取消息数，避免加载全部消息
            cur = storage.conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?",
                (session_id,),
            )
            row = cur.fetchone()
            count = row[0] if row else 0
            storage.update_session_meta(session_id, ended_at=now, message_count=count)
        except Exception as e:
            print(f"Warning: 无法更新会话元数据: {e}", file=sys.stderr)

    atexit.register(_cleanup)

    def _signal_handler(signum, frame):
        _cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)


def main(argv: list[str] | None = None):
    args = _parse_args(argv)

    # 初始化日志系统（在存储之前，确保异常能记录）
    log_level = _log_level_to_int("DEBUG" if args.debug else DEZHU_LOG_LEVEL)
    init_logging(log_dir=DEZHU_LOG_DIR, log_level=log_level)

    # 初始化存储
    storage = SQLiteBackend(args.db_path or "")

    # --search 模式（不进入对话循环）
    if args.search:
        _run_search(storage, args.search)
        return

    # --continue 模式 / 默认模式
    if args.continue_n is not None:
        session_id, history = _run_continue(storage, args.continue_n)
    else:
        session_id, history = _run_continue(storage, 10)

    # 设置日志 session 上下文
    set_session_context(session_id)

    # 注册退出处理
    _register_exit_handlers(storage, session_id)

    # 进入对话循环
    _run_conversation_loop(storage, session_id, history)


if __name__ == "__main__":
    main()
