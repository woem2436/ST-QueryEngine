import os
from typing import Optional


class LLMAgent:
    """Optional LLM wrapper.

    The project can run without an API key. When no key/client is available,
    this agent returns a concise fallback message instead of raising errors.
    """

    def __init__(self, api_key_env: str = "DEEPSEEK_API_KEY", model: str = "deepseek-chat"):
        self.api_key_env = api_key_env
        self.api_key = os.getenv(api_key_env)
        self.model = model

    def _call_llm(self, prompt: str) -> str:
        if not self.api_key:
            return "未启用 LLM：当前结果由离线检索与规则引擎生成。"
        return "LLM 接口已配置，但本项目默认使用离线可复现实验流程。"

    def answer(self, question: str, context: Optional[str] = "") -> str:
        prompt = f"问题：{question}\n上下文：{context or '无'}\n答案："
        return self._call_llm(prompt)

