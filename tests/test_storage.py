import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from src.storage.sql_storage import SQLStorage
from src.storage.kv_storage import KVStorage
from src.storage.vector_storage import VectorStorage

# 模拟一个简单表格
df = pd.DataFrame({
    "产品": ["产品A", "产品B"],
    "销售额": [100, 200],
    "利润率": [0.25, 0.30]
})

# 测试 SQL 存储
sql_db = SQLStorage("data/processed/test.db")
sql_db.create_table_from_df("products", df, if_exists="replace")
result = sql_db.execute_query("SELECT * FROM products WHERE 销售额 > 150")
print("SQL查询结果:", result)
sql_db.close()

# 测试 KV 存储
kv = KVStorage("data/processed/metadata.json")
kv.set("table_notes", "这是一个测试表格")
print("KV读取:", kv.get("table_notes"))

# 测试向量存储；当前实现使用离线 sparse-token fallback，不需要下载模型
vec = VectorStorage("data/processed/chroma_test", embedding_model_name="BAAI/bge-small-zh-v1.5")
vec.create_collection("test_collection")
vec.add_documents(
    ids=["1", "2"],
    documents=["产品A销售额100", "产品B利润率30%"],
    metadatas=[{"product": "A"}, {"product": "B"}]
)
search_result = vec.search("利润", top_k=1)
print("向量检索结果:", search_result)

print("所有存储模块测试通过")
