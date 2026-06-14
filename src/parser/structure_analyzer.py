from typing import Any, Dict, List

import pandas as pd


class StructureAnalyzer:
    """Extract lightweight structural signals from a parsed table."""

    def __init__(self, df: pd.DataFrame):
        self.df = df
        self.header_hierarchy: Dict[int, Dict[int, str]] = {}

    def detect_multi_level_header(self, header_rows: int = 2) -> Dict[int, Dict[int, str]]:
        hierarchy: Dict[int, Dict[int, str]] = {}
        max_rows = min(header_rows, len(self.df))
        for level in range(max_rows):
            row = self.df.iloc[level]
            hierarchy[level] = {
                idx: str(value)
                for idx, value in enumerate(row)
                if pd.notna(value) and str(value).strip() != ""
            }
        self.header_hierarchy = hierarchy
        return hierarchy

    def flatten_headers(self, hierarchy: Dict[int, Dict[int, str]]) -> List[str]:
        if not hierarchy:
            return [str(col) for col in self.df.columns]
        num_cols = max((max(level.keys()) for level in hierarchy.values() if level), default=-1) + 1
        flat_headers = []
        for col in range(num_cols):
            parts = []
            for level in sorted(hierarchy):
                value = hierarchy[level].get(col)
                if value and value not in parts:
                    parts.append(value)
            flat_headers.append(" -> ".join(parts) if parts else f"col_{col + 1}")
        return flat_headers

    def infer_column_types(self, data_rows: pd.DataFrame) -> Dict[Any, str]:
        types = {}
        for col in data_rows.columns:
            series = data_rows[col]
            numeric = pd.to_numeric(series, errors="coerce")
            if numeric.notna().sum() >= max(1, len(series.dropna()) * 0.7):
                types[col] = "numeric"
            elif pd.api.types.is_datetime64_any_dtype(series):
                types[col] = "datetime"
            else:
                types[col] = "text"
        return types

    def extract_metadata(self) -> Dict[str, Any]:
        total_cells = max(1, len(self.df) * max(1, len(self.df.columns)))
        return {
            "rows": len(self.df),
            "columns": len(self.df.columns),
            "null_ratio": float(self.df.isnull().sum().sum() / total_cells),
            "column_types": self.infer_column_types(self.df),
        }

