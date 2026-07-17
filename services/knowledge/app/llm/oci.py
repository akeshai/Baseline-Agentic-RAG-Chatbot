import asyncio
import logging
import json
from typing import Any, Dict, List

import oci
from oci.generative_ai_inference import GenerativeAiInferenceClient
from oci.generative_ai_inference.models import (
    GenerateTextDetails,
    OnDemandServingMode,
    DedicatedServingMode,
    CohereLlmInferenceRequest,
    ChatDetails,
    CohereChatRequest,
    GenericChatRequest,
    UserMessage,
    AssistantMessage,
    SystemMessage,
    ToolMessage,
    FunctionCall,
    CohereUserMessage,
    CohereChatBotMessage,
    CohereSystemMessage,
    TextContent,
)

from app.configs.llm import settings
from app.llm.base import BaseLLMAdapter

logger = logging.getLogger(__name__)


class OCILLMAdapter(BaseLLMAdapter):
    """
    OCI Generative AI implementation of BaseLLMAdapter.
    Wraps synchronous OCI SDK inference calls in asyncio.to_thread to prevent event loop blocks.
    Supports both OnDemandServingMode and DedicatedServingMode (custom endpoints).
    """

    def __init__(self):
        self._client = None

    @property
    def client(self) -> GenerativeAiInferenceClient:
        if self._client is None:
            # Load OCI config from file or fallback to default
            try:
                config = oci.config.from_file(
                    file_location=settings.oci_config_file,
                    profile_name=settings.oci_profile,
                )
            except Exception as e:
                logger.warning(
                    "Failed to load OCI config file. Attempting default fallback: %s", e
                )
                config = oci.config.from_file()

            # Initialize Inference Client
            self._client = GenerativeAiInferenceClient(
                config=config,
                service_endpoint=settings.oci_service_endpoint,
            )
        return self._client

    def _get_serving_mode(self) -> Any:
        """
        Determines serving mode based on model ID prefix.
        Uses DedicatedServingMode for endpoints (custom models), otherwise OnDemandServingMode.
        """
        model_id = settings.oci_llm_model_id
        if model_id.startswith("ocid1.generativeaiendpoint"):
            return DedicatedServingMode(endpoint_id=model_id)
        return OnDemandServingMode(model_id=model_id)

    async def generate(self, prompt: str, **kwargs: Any) -> str:
        """
        Generates text completion. Automatically routes to the chat API if the model is custom
        or chat-centric (non-Cohere) to avoid backend 500 service errors.
        """
        if not prompt:
            return ""

        # Route custom models and non-Cohere models to chat API
        is_custom = settings.oci_llm_model_id.startswith("ocid1.generativeaiendpoint")
        is_cohere = "cohere" in settings.oci_llm_model_id.lower()

        if is_custom or not is_cohere:
            messages = [{"role": "user", "content": prompt}]
            return await self.chat(messages, **kwargs)

        max_tokens = kwargs.get("max_tokens", 600)
        temperature = kwargs.get("temperature", 0.7)

        inference_request = CohereLlmInferenceRequest(
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            **{
                k: v
                for k, v in kwargs.items()
                if k not in ["max_tokens", "temperature"]
            },
        )

        details = GenerateTextDetails(
            compartment_id=settings.oci_compartment_id,
            serving_mode=self._get_serving_mode(),
            inference_request=inference_request,
        )

        response = await asyncio.to_thread(self.client.generate_text, details)

        try:
            return response.data.inference_response.generated_texts[0].text
        except (AttributeError, IndexError) as e:
            logger.error(
                "Failed to parse OCI LLM response: %s. Response data: %s",
                e,
                response.data,
            )
            raise ValueError(
                "Error parsing text generation response from OCI Generative AI"
            ) from e

    async def chat(self, messages: List[Dict[str, str]], **kwargs: Any) -> str:
        """
        Generates chat response. Wraps the synchronous OCI SDK chat call.
        Supports standard OpenAI/Cohere message structures, including user, assistant, system,
        and tool response roles with tool_calls.
        """
        if not messages:
            return ""

        max_tokens = kwargs.get("max_tokens", 600)
        temperature = kwargs.get("temperature", 0.7)

        # 1. Decide whether to use Cohere or Generic Chat API Request format
        if "cohere" in settings.oci_llm_model_id.lower():
            # Last message is the current query message
            current_message = messages[-1]["content"]

            # Previous messages form the history using CohereMessage subclasses
            chat_history = []
            for msg in messages[:-1]:
                role = msg.get("role", "USER").upper()
                content = msg.get("content", "")

                if role == "SYSTEM":
                    chat_history.append(CohereSystemMessage(message=content))
                elif role == "ASSISTANT" or role == "CHATBOT":
                    chat_history.append(CohereChatBotMessage(message=content))
                else:
                    chat_history.append(CohereUserMessage(message=content))

            chat_request = CohereChatRequest(
                message=current_message,
                chat_history=chat_history,
                max_tokens=max_tokens,
                temperature=temperature,
                **{
                    k: v
                    for k, v in kwargs.items()
                    if k not in ["max_tokens", "temperature"]
                },
            )
        else:
            # Llama and other models use GenericChatRequest
            chat_messages = []
            for msg in messages:
                role = msg.get("role", "USER").upper()
                content = msg.get("content", "")
                text_content = TextContent(text=content)

                if role == "SYSTEM":
                    chat_messages.append(SystemMessage(content=[text_content]))
                elif role == "TOOL":
                    # Tool output response message referencing a specific tool call ID
                    tool_call_id = msg.get("tool_call_id", "")
                    chat_messages.append(
                        ToolMessage(tool_call_id=tool_call_id, content=[text_content])
                    )
                elif role == "ASSISTANT" or role == "CHATBOT":
                    # Parse tool calls requested by the model if present
                    tool_calls = []
                    raw_tool_calls = msg.get("tool_calls", [])
                    if raw_tool_calls:
                        for tc in raw_tool_calls:
                            tc_id = tc.get("id")
                            func_data = tc.get("function", {})
                            func_name = func_data.get("name")
                            func_args = func_data.get("arguments", "{}")

                            # Ensure arguments are represented as a JSON string
                            if isinstance(func_args, dict):
                                func_args = json.dumps(func_args)

                            tool_calls.append(
                                FunctionCall(
                                    id=tc_id, name=func_name, arguments=func_args
                                )
                            )

                    chat_messages.append(
                        AssistantMessage(
                            content=[text_content] if content else [],
                            tool_calls=tool_calls if tool_calls else None,
                        )
                    )
                else:
                    chat_messages.append(UserMessage(content=[text_content]))

            chat_request = GenericChatRequest(
                messages=chat_messages,
                max_tokens=max_tokens,
                temperature=temperature,
                **{
                    k: v
                    for k, v in kwargs.items()
                    if k not in ["max_tokens", "temperature"]
                },
            )

        details = ChatDetails(
            compartment_id=settings.oci_compartment_id,
            serving_mode=self._get_serving_mode(),
            chat_request=chat_request,
        )

        response = await asyncio.to_thread(self.client.chat, details)

        try:
            data = response.data
            if "cohere" in settings.oci_llm_model_id.lower():
                return data.chat_response.text
            else:
                # GenericChatResponse
                chat_resp = data.chat_response
                if chat_resp and chat_resp.choices:
                    choice = chat_resp.choices[0]
                    if choice.message and choice.message.content:
                        return choice.message.content[0].text
                return ""
        except (AttributeError, IndexError) as e:
            logger.error(
                "Failed to parse OCI LLM chat response: %s. Response data: %s",
                e,
                response.data,
            )
            raise ValueError(
                "Error parsing chat response from OCI Generative AI"
            ) from e
