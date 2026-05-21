# webapp/schemas.py — 所有路由用到的 Pydantic 请求体

from typing import Dict, List, Optional

from pydantic import BaseModel


class InitRequest(BaseModel):
    input_dir: str
    output_dir: Optional[str] = ""
    target_mp: Optional[float] = None
    step: Optional[int] = None


class TargetUpdateRequest(BaseModel):
    target_mp: Optional[float] = None
    step: Optional[int] = None


class SaveCropRequest(BaseModel):
    filepath: Optional[str] = None
    filename: Optional[str] = None
    crops: List[Dict]
    status: str


class BatchProcessRequest(BaseModel):
    min_bucket_size: Optional[int] = 4
    target_mp: Optional[float] = None
    step: Optional[int] = None


class AutoBucketRequest(BaseModel):
    target_mp: Optional[float] = None
    step: Optional[int] = None
    scope: Optional[str] = "source"            # 'source' | 'pending'
    include_processed: Optional[bool] = False
    output_subdir: Optional[str] = None
    name_template: Optional[str] = "auto_{idx:05d}.png"


class AutoTagToggle(BaseModel):
    enabled: bool


class VideoExtractRequest(BaseModel):
    video_path: str
    output_dir: Optional[str] = None
    frame_count: Optional[int] = 3


class VideoProbeRequest(BaseModel):
    video_path: str
    scene_threshold: Optional[float] = None
    max_candidates: Optional[int] = 0
    sample_rate_hz: Optional[float] = 4.0


class VideoSaveRequest(BaseModel):
    job_id: str
    indices: List[int]
    output_dir: Optional[str] = None
    name_prefix: Optional[str] = None


class SimilarityScanRequest(BaseModel):
    threshold: Optional[int] = 6
    method: Optional[str] = "phash"
    ar_tolerance: Optional[float] = 0.15
    require_dual_hash: Optional[bool] = True
    # 'source'  = 扫描会话的输入图片（含 zip）
    # 'output'  = 扫描会话的输出目录
    # 'custom'  = 扫描指定 folder 下所有图片
    scope: Optional[str] = "source"
    folder: Optional[str] = None


class DeleteFilesRequest(BaseModel):
    filepaths: List[str]
    # 可选：把删除请求绑定到某次相似度扫描，仅允许删除该快照内的文件
    snap_id: Optional[str] = None


class IgnoreFilesRequest(BaseModel):
    filepaths: List[str]
