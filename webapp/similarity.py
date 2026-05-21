# webapp/similarity.py — 严格相似度聚类
#
# phash + dhash 双哈希交叉 → 宽高比预过滤 → 颜色直方图兜底 → 严格 clique 聚类。
# 单哈希 + 并查集会把 A~B、B~C 错误连成一组；严格 clique 要求组内任意两张都要在阈值内。

import io
import os
from typing import Any, Callable, Dict, List, Optional

import imagehash
from PIL import Image

from webapp import state


def _color_signature(img: Image.Image, bins: int = 4) -> List[float]:
    """
    取 32x32 缩略图，按 bins×bins×bins 计算归一化颜色直方图。
    用作第三层「颜色感」校验：phash/dhash 都靠灰度结构信息，对纯色或低熵图容易误判，
    再加一层颜色分布对比可以拒掉「构图相似但颜色完全不同」的两张图。
    返回 bins**3 维向量，和为 1。
    """
    small = img.resize((32, 32), Image.Resampling.BILINEAR)
    if small.mode != "RGB":
        small = small.convert("RGB")
    pixels = list(small.getdata())
    step = 256 // bins
    hist = [0.0] * (bins * bins * bins)
    for r, g, b in pixels:
        idx = (min(r // step, bins - 1) * bins +
               min(g // step, bins - 1)) * bins + min(b // step, bins - 1)
        hist[idx] += 1.0
    total = sum(hist) or 1.0
    return [v / total for v in hist]


def _color_distance(a: List[float], b: List[float]) -> float:
    """L1 距离，范围 [0, 2]。两张「色彩分布完全不同」的图通常 > 0.6。"""
    return sum(abs(x - y) for x, y in zip(a, b))


def disk_loader(filepath: str) -> Optional[io.BytesIO]:
    """普通磁盘文件加载器，给扫描磁盘目录场景用。"""
    try:
        if not os.path.exists(filepath):
            return None
        with open(filepath, "rb") as f:
            return io.BytesIO(f.read())
    except Exception as e:
        print(f"Read error {filepath}: {e}")
        return None


def analyze_similarity_internal(
        files: List[str],
        threshold: int = 6,
        method: str = "phash",
        loader_func: Optional[Callable[[str], Optional[io.BytesIO]]] = None,
        ar_tolerance: float = 0.15,
        require_dual_hash: bool = True,
        color_distance_max: Optional[float] = 0.55,
) -> List[List[Dict[str, Any]]]:
    """严格相似度分析，详见模块开头说明。"""
    if not files:
        return []

    progress = state.similarity_progress
    progress.update({
        "running": True, "phase": "hashing", "current": 0,
        "total": len(files), "cancel": False, "message": "",
    })

    print(f"🔍 正在分析 {len(files)} 张图片的相似度 "
          f"(阈值: {threshold}, 方法: {method}, dual_hash: {require_dual_hash}, "
          f"ar_tol: {ar_tolerance}, color_max: {color_distance_max})...")

    primary_hash_func = {
        "phash": imagehash.phash,
        "dhash": imagehash.dhash,
        "whash": imagehash.whash,
        "average": imagehash.average_hash,
    }.get(method, imagehash.phash)
    secondary_hash_func = imagehash.dhash if primary_hash_func is not imagehash.dhash else imagehash.phash

    img_data: List[Dict[str, Any]] = []

    for idx, filepath in enumerate(files):
        if progress.get("cancel"):
            print("⛔ 用户取消相似度分析")
            state.reset_similarity_progress()
            return []
        progress["current"] = idx + 1

        try:
            if loader_func:
                img_bytes = loader_func(filepath)
                if img_bytes is None:
                    continue
                img = Image.open(img_bytes)
            else:
                img = Image.open(filepath)

            if img.mode != "RGB":
                if img.mode in ("RGBA", "LA", "P"):
                    bg = Image.new("RGB", img.size, (255, 255, 255))
                    if img.mode == "P":
                        img = img.convert("RGBA")
                    if img.mode in ("RGBA", "LA"):
                        bg.paste(img, mask=img.split()[-1])
                    img = bg
                else:
                    img = img.convert("RGB")

            primary = primary_hash_func(img)
            secondary = secondary_hash_func(img) if require_dual_hash else None
            color_sig = _color_signature(img) if color_distance_max is not None else None
            w, h = img.size
            aspect = w / max(1, h)

            if filepath.startswith("zip://"):
                parts = filepath[6:].split("::", 1)
                display_name = f"[ZIP] {os.path.basename(parts[0])}/{parts[1]}"
            else:
                display_name = os.path.basename(filepath)

            img_data.append({
                "index": idx, "filepath": filepath, "filename": display_name,
                "hash": primary, "hash2": secondary, "color": color_sig,
                "aspect": aspect, "width": w, "height": h,
            })

            img.close()

        except Exception as e:
            print(f"⚠️ 跳过 {filepath}: {e}")
            continue

    if len(img_data) < 2:
        state.reset_similarity_progress()
        return []

    print(f"✅ 成功计算 {len(img_data)} 张图片的哈希")
    progress["phase"] = "clustering"

    n = len(img_data)
    secondary_threshold = threshold + 4
    adjacency: List[set] = [set() for _ in range(n)]
    pair_dist: Dict[tuple, int] = {}

    for i in range(n):
        if progress.get("cancel"):
            print("⛔ 用户取消相似度分析（聚类阶段）")
            state.reset_similarity_progress()
            return []

        ar_i = img_data[i]["aspect"]
        for j in range(i + 1, n):
            ar_j = img_data[j]["aspect"]
            ar_diff = abs(ar_i - ar_j) / max(ar_i, ar_j)
            if ar_diff > ar_tolerance:
                continue

            d1 = img_data[i]["hash"] - img_data[j]["hash"]
            if d1 > threshold:
                continue

            if require_dual_hash and img_data[i]["hash2"] is not None:
                d2 = img_data[i]["hash2"] - img_data[j]["hash2"]
                if d2 > secondary_threshold:
                    continue

            if color_distance_max is not None and img_data[i].get("color") and img_data[j].get("color"):
                cd = _color_distance(img_data[i]["color"], img_data[j]["color"])
                if cd > color_distance_max:
                    continue

            adjacency[i].add(j)
            adjacency[j].add(i)
            pair_dist[(i, j)] = d1

    visited = [False] * n
    result_groups: List[List[Dict[str, Any]]] = []
    order = sorted(range(n), key=lambda x: -len(adjacency[x]))

    for i in order:
        if visited[i]:
            continue
        if not adjacency[i]:
            continue

        cluster = [i]
        visited[i] = True
        candidates = {c for c in adjacency[i] if not visited[c]}

        while candidates:
            best, best_score = None, None
            for c in candidates:
                if not all(c in adjacency[m] or c == m for m in cluster):
                    continue
                dists = []
                for m in cluster:
                    a, b = (min(c, m), max(c, m))
                    if a == b:
                        continue
                    dists.append(pair_dist.get((a, b), threshold + 1))
                score = max(dists) if dists else 0
                if best is None or score < best_score:
                    best, best_score = c, score
            if best is None:
                break
            cluster.append(best)
            visited[best] = True
            candidates = {c for c in candidates if c != best and c in adjacency[best] and not visited[c]}

        if len(cluster) >= 2:
            group = [img_data[k] for k in cluster]
            group.sort(key=lambda x: x["filename"])
            result_groups.append(group)

    result_groups.sort(key=lambda x: len(x), reverse=True)
    print(f"📊 发现 {len(result_groups)} 组相似图片")

    progress["phase"] = "done"
    progress["running"] = False
    return result_groups
