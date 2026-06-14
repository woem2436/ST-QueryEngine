from src.evaluator.answer_matcher import AnswerMatcher
from src.router.query_router import RuleBasedRouter
from src.storage.vector_storage import VectorStorage


def test_answer_matcher_requires_exact_numeric_value():
    matcher = AnswerMatcher()
    assert matcher.match("50000", "50000人")
    assert not matcher.match("13", "3")


def test_rule_based_router_detects_count_query():
    router = RuleBasedRouter()
    result = router.classify("绩效指标中有多少一级指标？")
    assert result["category"] == "SQL_AGG"
    assert router.route("绩效指标中有多少一级指标？") == "sql_agent"


def test_vector_storage_offline_search(tmp_path):
    storage = VectorStorage(str(tmp_path))
    storage.create_collection("demo")
    storage.add_documents(["1", "2"], ["工程进度 可以加分", "质量控制 扣分"], [{"row": 1}, {"row": 2}])
    hits = storage.search("哪些项目可以加分", top_k=1)
    assert hits
    assert hits[0]["metadata"]["row"] == 1

