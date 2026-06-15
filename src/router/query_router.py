from typing import Any, Dict


class RuleBasedRouter:
    """用于表格 QA 问题类型的透明规则路由器。"""

    def __init__(self):
        self.sql_keywords = [
            "总和",
            "合计",
            "总计",
            "平均",
            "均值",
            "计数",
            "多少个",
            "多少条",
            "几个",
            "有多少",
            "最大",
            "最小",
            "最高",
            "最低",
            "排序",
            "排名",
            "分组",
            "汇总",
        ]
        self.kv_keywords = ["来源", "注释", "单位", "说明", "创建时间", "作者", "标题", "部门名称"]
        self.vector_keywords = ["相似", "类似", "相关", "哪些", "有哪些", "什么类型", "原因", "分析"]

    def classify(self, question: str) -> Dict[str, Any]:
        q = question.lower()
        if any(keyword in q for keyword in self.sql_keywords):
            return {"category": "SQL_AGG", "confidence": 0.78, "method": "rule"}
        if any(keyword in q for keyword in self.kv_keywords):
            return {"category": "KV_LOOKUP", "confidence": 0.65, "method": "rule"}
        if any(keyword in q for keyword in self.vector_keywords):
            return {"category": "VECTOR_SEARCH", "confidence": 0.62, "method": "rule"}
        return {"category": "CELL_LOOKUP", "confidence": 0.58, "method": "rule"}

    def route(self, question: str) -> str:
        category = self.classify(question)["category"]
        mapping = {
            "SQL_AGG": "sql_agent",
            "KV_LOOKUP": "kv_agent",
            "VECTOR_SEARCH": "vector_agent",
            "CELL_LOOKUP": "hybrid_agent",
            "COMPLEX_REASONING": "llm_agent",
        }
        return mapping.get(category, "hybrid_agent")
