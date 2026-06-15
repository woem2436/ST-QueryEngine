import os
from typing import Optional


class LLMAgent:
    """可选的 LLM 封装器。

    项目可以在没有 API key 的情况下运行；当没有配置 key 或客户端时，
    该 Agent 返回简短的离线兜底说明，而不是抛出异常中断流程。
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
