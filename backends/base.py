"""
Abstract base class for all LLM backends.

Every backend must implement ``generate()`` with the same signature so
the pipeline can swap backends without code changes.
"""

from __future__ import annotations

import abc


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
