import re
from typing import List

from src.text_utils import extract_numbers, normalize_text


class AnswerMatcher:
    def __init__(self, numeric_tolerance: float = 0.01, rouge_threshold: float = 0.5):
        self.numeric_tolerance = numeric_tolerance
        self.rouge_threshold = rouge_threshold

    def match(self, pred, truth) -> bool:
        pred_str = self._normalize_answer(str(pred))
        truth_str = self._normalize_answer(str(truth))
        if pred_str == truth_str:
            return True
        if extract_numbers(pred_str) and extract_numbers(truth_str):
            return self._numeric_match(pred_str, truth_str)
        if truth_str and (truth_str in pred_str or pred_str in truth_str):
            return True
        if self._list_match(pred_str, truth_str):
            return True
        lcs_len = self._lcs_length(pred_str, truth_str)
        rouge = lcs_len / max(len(truth_str), 1)
        return rouge >= self.rouge_threshold

    def _normalize_answer(self, text: str) -> str:
        text = normalize_text(text)
        text = text.replace("≧", "≥")
        text = text.replace("％", "%")
        text = re.sub(r"\s+", "", text)
        text = text.strip("。；;，,")
        return text

    def _numeric_match(self, pred: str, truth: str) -> bool:
        pred_numbers = extract_numbers(pred)
        truth_numbers = extract_numbers(truth)
        if not pred_numbers or not truth_numbers:
            return False
        for p_num in pred_numbers:
            for t_num in truth_numbers:
                if abs(p_num - t_num) <= self.numeric_tolerance:
                    return True
                if "%" in pred and "%" not in truth and abs(p_num / 100 - t_num) <= self.numeric_tolerance:
                    return True
                if "%" in truth and "%" not in pred and abs(p_num - t_num / 100) <= self.numeric_tolerance:
                    return True
        return False

    def _list_match(self, pred: str, truth: str) -> bool:
        pred_items = self._split_items(pred)
        truth_items = self._split_items(truth)
        if len(pred_items) < 2 or len(truth_items) < 2:
            return False
        return set(pred_items) == set(truth_items)

    def _split_items(self, text: str) -> List[str]:
        items = re.split(r"[，,、；;/]+", text)
        return [item.strip() for item in items if item.strip()]

    def _lcs_length(self, a: str, b: str) -> int:
        m, n = len(a), len(b)
        dp: List[int] = [0] * (n + 1)
        for i in range(1, m + 1):
            prev = 0
            for j in range(1, n + 1):
                temp = dp[j]
                if a[i - 1] == b[j - 1]:
                    dp[j] = prev + 1
                else:
                    dp[j] = max(dp[j], dp[j - 1])
                prev = temp
        return dp[n]
