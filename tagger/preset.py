# tagger/preset.py - tag preset persistence
# Adapted from 2_tagger/backend/preset.py (imports rewired to tagger.config).

import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict

from tagger.config import config


@dataclass
class Preset:
    name: str = "default"

    threshold: float = 0.35
    additional_tags: str = ""
    exclude_tags: str = ""

    replace_underscore: bool = True
    replace_underscore_excludes: str = (
        "0_0, (o)_(o), +_+, +_-, ._., <o>_<o>, <|>_<|>, =_=, >_<, "
        "3_3, 6_9, >_o, @_@, ^_^, o_o, u_u, x_x, |_|, ||_||"
    )
    escape_tag: bool = False
    sort_alphabetical: bool = False

    core_tags: str = ""
    core_tags_weight: float = 1.0
    add_confident_as_weight: bool = False
    use_weight_mapping: bool = False
    weight_mapping_exponent: float = 1.0
    weight_min: float = 0.9
    weight_max: float = 1.1

    max_tags: int = 0
    token_limit: int = 75
    tag_precision: int = 2

    output_format: str = "[name].txt"
    conflict_mode: str = "ignore"
    remove_duplicates: bool = True
    save_json: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Preset':
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)


class PresetManager:
    def __init__(self):
        self.presets_dir = config.presets_path
        self.presets_dir.mkdir(parents=True, exist_ok=True)

    def _get_preset_path(self, name: str) -> Path:
        safe = "".join(c for c in name if c.isalnum() or c in ('_', '-', ' ')).strip() or "unnamed"
        return self.presets_dir / f"{safe}.json"

    def save(self, name: str, data: Dict[str, Any]) -> bool:
        try:
            path = self._get_preset_path(name)
            save_data = {'name': name, 'version': 3}
            save_data.update(data)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(save_data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"[tagger] preset save '{name}' failed: {e}")
            return False

    def load(self, name: str) -> Optional[Dict[str, Any]]:
        try:
            path = self._get_preset_path(name)
            if not path.exists():
                return None
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"[tagger] preset load '{name}' failed: {e}")
            return None

    def delete(self, name: str) -> bool:
        try:
            path = self._get_preset_path(name)
            if path.exists():
                path.unlink()
                return True
            return False
        except Exception as e:
            print(f"[tagger] preset delete '{name}' failed: {e}")
            return False

    def list_presets(self) -> List[str]:
        return sorted(p.stem for p in self.presets_dir.glob("*.json"))

    def get_default(self) -> Dict[str, Any]:
        return Preset().to_dict()


preset_manager = PresetManager()
