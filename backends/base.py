"""
Abstract base class for all LLM backends.

Every backend must implement ``generate()`` with the same signature so
the pipeline can swap backends without code changes.
"""

from __future__ import annotations

import abc
from typing import Dict, List


class BaseBackend(abc.ABC):
    """Common interface for LLM inference backends."""

    @abc.abstractmethod
    def generate(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> str:
        """Send a prompt to the model and return the assistant text.

        Parameters
        ----------
        prompt : str
            The user / assembled prompt.
        system : str
            System-level instruction.
        max_tokens : int
            Maximum tokens in the response.
        temperature : float
            Sampling temperature.

        Returns
        -------
        str
            The model's response text.
        """
        ...

    def chat(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = 0.3,
    ) -> str:
        """Multi-turn chat: send a full message list and return the reply.

        Default implementation concatenates messages into a single prompt
        and delegates to ``generate()``.  Subclasses should override for
        native multi-turn support.

        Parameters
        ----------
        messages : list of dict
            ``[{"role": "system"|"user"|"assistant", "content": str}, ...]``
        max_tokens : int
            Maximum tokens in the response.
        temperature : float
            Sampling temperature.
        """
        system_parts = []
        conversation = []
        for m in messages:
            if m["role"] == "system":
                system_parts.append(m["content"])
            else:
                tag = "User" if m["role"] == "user" else "Assistant"
                conversation.append(f"[{tag}]: {m['content']}")

        system = "\n".join(system_parts)
        prompt = "\n\n".join(conversation)
        return self.generate(prompt, system=system, max_tokens=max_tokens, temperature=temperature)
