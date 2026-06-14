import sqlite3
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd


class SQLStorage:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.cursor = self.conn.cursor()

    def create_table_from_df(self, table_name: str, df: pd.DataFrame, if_exists: str = "replace") -> None:
        df.to_sql(table_name, self.conn, if_exists=if_exists, index=False)
        print(f"表 {table_name} 创建成功，行数：{len(df)}")

    def execute_query(self, sql: str) -> List[Dict[str, Any]]:
        try:
            self.cursor.execute(sql)
            columns = [desc[0] for desc in self.cursor.description] if self.cursor.description else []
            rows = self.cursor.fetchall()
            return [dict(zip(columns, row)) for row in rows]
        except sqlite3.Error as exc:
            print(f"SQL 执行错误：{exc}")
            return []

    def get_table_names(self) -> List[str]:
        self.cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        return [row[0] for row in self.cursor.fetchall()]

    def close(self):
        self.conn.close()

