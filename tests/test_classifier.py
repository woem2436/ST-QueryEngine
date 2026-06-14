import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.router.query_classifier import QueryClassifier


def main():
    classifier = QueryClassifier("config.yaml")

    test_questions = [
        "2024年销售额总和是多少？",
        "这个表格的数据来源是什么？",
        "表中哪些产品利润比较高？",
        "比较产品A和产品B的利润率差异，并分析原因"
    ]

    for q in test_questions:
        result = classifier.classify(q)
        print(f"问题: {q}")
        print(f"分类: {result}\n")


if __name__ == "__main__":
    main()