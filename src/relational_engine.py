import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .table_indexer import RowRecord
from .text_utils import dedupe_keep_order, extract_numbers, normalize_text, query_chunks, weighted_overlap


@dataclass
class RelationalResult:
    answer: str
    confidence: float
    reason: str


@dataclass
class TableView:
    table_id: int
    sheet_name: str
    title: str
    headers: List[str]
    records: List[Dict[str, str]]
    first_header: str
    month_headers: List[str]

    @property
    def is_wide_month_table(self) -> bool:
        return len(self.month_headers) >= 2 and bool(self.first_header)


class RelationalAnswerEngine:
    """针对规则关系表和月份宽表的高置信度执行器。"""

    def __init__(self, rows_by_table: Dict[int, List[RowRecord]]):
        self.rows_by_table = rows_by_table
        self._cache: Dict[int, List[TableView]] = {}

    def answer(self, question: str, table_id: Optional[int]) -> Optional[RelationalResult]:
        if table_id is None:
            return None
        views = self._views_for_table(int(table_id))
        if not views:
            return None

        q = normalize_text(question)
        for view in views:
            if view.is_wide_month_table:
                result = self._answer_wide_table(question, q, view)
                if result:
                    return result

        for view in views:
            result = self._answer_regular_table(question, q, view)
            if result:
                return result
        return None

    def _views_for_table(self, table_id: int) -> List[TableView]:
        if table_id in self._cache:
            return self._cache[table_id]
        rows = self.rows_by_table.get(table_id, [])
        by_sheet: Dict[str, List[RowRecord]] = {}
        for row in rows:
            by_sheet.setdefault(row.sheet_name, []).append(row)

        views: List[TableView] = []
        for sheet_name, sheet_rows in by_sheet.items():
            sheet_rows = sorted(sheet_rows, key=lambda item: item.row)
            header_pos = self._detect_header_row(sheet_rows)
            if header_pos is None:
                continue
            headers, data_start = self._build_headers(sheet_rows, header_pos)
            if len(headers) < 2:
                continue
            records = []
            current_section = sheet_rows[0].title if sheet_rows else ""
            for row in sheet_rows[data_start:]:
                values = row.values[: len(headers)]
                non_empty = [v for v in values if v]
                if len(non_empty) == 1:
                    current_section = non_empty[0]
                    continue
                if len(non_empty) < 2:
                    continue
                if self._is_repeated_title_row(values):
                    current_section = non_empty[0]
                    continue
                record = {headers[idx]: values[idx] if idx < len(values) else "" for idx in range(len(headers))}
                record["__row_text__"] = " | ".join(v for v in values if v)
                record["__row_index__"] = str(row.row)
                record["__section__"] = current_section
                records.append(record)
            if records:
                views.append(
                    TableView(
                        table_id=table_id,
                        sheet_name=sheet_name,
                        title=sheet_rows[0].title if sheet_rows else "",
                        headers=headers,
                        records=records,
                        first_header=headers[0],
                        month_headers=[h for h in headers if _is_month_header(h)],
                    )
                )
        self._cache[table_id] = views
        return views

    def _detect_header_row(self, rows: List[RowRecord]) -> Optional[int]:
        best_idx = None
        best_score = -1
        for idx, row in enumerate(rows[:8]):
            values = [v for v in row.values if v]
            if len(values) < 2 or self._is_repeated_title_row(values):
                continue
            text_count = sum(1 for v in values if not extract_numbers(v) or _is_headerish(v))
            known_hits = sum(1 for v in values if _is_headerish(v))
            score = text_count + known_hits * 3 + len(set(values))
            if score > best_score:
                best_score = score
                best_idx = idx
        return best_idx

    def _build_headers(self, rows: List[RowRecord], header_pos: int) -> Tuple[List[str], int]:
        current = rows[header_pos].values
        headerless = _headerless_text_headers(current)
        if headerless:
            return _unique_headers(headerless), header_pos
        headers = [str(v).strip() if v else f"col_{i + 1}" for i, v in enumerate(current)]
        data_start = header_pos + 1
        if data_start < len(rows):
            nxt = rows[data_start].values
            current_headerish = sum(1 for v in current if v and _is_headerish(v))
            current_blanks = sum(1 for v in current if not v)
            next_headerish = sum(1 for v in nxt if v and (_is_headerish(v) or len(v) <= 4))
            next_numeric = sum(1 for v in nxt if extract_numbers(v))
            next_looks_like_data = current_headerish >= 2 and next_numeric > 0
            should_merge_subheader = (
                next_headerish >= 2
                and next_numeric <= 2
                and not next_looks_like_data
                and (current_blanks > 0 or current_headerish <= 1)
                and not self._is_repeated_title_row(nxt)
            )
            if should_merge_subheader:
                merged = []
                for idx, header in enumerate(headers):
                    sub = nxt[idx] if idx < len(nxt) else ""
                    merged.append(_merge_header(header, sub))
                headers = merged
                data_start += 1
        return _unique_headers(headers), data_start

    def _answer_wide_table(self, question: str, q: str, view: TableView) -> Optional[RelationalResult]:
        month_headers = self._select_month_headers(q, view.month_headers)
        row_matches = _detail_records(self._matching_records(question, view, include_numeric=False))
        if not row_matches:
            return None

        if any(word in q for word in ["几个月", "多少个月"]):
            return RelationalResult(str(len(view.month_headers)), 0.9, "wide_count_months")

        if any(word in q for word in ["几种", "多少种", "公司一共有"]):
            return RelationalResult(str(len(row_matches)), 0.9, "wide_count_records")

        threshold = self._threshold(question)
        if threshold is not None and month_headers:
            values = []
            for record in _detail_records(view.records):
                nums = extract_numbers(record.get(month_headers[0], ""))
                if nums and nums[0] > threshold:
                    values.append(record.get(view.first_header, ""))
            if values:
                return RelationalResult("，".join(dedupe_keep_order(values)), 0.92, "wide_threshold_list")

        if "多少倍" in q and month_headers:
            entities = self._entities_in_question(q, view)
            if len(entities) >= 2:
                a = self._find_record_by_entity(view, entities[0])
                b = self._find_record_by_entity(view, entities[1])
                if a and b:
                    av = _first_number(a.get(month_headers[0], ""))
                    bv = _first_number(b.get(month_headers[0], ""))
                    if av is not None and bv:
                        return RelationalResult(_format_number(av / bv), 0.92, "wide_ratio")

        if any(word in q for word in ["减少", "增加", "相比", "差"]) and len(month_headers) >= 2:
            record = row_matches[0]
            nums = [_first_number(record.get(header, "")) for header in month_headers[:2]]
            if nums[0] is not None and nums[1] is not None:
                return RelationalResult(_format_number(abs(nums[1] - nums[0])), 0.9, "wide_difference")

        if any(word in q for word in ["总和", "总计", "合计", "整个", "上半年", "前三个月"]):
            record = row_matches[0]
            headers = month_headers or view.month_headers
            values = [_first_number(record.get(header, "")) for header in headers]
            values = [v for v in values if v is not None]
            if values:
                return RelationalResult(_format_number(sum(values)), 0.88, "wide_sum")

        if any(word in q for word in ["最高", "最低", "最大", "最小"]):
            record = row_matches[0]
            headers = month_headers or view.month_headers
            pairs = [(header, _first_number(record.get(header, ""))) for header in headers]
            pairs = [(h, v) for h, v in pairs if v is not None]
            if pairs:
                pick_min = any(word in q for word in ["最低", "最小"])
                header, value = min(pairs, key=lambda x: x[1]) if pick_min else max(pairs, key=lambda x: x[1])
                if "多少" in q:
                    return RelationalResult(_format_number(value), 0.86, "wide_extreme_value")
                return RelationalResult(_month_label(header), 0.9, "wide_extreme_month")

        if month_headers:
            record = row_matches[0]
            value = record.get(month_headers[0], "")
            if value:
                return RelationalResult(value, 0.82, "wide_lookup")
        return None

    def _answer_regular_table(self, question: str, q: str, view: TableView) -> Optional[RelationalResult]:
        multi_entity = self._answer_multi_entity(q, view)
        if multi_entity:
            return multi_entity

        records = self._filter_records(question, q, view)
        if not records:
            return None

        if "总结" in q and "报销" in q:
            summary = self._summarize_reimbursement(q, view, records)
            if summary:
                return RelationalResult(summary, 0.9, "reimbursement_summary")

        target = self._target_header(q, view)
        if any(word in q for word in ["有多少", "多少笔", "多少条", "多少次", "几个", "计数"]):
            count_records = _detail_records(records)
            distinct_header = self._count_distinct_header(q, view)
            if distinct_header:
                values = [r.get(distinct_header, "") for r in count_records if r.get(distinct_header, "")]
                return RelationalResult(str(len(dedupe_keep_order(values))), 0.86, "regular_distinct_field_count")
            if any(word in q for word in ["几种", "哪些"]):
                answer_header = self._answer_header(q, view, target)
                values = [r.get(answer_header, "") for r in count_records if r.get(answer_header, "")]
                return RelationalResult(str(len(dedupe_keep_order(values))), 0.82, "regular_distinct_count")
            return RelationalResult(str(len(count_records)), 0.86, "regular_count")

        if "平均" in q:
            target = self._numeric_header(q, view)
            avg = self._average_records(records, target)
            if avg is not None:
                return RelationalResult(_format_number(avg), 0.88, "regular_average")

        if any(word in q for word in ["总和", "总计", "总额", "总金额", "总收入", "总支出", "总费用", "总成本", "总工龄工资", "总人数", "合计"]) or (
            "共" in q and any(word in q for word in ["多少金额", "支出", "采购"])
        ):
            if not target or not self._header_has_numbers(view, target):
                target = self._numeric_header(q, view)
            total = self._sum_records(records, target)
            if total is not None:
                return RelationalResult(_format_number(total), 0.9, "regular_sum")

        if any(word in q for word in ["差额", "相差", "多多少", "少多少"]):
            diff = self._difference_answer(q, view, records)
            if diff is not None:
                return RelationalResult(_format_number(diff), 0.88, "regular_difference")

        if any(word in q for word in ["最高", "最低", "最大", "最小", "最多", "最少"]):
            result = self._extreme_answer(q, view, records, target)
            if result:
                return result

        if any(word in q for word in ["哪些", "有哪些", "列出"]):
            answer_header = self._list_answer_header(q, view, target)
            values = [r.get(answer_header, "") for r in records if r.get(answer_header, "")]
            if values:
                return RelationalResult("，".join(dedupe_keep_order(values)), 0.84, "regular_list")

        if target:
            values = [r.get(target, "") for r in records if r.get(target, "")]
            if values:
                return RelationalResult("，".join(dedupe_keep_order(values)), 0.84, "regular_lookup")

        return None

    def _matching_records(self, question: str, view: TableView, include_numeric: bool = True) -> List[Dict[str, str]]:
        q = normalize_text(question)
        scored = []
        for record in view.records:
            text = " ".join(str(v) for k, v in record.items() if not k.startswith("__") and (include_numeric or not extract_numbers(v)))
            score = weighted_overlap(question, text)
            first_value = normalize_text(record.get(view.first_header, ""))
            if first_value and all(part in first_value for part in _entity_parts(q)):
                score += 8
            if score > 0:
                scored.append((score, record))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [record for _, record in scored]

    def _answer_multi_entity(self, q: str, view: TableView) -> Optional[RelationalResult]:
        candidates = _entity_candidates(q, view)
        multi_headers = [(header, values) for header, values in candidates.items() if len(values) >= 2]
        if not multi_headers:
            return None

        entity_header, entity_values = max(multi_headers, key=lambda item: len(item[1]))
        target_for_constraints = self._numeric_header(q, view)
        entity_keys = {_compact(value) for value in entity_values}
        constraints = {
            header: [value for value in values if _compact(value) not in entity_keys]
            for header, values in candidates.items()
            if header not in {entity_header, target_for_constraints} and values
        }
        constraints = {header: values for header, values in constraints.items() if values}
        base_records = view.records
        for header, values in constraints.items():
            matched = [
                record
                for record in base_records
                if any(_record_value_matches(record, header, value) for value in values)
            ]
            if matched:
                base_records = matched

        if any(word in q for word in ["有多少", "多少个", "多少项", "多少类", "多少条", "多少笔", "数量分别"]):
            counts = []
            for value in entity_values:
                records = [record for record in base_records if _record_value_matches(record, entity_header, value)]
                counts.append(len(_detail_records(records)))
            if "分别" in q:
                return RelationalResult("，".join(str(count) for count in counts), 0.88, "multi_entity_counts")
            if counts and any(word in q for word in ["总数", "总共", "一共"]):
                return RelationalResult(str(sum(counts)), 0.88, "multi_entity_count_sum")

        target = self._numeric_header(q, view)
        if not target:
            return None
        values: List[float] = []
        for value in entity_values:
            records = [record for record in base_records if _record_value_matches(record, entity_header, value)]
            total = self._sum_records(records, target)
            if total is not None:
                values.append(total)
        if len(values) < 2:
            return None

        if any(word in q for word in ["百分比", "占"]):
            if values[1] != 0:
                return RelationalResult(f"{_format_number(values[0] / values[1] * 100)}%", 0.9, "multi_entity_percentage")
        if "比例" in q:
            total = sum(values)
            if total:
                return RelationalResult("，".join(_format_ratio(value / total) for value in values), 0.9, "multi_entity_ratio")
        if "分别" in q:
            return RelationalResult("，".join(_format_number(value) for value in values), 0.9, "multi_entity_values")
        if any(word in q for word in ["总和", "总计", "总额", "总金额", "合计"]):
            return RelationalResult(_format_number(sum(values)), 0.9, "multi_entity_sum")
        if any(word in q for word in ["差异", "差额", "相差", "多多少", "少多少", "比"]):
            return RelationalResult(_format_number(abs(values[0] - values[1])), 0.88, "multi_entity_difference")
        return None

    def _filter_records(self, question: str, q: str, view: TableView) -> List[Dict[str, str]]:
        records = view.records
        section_matches = [
            section
            for section in dedupe_keep_order(record.get("__section__", "") for record in records)
            if section and _compact(section) in _compact(q)
        ]
        section_filtered = bool(section_matches)
        if section_filtered:
            records = [record for record in records if record.get("__section__") in section_matches]

        year, months, days = _date_parts(q)
        full_dates = _full_dates(q)
        date_headers = _date_headers(view)
        if full_dates and date_headers:
            records = [
                r
                for r in records
                if any(_record_date_value(r.get(header, "")) in full_dates for header in date_headers)
            ]
        elif date_headers and (months or days):
            records = [
                r
                for r in records
                if any(
                    (not months or _record_month_value(r.get(header, "")) in months)
                    and (not days or _record_day_value(r.get(header, "")) in days)
                    for header in date_headers
                )
            ]
        if not months:
            if "前三个月" in q:
                months = ["1", "2", "3"]
            elif "二季度" in q:
                months = ["4", "5", "6"]
            elif "上半年" in q:
                months = ["1", "2", "3", "4", "5", "6"]
        if year and any(_header_eq(h, "年") for h in view.headers):
            records = [r for r in records if str(int(_first_number(r.get(_find_header(view.headers, "年"), "0")) or 0)) == year]
        if months and any(_header_eq(h, "月") for h in view.headers):
            mh = _best_month_header(view, months)
            records = [r for r in records if _record_month_value(r.get(mh, "")) in months]
        if days and any(_header_eq(h, "日") for h in view.headers):
            dh = _find_header(view.headers, "日")
            records = [r for r in records if str(int(_first_number(r.get(dh, "0")) or -1)) in days]

        condition = self._condition(q, view)
        if condition:
            header, op, number = condition
            records = [r for r in records if _compare(_first_number(r.get(header, "")), op, number)]

        if "转账" in q:
            type_header = _find_header(view.headers, "类型")
            if type_header:
                matched = [r for r in records if normalize_text(r.get(type_header, "")) in {"转", "转账"}]
                if matched:
                    records = matched

        for header, term in _column_value_filters(q, view):
            matched = [r for r in records if term in normalize_text(r.get(header, ""))]
            if matched:
                records = matched

        for term in _topic_terms(q):
            matched = [
                r
                for r in records
                if term in normalize_text(r.get("__row_text__", ""))
                or term in normalize_text(r.get("__row_text__", "")).replace("的", "")
            ]
            if matched:
                records = matched

        for term in _exact_value_terms(question, q, view)[:5]:
            matched = [r for r in records if term in normalize_text(r.get("__row_text__", ""))]
            if matched:
                records = matched

        if not section_filtered:
            chunks = [c for c in query_chunks(question) if len(c) >= 2]
            filter_chunks = [c for c in chunks if not any(c in normalize_text(h) or normalize_text(h) in c for h in view.headers)]
            filter_chunks = [c for c in filter_chunks if not _looks_like_time_or_query_word(c)]
            for chunk in filter_chunks[:4]:
                matched = [r for r in records if chunk in normalize_text(r.get("__row_text__", ""))]
                if matched:
                    records = matched
        return records

    def _target_header(self, q: str, view: TableView) -> str:
        headers = sorted(view.headers, key=len, reverse=True)
        generic = {"项目", "内容", "名称", "序号", "月份", "年", "月", "日"}
        priority_aliases = [
            ("办公电话", ["电话", "联系方式"]),
            ("电话号码", ["电话", "联系方式"]),
            ("邮箱", ["邮箱", "电子邮箱"]),
            ("负责老师", ["负责老师", "老师"]),
            ("就业联系人", ["负责老师", "老师", "联系人"]),
            ("税率", ["税率"]),
            ("发票号码", ["发票号码", "发票号"]),
            ("人数", ["人数", "学生人数", "总人数"]),
            ("单价", ["单价"]),
            ("基本工资", ["基本工资"]),
            ("工龄工资", ["工龄工资"]),
            ("借方金额", ["借方金额"]),
            ("贷方金额", ["贷方金额"]),
            ("借方", ["借方"]),
            ("贷方", ["贷方"]),
            ("费用金额", ["费用金额", "金额", "报销"]),
            ("成本金额", ["成本金额", "成本"]),
            ("支出金额", ["支出金额", "支出"]),
            ("收入金额", ["收入金额", "收入"]),
            ("发生金额", ["发生金额"]),
            ("销售金额", ["销售金额"]),
            ("姓名", ["姓名", "员工", "哪位", "谁"]),
        ]
        if "科目" in q and not any(word in q for word in ["借方", "贷方", "金额", "总额", "总金额"]):
            header = _find_header(view.headers, "科目")
            if header:
                return header
        for header in headers:
            nh = normalize_text(header)
            if "人数" in nh:
                place = _header_place_key(nh)
                if place and place in q:
                    return header
        for canonical, words in priority_aliases:
            header = _find_header(view.headers, canonical)
            if header and any(word in q for word in words):
                return header
        if "描述" in q or "说明" in q:
            return _long_text_header(view, exclude={view.first_header})
        for header in headers:
            nh = normalize_text(header)
            if nh and nh not in generic and nh in q:
                return header
        aliases = [
            ("费用金额", ["费用金额", "金额", "报销"]),
            ("成本金额", ["成本金额", "成本"]),
            ("支出金额", ["支出金额", "支出"]),
            ("收入金额", ["收入金额", "收入"]),
            ("金额", ["金额", "总收入", "总支出"]),
            ("数量", ["数量", "人数"]),
            ("借方", ["借方金额", "借方"]),
            ("贷方", ["贷方金额", "贷方"]),
            ("单价", ["单价"]),
            ("姓名", ["姓名", "员工", "哪位", "谁"]),
            ("专业", ["专业"]),
            ("负责老师", ["负责老师", "老师"]),
            ("办公电话", ["电话", "联系方式"]),
            ("内容说明", ["哪一次", "报销记录", "内容说明"]),
            ("摘要", ["摘要", "哪笔", "哪一项"]),
            ("科目名称", ["科目名称"]),
            ("科目", ["科目"]),
            ("部门", ["部门"]),
        ]
        for canonical, words in aliases:
            header = _find_header(view.headers, canonical)
            if header and any(word in q for word in words):
                return header
        return ""

    def _count_distinct_header(self, q: str, view: TableView) -> str:
        if "负责老师" in q or "老师" in q:
            return _find_header(view.headers, "负责老师") or _find_header(view.headers, "就业联系人")
        if "专业" in q:
            return _find_header(view.headers, "专业")
        if "服务子项" in q or "子项" in q:
            return _find_header(view.headers, "服务子项")
        if "指标类型" in q:
            return _find_header(view.headers, "指标类型")
        if "指标名称" in q:
            return _find_header(view.headers, "指标名称")
        return ""

    def _numeric_header(self, q: str, view: TableView) -> str:
        target = self._target_header(q, view)
        if target and self._header_has_numbers(view, target):
            return target
        numeric_headers = [h for h in view.headers if self._header_has_numbers(view, h)]
        preferred = [
            "金额",
            "费用金额",
            "成本金额",
            "支出金额",
            "收入金额",
            "发生金额",
            "借方金额",
            "贷方金额",
            "销售金额",
            "数量",
            "人数",
            "单价",
            "基本工资",
            "工龄工资",
            "借方",
            "贷方",
        ]
        if any(word in q for word in ["金额", "总额", "总收入", "总支出", "费用", "成本", "收入", "支出"]):
            for name in preferred:
                h = _find_header(numeric_headers, name)
                if h:
                    return h
        for h in numeric_headers:
            if normalize_text(h) in q and not _is_generic_numeric_header(h):
                return h
        for name in preferred:
            h = _find_header(numeric_headers, name)
            if h:
                return h
        return numeric_headers[0] if numeric_headers else ""

    def _answer_header(self, q: str, view: TableView, target: str = "") -> str:
        preferences = []
        if "产品" in q:
            preferences = ["产品名称", "名称与规格", "名称", "品名"]
        elif "员工" in q or "哪位" in q or "谁" in q:
            preferences = ["姓名", "员工", "人员", "报销人", "负责人"]
        elif "专业" in q:
            preferences = ["专业", "专业名称"]
        elif "项目" in q:
            preferences = ["名称说明", "项目", "评审项目", "服务子项", "货品名称", "费用支出项目", "项目内容"]
        elif "科目" in q:
            preferences = ["科目", "科目名称", "总账科目", "明细科目"]
        elif "供应商" in q:
            preferences = ["供应商名称", "供应商", "单位名称"]
        elif "部门" in q:
            preferences = ["部门", "产生部门", "使用部门", "所在部门"]
        elif "负责人" in q:
            preferences = ["负责人"]
        elif "老师" in q:
            preferences = ["负责老师", "老师"]
        elif "哪一次" in q or "哪笔" in q or "哪一项" in q or "摘要" in q:
            preferences = ["内容说明", "摘要", "名称说明", "科目名称", view.first_header]
        elif "凭证号" in q:
            preferences = ["凭证号"]
        elif "来源" in q:
            preferences = ["摘要", "收入来源", "来源"]
        else:
            preferences = [view.first_header, "名称说明", "摘要", "内容说明", "科目名称"]
        for pref in preferences:
            h = _find_header(view.headers, pref)
            if h and h != target:
                return h
        return view.first_header

    def _list_answer_header(self, q: str, view: TableView, target: str = "") -> str:
        if "费用类型" in q or "费用监控类型" in q:
            header = _find_header(view.headers, "费用监控类型")
            if header:
                return header
        if "项目" in q:
            header = (
                _find_header(view.headers, "项目")
                or _find_header(view.headers, "名称说明")
                or _find_header(view.headers, "评审项目")
                or _find_header(view.headers, "项目内容")
            )
            if header:
                return header
        if "部门" in q:
            header = _find_header(view.headers, "部门") or _find_header(view.headers, "使用部门")
            if header:
                return header
        if "供应商" in q:
            header = _find_header(view.headers, "供应商名称") or _find_header(view.headers, "供应商")
            if header:
                return header
        explicit_targets = [
            ("服务子项", ["服务子项", "子项"]),
            ("评审项目", ["子项目", "评审项目"]),
            ("课程主题", ["课程", "课程主题"]),
            ("专业", ["专业"]),
            ("内容", ["内容", "步骤", "流程", "方法", "情形", "包括"]),
            ("适用情形", ["适用", "情形"]),
            ("描述", ["描述"]),
            ("备注", ["备注"]),
        ]
        for canonical, words in explicit_targets:
            if any(word in q for word in words):
                header = _find_header(view.headers, canonical)
                if header:
                    return header
        if target and _is_text_answer_header(target):
            return target
        if any(word in q for word in ["内容", "步骤", "流程", "方法", "情形", "包括", "有关", "涉及"]):
            return _long_text_header(view, exclude={target, view.first_header})
        return self._answer_header(q, view, target)

    def _sum_records(self, records: List[Dict[str, str]], target: str) -> Optional[float]:
        if not target:
            return None
        records = _detail_records(records)
        values = [_first_number(r.get(target, "")) for r in records]
        values = [v for v in values if v is not None]
        return sum(values) if values else None

    def _average_records(self, records: List[Dict[str, str]], target: str) -> Optional[float]:
        if not target:
            return None
        records = _detail_records(records)
        values = [_first_number(r.get(target, "")) for r in records]
        values = [v for v in values if v is not None]
        return sum(values) / len(values) if values else None

    def _difference_answer(self, q: str, view: TableView, records: List[Dict[str, str]]) -> Optional[float]:
        numeric_headers = [h for h in view.headers if self._header_has_numbers(view, h)]
        mentioned = [h for h in numeric_headers if normalize_text(h) in q]
        if len(mentioned) >= 2:
            a = self._sum_records(records, mentioned[0])
            b = self._sum_records(records, mentioned[1])
            if a is not None and b is not None:
                return abs(a - b)
        return None

    def _extreme_answer(self, q: str, view: TableView, records: List[Dict[str, str]], target: str) -> Optional[RelationalResult]:
        target = target if target and self._header_has_numbers(view, target) else self._numeric_header(q, view)
        if not target:
            return None
        records = _detail_records(records)
        pairs = []
        for record in records:
            value = _first_number(record.get(target, ""))
            if value is not None:
                pairs.append((value, record))
        if not pairs:
            return None
        pick_min = any(word in q for word in ["最低", "最小", "最少"])
        value, record = min(pairs, key=lambda item: item[0]) if pick_min else max(pairs, key=lambda item: item[0])
        if "多少" in q:
            return RelationalResult(_format_number(value), 0.88, "regular_extreme_value")
        answer_header = self._answer_header(q, view, target)
        answer = record.get(answer_header, "")
        return RelationalResult(answer or _format_number(value), 0.86, "regular_extreme_label")

    def _summarize_reimbursement(self, q: str, view: TableView, records: List[Dict[str, str]]) -> str:
        name_header = _find_header(view.headers, "报销人")
        type_header = _find_header(view.headers, "报销类型")
        amount_header = _find_header(view.headers, "费用金额")
        status_header = _find_header(view.headers, "状态")
        if not (name_header and type_header and amount_header):
            return ""
        person = ""
        for record in records:
            if normalize_text(record.get(name_header, "")) in q:
                person = record.get(name_header, "")
                break
        if not person:
            return ""
        total = self._sum_records(records, amount_header)
        types = dedupe_keep_order([r.get(type_header, "") for r in records if r.get(type_header, "")])
        paid = len([r for r in records if "已报销" in r.get(status_header, "")])
        unpaid = len(records) - paid
        return f"{person}共有{len(records)}次{ '、'.join(types) }报销，费用总额为{_format_number(total or 0)}元，其中{paid}次已报销，{unpaid}次未报销。"

    def _select_month_headers(self, q: str, month_headers: List[str]) -> List[str]:
        _, months, _ = _date_parts(q)
        if "前三个月" in q:
            months = ["1", "2", "3"]
        elif "二季度" in q:
            months = ["4", "5", "6"]
        elif "上半年" in q or "所有月份" in q:
            return month_headers
        selected = [h for h in month_headers if _month_number(h) in months]
        if "相比" in q and len(selected) == 2:
            return selected
        return selected

    def _entities_in_question(self, q: str, view: TableView) -> List[str]:
        entities = []
        for record in view.records:
            entity = record.get(view.first_header, "")
            normalized = normalize_text(entity)
            if entity and normalized in q:
                entities.append((q.find(normalized), entity))
        entities.sort(key=lambda item: item[0])
        return [entity for _, entity in entities]

    def _find_record_by_entity(self, view: TableView, entity: str) -> Optional[Dict[str, str]]:
        n = normalize_text(entity)
        for record in view.records:
            if normalize_text(record.get(view.first_header, "")) == n:
                return record
        return None

    def _threshold(self, question: str) -> Optional[float]:
        match = re.search(r"超过\s*([0-9]+(?:\.[0-9]+)?)", question)
        return float(match.group(1)) if match else None

    def _condition(self, q: str, view: TableView) -> Optional[Tuple[str, str, float]]:
        for header in view.headers:
            nh = normalize_text(header)
            if not nh:
                continue
            match = re.search(re.escape(nh) + r"(?:为|是|大于|超过|小于|低于)了?\s*([0-9]+(?:\.[0-9]+)?)", q)
            if match:
                op = ">"
                if "小于" in match.group(0) or "低于" in match.group(0):
                    op = "<"
                elif "为" in match.group(0) or "是" in match.group(0):
                    op = "="
                return header, op, float(match.group(1))
        return None

    def _header_has_numbers(self, view: TableView, header: str) -> bool:
        return any(_first_number(r.get(header, "")) is not None for r in view.records)

    def _is_repeated_title_row(self, values: List[str]) -> bool:
        vals = [v for v in values if v]
        return len(vals) > 1 and len(set(vals)) == 1


def _unique_headers(headers: List[str]) -> List[str]:
    counts: Dict[str, int] = {}
    result = []
    for idx, header in enumerate(headers):
        header = header.strip() or f"col_{idx + 1}"
        counts[header] = counts.get(header, 0) + 1
        result.append(header if counts[header] == 1 else f"{header}_{counts[header]}")
    return result


def _headerless_text_headers(values: List[str]) -> List[str]:
    vals = [str(v).strip() for v in values]
    non_empty = [v for v in vals if v]
    if len(non_empty) < 3:
        return []
    first, second = vals[0], vals[1] if len(vals) > 1 else ""
    if first in {"总体项目", "具体项目"} and _first_number(second) is not None:
        return ["类别", "序号", "项目", "内容"] + [f"补充{i}" for i in range(1, max(0, len(vals) - 4) + 1)]
    if _first_number(first) is not None and len(vals) >= 4 and len(vals[1]) >= 2 and len(vals[2]) >= 8:
        return ["序号", "项目", "内容", "适用情形"] + [f"补充{i}" for i in range(1, max(0, len(vals) - 4) + 1)]
    return []


def _merge_header(top: str, sub: str) -> str:
    top = top.strip()
    sub = sub.strip()
    if not sub or sub == top:
        return top
    if re.fullmatch(r"\d{4}年", top) and sub in {"月", "日"}:
        return sub
    if top in {"凭证", "2013年"}:
        return sub
    return sub if len(sub) <= 4 and _is_headerish(sub) else f"{top}_{sub}"


def _is_text_answer_header(header: str) -> bool:
    text = normalize_text(header)
    return any(word in text for word in ["内容", "说明", "描述", "备注", "子项", "项目", "课程", "专业", "情形", "流程"])


def _long_text_header(view: TableView, exclude: Optional[set] = None) -> str:
    exclude = exclude or set()
    candidates = []
    for header in view.headers:
        if header in exclude or _is_generic_numeric_header(header):
            continue
        values = [record.get(header, "") for record in view.records if record.get(header, "")]
        if not values:
            continue
        avg_len = sum(len(str(value)) for value in values) / len(values)
        text_ratio = sum(1 for value in values if not extract_numbers(value) or len(str(value)) > 8) / len(values)
        name_bonus = 8 if _is_text_answer_header(header) else 0
        candidates.append((avg_len * text_ratio + name_bonus, header))
    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][1]
    return view.headers[-1] if view.headers else ""


def _is_headerish(value: str) -> bool:
    return any(
        word in str(value)
        for word in [
            "序号",
            "编号",
            "日期",
            "部门",
            "人员",
            "报销",
            "类型",
            "金额",
            "状态",
            "内容",
            "摘要",
            "科目",
            "月份",
            "项目",
            "名称",
            "数量",
            "借方",
            "贷方",
            "收入",
            "支出",
            "成本",
            "利润",
            "凭证",
            "单位",
        ]
    ) or _is_month_header(str(value))


def _is_month_header(value: str) -> bool:
    return bool(re.fullmatch(r"(?:第)?\d{1,2}月份?", normalize_text(value)))


def _month_number(value: str) -> str:
    match = re.search(r"(\d{1,2})月", normalize_text(value))
    return match.group(1) if match else ""


def _month_label(value: str) -> str:
    num = _month_number(value)
    return f"{num}月" if num else value


def _date_parts(q: str) -> Tuple[str, List[str], List[str]]:
    year_match = re.search(r"(\d{4})年", q)
    months = re.findall(r"(\d{1,2})月份?", q)
    days = re.findall(r"(\d{1,2})日", q)
    cn_months = {
        "一": "1",
        "二": "2",
        "三": "3",
        "四": "4",
        "五": "5",
        "六": "6",
        "七": "7",
        "八": "8",
        "九": "9",
        "十": "10",
        "十一": "11",
        "十二": "12",
    }
    for cn, number in cn_months.items():
        if f"{cn}月份" in q or f"{cn}月" in q:
            months.append(number)
    return year_match.group(1) if year_match else "", months, days


def _full_dates(q: str) -> List[str]:
    dates = []
    for y, m, d in re.findall(r"(\d{4})年(\d{1,2})月(\d{1,2})日", q):
        dates.append(f"{int(y):04d}-{int(m):02d}-{int(d):02d}")
    for y, m, d in re.findall(r"(\d{4})-(\d{1,2})-(\d{1,2})", q):
        dates.append(f"{int(y):04d}-{int(m):02d}-{int(d):02d}")
    return dedupe_keep_order(dates)


def _date_headers(view: TableView) -> List[str]:
    headers = []
    for header in view.headers:
        nh = normalize_text(header)
        if "日期" in nh or "时间" in nh or "月份" in nh:
            if any(_record_date_value(record.get(header, "")) for record in view.records):
                headers.append(header)
    return headers


def _record_date_value(value: str) -> str:
    text = normalize_text(value)
    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if match:
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    return ""


def _best_month_header(view: TableView, months: List[str]) -> str:
    candidates = [h for h in view.headers if _header_eq(h, "月")]
    if not candidates:
        return ""

    def score(header: str) -> int:
        total = 0
        for record in view.records:
            value = _record_month_value(record.get(header, ""))
            if value in months:
                total += 2
            elif value:
                total += 1
        return total

    return max(candidates, key=score)


def _record_month_value(value: str) -> str:
    text = normalize_text(value)
    date_match = re.search(r"\d{4}-(\d{1,2})-\d{1,2}", text)
    if date_match:
        return str(int(date_match.group(1)))
    month = _month_number(text)
    if month:
        return month
    number = _first_number(text)
    if number is not None and 1 <= int(number) <= 12 and "季度" not in text:
        return str(int(number))
    return ""


def _record_day_value(value: str) -> str:
    text = normalize_text(value)
    date_match = re.search(r"\d{4}-\d{1,2}-(\d{1,2})", text)
    if date_match:
        return str(int(date_match.group(1)))
    day_match = re.search(r"(\d{1,2})日", text)
    return str(int(day_match.group(1))) if day_match else ""


def _column_value_filters(q: str, view: TableView) -> List[Tuple[str, str]]:
    filters: List[Tuple[str, str]] = []
    priority_headers = [
        "学院",
        "学历",
        "专业",
        "品种",
        "类型",
        "部门",
        "负责人",
        "负责老师",
        "就业联系人",
        "供应商名称",
        "科目",
        "代码",
        "编号",
        "姓名",
    ]
    seen = set()
    for name in priority_headers:
        header = _find_header(view.headers, name)
        if not header or header in seen:
            continue
        seen.add(header)
        if name == "学历":
            for phrase, term in [("研究生", "研究生"), ("本科生", "本科"), ("本科", "本科"), ("专科生", "专科"), ("专科", "专科")]:
                if phrase in q:
                    filters.append((header, term))
                    break
            continue
        values = sorted(
            {
                normalize_text(record.get(header, ""))
                for record in view.records
                if normalize_text(record.get(header, ""))
            },
            key=len,
            reverse=True,
        )
        for value in values:
            base = _entity_base(value)
            if len(base) >= 2 and base in q:
                filters.append((header, base))
                break
            if "学院" in normalize_text(header):
                short = _college_short_name(base)
                if short and short in q:
                    filters.append((header, base))
                    break
            if len(value) >= 2 and value in q:
                filters.append((header, value))
                break
    return filters


def _entity_candidates(q: str, view: TableView) -> Dict[str, List[str]]:
    compact_q = _compact(q)
    result: Dict[str, List[Tuple[int, str]]] = {}
    for header in view.headers:
        if _is_generic_numeric_header(header):
            continue
        values = dedupe_keep_order(record.get(header, "") for record in view.records if record.get(header, ""))
        for value in values:
            text = normalize_text(value)
            compact = _compact(text)
            if len(compact) < 2 or len(compact) > 40:
                continue
            if _looks_like_time_or_query_word(text):
                continue
            if extract_numbers(text) and not re.search(r"\d{4}-\d{1,2}-\d{1,2}", text):
                continue
            pos = compact_q.find(compact)
            if pos >= 0:
                result.setdefault(header, []).append((pos, value))
    return {
        header: [value for _, value in sorted(items, key=lambda item: item[0])]
        for header, items in result.items()
    }


def _record_value_matches(record: Dict[str, str], header: str, value: str) -> bool:
    cell = _compact(record.get(header, ""))
    wanted = _compact(value)
    if extract_numbers(cell) or extract_numbers(wanted):
        return cell == wanted
    return bool(wanted and (cell == wanted or wanted in cell or cell in wanted))


def _compact(value: Any) -> str:
    return normalize_text(value).replace(" ", "").replace("　", "")


def _entity_base(value: Any) -> str:
    text = normalize_text(value)
    text = re.sub(r"[（(].*?[）)]", "", text)
    return text.strip()


def _college_short_name(value: str) -> str:
    text = _entity_base(value)
    if text.endswith("学院") and len(text) > 4:
        return text[:2] + "学院"
    return ""


def _header_place_key(header: str) -> str:
    text = normalize_text(header)
    text = re.sub(r"[（(]?人数[）)]?", "", text)
    text = text.replace(" ", "").replace("　", "")
    return text.strip("：:，,")


def _topic_terms(q: str) -> List[str]:
    terms: List[str] = []
    patterns = [
        r"(?:有关|涉及|包含|包括)([^，。？?]{2,24}?)(?:的|中|有哪些|有几|是多少|$)",
        r"(?:课程主题|项目|子项目|指标名称)中?(?:包含|涉及)([^，。？?]{2,24}?)(?:的|中|有哪些|有几|是多少|$)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, q):
            term = match.group(1)
            term = re.sub(r"^(?:和|与|为|是|了|“|\"|'|：|:)+", "", term)
            term = re.sub(r"(?:的)?(?:子项目|项目|课程|条目|内容|描述|定义|情况|记录|信息)$", "", term)
            term = term.strip("“”\"' ：:")
            if len(term) >= 2 and not _looks_like_time_or_query_word(term):
                terms.append(term)
                compact = term.replace("的", "")
                if compact != term and len(compact) >= 2:
                    terms.append(compact)
                if term.endswith("管理") and len(term) > 4:
                    terms.append(term[:-2])
    return sorted(dedupe_keep_order(terms), key=len, reverse=True)


def _is_summary_record(record: Dict[str, str]) -> bool:
    for key, value in record.items():
        if key.startswith("__"):
            continue
        text = normalize_text(value)
        if text in {"合计", "总计", "小计", "汇总"} or text.endswith("合计"):
            return True
    return False


def _is_table_header_record(record: Dict[str, str]) -> bool:
    values = [_compact(value) for key, value in record.items() if not key.startswith("__") and value]
    if not values:
        return False
    header_words = {"代码", "描述", "说明", "名称", "信息项", "指标名称", "指标值", "计量单位"}
    return len(values) <= 3 and all(value in header_words for value in values)


def _detail_records(records: List[Dict[str, str]]) -> List[Dict[str, str]]:
    details = [record for record in records if not _is_summary_record(record) and not _is_table_header_record(record)]
    return details or records


def _exact_value_terms(question: str, q: str, view: TableView) -> List[str]:
    del question
    header_norms = [normalize_text(header) for header in view.headers if header]
    terms: List[str] = []
    for record in view.records:
        for key, value in record.items():
            if key.startswith("__"):
                continue
            text = normalize_text(value)
            if len(text) < 2 or _looks_like_time_or_query_word(text):
                continue
            if extract_numbers(text) and len(text) <= 8:
                continue
            if text not in q:
                continue
            if any(text == header or text in header or header in text for header in header_norms):
                continue
            terms.append(text)
    return sorted(dedupe_keep_order(terms), key=len, reverse=True)


def _first_number(value: Any) -> Optional[float]:
    nums = extract_numbers(value)
    return nums[0] if nums else None


def _format_number(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _format_ratio(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _find_header(headers: List[str], name: str) -> str:
    n = normalize_text(name)
    for header in headers:
        h = normalize_text(header)
        if h == n or n in h or h in n:
            return header
    return ""


def _header_eq(header: str, name: str) -> bool:
    return normalize_text(header) == normalize_text(name) or normalize_text(name) in normalize_text(header)


def _is_generic_numeric_header(header: str) -> bool:
    text = normalize_text(header)
    return text in {"序号", "编号", "号", "年", "月", "日", "月份", "行次", "代码"} or text.endswith("编号")


def _is_amount_header(header: str) -> bool:
    text = normalize_text(header)
    return any(word in text for word in ["金额", "费用", "支出", "收入", "发生额", "发生金额", "采购金额"])


def _looks_like_time_or_query_word(chunk: str) -> bool:
    return bool(re.fullmatch(r"\d{4}年?|\d{1,2}月份?|\d{1,2}日", chunk)) or chunk in {
        "多少",
        "多少次",
        "多少笔",
        "多少条",
        "总和",
        "总额",
        "最高",
        "最低",
        "最大",
        "最小",
        "哪些",
        "哪个",
        "哪一项",
        "哪一次",
        "记录",
        "所有",
        "一共",
        "状态",
        "阶段",
        "代码",
        "名称",
        "描述",
        "说明",
        "条目",
    }


def _entity_parts(q: str) -> List[str]:
    return [part for part in re.findall(r"[a-zA-Z]\w*|[\u4e00-\u9fff]{2,}", q) if len(part) >= 2]


def _compare(value: Optional[float], op: str, expected: float) -> bool:
    if value is None:
        return False
    if op == ">":
        return value > expected
    if op == "<":
        return value < expected
    return abs(value - expected) < 1e-9
