"""Dynamic proxy ChatModel that resolves LLM credentials dynamically at call-time.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional, Iterator, AsyncIterator

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult, ChatGenerationChunk

logger = logging.getLogger(__name__)


class DynamicChatModel(BaseChatModel):
    """A proxy chat model that dynamically resolves the active LLM based on ContextVar.
    """
    role: str = "main"
    streaming: bool = True

    def __init__(self, role: str = "main", streaming: bool = True, **kwargs: Any) -> None:
        super().__init__(role=role, streaming=streaming, **kwargs)

    @property
    def _llm_type(self) -> str:
        return "dynamic-chat-model"

    def _get_target_model(self) -> BaseChatModel:
        # Avoid circular import
        from service.context import get_current_user_ctx
        from provider.factory import ModelFactory, get_model_factory

        ctx = get_current_user_ctx()
        if ctx and ctx.provider_config:
            # Reconstruct model dynamically from user context BYOK config
            # Ensure it is wrapped in providers format
            factory = ModelFactory(config={
                "providers": [ctx.provider_config],
                "active_provider": 0
            })
            return factory.get_model(self.role, streaming=self.streaming)

        # Fallback to local config singleton ModelFactory
        return get_model_factory().get_model(self.role, streaming=self.streaming)

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[Any] = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._get_target_model()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[Any] = None,
        **kwargs: Any,
    ) -> ChatResult:
        return await self._get_target_model()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def _stream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[Any] = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        return self._get_target_model()._stream(messages, stop=stop, run_manager=run_manager, **kwargs)

    def _astream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[Any] = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        return self._get_target_model()._astream(messages, stop=stop, run_manager=run_manager, **kwargs)

    def bind_tools(self, tools: list[Any], **kwargs: Any) -> Any:
        return self._get_target_model().bind_tools(tools, **kwargs)

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
        return self._get_target_model().with_structured_output(schema, **kwargs)

    # 忽略 Cython 编译出来的 cyfunction 方法类型，避免 Pydantic 报错
    model_config = {
        "ignored_types": (type(bind_tools),)
    }


