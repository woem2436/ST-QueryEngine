from typing import Optional

from src.storage.sql_storage import SQLStorage


class SQLAgent:
    def __init__(self, db_path: str):
        self.storage = SQLStorage(db_path)
        self.table_name = self._get_first_table()
        self.columns = self._get_columns()

    def _get_first_table(self) -> Optional[str]:
        tables = self.storage.get_table_names()
        return tables[0] if tables else None

    def _get_columns(self) -> list:
        if not self.table_name:
            return []
        sql = f"PRAGMA table_info({quote_identifier(self.table_name)})"
        result = self.storage.execute_query(sql)
        return [col["name"] for col in result]

    def _parse_question(self, question: str) -> dict:
        q = question.lower()
        result = {"agg_type": None, "column": None}
        if any(word in q for word in ["总和", "合计", "总计", "汇总"]):
            result["agg_type"] = "sum"
        elif any(word in q for word in ["平均", "均值"]):
            result["agg_type"] = "avg"
        elif any(word in q for word in ["计数", "多少个", "多少条", "有多少", "几个"]):
            result["agg_type"] = "count"
        elif any(word in q for word in ["最大", "最高"]):
            result["agg_type"] = "max"
        elif any(word in q for word in ["最小", "最低"]):
            result["agg_type"] = "min"
        for col in self.columns:
            if col and col.lower() in q:
                result["column"] = col
                break
        return result

    def answer(self, question: str) -> str:
        if not self.table_name:
            return "错误：数据库中没有表"
        parsed = self._parse_question(question)
        agg = parsed["agg_type"]
        col = parsed["column"]
        table = quote_identifier(self.table_name)
        if agg is None:
            return "无法识别查询类型，请使用总和、平均、计数、最大、最小等聚合关键词"
        if agg == "count":
            sql = f"SELECT COUNT(*) AS value FROM {table}"
        elif col is None:
            return f"无法确定要聚合的列，可用列：{', '.join(self.columns)}"
        else:
            sql = f"SELECT {agg.upper()}({quote_identifier(col)}) AS value FROM {table}"
        results = self.storage.execute_query(sql)
        if results:
            return str(results[0]["value"])
        return "查询无结果"

    def close(self):
        self.storage.close()


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'

