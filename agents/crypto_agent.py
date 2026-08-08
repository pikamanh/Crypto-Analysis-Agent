import json
import os

from openai import OpenAI
from dotenv import load_dotenv

from tools.registry import TOOL_FUNCTIONS, TOOL_SCHEMAS

load_dotenv()

client = OpenAI(
    base_url="http://127.0.0.1:8080/v1",
    api_key=os.getenv("OPENAI_API_KEY")
)

SYSTEM_PROMPT = """
You are a cryptocurrency research assistant.

You help users understand cryptocurrency market information.

You have access to tools that provide real-time cryptocurrency data.

Rules:
- Always use the tool when the user asks for current market data.
- Never invent prices or market statistics.
- Clearly distinguish between factual data and your own analysis.
- Do not provide financial advice.
"""

def ask_agent(messages: list):
    response = client.chat.completions.create(
        model=os.getenv("MODEL"),
        messages=messages,
        tools=TOOL_SCHEMAS,
    )
    message = response.choices[0].message

    if not message.tool_calls:
        return message.content

    messages.append(message)

    for tool_call in message.tool_calls:
        func = TOOL_FUNCTIONS[tool_call.function.name]
        args = json.loads(tool_call.function.arguments)
        result = func(**args)

        if result is None:
            content = "Coin not found."
        else:
            content = result.model_dump_json()

        messages.append({
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": content,
        })

    response = client.chat.completions.create(
        model=os.getenv("MODEL"),
        messages=messages,
    )

    return response.choices[0].message.content