from collections import defaultdict
from typing import Dict, List


class Statistics:
    def __init__(self):
        self.results = []

    def add_result(self, question: str, pred, truth, is_correct: bool, category: str = "", **extra):
        row = {
            "question": question,
            "pred": pred,
            "truth": truth,
            "correct": is_correct,
            "category": category,
        }
        row.update(extra)
        self.results.append(row)

    def get_accuracy(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for item in self.results if item["correct"]) / len(self.results)

    def get_category_accuracy(self) -> Dict[str, float]:
        cat_correct = defaultdict(int)
        cat_total = defaultdict(int)
        for item in self.results:
            cat = item.get("category", "unknown")
            cat_total[cat] += 1
            if item["correct"]:
                cat_correct[cat] += 1
        return {cat: cat_correct[cat] / cat_total[cat] for cat in cat_total}

    def report(self) -> str:
        lines = [
            f"样本数：{len(self.results)}",
            f"整体准确率：{self.get_accuracy():.2%}",
            "分类准确率：",
        ]
        for cat, acc in sorted(self.get_category_accuracy().items()):
            total = sum(1 for item in self.results if item.get("category") == cat)
            lines.append(f"  {cat}: {acc:.2%} ({total})")
        return "\n".join(lines)

    def to_dict(self) -> Dict:
        return {
            "total": len(self.results),
            "accuracy": self.get_accuracy(),
            "category_accuracy": self.get_category_accuracy(),
            "results": self.results,
        }

    def get_failed_cases(self, limit: int = 5) -> List[Dict]:
        return [item for item in self.results if not item["correct"]][:limit]

