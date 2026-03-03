"""OpenAI-powered conversational agent that generates meeting responses."""

import asyncio

from openai import AsyncOpenAI


class ClaudeAgent:
    """Maintains a running conversation history and generates contextual responses.

    Each call to `respond()` appends the new utterance to history and asks
    GPT to reply, keeping the full meeting context in view.
    """

    MAX_HISTORY_TURNS = 20  # Keep last N user+assistant turns to avoid token bloat

    def __init__(self, api_key: str, system_prompt: str):
        self.client = AsyncOpenAI(api_key=api_key)
        self.system_prompt = system_prompt
        self._history: list[dict] = []
        self._lock = asyncio.Lock()

    async def respond(self, utterance: str) -> str:
        """Generate a reply to `utterance` in the context of the conversation."""
        async with self._lock:
            self._history.append({"role": "user", "content": utterance})
            self._trim_history()

            messages = [{"role": "system", "content": self.system_prompt}] + self._history

            completion = await self.client.chat.completions.create(
                model="gpt-4.1-mini",  # Most capable — best for live Q&A
                # max_tokens=512,
                messages=messages,
            )

            reply = completion.choices[0].message.content.strip()
            self._history.append({"role": "assistant", "content": reply})
            return reply

    def _trim_history(self) -> None:
        """Keep only the most recent MAX_HISTORY_TURNS turns."""
        max_messages = self.MAX_HISTORY_TURNS * 2  # each turn = user + assistant
        if len(self._history) > max_messages:
            self._history = self._history[-max_messages:]

    def clear_history(self) -> None:
        """Reset conversation — useful between distinct meeting segments."""
        self._history.clear()

    def add_context(self, note: str) -> None:
        """Inject a system-level note into the conversation (as assistant context)."""
        self._history.append({
            "role": "user",
            "content": f"[Meeting context update: {note}]"
        })
        self._history.append({
            "role": "assistant",
            "content": "Understood, I'll keep that in mind."
        })
