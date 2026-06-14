import json
from pathlib import Path
from typing import Any

class KVStorage:
    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.data = self._load()

    def _load(self) -> dict:
        if self.file_path.exists():
            with open(self.file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    def _save(self):
        with open(self.file_path, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def set(self, key: str, value: Any):
        self.data[key] = value
        self._save()

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def delete(self, key: str):
        if key in self.data:
            del self.data[key]
            self._save()

    def contains(self, key: str) -> bool:
        return key in self.data

    def get_all(self) -> dict:
        return self.data.copy()