import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.storage.kv_storage import KVStorage

kv_file = Path("data/processed/metadata.json")
kv = KVStorage(str(kv_file))

# 写入一些示例元数据
kv.set("数据来源", "企业内部销售系统导出")
kv.set("表格说明", "2024年度各产品销售额与利润情况")
kv.set("单位", "万元")
kv.set("创建时间", "2025-03-15")

print("KV数据已准备")