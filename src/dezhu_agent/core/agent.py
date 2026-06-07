import json
import subprocess
from idlelib import history

from openai import OpenAI

from dezhu_agent.config import get_config

_config = get_config()

SYSTEM_PROMPT = "You are a helpful assistant. You can run shell commands via the terminal tool."

BLOCKED_COMMANDS = [
    "rm -rf /",
    "mkfs",
    "dd if=",
    "shutdown",
    "reboot",
]


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "terminal",
            "description": "Run a shell command and return its output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute",
                    }
                },
                "required": ["command"],
            },
        },
    }
]


client = OpenAI(base_url=_config.BASE_URL, api_key=_config.API_KEY)


def agent_loop() -> None:

    print("=== Agent Loop ===")
    print(f"Model: {_config.MODEL}")
    print(f"Base URL: {_config.BASE_URL}")
    print("Type 'quit' to exit.\n")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    while True:
        user_input = input("You: ").strip()
        if not user_input or user_input.lower() in ("quit", "exit"):
            break

        result = run_conversation(user_input, messages)
        print(f"\nAssistant: {result['final_response']}\n")


def run_conversation(user_message: str, messages: list) -> dict:
    """Synchronous agent loop: call model, run tools, feed results back, repeat."""
    # messages 是对话全历史；每轮把 system prompt 拼在前面发给模型
    messages.append({"role": "user", "content": user_message})

    for iteration in range(_config.MAX_ITERATIONS):
        api_messages = messages

        response = client.chat.completions.create(
            model=_config.MODEL,
            messages=api_messages,
            tools=TOOLS,
        )

        assistant_msg = response.choices[0].message

        # 组装成历史消息；有 tool_calls 时必须原样回写，否则模型无法匹配 tool 结果
        msg_dict: dict = {
            "role": "assistant",
            "content": assistant_msg.content or "",
        }
        if assistant_msg.tool_calls:
            msg_dict["tool_calls"] = [
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments,
                    },
                }
                for tool_call in assistant_msg.tool_calls
            ]
        messages.append(msg_dict)

        # 终止条件：模型不再请求工具，说明它已经得到了回答所需的一切
        if not assistant_msg.tool_calls:
            return {
                "final_response": assistant_msg.content,
                "messages": messages,
            }

        # 依次执行工具调用；每条结果作为一条 role=tool 消息返回
        for tool_call in assistant_msg.tool_calls:
            print(f"  [tool] {tool_call.function.name}: {tool_call.function.arguments}")
            output = run_tool(
                tool_call.function.name,
                tool_call.function.arguments,
            )
            # tool_call_id 必须和上面 assistant 消息里的 id 对上
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": output,
                }
            )

    # 达到最大轮数仍没结束：防止模型陷入死循环烧钱
    return {
        "final_response": "(max iterations reached)",
        "messages": messages,
    }


def run_tool(name: str, arguments: str) -> str:
    """Execute a tool call by name and return the result as a string."""
    # 模型返回的 arguments 是 JSON 字符串，需要解析
    parsed_args = json.loads(arguments)

    if name == "terminal":
        command = parsed_args.get("command", "")

        # 命中黑名单直接拒绝；让模型看到 error 它会自行调整
        for blocked in BLOCKED_COMMANDS:
            if blocked in command:
                return json.dumps({"error": f"Blocked: {blocked}"})

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            # 合并 stdout/stderr，并截断避免单条工具输出占满上下文
            output = result.stdout + result.stderr
            return output[:10000] if output else "(no output)"
        except subprocess.TimeoutExpired:
            return "(command timed out after 30s)"
        except Exception as exc:
            return f"(error: {exc})"

    return json.dumps({"error": f"Unknown tool: {name}"})
