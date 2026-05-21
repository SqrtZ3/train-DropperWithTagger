# tagger/config.py - tagger module configuration
#
# 与 1_droptools/server.py 共用同一个进程，不再独立绑端口。
# base_path 默认指向 1_droptools/tagger/ 这一层，模型放 tagger/models/，预设放 tagger/presets/。

import os
import re
from pathlib import Path
from typing import Optional

re_special = re.compile(r'([\\()])')


class Config:
    def __init__(self):
        # 设备
        self.use_cpu: bool = False
        self.device_id: Optional[int] = None

        # 路径根：tagger/ 目录本身
        self.base_path: Path = Path(__file__).parent

        # 默认模型（前端不指定时使用）
        self.default_model: str = "hf:Camais03/camie-tagger-v2"

        self._load_from_env()
        self._ensure_directories()

    def _load_from_env(self):
        if os.environ.get("TAGGER_USE_CPU"):
            self.use_cpu = os.environ.get("TAGGER_USE_CPU", "").lower() in ("1", "true", "yes")
        if os.environ.get("TAGGER_DEVICE_ID"):
            try:
                self.device_id = int(os.environ.get("TAGGER_DEVICE_ID"))
            except ValueError:
                pass
        if os.environ.get("TAGGER_BASE_PATH"):
            custom = Path(os.environ.get("TAGGER_BASE_PATH"))
            if custom.exists():
                self.base_path = custom
        if os.environ.get("TAGGER_DEFAULT_MODEL"):
            self.default_model = os.environ.get("TAGGER_DEFAULT_MODEL")

    def _ensure_directories(self):
        self.models_path.mkdir(parents=True, exist_ok=True)
        self.onnx_models_path.mkdir(parents=True, exist_ok=True)
        self.deepdanbooru_projects_path.mkdir(parents=True, exist_ok=True)
        self.presets_path.mkdir(parents=True, exist_ok=True)

    @property
    def models_path(self) -> Path:
        """所有本地模型的统一根目录（每个子目录 = 一个模型）"""
        return self.base_path / "models"

    @property
    def onnx_models_path(self) -> Path:
        """Legacy: 旧约定下的 ONNX 模型目录 (models/onnx/<name>/)"""
        return self.base_path / "models" / "onnx"

    @property
    def deepdanbooru_projects_path(self) -> Path:
        return self.base_path / "models" / "deepdanbooru"

    @property
    def presets_path(self) -> Path:
        return self.base_path / "presets"


config = Config()
