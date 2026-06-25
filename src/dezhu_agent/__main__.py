"""CLI 入口：交互式对话，每行输入发给 agent."""

from dezhu_agent.config import STREAM_MODE, THINKING_ENABLED
from dezhu_agent.loop import run_conversation


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


def main():
    while True:
        try:
            user_message = input("> ")
        except EOFError:
            break
        if not user_message.strip():
            continue

        if STREAM_MODE:
            final_reply, _history = run_conversation(
                user_message,
                on_stream_chunk=_make_stream_printer(),
            )
        else:
            final_reply, history = run_conversation(user_message)

            if THINKING_ENABLED and history:
                for m in reversed(history):
                    if m.role == "assistant" and m.reasoning_content:
                        print(f"\n💭 思考中 {'─' * 44}")
                        print(f"\033[2m{m.reasoning_content}\033[0m")
                        print("─" * 50)
                        break

            # 取最后一条 assistant 消息的缓存统计
            cache_hit = cache_miss = 0
            for m in reversed(history):
                if m.role == "assistant":
                    cache_hit = m.prompt_cache_hit_tokens
                    cache_miss = m.prompt_cache_miss_tokens
                    break

            print(f"💬 {final_reply}")
            _print_cache_stats(cache_hit, cache_miss)


if __name__ == "__main__":
    main()
