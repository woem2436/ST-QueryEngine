import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.parser.excel_parser import ExcelParser
from src.storage.vector_storage import VectorStorage
import pandas as pd

def vectorize_table(excel_path: Path, collection_name: str, vector_storage: VectorStorage):
    parser = ExcelParser(str(excel_path))
    df = parser.parse_sheet()
    docs = []
    ids = []
    metas = []
    for idx, row in df.iterrows():
        row_str = "，".join([f"{col}: {val}" for col, val in row.items() if pd.notna(val)])
        if row_str:
            docs.append(row_str)
            ids.append(f"{excel_path.stem}_row_{idx}")
            metas.append({"source": str(excel_path), "row": idx})
    if docs:
        vector_storage.create_collection(collection_name, recreate=False)
        vector_storage.add_documents(ids, docs, metas)
        print(f"已向量化 {len(docs)} 行来自 {excel_path.name}")

def main():
    project_root = Path(__file__).parent.parent
    raw_dir = project_root / "data" / "raw"
    chroma_dir = project_root / "data" / "processed" / "chroma_db"
    chroma_dir.mkdir(parents=True, exist_ok=True)
    vs = VectorStorage(str(chroma_dir), embedding_model_name="BAAI/bge-small-zh-v1.5")
    for excel_file in raw_dir.glob("*.xlsx"):
        print(f"处理: {excel_file.name}")
        try:
            vectorize_table(excel_file, "table_content", vs)
        except Exception as e:
            print(f"错误: {e}")

if __name__ == "__main__":
    main()