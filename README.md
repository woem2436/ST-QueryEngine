# ST-QueryEngine

数据库原理与运用课程项目：表格数据的智能查询系统。

## 功能

- 解析 SSTQA-zh 的 Excel 表格，填充合并单元格并保留半结构化上下文。
- 构建 SQLite 单元格/行索引和 JSON 检索索引。
- 根据自然语言问题进行路由：关系表执行、单元格查值、列表检索、计数、最大/最小、求和/平均。
- 支持离线 BM25 风格检索，LLM 和向量数据库作为可选扩展，不影响基础运行。
- 读取 `data/raw/test.jsonl`，与标准答案对比并输出准确率报告。

## 运行

安装依赖：

```bash
pip install -r requirements.txt
```

重建索引并评测前 30 条：

```bash
python src/main.py --rebuild-index --evaluate --limit 30 --quiet
```

运行全量评测：

```bash
python src/main.py --evaluate --quiet
```

单条查询：

```bash
python src/main.py --question "基层服务覆盖范围的指标值是什么？" --table-id 1 --explain
```

评测结果写入：

```text
data/processed/evaluation_report.json
```

## 当前实验结果

在 764 条 `test.jsonl` 全量样本上，当前离线混合引擎准确率为 55.37%。

```text
Content Match: 61.16%
Numeric Computation: 43.95%
Semantic-Aware: 47.15%
```
