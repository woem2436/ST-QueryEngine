import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.router.query_router import RuleBasedRouter

router = RuleBasedRouter()
questions = [
    "2024年销售额总和是多少？",
    "这个表格的数据来源是什么？",
    "哪些产品利润较高？",
    "比较产品A和B的差异"
]
for q in questions:
    print(f"{q} -> {router.classify(q)['category']} -> {router.route(q)}")