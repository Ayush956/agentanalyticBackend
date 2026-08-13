import asyncio
from collections.abc import AsyncGenerator


def build_mock_reply(prompt: str) -> str:
    return (
        f"I can certainly help you with processing your request regarding: '{prompt}'."
    )


async def stream_chat(prompt: str) -> AsyncGenerator[str, None]:
    reply = build_mock_reply(prompt)
    words = reply.split(" ")

    for index, word in enumerate(words):
        chunk = word if index == 0 else f" {word}"
        yield chunk
        await asyncio.sleep(0.04)
