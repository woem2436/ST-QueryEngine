import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from src.storage.vector_storage import VectorStorage
from src.parser.excel_parser import ExcelParser


def main():
    # 示例流程：从 Excel 解析出 DataFrame，再按行构造检索文档
    test_excel = Path("data/raw/1.xlsx")
    if not test_excel.exists():
        print("示例 Excel 文件不存在，跳过向量准备")
        return

    parser = ExcelParser(test_excel)
    df = parser.parse_sheet()

    # 简单分块：每一行生成一个文档片段
    documents = []
    ids = []
    for idx, row in df.iterrows():
        # 将行内容转换为“列名: 值”的文本描述
        row_text = "；".join([f"{col}: {val}" for col, val in row.items() if pd.notna(val)])
        documents.append(row_text)
        ids.append(f"row_{idx}")

    # 初始化检索存储；当前 VectorStorage 默认使用离线 sparse-token fallback
    persist_dir = Path("data/processed/chroma_db")
    vs = VectorStorage(str(persist_dir), embedding_model_name="BAAI/bge-small-zh-v1.5")
    vs.create_collection("table_content", recreate=True)
    vs.add_documents(ids, documents)
    print(f"成功添加 {len(documents)} 条文档到 collection")


if __name__ == "__main__":
    main()
