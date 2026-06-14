import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.evaluator.answer_matcher import AnswerMatcher
from src.evaluator.statistics import Statistics
from src.hybrid_engine import HybridQueryEngine
from src.router.query_router import RuleBasedRouter


class TableQuerySystem:
    def __init__(self, config: dict, rebuild_index: bool = False):
        self.config = config
        self.project_root = Path(__file__).parent.parent
        storage_config = config.setdefault("storage", {})
        raw_dir = self.project_root / storage_config.get("raw_dir", "data/raw")
        sqlite_path = self.project_root / storage_config.get("sqlite_db_path", "data/processed/table_data.db")
        index_path = self.project_root / storage_config.get("index_json_path", "data/processed/table_index.json")

        self.router = RuleBasedRouter()
        self.matcher = AnswerMatcher(
            numeric_tolerance=config.get("evaluation", {}).get("numeric_tolerance", 0.01),
            rouge_threshold=config.get("evaluation", {}).get("rouge_l_threshold", 0.5),
        )
        self.engine = HybridQueryEngine(
            raw_dir=str(raw_dir),
            sqlite_path=str(sqlite_path),
            index_path=str(index_path),
            rebuild=rebuild_index,
        )
        self.statistics = Statistics()

    def answer_question(self, question: str, table_id: Optional[int] = None, explain: bool = False) -> str:
        return self.engine.answer(question, table_id=table_id, explain=explain)

    def evaluate_from_jsonl(
        self,
        jsonl_path: str,
        limit: Optional[int] = None,
        report_path: Optional[str] = None,
        quiet: bool = False,
    ) -> Statistics:
        jsonl_file = Path(jsonl_path)
        if not jsonl_file.exists():
            raise FileNotFoundError(f"文件不存在：{jsonl_path}")

        with open(jsonl_file, "r", encoding="utf-8") as handle:
            for idx, line in enumerate(handle):
                if limit is not None and idx >= limit:
                    break
                data = json.loads(line.strip())
                question = data.get("question") or data.get("query")
                truth = data.get("answer") or data.get("label") or data.get("gold") or data.get("ground_truth")
                table_id = data.get("table_id")
                if not question or truth is None:
                    continue
                pred = self.answer_question(question, table_id=table_id)
                is_correct = self.matcher.match(pred, truth)
                category = data.get("type") or self.router.classify(question)["category"]
                self.statistics.add_result(
                    question,
                    pred,
                    truth,
                    is_correct,
                    category,
                    table_id=table_id,
                    difficulty=data.get("difficulty", ""),
                    route=self.engine.last_trace.get("route", ""),
                )
                if not quiet:
                    print(f"[{idx + 1}] table={table_id} route={self.engine.last_trace.get('route')}")
                    print(f"Q: {question}")
                    print(f"Pred: {pred}")
                    print(f"Truth: {truth}")
                    print(f"Correct: {is_correct}\n")

        if report_path:
            path = Path(report_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(self.statistics.to_dict(), handle, ensure_ascii=False, indent=2)
        return self.statistics

    def close(self):
        pass


def load_config(config_path: Path) -> dict:
    with open(config_path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SSTQA-zh 半结构化表格智能查询系统")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--rebuild-index", action="store_true", help="重新解析 Excel 并重建 SQLite/JSON 索引")
    parser.add_argument("--evaluate", action="store_true", help="运行 test.jsonl 评测")
    parser.add_argument("--test-file", default="data/raw/test.jsonl", help="评测 JSONL 路径")
    parser.add_argument("--limit", type=int, default=None, help="评测样本上限")
    parser.add_argument("--report-file", default="data/processed/evaluation_report.json", help="评测结果 JSON 输出")
    parser.add_argument("--question", default="", help="单条自然语言查询")
    parser.add_argument("--table-id", type=int, default=None, help="单条查询对应的表格 ID")
    parser.add_argument("--explain", action="store_true", help="输出检索证据和路由轨迹")
    parser.add_argument("--quiet", action="store_true", help="评测时只输出汇总")
    return parser


def main():
    args = build_arg_parser().parse_args()
    project_root = Path(__file__).parent.parent
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = project_root / config_path
    config = load_config(config_path)
    system = TableQuerySystem(config, rebuild_index=args.rebuild_index)

    if args.question:
        print(system.answer_question(args.question, table_id=args.table_id, explain=args.explain))
        return

    if args.evaluate or not args.question:
        test_file = Path(args.test_file)
        if not test_file.is_absolute():
            test_file = project_root / test_file
        report_file = Path(args.report_file)
        if not report_file.is_absolute():
            report_file = project_root / report_file
        stats = system.evaluate_from_jsonl(
            str(test_file),
            limit=args.limit,
            report_path=str(report_file),
            quiet=args.quiet,
        )
        print(stats.report())
        print(f"详细结果已写入：{report_file}")


if __name__ == "__main__":
    main()

