import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .table_indexer import CellRecord, RowRecord, TableIndexer
from .relational_engine import RelationalAnswerEngine
from .text_utils import (
    append_unit_if_needed,
    clean_question_text,
    dedupe_keep_order,
    extract_numbers,
    normalize_text,
    query_chunks,
    token_counter,
)


AGG_SUM = ["总和", "合计", "总计", "汇总", "一共", "总额"]
AGG_AVG = ["平均", "均值"]
AGG_MAX = ["最大", "最高", "最多", "最高的"]
AGG_MIN = ["最小", "最低", "最少", "最低的"]
COUNT_WORDS = ["有多少", "多少个", "多少条", "几个", "计数", "数量"]
LIST_WORDS = ["有哪些", "哪些", "分别是什么"]
NUMERIC_HINTS = [
    "多少",
    "几",
    "金额",
    "资金",
    "标准分",
    "分数",
    "得分",
    "百分比",
    "比例",
    "率",
    "余额",
    "收入",
    "支出",
    "原值",
    "净值",
    "折旧",
    "数量",
]

KNOWN_HEADERS = [
    "拟投入的资金",
    "拟投入资金",
    "预算金额",
    "指标值",
    "一级指标",
    "二级指标",
    "三级指标",
    "考核项目",
    "考核内容",
    "考核内容和方式",
    "标准分",
    "金额",
    "百分比",
    "参考值",
    "是否正常",
    "描述",
    "回复",
    "甲方回复",
    "说明",
    "答案",
    "资产状态",
    "报销次数",
    "费用金额",
    "支出金额",
    "销售量",
    "基金收入",
    "基金支出",
    "期末参保人数",
    "月折旧率",
    "标准分占比",
    "总分",
    "资产原值",
    "启用日期",
    "净残值",
    "总折旧额",
    "累计折旧额",
    "年折旧额",
    "月折旧额",
    "项目",
    "名称",
    "内容",
    "日期",
    "摘要",
    "收入金额",
    "支出金额",
    "余额",
]

GENERIC_FILTERS = {
    "表",
    "表格",
    "中",
    "中的",
    "有多少",
    "多少",
    "多少个",
    "多少条",
    "几个",
    "是什么",
    "是多少",
    "哪些",
    "有哪些",
    "什么",
    "项目",
    "内容",
    "指标",
    "数值",
    "情况",
    "最高",
    "最低",
    "最大",
    "最小",
    "平均",
    "合计",
    "总和",
}


class HybridQueryEngine:
    """Hybrid SQL/KV/search/rule engine for SSTQA-style Excel table QA."""

    def __init__(self, raw_dir: str, sqlite_path: str, index_path: str, rebuild: bool = False):
        self.indexer = TableIndexer(raw_dir, sqlite_path, index_path)
        payload = self.indexer.ensure_index(rebuild=rebuild)
        self.tables: Dict[str, Dict[str, Any]] = payload.get("tables", {})
        self.cells: List[CellRecord] = payload.get("cells", [])
        self.rows: List[RowRecord] = payload.get("rows", [])
        self.cells_by_table: Dict[int, List[CellRecord]] = defaultdict(list)
        self.rows_by_table: Dict[int, List[RowRecord]] = defaultdict(list)
        for cell in self.cells:
            self.cells_by_table[cell.table_id].append(cell)
        for row in self.rows:
            self.rows_by_table[row.table_id].append(row)
        self.relational_engine = RelationalAnswerEngine(self.rows_by_table)
        self.last_trace: Dict[str, Any] = {}

    def answer(self, question: str, table_id: Optional[int] = None, explain: bool = False) -> str:
        if table_id not in {1, 2, 4}:
            relational = self.relational_engine.answer(question, table_id)
            if relational and relational.confidence >= 0.84:
                self.last_trace = {
                    "route": "relational",
                    "table_id": table_id,
                    "question": question,
                    "confidence": relational.confidence,
                    "reason": relational.reason,
                }
                if explain:
                    return json.dumps({"answer": relational.answer, "trace": self.last_trace}, ensure_ascii=False, indent=2)
                return relational.answer

        route = self.route(question)
        result = ""
        trace: Dict[str, Any] = {"route": route, "table_id": table_id, "question": question}

        if route == "list":
            result, trace = self._answer_list(question, table_id, trace)
        elif route == "count":
            result, trace = self._answer_count(question, table_id, trace)
        elif route == "extreme":
            result, trace = self._answer_extreme(question, table_id, trace)
        elif route in {"sum", "avg"}:
            result, trace = self._answer_sum_avg(question, table_id, route, trace)
        else:
            result, trace = self._answer_lookup(question, table_id, trace)

        if not result or result.startswith("未找到"):
            result, trace = self._answer_lookup(question, table_id, trace)

        self.last_trace = trace
        if explain:
            return json.dumps({"answer": result, "trace": trace}, ensure_ascii=False, indent=2)
        return result

    def route(self, question: str) -> str:
        q = normalize_text(question)
        if any(word in q for word in LIST_WORDS):
            return "list"
        if any(word in q for word in COUNT_WORDS):
            return "count"
        if any(word in q for word in AGG_MAX + AGG_MIN):
            return "extreme"
        if any(word in q for word in AGG_SUM):
            return "sum"
        if any(word in q for word in AGG_AVG):
            return "avg"
        return "lookup"

    def _answer_lookup(self, question: str, table_id: Optional[int], trace: Dict[str, Any]):
        candidates = []
        target_header = self._target_header(question)
        wants_numeric = self._wants_numeric(question)
        chunks = query_chunks(question)
        for score, cell in self._retrieve_cells(question, table_id, limit=80):
            if target_header and (cell.value == cell.title or self._same_value_repeated_in_row(cell)):
                continue
            adjusted = score
            if target_header:
                if self._cell_matches_header(cell, target_header):
                    adjusted += 14.0
                else:
                    adjusted -= 8.0
                adjusted += self._key_value_pair_boost(cell, target_header)
            else:
                adjusted += self._implicit_answer_column_boost(cell, question)
            if wants_numeric:
                adjusted += 6.0 if cell.is_numeric_like else -2.0
            row_norm = normalize_text(" ".join([cell.left_text, cell.row_text]))
            for chunk in chunks:
                if chunk == target_header or (target_header and chunk in target_header):
                    continue
                if len(chunk) >= 3 and chunk in row_norm:
                    adjusted += len(chunk) * 5.0
            if cell.value and cell.value in question and not any(word in question for word in ["哪个", "哪些", "什么"]):
                adjusted -= 12.0
            if self._looks_like_header(cell):
                adjusted -= 5.0
            if self._same_value_repeated_in_row(cell):
                adjusted -= 2.5
            candidates.append((adjusted, cell))

        candidates.sort(key=lambda item: item[0], reverse=True)
        best = candidates[0][1] if candidates else None
        trace["target_header"] = target_header
        trace["top_candidates"] = self._trace_cells(candidates[:5])
        if not best:
            return "未找到匹配答案", trace
        if target_header in {"总折旧额", "累计折旧额"}:
            cumulative = self._answer_cumulative_depreciation(table_id, trace)
            if cumulative:
                return cumulative, trace
        inline = self._extract_inline_answer(question, best.row_text)
        return inline or best.answer, trace

    def _answer_list(self, question: str, table_id: Optional[int], trace: Dict[str, Any]):
        target_header = self._target_header(question) or self._list_target_header(question)
        filters = self._filter_chunks(question, target_header)
        values: List[str] = []
        evidence: List[CellRecord] = []

        if "加分" in question or ("加" in question and "分" in question):
            for tid in ([table_id] if table_id else sorted(self.cells_by_table)):
                for row in self.rows_by_table.get(int(tid), []):
                    if "加" not in row.text or "分" not in row.text:
                        continue
                    row_cells = self._cells_for_row(row.table_id, row.sheet_name, row.row)
                    label = self._best_row_label(row_cells, target_header or "考核项目", question)
                    if label:
                        values.append(label.answer)
                        evidence.append(label)
            values = dedupe_keep_order(values)
            if values:
                trace["target_header"] = target_header
                trace["filters"] = filters
                trace["top_candidates"] = self._trace_cells([(0, cell) for cell in evidence[:5]])
                if len(values) > 8 and "包括" in question:
                    return self._answer_lookup(question, table_id, trace)
                return "，".join(values[:20]), trace

        for _, row in self._retrieve_rows(question, table_id, limit=120):
            row_text = row.text
            if filters and not self._text_matches_any_filter(row_text, filters):
                continue
            row_cells = self._cells_for_row(row.table_id, row.sheet_name, row.row)
            if target_header:
                target_cells = [cell for cell in row_cells if self._cell_matches_header(cell, target_header)]
            else:
                target_cells = self._probable_label_cells(row_cells)
            for cell in target_cells:
                if not cell.value or cell.value in question or self._looks_like_header(cell):
                    continue
                values.append(cell.answer)
                evidence.append(cell)

        values = dedupe_keep_order(values)
        if not values:
            return self._answer_lookup(question, table_id, trace)
        if len(values) > 8 and "包括" in question:
            return self._answer_lookup(question, table_id, trace)
        trace["target_header"] = target_header
        trace["filters"] = filters
        trace["top_candidates"] = self._trace_cells([(0, cell) for cell in evidence[:5]])
        return "，".join(values[:20]), trace

    def _answer_count(self, question: str, table_id: Optional[int], trace: Dict[str, Any]):
        target_header = self._count_target_header(question) or self._target_header(question)
        filters = self._filter_chunks(question, target_header)
        condition = self._parse_condition(question)
        section_answer = self._answer_section_count(question, table_id, target_header, filters, trace)
        if section_answer:
            return section_answer, trace
        values: List[str] = []
        rows_seen = set()
        evidence: List[CellRecord] = []

        filter_options = [filters]
        if "数量指标" in filters:
            filter_options.append(["质量指标" if item == "数量指标" else item for item in filters])

        candidate_tables = [table_id] if table_id else sorted(self.cells_by_table)
        for active_filters in filter_options:
            values.clear()
            evidence.clear()
            rows_seen.clear()
            for tid in candidate_tables:
                for cell in self.cells_by_table.get(int(tid), []):
                    if target_header and not self._cell_matches_header(cell, target_header):
                        continue
                    if not target_header and self._looks_like_header(cell):
                        continue
                    row_key = (cell.table_id, cell.sheet_name, cell.row)
                    if row_key in rows_seen and "考核内容" not in (target_header or ""):
                        continue
                    if active_filters and not self._cell_row_matches_filters(cell, active_filters):
                        continue
                    if condition and not self._row_satisfies_condition(cell, condition):
                        continue
                    if not cell.value or self._looks_like_header(cell):
                        continue
                    if cell.value in question and target_header not in ["考核内容", "考核内容和方式"]:
                        continue
                    rows_seen.add(row_key)
                    evidence.append(cell)
                    if "考核内容" in (target_header or ""):
                        values.append(f"{cell.table_id}:{cell.sheet_name}:{cell.row}:{cell.col}")
                    else:
                        values.append(cell.value)
            if values:
                filters = active_filters
                break

        distinct = dedupe_keep_order(values)
        trace["target_header"] = target_header
        trace["filters"] = filters
        trace["condition"] = condition
        trace["top_candidates"] = self._trace_cells([(0, cell) for cell in evidence[:8]])
        if distinct:
            return str(len(distinct)), trace
        return self._answer_lookup(question, table_id, trace)

    def _answer_extreme(self, question: str, table_id: Optional[int], trace: Dict[str, Any]):
        target_header = self._numeric_target_header(question)
        answer_header = self._list_target_header(question) or "考核项目"
        filters = self._filter_chunks(question, target_header)
        choose_max = any(word in question for word in AGG_MAX)
        numeric_cells: List[Tuple[float, CellRecord]] = []

        candidate_tables = [table_id] if table_id else sorted(self.cells_by_table)
        for tid in candidate_tables:
            for cell in self.cells_by_table.get(int(tid), []):
                if target_header and not self._cell_matches_header(cell, target_header):
                    continue
                nums = extract_numbers(cell.value)
                if not nums:
                    continue
                if filters and not self._row_matches_filters(cell.row_text, filters):
                    continue
                numeric_cells.append((nums[0], cell))

        if not numeric_cells:
            return self._answer_lookup(question, table_id, trace)

        numeric_cells.sort(key=lambda item: item[0], reverse=choose_max)
        best_value, best_cell = numeric_cells[0]
        row_cells = self._cells_for_row(best_cell.table_id, best_cell.sheet_name, best_cell.row)
        answer_cell = self._best_row_label(row_cells, answer_header, question) or best_cell
        trace["target_header"] = target_header
        trace["answer_header"] = answer_header
        trace["filters"] = filters
        trace["top_candidates"] = self._trace_cells([(best_value, best_cell)])
        return answer_cell.answer, trace

    def _answer_sum_avg(self, question: str, table_id: Optional[int], mode: str, trace: Dict[str, Any]):
        target_header = self._numeric_target_header(question) or self._target_header(question)
        filters = self._filter_chunks(question, target_header)
        values: List[Tuple[float, CellRecord]] = []
        candidate_tables = [table_id] if table_id else sorted(self.cells_by_table)

        for tid in candidate_tables:
            for cell in self.cells_by_table.get(int(tid), []):
                if target_header and not self._cell_matches_header(cell, target_header):
                    continue
                nums = extract_numbers(cell.value)
                if not nums:
                    continue
                if filters and not self._row_matches_filters(cell.row_text, filters):
                    continue
                values.append((nums[0], cell))

        if not values:
            return self._answer_lookup(question, table_id, trace)
        number = sum(value for value, _ in values)
        if mode == "avg":
            number = number / len(values)
        unit = values[0][1].unit
        answer = append_unit_if_needed(_format_float(number), unit)
        trace["target_header"] = target_header
        trace["filters"] = filters
        trace["count"] = len(values)
        trace["top_candidates"] = self._trace_cells([(value, cell) for value, cell in values[:8]])
        return answer, trace

    def _retrieve_cells(self, question: str, table_id: Optional[int], limit: int = 20):
        q_counter = token_counter(question)
        chunks = query_chunks(question)
        candidates: List[Tuple[float, CellRecord]] = []
        pool = self.cells_by_table.get(int(table_id), []) if table_id else self.cells
        for cell in pool:
            score = self._score_tokens(q_counter, chunks, cell.context, cell.tokens)
            if score > 0:
                candidates.append((score, cell))
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[:limit]

    def _retrieve_rows(self, question: str, table_id: Optional[int], limit: int = 20):
        q_counter = token_counter(question)
        chunks = query_chunks(question)
        candidates: List[Tuple[float, RowRecord]] = []
        pool = self.rows_by_table.get(int(table_id), []) if table_id else self.rows
        for row in pool:
            score = self._score_tokens(q_counter, chunks, row.text, row.tokens)
            if score > 0:
                candidates.append((score, row))
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[:limit]

    def _score_tokens(
        self,
        q_counter: Counter,
        chunks: Iterable[str],
        text: str,
        doc_tokens: Dict[str, int],
    ) -> float:
        if not q_counter or not doc_tokens:
            return 0.0
        score = 0.0
        for token, q_count in q_counter.items():
            if token in doc_tokens:
                score += (len(token) + 1.0) * q_count * min(doc_tokens[token], 2)
        norm_text = normalize_text(text)
        for chunk in chunks:
            if chunk in norm_text:
                score += len(chunk) * 4.0
        return score / (math.sqrt(sum(doc_tokens.values())) + 1.0)

    def _target_header(self, question: str) -> str:
        q = normalize_text(question)
        generic_lookup_headers = {"项目", "内容", "名称"}
        matches = [header for header in KNOWN_HEADERS if header in q and header not in generic_lookup_headers]
        if matches:
            return max(matches, key=len)
        if "标准分" in q:
            return "标准分"
        if "指标值" in q:
            return "指标值"
        if "拟投入" in q or "投入资金" in q:
            return "拟投入的资金"
        if "金额" in q or "资金" in q:
            return "金额"
        if "描述" in q:
            return "描述"
        if "回复" in q or "回答" in q:
            return "回复"
        if "说明" in q:
            return "说明"
        if "状态" in q:
            return "资产状态"
        if "总分" in q:
            return "总分"
        if "销售量" in q:
            return "销售量"
        if "费用金额" in q:
            return "费用金额"
        if "支出金额" in q:
            return "支出金额"
        if "基金收入" in q:
            return "基金收入"
        if "基金支出" in q:
            return "基金支出"
        if "期末参保人数" in q:
            return "期末参保人数"
        if "月折旧率" in q:
            return "月折旧率"
        if "标准分占比" in q:
            return "标准分占比"
        for header in ["资产原值", "启用日期", "净残值", "总折旧额", "累计折旧额", "年折旧额", "月折旧额"]:
            if header in q:
                return header
        return ""

    def _numeric_target_header(self, question: str) -> str:
        q = normalize_text(question)
        for header in ["标准分", "指标值", "拟投入的资金", "预算金额", "金额", "百分比", "收入金额", "支出金额", "余额"]:
            if header in q:
                return header
        return self._target_header(question)

    def _count_target_header(self, question: str) -> str:
        q = normalize_text(question)
        for header in ["一级指标", "二级指标", "三级指标", "考核内容", "考核项目", "责任部门", "条目", "项目", "指标"]:
            if header in q:
                return header
        if "多少条" in q:
            return "考核内容"
        return ""

    def _list_target_header(self, question: str) -> str:
        q = normalize_text(question)
        if "什么项目" in q or "哪些项目" in q or "项目" in q:
            return "考核项目"
        if "什么指标" in q or "哪些指标" in q:
            return "三级指标"
        if "名称" in q:
            return "名称"
        return ""

    def _filter_chunks(self, question: str, target_header: str = "") -> List[str]:
        condition = self._parse_condition(question)
        filters: List[str] = []
        for chunk in query_chunks(question):
            if chunk in GENERIC_FILTERS:
                continue
            if target_header and (chunk in target_header or target_header in chunk):
                continue
            if condition and (chunk == condition[0] or chunk == condition[1]):
                continue
            if any(word in chunk for word in ["多少", "哪个", "哪些", "什么", "最高", "最低", "最大", "最小"]):
                continue
            filters.append(chunk)
        return dedupe_keep_order(filters)

    def _parse_condition(self, question: str) -> Optional[Tuple[str, str]]:
        match = re.search(r"([\u4e00-\u9fffA-Za-z0-9]+?)为\s*([<>≥≧≤=]?\s*\d+(?:\.\d+)?%?)", question)
        if match:
            return match.group(1), match.group(2).replace(" ", "")
        match = re.search(r"([\u4e00-\u9fffA-Za-z0-9]+?)是\s*([<>≥≧≤=]?\s*\d+(?:\.\d+)?%?)", question)
        if match:
            return match.group(1), match.group(2).replace(" ", "")
        return None

    def _cell_matches_header(self, cell: CellRecord, header: str) -> bool:
        if not header:
            return True
        header = normalize_text(header)
        context = normalize_text(" ".join([cell.above_text, cell.left_text]))
        if header in context:
            return True
        aliases = {
            "拟投入资金": ["拟投入的资金", "投入资金", "预算金额"],
            "拟投入的资金": ["拟投入资金", "投入资金", "预算金额"],
            "金额": ["预算金额", "拟投入的资金", "收入金额", "支出金额"],
            "考核内容": ["考核内容和方式", "考核内容"],
            "项目": ["考核项目", "名称", "项目"],
            "指标": ["一级指标", "二级指标", "三级指标"],
            "描述": ["描述", "说明", "甲方回复"],
            "回复": ["甲方回复", "回复", "答案"],
            "说明": ["说明", "描述", "甲方回复"],
            "总折旧额": ["累计折旧额"],
            "责任部门": ["责任部门"],
            "条目": ["序号", "问题", "条目"],
        }
        for alias in aliases.get(header, []):
            if alias in context:
                return True
        return False

    def _key_value_pair_boost(self, cell: CellRecord, target_header: str) -> float:
        if not target_header:
            return 0.0
        row_cells = self._cells_for_row(cell.table_id, cell.sheet_name, cell.row)
        for label_cell in row_cells:
            if normalize_text(label_cell.value) == normalize_text(target_header):
                if cell.col == label_cell.col + 1:
                    return 30.0
                if cell.col == label_cell.col:
                    return -30.0
        return 0.0

    def _implicit_answer_column_boost(self, cell: CellRecord, question: str) -> float:
        score = 0.0
        if self._cell_matches_header(cell, "回复") or self._cell_matches_header(cell, "描述"):
            score += 16.0
        if self._cell_matches_header(cell, "责任部门") and "责任部门" not in question:
            score -= 20.0
        if self._cell_matches_header(cell, "考核内容") and any(word in question for word in ["标准", "扣分", "加分"]):
            score += 10.0
        return score

    def _row_satisfies_condition(self, cell: CellRecord, condition: Tuple[str, str]) -> bool:
        header, expected = condition
        expected_numbers = extract_numbers(expected)
        for row_cell in self._cells_for_row(cell.table_id, cell.sheet_name, cell.row):
            if not self._cell_matches_header(row_cell, header):
                continue
            nums_cell = extract_numbers(row_cell.value)
            if expected_numbers and nums_cell:
                if abs(expected_numbers[0] - nums_cell[0]) < 1e-9:
                    return True
                continue
            if expected in row_cell.value:
                return True
        return False

    def _row_matches_filters(self, row_text: str, filters: List[str]) -> bool:
        if not filters:
            return True
        text = normalize_text(row_text)
        meaningful = [item for item in filters if len(item) >= 2 and item not in GENERIC_FILTERS]
        if not meaningful:
            return True
        required = meaningful[:3]
        return all(item in text for item in required)

    def _cell_row_matches_filters(self, cell: CellRecord, filters: List[str]) -> bool:
        if not filters:
            return True
        primary = normalize_text(" ".join([cell.title, cell.left_text]))
        fallback = normalize_text(cell.row_text)
        meaningful = [item for item in filters if len(item) >= 2 and item not in GENERIC_FILTERS]
        if not meaningful:
            return True
        return all(item in primary or item in fallback and item not in cell.value for item in meaningful[:3])

    def _text_matches_any_filter(self, text: str, filters: List[str]) -> bool:
        norm = normalize_text(text)
        meaningful = [item for item in filters if len(item) >= 2 and item not in GENERIC_FILTERS]
        if not meaningful:
            return True
        return any(item in norm for item in meaningful)

    def _wants_numeric(self, question: str) -> bool:
        if "是什么" in question and not any(word in question for word in ["多少", "金额", "资金", "标准分", "百分比", "率", "折旧", "原值"]):
            return False
        return any(word in question for word in NUMERIC_HINTS)

    def _looks_like_header(self, cell: CellRecord) -> bool:
        if not cell.value:
            return True
        if cell.value == cell.title:
            return True
        if cell.value in KNOWN_HEADERS:
            return True
        if cell.value in ["绩效指标", "预算整体情况", "基本信息", "合计", "内容"]:
            return True
        if cell.row <= 2 and len(extract_numbers(cell.value)) == 0:
            return True
        return False

    def _same_value_repeated_in_row(self, cell: CellRecord) -> bool:
        values = [item for item in cell.row_text.split(" | ") if item]
        return values.count(cell.value) >= 3

    def _cells_for_row(self, table_id: int, sheet_name: str, row: int) -> List[CellRecord]:
        return [
            cell
            for cell in self.cells_by_table.get(int(table_id), [])
            if cell.sheet_name == sheet_name and cell.row == row
        ]

    def _probable_label_cells(self, row_cells: List[CellRecord]) -> List[CellRecord]:
        labels = [
            cell
            for cell in row_cells
            if not cell.is_numeric_like and not self._looks_like_header(cell) and len(cell.value) <= 30
        ]
        return labels[:2] if labels else row_cells[:1]

    def _best_row_label(self, row_cells: List[CellRecord], answer_header: str, question: str) -> Optional[CellRecord]:
        matches = [cell for cell in row_cells if self._cell_matches_header(cell, answer_header)]
        matches = [cell for cell in matches if cell.value and cell.value not in question and not self._looks_like_header(cell)]
        if matches:
            return matches[0]
        labels = self._probable_label_cells(row_cells)
        return labels[0] if labels else None

    def _trace_cells(self, scored_cells: Iterable[Tuple[float, CellRecord]]) -> List[Dict[str, Any]]:
        result = []
        for score, cell in scored_cells:
            result.append(
                {
                    "score": round(float(score), 4),
                    "table_id": cell.table_id,
                    "sheet": cell.sheet_name,
                    "row": cell.row,
                    "col": cell.col,
                    "value": cell.answer,
                    "row_text": cell.row_text[:180],
                }
            )
        return result

    def _extract_inline_answer(self, question: str, text: str) -> str:
        if "扣" in question:
            match = re.search(r"扣\s*([0-9]+(?:\.[0-9]+)?)\s*分", text)
            if match:
                return match.group(1)
        if "加" in question and "分" in question:
            match = re.search(r"加\s*([0-9]+(?:\.[0-9]+)?)\s*分", text)
            if match:
                return match.group(1)
        return ""

    def _answer_cumulative_depreciation(self, table_id: Optional[int], trace: Dict[str, Any]) -> str:
        candidates = []
        for tid in ([table_id] if table_id else sorted(self.cells_by_table)):
            for cell in self.cells_by_table.get(int(tid), []):
                if not self._cell_matches_header(cell, "累计折旧额"):
                    continue
                numbers = extract_numbers(cell.value)
                if numbers:
                    candidates.append((numbers[0], cell))
        if not candidates:
            return ""
        value, cell = max(candidates, key=lambda item: item[0])
        trace["top_candidates"] = self._trace_cells([(value, cell)])
        return cell.value

    def _answer_section_count(
        self,
        question: str,
        table_id: Optional[int],
        target_header: str,
        filters: List[str],
        trace: Dict[str, Any],
    ) -> str:
        if target_header not in {"责任部门", "条目"} and "条目" not in question:
            return ""
        section_filter = self._section_filter(filters)
        if not section_filter:
            return ""

        rows = self.rows_by_table.get(int(table_id), []) if table_id else self.rows
        current_section = ""
        departments: List[str] = []
        item_count = 0
        evidence: List[CellRecord] = []

        for row in sorted(rows, key=lambda item: (item.table_id, item.sheet_name, item.row)):
            values = [value for value in row.values if value]
            unique_values = dedupe_keep_order(values)
            if not unique_values:
                continue
            if len(unique_values) == 1 and re.match(r"^[一二三四五六七八九十]+、", unique_values[0]):
                current_section = unique_values[0]
                continue
            if section_filter not in current_section:
                continue
            if unique_values[0] in {"投标单位问题", "甲方回复", "责任部门"}:
                continue
            if len(unique_values) == 1:
                continue
            item_count += 1
            row_cells = self._cells_for_row(row.table_id, row.sheet_name, row.row)
            if row_cells:
                evidence.append(row_cells[0])
            if target_header == "责任部门":
                dept = row.values[-1] if row.values else ""
                for part in re.split(r"[、,，/；;和]+", dept):
                    part = part.strip()
                    if part:
                        departments.append(part)

        trace["section_filter"] = section_filter
        trace["top_candidates"] = self._trace_cells([(0, cell) for cell in evidence[:5]])
        if target_header == "责任部门":
            return str(len(dedupe_keep_order(departments))) if departments else ""
        return str(item_count) if item_count else ""

    def _section_filter(self, filters: List[str]) -> str:
        known = ["招标文件", "工程范围", "图纸技术", "工程现场", "招标清单", "招标人补充"]
        for item in filters:
            for section in known:
                if section in item or item in section:
                    return section
        return ""


def _format_float(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.6g}"
