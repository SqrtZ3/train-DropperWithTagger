# webapp/ — Drop Studio server package
#
# 拆分自原 server.py（2630 行单文件）。每个文件按职责切片：
#   config.py             路径/分桶/缩略图常量
#   state.py              进程内共享单例：session、tagger_queue、HAS_* 标志
#   vendor.py             React/Babel CDN 资源本地镜像
#   history.py            recent dir 列表（合并在 session_manager 里也行，独立更清）
#   buckets.py            分桶数学 + 智能裁切框
#   thumbnails.py         .thumb_cache 维护
#   snapshots.py          相似度结果快照表（snap_id → files）
#   similarity.py         phash+dhash+颜色双校验聚类
#   session_manager.py    SessionManager 类（断点续作）
#   schemas.py            所有 Pydantic 请求模型
#   lan.py                局域网 IP 探测
#   routes_*.py           分域 APIRouter
