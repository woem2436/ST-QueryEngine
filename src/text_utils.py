import math
import re
from collections import Counter
from datetime import date, datetime
from typing import Any, Counter as CounterType, Iterable, List, Optional


QUESTION_STOPWORDS = [
    "请问",
    "根据",
    "表中",
    "表格",
    "中的",
    "中",
    "的是",
    "是多少",
    "是什么",
    "有哪些",
    "哪个",
    "哪些",
    "什么",
    "多少",
    "几个",
    "有多少",
    "为多少",
    "分别",
    "的",
    "是",
    "为",
]

GENERIC_TOKENS = {
    "表",
    "中",
    "的",
    "是",
    "为",
    "和",
    "与",
    "及",
    "了",
    "吗",
    "多少",
    "什么",
    "哪些",
    "哪个",
    "几个",
    "有多少",
    "指标",
    "项目",
    "内容",
    "情况",
}


def normalize_text(value: Any) -> str:
    """规范化匹配文本，同时保留中文字符本身。"""
    if value is None:
        return ""
    text = str(value).lower()
    text = text.replace("\u3000", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def display_value(value: Any, number_format: str = "") -> str:
    """把 Excel 单元格值转换成稳定、易读的答案字符串。"""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        fmt = number_format or ""
        if "%" in fmt and abs(value) <= 100:
            percent = value * 100
            return f"{_format_number(percent)}%"
        return _format_number(value)
    return re.sub(r"\s+", " ", str(value)).strip()


def _format_number(value: float) -> str:
    if math.isfinite(value) and abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.10g}"


def extract_numbers(text: Any) -> List[float]:
    values = []
    for item in re.findall(r"[-+]?\d+(?:\.\d+)?", str(text).replace(",", "")):
        try:
            values.append(float(item))
        except ValueError:
            continue
    return values


def clean_question_text(question: str) -> str:
    text = normalize_text(question)
    for word in QUESTION_STOPWORDS:
        text = text.replace(word, " ")
    text = re.sub(r"[？?，,。；;：:、“”\"'（）()\[\]【】]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def query_chunks(question: str) -> List[str]:
    raw = normalize_text(question)
    pieces = re.split(r"[？?，,。；;：:、“”\"'（）()\[\]\s]+", raw)
    chunks: List[str] = []
    for piece in pieces:
        for word in QUESTION_STOPWORDS:
            piece = piece.replace(word, " ")
        for chunk in re.split(r"\s+|的|是|为|有|与|和|及", piece):
            chunk = chunk.strip()
            if len(chunk) >= 2 and chunk not in GENERIC_TOKENS:
                chunks.append(chunk)
    return dedupe_keep_order(chunks)


def tokenize(text: Any) -> List[str]:
    text = clean_question_text(str(text))
    parts = re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z0-9_.%]+|[≥≧≤<>]+", text)
    tokens: List[str] = []
    for part in parts:
        if re.fullmatch(r"[\u4e00-\u9fff]+", part):
            if len(part) <= 2:
                tokens.append(part)
            else:
                tokens.append(part)
                for n in (2, 3, 4):
                    if len(part) >= n:
                        tokens.extend(part[i : i + n] for i in range(len(part) - n + 1))
        else:
            tokens.append(part)
    return [token for token in tokens if token and token not in GENERIC_TOKENS]


def token_counter(text: Any) -> CounterType[str]:
    return Counter(tokenize(text))


def weighted_overlap(query: str, text: str, text_tokens: Optional[CounterType[str]] = None) -> float:
    q_tokens = token_counter(query)
    if not q_tokens:
        return 0.0
    doc_tokens = text_tokens or token_counter(text)
    if not doc_tokens:
        return 0.0
    score = 0.0
    for token, q_count in q_tokens.items():
        if token in doc_tokens:
            score += (len(token) + 1.0) * q_count * min(doc_tokens[token], 2)
    for chunk in query_chunks(query):
        if chunk and chunk in normalize_text(text):
            score += len(chunk) * 4.0
    return score / (math.sqrt(sum(doc_tokens.values())) + 1.0)


def dedupe_keep_order(values: Iterable[Any]) -> List[Any]:
    seen = set()
    result = []
    for value in values:
        key = str(value)
        if key in seen or key == "":
            continue
        seen.add(key)
        result.append(value)
    return result


def extract_unit(context: str) -> str:
    """从附近表头中提取简短单位标记，例如“金额（万元）”。"""
    text = str(context)
    matches = re.findall(r"[（(]([^（）()]{1,8})[）)]", text)
    for unit in reversed(matches):
        unit = unit.strip()
        if unit and not any(ch.isdigit() for ch in unit):
            return unit
    for unit in ["万元", "亿元", "元", "人", "个", "项", "次", "分", "天", "月", "年"]:
        if unit in text:
            return unit
    return ""


def append_unit_if_needed(value: str, unit: str) -> str:
    if not value or not unit:
        return value
    if unit in value:
        return value
    if "%" in value or unit == "%":
        return value
    if any(mark in value for mark in ["≥", "≧", "≤", "<", ">"]) and unit in value:
        return value
    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", value.replace(",", "")):
        return f"{value}{unit}"
    return value
