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


class ExportBucketOptions(BaseModel):
    # 默认 resize：任何裁切都上/下采样贴合所选桶，输出永远是干净桶尺寸。
    # crop-first（arb_crop）保留为可选项（统计已对齐真实输出桶）。
    export_strategy: Optional[str] = "resize"
    target_mp: Optional[float] = None
    step: Optional[int] = None
    bucket_base_resos: Optional[List[int]] = None
    bucket_min_base_reso: Optional[int] = 0
    bucket_max_base_reso: Optional[int] = 2048
    output_max_base_reso: Optional[int] = 1024
    bucket_base_reso_steps: Optional[int] = 256
    min_bucket_reso: Optional[int] = 512
    max_bucket_reso: Optional[int] = 2048
    # 纹理补充桶（原生裁切、零重采样）：勾了「纹理」的框走这套
    texture_subdir: Optional[str] = "texture"
    texture_copy_caption: Optional[bool] = True
    texture_max_ar: Optional[float] = 2.0


class BatchProcessRequest(ExportBucketOptions):
    min_bucket_size: Optional[int] = 4


class AutoBucketRequest(ExportBucketOptions):
    scope: Optional[str] = "source"            # 'source' | 'pending'
    include_processed: Optional[bool] = False
    output_subdir: Optional[str] = None
    name_template: Optional[str] = "auto_{idx:05d}.png"


class AutoTagToggle(BaseModel):
    enabled: bool


class VideoProbeRequest(BaseModel):
    video_path: str
    scene_threshold: Optional[float] = None
    max_candidates: Optional[int] = 0
    sample_rate_hz: Optional[float] = 4.0
    # 仅当 video_path 是目录时生效；接收文件路径时忽略。
    recursive: Optional[bool] = True


class VideoProbeBatchRequest(BaseModel):
    scan_id: str
    # 指向 scan 结果里 videos[] 的下标
    vids: List[int]
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
