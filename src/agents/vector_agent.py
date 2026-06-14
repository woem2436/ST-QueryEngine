from typing import Any, Optional

from src.storage.vector_storage import VectorStorage


class VectorAgent:
    def __init__(
        self,
        persist_dir: str,
        collection_name: str = "table_content",
        embedding_model: str = "offline-bm25",
        llm_agent: Optional[Any] = None,
    ):
        self.storage = VectorStorage(persist_dir, embedding_model_name=embedding_model)
        self.collection_name = collection_name
        self.llm_agent = llm_agent
        self.storage.create_collection(collection_name, recreate=False)

    def _format_context(self, results: list) -> str:
        parts = []
        for idx, result in enumerate(results, 1):
            doc = result.get("document", "")
            if doc:
                parts.append(f"【片段{idx}】{doc}")
        return "\n".join(parts)

    def answer(self, question: str, top_k: int = 3) -> str:
        results = self.storage.search(question, top_k=top_k)
        if not results:
            return "未找到相关内容"
        context = self._format_context(results)
        if self.llm_agent is not None:
            prompt = f"问题：{question}\n相关内容：{context}\n请基于以上信息给出简洁答案："
            return self.llm_agent.answer(prompt, context=context)
        return f"检索到以下相关内容：\n{context}"

    def close(self):
        pass

