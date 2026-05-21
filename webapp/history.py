# webapp/history.py — 最近会话记录

import json
import os

from webapp.config import HISTORY_FILE


def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_to_history(input_path: str, output_path: str) -> None:
    history = load_history()
    new_record = {"input": input_path, "output": output_path}
    history = [h for h in history if h.get("input") != input_path]
    history.insert(0, new_record)
    history = history[:10]
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"保存历史失败: {e}")
