# src/spma/llm/providers/local_vllm.py
"""本地 vLLM Provider——用于 L1 降级兜底。

继承 OpenAICompatProvider，覆盖默认 base_url 为本地 vLLM 地址，
禁用 thinking mode。
"""

from spma.llm.providers.base import ProviderConfig
from spma.llm.providers.openai_compat import OpenAICompatProvider


class LocalVLLMProvider(OpenAICompatProvider):
    """本地 vLLM 部署的 LLM Provider（如 Qwen3-8B）。

    与 OpenAICompatProvider 的唯一区别：
    - 默认 base_url 指向本地 vLLM 服务
    - supports_thinking() 始终返回 False
    """

    def __init__(self, name: str, config: ProviderConfig):
        # 确保 base_url 指向本地 vLLM
        if not config.base_url:
            config.base_url = "http://localhost:8000/v1"
        super().__init__(name, config)

    def supports_thinking(self) -> bool:
        return False
