import logging

import aiohttp

from community_intern.ai.interfaces import AIClient, AIConfig
from community_intern.core.models import AIResult, Conversation, RequestContext

logger = logging.getLogger(__name__)

class AIClientImpl(AIClient):
    def __init__(self, config: AIConfig):
        self._config = config

    async def generate_reply(self, conversation: Conversation, context: RequestContext) -> AIResult:
        # TODO: Implement full LangGraph workflow here.
        # For now, return should_reply=False as we are focusing on summarization.
        logger.info("generate_reply called (not implemented yet)")
        return AIResult(should_reply=False, reply_text=None, citations=[])

    async def summarize_for_kb_index(
        self,
        *,
        source_id: str,
        text: str,
        timeout_seconds: float,
    ) -> str:
        """
        Summarize text for the Knowledge Base index using the LLM.

        This is implemented as a direct LLM call rather than a graph
        because it is a single-step transformation without complex control flow.
        """
        if not text.strip():
            return ""

        url = f"{self._config.llm_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._config.llm_api_key}",
            "Content-Type": "application/json",
        }

        messages = []
        if self._config.summarization_prompt:
            messages.append({"role": "system", "content": self._config.summarization_prompt})
        messages.append({"role": "user", "content": text})

        payload = {
            "model": self._config.llm_model,
            "messages": messages,
            "temperature": 0.0,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, headers=headers, json=payload, timeout=timeout_seconds
                ) as resp:
                    if resp.status != 200:
                        response_text = await resp.text()
                        logger.error(f"LLM request failed for source {source_id}: {resp.status} - {response_text}")
                        raise RuntimeError(f"LLM request failed with status {resp.status}")

                    data = await resp.json()
                    content = data["choices"][0]["message"]["content"]
                    return content.strip()
        except Exception as e:
            logger.error(f"Failed to summarize source {source_id}: {e}")
            raise
