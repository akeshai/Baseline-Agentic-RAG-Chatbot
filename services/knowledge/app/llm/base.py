from abc import ABC, abstractmethod
from typing import Any, Dict, List


class BaseLLMAdapter(ABC):
    """
    Abstract Base Class defining the interface for LLM completions and chats.
    """

    @abstractmethod
    async def generate(self, prompt: str, **kwargs: Any) -> str:
        """
        Generates text completion for the given prompt.
        """
        pass

    @abstractmethod
    async def chat(self, messages: List[Dict[str, str]], **kwargs: Any) -> str:
        """
        Generates a chat completion/response for the given conversation history.
        """
        pass
