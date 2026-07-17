from app.llm.base import BaseLLMAdapter
from app.llm.oci import OCILLMAdapter

_llm_adapter: BaseLLMAdapter | None = None


def get_llm_adapter() -> BaseLLMAdapter:
    """
    Returns a singleton instance of the LLM adapter.
    """
    global _llm_adapter
    if _llm_adapter is None:
        _llm_adapter = OCILLMAdapter()
    return _llm_adapter


__all__ = ["BaseLLMAdapter", "OCILLMAdapter", "get_llm_adapter"]
