import sys
from pathlib import Path

# 获取当前脚本所在目录（tests/）
current_dir = Path(__file__).parent
# 获取项目根目录（即 tests 的上一级）
project_root = current_dir.parent
# 将项目根目录加入 Python 路径
sys.path.insert(0, str(project_root))

from src.parser.excel_parser import ExcelParser
from src.parser.structure_analyzer import StructureAnalyzer

# 构建目标文件的绝对路径
test_file = project_root / "data" / "raw" / "1.xlsx"

if test_file.exists():
    parser = ExcelParser(test_file)
    df = parser.parse_sheet()
    print("解析后表格形状:", df.shape)
    print("前5行:\n", df.head())

    analyzer = StructureAnalyzer(df)
    hierarchy = analyzer.detect_multi_level_header(header_rows=2)
    print("多级表头树:", hierarchy)
    flat = analyzer.flatten_headers(hierarchy)
    print("展平后列名:", flat[:10])
else:
    print(f"测试文件不存在: {test_file}")
    # 可选：打印当前工作目录和预期路径，方便调试
    print(f"当前工作目录: {Path.cwd()}")
    print(f"脚本目录: {current_dir}")
    print(f"项目根目录: {project_root}")