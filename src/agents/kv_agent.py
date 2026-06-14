from typing import Any, Dict, List, Optional

from src.storage.kv_storage import KVStorage
from src.text_utils import weighted_overlap


class KVAgent:
    def __init__(self, kv_file_path: str):
        self.storage = KVStorage(kv_file_path)

    def _match_key(self, question: str, keys: List[str]) -> Optional[str]:
        q_low = question.lower()
        for key in keys:
            if key.lower() in q_low:
                return key
        scored = [(weighted_overlap(question, key), key) for key in keys]
        scored.sort(reverse=True)
        return scored[0][1] if scored and scored[0][0] > 0 else None

    def _search_in_values(self, question: str, data: Dict[str, Any]) -> Optional[str]:
        best_value = None
        best_score = 0.0
        for key, value in data.items():
            text = f"{key} {value}"
            score = weighted_overlap(question, text)
            if score > best_score:
                best_value = value
                best_score = score
        return str(best_value) if best_value is not None and best_score > 0 else None

    def answer(self, question: str) -> str:
        all_data = self.storage.get_all()
        if not all_data:
            return "KV 存储为空"
        matched_key = self._match_key(question, list(all_data.keys()))
        if matched_key:
            value = self.storage.get(matched_key)
            return str(value) if value is not None else "无值"
        matched_value = self._search_in_values(question, all_data)
        if matched_value:
            return matched_value
        return "未找到匹配的键值对"

    def close(self):
        pass

