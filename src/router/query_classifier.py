import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from .query_router import RuleBasedRouter


class QueryClassifier:
    """可选 LLM 的问题分类器。

    如果安装了 langchain_deepseek 且配置了 API key，分类器可以调用模型；
    否则回退到 RuleBasedRouter，保证项目在离线课堂环境中也能运行。
    """

    def __init__(self, config_path: str = "config.yaml"):
        self.config = self._load_config(config_path)
        self.rule_router = RuleBasedRouter()
        self.llm = self._init_llm()

    def _load_config(self, config_path: str) -> Dict:
        path = Path(config_path)
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}

    def _init_llm(self) -> Optional[Any]:
        llm_config = self.config.get("llm", {})
        enabled = bool(llm_config.get("enabled", False))
        api_key = os.getenv(llm_config.get("api_key_env", "DEEPSEEK_API_KEY"), "")
        if not enabled or not api_key:
            return None
        try:
            from langchain_deepseek import ChatDeepSeek
        except ImportError:
            return None
        return ChatDeepSeek(
            api_key=api_key,
            model=llm_config.get("model", "deepseek-chat"),
            base_url=llm_config.get("base_url", "https://api.deepseek.com/v1"),
            temperature=0.0,
        )

    def classify(self, question: str) -> Dict[str, Any]:
        if self.llm is None:
            result = self.rule_router.classify(question)
            result.setdefault("key_entities", [])
            return result

        prompt = f"""
你是一个表格问答查询分类器。给定用户问题，请判断类别并抽取关键实体。
类别只能是 SQL_AGG, KV_LOOKUP, VECTOR_SEARCH, CELL_LOOKUP, COMPLEX_REASONING。
请只输出 JSON：{{"category": "...", "key_entities": [...], "confidence": 0.0}}

问题：{question}
"""
        response = self.llm.invoke(prompt)
        content = response.content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.endswith("```"):
            content = content[:-3]
        try:
            result = json.loads(content)
            result.setdefault("method", "llm")
            return result
        except json.JSONDecodeError:
            result = self.rule_router.classify(question)
            result.setdefault("key_entities", [])
            return result
