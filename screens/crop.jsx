/* ============================================================
   CROP — image-by-image cropping workspace.
   Uses the existing backend:
     - GET  /api/image/{idx}             → metadata + suggested_box
     - GET  /api/file/{idx}              → original pixels
     - GET  /api/thumb/source/{idx}      → 384px thumb for filmstrip
     - POST /api/save { filepath, crops, status }
     - POST /api/goto/{idx}
     - POST /api/undo
   ============================================================ */

const CROP_COLORS = ['#6EE7C8', '#F4B860', '#82A4FF', '#FF8FAB', '#C9A0FF', '#7BD3F7'];

/* ============================================================
   CropBox — 单个裁切框组件。
   多框交互核心规则：
   - **只有 active 框** 接受拖移与变形（handles 仅在 active 时渲染）。
     这避免「想拖 B 中心却抓到 A 角手柄」——A 的手柄 ::after 命中区
     扩张 ±16px (见 tokens.css)，旧实现下会越界吃到旁边的框。
   - **非 active 框**仅在「中央」点击切换，不接管移动。
   ============================================================ */

function CropBox({ crop, color, scale, isActive, aspectRatio, activeBucket, onChange, onPick, onDelete, onToggleKind, onGeometryCommit, imgW, imgH }) {
    const start = React.useRef(null);
    const isTexture = crop.kind === 'texture';
    // 纹理框忽略比例锁：自由画 + 松手吸附说了算
    const hasRatioLock = aspectRatio !== 'free' && !isTexture;

    // makeHandle 工厂：返回 mousedown/touchstart 监听器。
    const makeHandle = (mode) => (e) => {
        e.stopPropagation(); e.preventDefault();
        const t = e.touches ? e.touches[0] : e;
        start.current = { mx: t.clientX, my: t.clientY, ...crop, mode };
        const move = (ev) => {
            const tt = ev.touches ? ev.touches[0] : ev;
            const dx = (tt.clientX - start.current.mx) / scale;
            const dy = (tt.clientY - start.current.my) / scale;
            let { x, y, w, h } = start.current;
            if (mode === 'move') {
                x = Math.max(0, Math.min(imgW - w, x + dx));
                y = Math.max(0, Math.min(imgH - h, y + dy));
            } else {
                if (hasRatioLock) {
                    let ratio = 1;
                    if (aspectRatio === '1:1') ratio = 1;
                    else if (aspectRatio === '4:3') ratio = 4 / 3;
                    else if (aspectRatio === '3:4') ratio = 3 / 4;
                    else if (aspectRatio === '16:9') ratio = 16 / 9;
                    else if (aspectRatio === '9:16') ratio = 9 / 16;
                    else if (aspectRatio === 'target' && activeBucket) {
                        ratio = activeBucket.w / activeBucket.h;
                    }

                    let nw = w, nh = h;
                    if (mode === 'se') {
                        nw = Math.max(20, Math.min(imgW - x, w + dx));
                        nh = nw / ratio;
                        if (nh > imgH - y) {
                            nh = imgH - y;
                            nw = nh * ratio;
                        }
                        w = nw; h = nh;
                    } else if (mode === 'sw') {
                        nw = Math.max(20, Math.min(x + w, w - dx));
                        nh = nw / ratio;
                        if (nh > imgH - y) {
                            nh = imgH - y;
                            nw = nh * ratio;
                        }
                        x = x + w - nw; w = nw; h = nh;
                    } else if (mode === 'ne') {
                        nw = Math.max(20, Math.min(imgW - x, w + dx));
                        nh = nw / ratio;
                        if (nh > y + h) {
                            nh = y + h;
                            nw = nh * ratio;
                        }
                        y = y + h - nh; w = nw; h = nh;
                    } else if (mode === 'nw') {
                        nw = Math.max(20, Math.min(x + w, w - dx));
                        nh = nw / ratio;
                        if (nh > y + h) {
                            nh = y + h;
                            nw = nh * ratio;
                        }
                        x = x + w - nw; y = y + h - nh; w = nw; h = nh;
                    }
                } else {
                    if (mode.includes('e')) w = Math.max(20, Math.min(imgW - x, w + dx));
                    if (mode.includes('s')) h = Math.max(20, Math.min(imgH - y, h + dy));
                    if (mode.includes('w')) {
                        const nx = Math.max(0, Math.min(x + w - 20, x + dx));
                        w = w + (x - nx); x = nx;
                    }
                    if (mode.includes('n')) {
                        const ny = Math.max(0, Math.min(y + h - 20, y + dy));
                        h = h + (y - ny); y = ny;
                    }
                }
            }
            onChange({ x, y, w, h });
        };
        const up = () => {
            window.removeEventListener('mousemove', move);
            window.removeEventListener('mouseup', up);
            window.removeEventListener('touchmove', move);
            window.removeEventListener('touchend', up);
            // 松手：纹理框由父级吸附到合法桶（普通框父级会忽略）
            if (onGeometryCommit) onGeometryCommit();
        };
        window.addEventListener('mousemove', move);
        window.addEventListener('mouseup', up);
        window.addEventListener('touchmove', move, { passive: false });
        window.addEventListener('touchend', up);
    };

    // 非 active 框只接管"中心点击切换"，不接 mousedown 拖移。
    const pickOnly = (e) => {
        e.stopPropagation(); e.preventDefault();
        onPick();
    };

    return (
        <div
            onMouseDown={isActive ? makeHandle('move') : undefined}
            onTouchStart={isActive ? makeHandle('move') : undefined}
            style={{
                position: 'absolute',
                left: crop.x * scale,
                top: crop.y * scale,
                width: crop.w * scale,
                height: crop.h * scale,
                border: `2px ${isTexture ? 'dashed' : 'solid'} ${color}`,
                outline: isActive ? `1px solid ${color}` : 'none',
                outlineOffset: 1,
                opacity: isActive ? 1 : 0.78,
                cursor: isActive ? 'move' : 'default',
                boxShadow: isActive ? `0 0 0 9999px rgba(0,0,0,.42)` : 'none',
                touchAction: 'none',
                transition: 'box-shadow .15s var(--ease), opacity .15s var(--ease)',
                zIndex: isActive ? 50 : 10,
                pointerEvents: isActive ? 'auto' : 'none',
            }}
        >
            <div style={{
                position: 'absolute', top: -1, left: -1,
                background: color, color: 'var(--accent-ink)',
                fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 600,
                padding: '2px 6px', borderRadius: '0 0 4px 0',
                pointerEvents: 'none',
            }}>
                {isTexture ? `纹${crop.idx + 1}` : crop.idx + 1}
            </div>

            {!isActive && (
                <button
                    onMouseDown={pickOnly}
                    onTouchStart={pickOnly}
                    title={`切换到框 ${crop.idx + 1}`}
                    aria-label={`切换到框 ${crop.idx + 1}`}
                    style={{
                        position: 'absolute',
                        left: '25%', top: '25%',
                        width: '50%', height: '50%',
                        appearance: 'none',
                        background: 'transparent',
                        border: 'none',
                        cursor: 'pointer',
                        pointerEvents: 'auto',
                    }}
                />
            )}

            {isActive && (
                <div style={{
                    position: 'absolute', bottom: -22, left: 0,
                    fontFamily: 'var(--font-mono)', fontSize: 10,
                    color: color, background: 'rgba(0,0,0,.7)',
                    padding: '2px 6px', borderRadius: 3, whiteSpace: 'nowrap',
                    pointerEvents: 'none',
                }}>
                    {isTexture
                        ? `纹理 ${Math.round(crop.w)}×${Math.round(crop.h)} · 原生`
                        : `${Math.round(crop.w)}×${Math.round(crop.h)} · ${(crop.w / crop.h).toFixed(2)}`}
                </div>
            )}

            {isActive && (
                <>
                    <button
                        onMouseDown={(e) => e.stopPropagation()}
                        onTouchStart={(e) => e.stopPropagation()}
                        onClick={(e) => { e.stopPropagation(); onToggleKind && onToggleKind(); }}
                        style={{
                            position: 'absolute', top: -14, left: -14,
                            width: 28, height: 28,
                            background: isTexture ? '#F4B860' : 'var(--surface-0)',
                            color: isTexture ? 'var(--accent-ink)' : 'var(--text-2)',
                            borderRadius: '50%', border: '2px solid var(--surface-0)',
                            boxShadow: '0 1px 4px rgba(0,0,0,.5)',
                            fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 700,
                            touchAction: 'none', zIndex: 60,
                        }}
                        title={isTexture ? '纹理框（原生裁切·零重采样）→ 点击转普通' : '普通框（重采样贴桶）→ 点击转纹理'}
                    >
                        {isTexture ? '纹' : '全'}
                    </button>
                    <button
                        className="btn-icon"
                        onMouseDown={(e) => e.stopPropagation()}
                        // 触摸屏必须显式 stopPropagation，否则事件冒泡到 CropBox
                        // 的 onTouchStart={makeHandle('move')}，触发框拖动 + preventDefault
                        // 让浏览器不再生成 click，删除永远不会发生。
                        onTouchStart={(e) => e.stopPropagation()}
                        onClick={(e) => { e.stopPropagation(); onDelete(); }}
                        style={{
                            position: 'absolute', top: -14, right: -14,
                            width: 28, height: 28, background: color,
                            color: 'var(--accent-ink)',
                            borderRadius: '50%',
                            border: '2px solid var(--surface-0)',
                            boxShadow: '0 1px 4px rgba(0,0,0,.5)',
                            touchAction: 'none',
                            zIndex: 60,
                        }}
                        title="删除此框"
                    >
                        <I.Close size={14} strokeWidth={2.5}/>
                    </button>
                    {['nw','ne','sw','se'].map(c => (
                        <div key={c}
                            className="crop-handle-corner"
                            onMouseDown={makeHandle(c)} onTouchStart={makeHandle(c)}
                            style={{
                                left: c.includes('w') ? 0 : '100%',
                                top: c.includes('n') ? 0 : '100%',
                                cursor: c === 'nw' || c === 'se' ? 'nwse-resize' : 'nesw-resize',
                            }}
                        />
                    ))}
                    {!hasRatioLock && (
                        <>
                            <div className="crop-handle-edge h" onMouseDown={makeHandle('n')} onTouchStart={makeHandle('n')} style={{ left: '50%', top: 0, cursor: 'ns-resize' }}/>
                            <div className="crop-handle-edge h" onMouseDown={makeHandle('s')} onTouchStart={makeHandle('s')} style={{ left: '50%', top: '100%', cursor: 'ns-resize' }}/>
                            <div className="crop-handle-edge v" onMouseDown={makeHandle('w')} onTouchStart={makeHandle('w')} style={{ left: 0, top: '50%', cursor: 'ew-resize' }}/>
                            <div className="crop-handle-edge v" onMouseDown={makeHandle('e')} onTouchStart={makeHandle('e')} style={{ left: '100%', top: '50%', cursor: 'ew-resize' }}/>
                        </>
                    )}
                </>
            )}
        </div>
    );
}

function CropScreen({ session, refresh, targetMP, setTargetMP, step, setStep }) {
    const narrow = useIsNarrow();
    const total = session?.total_count || 0;
    // 图片 URL 的 cache-buster：用 session_id 后缀。
    // 原因：换了数据集后 /api/file/{idx} 的 URL 不变，但 server 端 session.files[idx]
    // 指向新文件。浏览器对 <img src=> 会启发式缓存（thumb 路由还显式 max-age=86400），
    // 看到 URL 一字不变就用缓存，于是工作台上仍然显示旧数据集的图片。
    // 加上 ?s=session_id 后，新会话的 URL 与旧会话完全不同 → 浏览器重新请求；
    // 同一会话内的同一索引继续命中浏览器缓存，性能无损。
    const sid = session?.session_id || '';
    const [imgIdx, setImgIdx] = React.useState(session?.current_index || 0);
    const [info, setInfo] = React.useState(null);          // { width, height, filename, filepath, suggested_box, saved_crops, ... }
    const [crops, setCrops] = React.useState([]);          // [{x,y,w,h,idx}]
    const [activeIdx, setActiveIdx] = React.useState(0);
    const [zoom, setZoom] = React.useState(1);
    const [busy, setBusy] = React.useState(false);
    const [aspectRatio, setAspectRatio] = React.useState(localStorage.getItem('drop.cropAspectRatio') || 'free');

    const dragStart = React.useRef(null);
    // drawingBox 既要驱动渲染（虚线预览框）又要在 mouseup 回调里读"最新值"。
    // 之前只用 useState：mouseup 闭包里看到的是 mousedown 时的快照（一般是 null），
    // 导致 (1) 正常拖拽其实从不创建新框；(2) 上次 mouseup 丢失时残留的非 null
    // 旧值，让下次随便点一下都凭空冒出一个框。
    // 改成 ref + state 并行：move 同步写两份，up 从 ref 读最新值。
    const drawingBoxRef = React.useRef(null);
    const [drawingBox, setDrawingBoxState] = React.useState(null);
    const setDrawingBox = (v) => { drawingBoxRef.current = v; setDrawingBoxState(v); };
    const localNavTouchedRef = React.useRef(0);

    React.useEffect(() => {
        localStorage.setItem('drop.cropAspectRatio', aspectRatio);
    }, [aspectRatio]);

    React.useEffect(() => {
        const el = document.getElementById(`filmstrip-thumb-${imgIdx}`);
        if (el) {
            el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
    }, [imgIdx]);

    React.useEffect(() => {
        if (aspectRatio === 'free' || crops.length === 0 || activeIdx >= crops.length) return;
        const active = crops[activeIdx];
        let ratio = 1;
        if (aspectRatio === '1:1') ratio = 1;
        else if (aspectRatio === '4:3') ratio = 4 / 3;
        else if (aspectRatio === '3:4') ratio = 3 / 4;
        else if (aspectRatio === '16:9') ratio = 16 / 9;
        else if (aspectRatio === '9:16') ratio = 9 / 16;
        else if (aspectRatio === 'target' && info?.width) {
            const b = calcBucket(active.w, active.h, targetMP, step);
            if (b) ratio = b.w / b.h;
        }

        const nw = active.w;
        const nh = nw / ratio;
        let nx = active.x;
        let ny = active.y;

        let finalW = nw;
        let finalH = nh;
        if (nx + finalW > info.width) {
            finalW = info.width - nx;
            finalH = finalW / ratio;
        }
        if (ny + finalH > info.height) {
            finalH = info.height - ny;
            finalW = finalH * ratio;
        }

        updateCrop(activeIdx, { w: finalW, h: finalH });
    }, [aspectRatio, activeIdx]);
    const [containerSize, setContainerSize] = React.useState({ w: 0, h: 0 });
    const [filmstripOpen, setFilmstripOpen] = React.useState(false);
    const [inspectorOpen, setInspectorOpen] = React.useState(false);

    // Close mobile drawers whenever we switch images so the canvas comes back into view.
    React.useEffect(() => { setFilmstripOpen(false); }, [imgIdx]);

    const containerRef = React.useRef(null);

    React.useEffect(() => {
        if (typeof session?.current_index === 'number' && session.current_index !== imgIdx) {
            const recentlyTouched = Date.now() - localNavTouchedRef.current < 3000;
            if (!recentlyTouched) {
                setImgIdx(Math.min(Math.max(0, session.current_index), Math.max(0, total - 1)));
            }
        }
        // eslint-disable-next-line
    }, [session?.current_index]);

    // Load image info when index changes.
    // 关键：deps 里包含 sid，换数据集后 session_id 变 → 即使 imgIdx 还是 0、total 还
    // 是同一个数字（两个数据集恰好同图片数）也强制重新拉 /api/image/{idx}，
    // 避免 info 残留旧数据集的 width/height/filename。
    React.useEffect(() => {
        if (!session?.is_initialized || imgIdx >= total) return;
        let alive = true;
        api.get(`/api/image/${imgIdx}`).then(d => {
            if (!alive) return;
            setInfo(d);
            const saved = d.saved_crops || [];
            const list = saved.length > 0
                ? saved.map((c, i) => ({ x: c.x, y: c.y, w: c.w, h: c.h, idx: i, kind: c.kind || 'full' }))
                : (d.suggested_box ? [{
                    x: d.suggested_box[0], y: d.suggested_box[1],
                    w: d.suggested_box[2] - d.suggested_box[0],
                    h: d.suggested_box[3] - d.suggested_box[1],
                    idx: 0, kind: 'full'
                  }] : []);
            setCrops(list);
            setActiveIdx(0);
        }).catch(e => { ToastBus.emit(e.message, 'error'); });
        return () => { alive = false; };
    }, [imgIdx, session?.is_initialized, total, sid]);

    // 换会话时把 imgIdx 拉回新会话的 current_index。
    // 原有的 useEffect([session?.current_index]) 在「新旧 current_index 同值」时不会触发
    // （比如两个会话都从 0 开始），但我们 *仍然* 需要这一刻能感知到「这是不同的会话」
    // 然后重置裁切框、info 等本地 state。改成监听 sid 变化更稳。
    const lastSidRef = React.useRef(sid);
    React.useEffect(() => {
        if (lastSidRef.current === sid) return;
        lastSidRef.current = sid;
        // 新会话：拉回新 current_index（一般 0；有断点续作时是上次留下的索引）
        const next = Math.max(0, Math.min(session?.current_index || 0, Math.max(0, total - 1)));
        setImgIdx(next);
        // 清掉本地图像 state，避免渲染瞬间还显示旧 info
        setInfo(null);
        setCrops([]);
        setActiveIdx(0);
    }, [sid, session?.current_index, total]);

    // Container resize observer. Deps include the early-return predicates so
    // when session/total flip from "not ready" → "ready" and the canvas div
    // finally mounts, we re-attach the observer to the live ref. With `[]`
    // deps the observer would attach against the null initial ref and never
    // re-bind, leaving scale stuck at the fallback 1 (image at 1:1, huge).
    const canvasReady = !!session?.is_initialized && total > 0;
    React.useEffect(() => {
        if (!canvasReady) return;
        const node = containerRef.current;
        if (!node) return;
        const ro = new ResizeObserver(es => {
            const rect = node.getBoundingClientRect();
            setContainerSize(prev => {
                if (prev.w === rect.width && prev.h === rect.height) {
                    return prev;
                }
                return { w: rect.width, h: rect.height };
            });
        });
        ro.observe(node);
        return () => ro.disconnect();
    }, [canvasReady]);

    const scale = React.useMemo(() => {
        // Wait for both real image metadata and a settled container box.
        // During mount/layout-transition the container's height can briefly
        // be 0 — if we don't guard, (h - 40)/imgH goes NEGATIVE, Math.min
        // picks it, and the image scales to a dot (or vanishes entirely).
        if (!info?.width || !info?.height) return 1;
        if (containerSize.w < 50 || containerSize.h < 50) return 1;
        const sx = (containerSize.w - 40) / info.width;
        const sy = (containerSize.h - 40) / info.height;
        const fit = Math.min(sx, sy);
        return Math.max(0.01, fit * zoom);
    }, [info?.width, info?.height, containerSize, zoom]);

    function addCrop() {
        if (!info?.width) return;
        // 用「上次操作过的框」（= 当前 active 框）的尺寸作为新框尺寸。
        // 连续添加多个同规格的框时一致——比如全图相同尺寸地圈三个细节，
        // 不必每次新建出半图大的框再缩两次。Fallback 到 50% 用在没有任何
        // 参照框（首次添加）的情形。
        const ref = crops[activeIdx];
        let w, h;
        if (ref && ref.w > 0 && ref.h > 0) {
            w = Math.min(ref.w, info.width);
            h = Math.min(ref.h, info.height);
        } else {
            w = info.width * 0.5;
            h = info.height * 0.5;
        }
        // 新框居中。若图片大小不能容纳参考尺寸（极端情况），夹到 [0, max] 内。
        const x = Math.max(0, Math.min(info.width - w, (info.width - w) / 2));
        const y = Math.max(0, Math.min(info.height - h, (info.height - h) / 2));
        const nc = { x, y, w, h, idx: crops.length, kind: 'full' };
        setCrops(cs => [...cs, nc]);
        setActiveIdx(crops.length);
    }

    function updateCrop(i, c) {
        setCrops(cs => cs.map((x, j) => j === i ? { ...x, ...c } : x));
    }
    function delCrop(i) {
        const next = crops.filter((_, j) => j !== i).map((c, k) => ({ ...c, idx: k }));
        setCrops(next);
        setActiveIdx(Math.max(0, Math.min(activeIdx, next.length - 1)));
    }

    // 切换框的类型。转纹理时立即吸附到合法桶；放不下则提示并保持普通。
    function toggleKind(i) {
        setCrops(cs => cs.map((c, j) => {
            if (j !== i) return c;
            if (c.kind === 'texture') return { ...c, kind: 'full' };
            const snapped = info?.width
                ? snapTextureBucket(c, info.width, info.height, targetMP, step)
                : null;
            if (!snapped) {
                ToastBus.emit('此区域放不下任何桶（源图太小），无法设为纹理', 'warn');
                return c;
            }
            return { ...c, kind: 'texture', ...snapped };
        }));
    }

    // 纹理框松手后吸附到合法桶（普通框不动）。从最新 state 读，避免闭包快照。
    function snapCropIfTexture(i) {
        setCrops(cs => cs.map((c, j) => {
            if (j !== i || c.kind !== 'texture' || !info?.width) return c;
            const snapped = snapTextureBucket(c, info.width, info.height, targetMP, step);
            if (!snapped) {
                ToastBus.emit('此区域放不下任何桶（源图太小）', 'warn');
                return c;
            }
            return { ...c, ...snapped };
        }));
    }

    async function commit(action) {
        if (busy || !info) return;
        setBusy(true);
        localNavTouchedRef.current = Date.now();
        try {
            const body = {
                filepath: info.filepath,
                filename: info.filename,
                crops: action === 'accept' ? crops.map(c => ({
                    x: Math.round(c.x), y: Math.round(c.y),
                    w: Math.round(c.w), h: Math.round(c.h),
                    kind: c.kind || 'full',
                })) : [],
                status: action === 'accept' ? 'cropped' : 'skip',
            };
            const r = await api.post('/api/save', body);
            ToastBus.emit(action === 'accept' ? `+${body.crops.length} 入队列` : '已跳过', 'ok');
            if (typeof r.current_index === 'number') {
                setImgIdx(r.current_index);
                if (r.current_index >= total) {
                    ToastBus.emit('所有图片处理完毕', 'ok');
                    refresh();
                }
            } else {
                setImgIdx(i => Math.min(i + 1, total - 1));
            }
            refresh();
        } catch (e) {
            ToastBus.emit(e.message || '保存失败', 'error');
        } finally { setBusy(false); }
    }

    async function undo() {
        localNavTouchedRef.current = Date.now();
        try {
            const r = await api.post('/api/undo');
            ToastBus.emit('已撤销', 'ok');
            if (typeof r.current_index === 'number') setImgIdx(r.current_index);
            refresh();
        } catch (e) { ToastBus.emit(e.message, 'error'); }
    }

    async function gotoIdx(i) {
        const target = Math.max(0, Math.min(total - 1, i));
        if (target === imgIdx) return;
        localNavTouchedRef.current = Date.now();
        setImgIdx(target);
        try {
            await api.post(`/api/goto/${target}`);
            refresh();
        } catch {}
    }

    // Keyboard shortcuts
    useKey(' ', () => commit('accept'), [crops, info]);
    useKey('s', () => commit('skip'), [info]);
    useKey('z', () => undo(), []);
    useKey('a', () => addCrop(), [info, crops]);
    useKey('ArrowLeft', () => gotoIdx(imgIdx - 1), [imgIdx, total]);
    useKey('ArrowRight', () => gotoIdx(imgIdx + 1), [imgIdx, total]);
    useKey((e) => e.key >= '1' && e.key <= '9', (e) => {
        const idx = parseInt(e.key) - 1;
        if (idx < crops.length) setActiveIdx(idx);
    }, [crops.length]);

    if (!session?.is_initialized) {
        return <div className="center" style={{ flex: 1, color: 'var(--text-3)' }}>请先在主页初始化会话</div>;
    }
    if (!total) {
        return <div className="center" style={{ flex: 1, color: 'var(--text-3)' }}>当前会话没有图片</div>;
    }

    const active = crops[activeIdx];
    const bucket = active ? calcBucket(active.w, active.h, targetMP, step) : null;
    const progress = total > 0 ? (imgIdx / total) : 0;

    // Filmstrip body — reused inside the desktop left aside and the mobile slide-in drawer.
    const filmstripBody = (
        <>
            <div className="row" style={{
                padding: '10px 12px', borderBottom: '1px solid var(--line)',
                justifyContent: 'space-between', gap: 6,
            }}>
                <span className="num" style={{ fontSize: 11, color: 'var(--text-3)' }}>{imgIdx + 1}/{total}</span>
                <div className="bar" style={{ flex: 1 }}><div className="fill" style={{ width: `${progress * 100}%` }}/></div>
            </div>
            <div className="col" style={{ overflow: 'auto', padding: 6, gap: 4, flex: 1, minHeight: 0 }}>
                {Array.from({ length: total }, (_, i) => i).map(i => {
                    const isCur = i === imgIdx;
                    return (
                        <button
                            key={i}
                            id={`filmstrip-thumb-${i}`}
                            onClick={() => gotoIdx(i)}
                            style={{
                                appearance: 'none', border: 'none',
                                padding: 0, cursor: 'pointer',
                                background: 'transparent',
                                position: 'relative',
                                aspectRatio: '1 / 1',
                                flexShrink: 0,
                                borderRadius: 4, overflow: 'hidden',
                                outline: isCur ? '2px solid var(--accent)' : '1px solid var(--line)',
                                outlineOffset: isCur ? -1 : 0,
                            }}
                        >
                            <LazyImg src={`/api/thumb/source/${i}?s=${sid}`} style={{ width: '100%', height: '100%' }}/>
                            <span style={{
                                position: 'absolute', top: 2, left: 3,
                                fontFamily: 'var(--font-mono)', fontSize: 9,
                                color: '#fff', textShadow: '0 1px 2px rgba(0,0,0,.8)',
                            }}>{i + 1}</span>
                        </button>
                    );
                })}
            </div>
        </>
    );

    // Inspector body — reused inside desktop right aside and mobile drawer.
    const inspectorBody = (
        <>
            <Panel eyebrow="当前图片" title={info ? basename(info.filename || '') : '—'}
                style={{ borderRadius: 0, border: 'none', borderBottom: '1px solid var(--line)' }}>
                <InspectorRow label="尺寸" value={info ? `${info.width} × ${info.height}` : '—'}/>
                <InspectorRow label="比例" value={info?.width ? (info.width / info.height).toFixed(3) : '—'}/>
                <InspectorRow label="进度" value={`${imgIdx + 1} / ${total}`}/>
                <InspectorRow label="队列" value={`${session.queue_len || 0}`} accent/>
            </Panel>

            <Panel eyebrow="目标分桶" title={`${targetMP} MP · ${step}px`}
                style={{ borderRadius: 0, border: 'none', borderBottom: '1px solid var(--line)' }}>
                <BucketResolutionPicker
                    targetMP={targetMP} setTargetMP={setTargetMP}
                    step={step} setStep={setStep}
                    compact
                />
            </Panel>

            <Panel eyebrow="裁切参数" title="比例锁定"
                style={{ borderRadius: 0, border: 'none', borderBottom: '1px solid var(--line)' }}>
                <div className="col gap-2" style={{ padding: '10px 14px 12px' }}>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 6 }}>
                        {[
                            { id: 'free', label: '自由' },
                            { id: 'target', label: '目标桶' },
                            { id: '1:1', label: '1:1' },
                            { id: '4:3', label: '4:3' },
                            { id: '3:4', label: '3:4' },
                            { id: '16:9', label: '16:9' },
                            { id: '9:16', label: '9:16' },
                        ].map(opt => {
                            const isCur = aspectRatio === opt.id;
                            return (
                                <button
                                    key={opt.id}
                                    className={`btn btn-sm ${isCur ? 'btn-primary' : 'btn-soft'}`}
                                    onClick={() => setAspectRatio(opt.id)}
                                    style={{
                                        border: isCur ? '1px solid var(--accent)' : '1px solid var(--line)',
                                        fontWeight: isCur ? '600' : 'normal',
                                    }}
                                >
                                    {opt.label}
                                </button>
                            );
                        })}
                    </div>
                </div>
            </Panel>

            {active && (
                <Panel eyebrow={`框 ${activeIdx + 1} / ${crops.length}`}
                    title={active.kind === 'texture' ? '纹理桶 · 原生' : '桶预测'}
                    style={{ borderRadius: 0, border: 'none', borderBottom: '1px solid var(--line)' }}>
                    <div className="row gap-2" style={{ padding: '10px 14px 6px' }}>
                        <button className={`btn btn-sm ${active.kind !== 'texture' ? 'btn-primary' : 'btn-soft'}`}
                            style={{ flex: 1 }}
                            onClick={() => { if (active.kind === 'texture') toggleKind(activeIdx); }}>
                            全图
                        </button>
                        <button className={`btn btn-sm ${active.kind === 'texture' ? 'btn-primary' : 'btn-soft'}`}
                            style={{ flex: 1 }}
                            onClick={() => { if (active.kind !== 'texture') toggleKind(activeIdx); }}>
                            纹理
                        </button>
                    </div>
                    <InspectorRow label="选区" value={`${Math.round(active.w)} × ${Math.round(active.h)}`}/>
                    {active.kind === 'texture' ? (
                        <>
                            <InspectorRow label="纹理桶" value={`${Math.round(active.w)} × ${Math.round(active.h)}`} accent/>
                            <InspectorRow label="重采样" value="无（原生像素）"/>
                        </>
                    ) : (bucket && (
                        <>
                            <InspectorRow label="选区比例" value={(active.w / active.h).toFixed(3)}/>
                            <InspectorRow label="目标桶" value={`${bucket.w} × ${bucket.h}`} accent/>
                            <InspectorRow label="桶比例" value={(bucket.w / bucket.h).toFixed(3)}/>
                        </>
                    ))}
                </Panel>
            )}

            <Panel eyebrow="快捷键" title={null} style={{ borderRadius: 0, border: 'none', flex: 1 }}>
                <div className="col gap-1" style={{ padding: '4px 14px 14px' }}>
                    {[
                        ['Space', '加入队列'],
                        ['S',     '跳过'],
                        ['A',     '新增框'],
                        ['Z',     '撤销'],
                        ['1-9',   '选中框'],
                        ['← →',   '切换图片'],
                    ].map(([k, l]) => (
                        <div key={k} className="row" style={{ justifyContent: 'space-between', padding: '5px 0' }}>
                            <span style={{ fontSize: 12, color: 'var(--text-2)' }}>{l}</span>
                            <span className="kbd">{k}</span>
                        </div>
                    ))}
                </div>
            </Panel>
        </>
    );

    // Center workspace (toolbar + canvas + action bar). Identical content
    // for both layouts; mobile prepends drawer-toggle buttons in the toolbar
    // and pads the canvas bottom so the action bar + bottom nav clear.
    const workspace = (
        <main className="col" style={{ flex: 1, minWidth: 0 }}>
            {/* Toolbar */}
            <div className="row" style={{
                height: 48, padding: '0 16px', flexShrink: 0,
                borderBottom: '1px solid var(--line)',
                background: 'var(--surface-0)',
                gap: 12, flexWrap: 'wrap',
            }}>
                {narrow && (
                    <button className="btn btn-icon" onClick={() => setFilmstripOpen(true)} title="缩略图列表">
                        <I.Layers size={16}/>
                    </button>
                )}
                <button className="btn btn-icon" onClick={() => gotoIdx(imgIdx - 1)} title="上一张 ←"><I.ArrowLeft size={16}/></button>
                <button className="btn btn-icon" onClick={() => gotoIdx(imgIdx + 1)} title="下一张 →"><I.ArrowRight size={16}/></button>
                <span style={{ width: 1, height: 22, background: 'var(--line)' }}/>
                <button className="btn btn-soft btn-sm" onClick={addCrop} title="新增框 A"><I.Plus size={14}/> 新增框</button>
                <button className="btn btn-soft btn-sm" onClick={undo} title="撤销 Z" disabled={!session.can_undo}><I.Undo size={14}/> 撤销</button>
                <span className="flex-1"/>
                <div className="seg">
                    <button className={zoom === 0.5 ? 'on' : ''} onClick={() => setZoom(0.5)}>50%</button>
                    <button className={zoom === 1 ? 'on' : ''} onClick={() => setZoom(1)}>适应</button>
                    <button className={zoom === 1.5 ? 'on' : ''} onClick={() => setZoom(1.5)}>150%</button>
                    <button className={zoom === 2 ? 'on' : ''} onClick={() => setZoom(2)}>200%</button>
                </div>
                {narrow && (
                    <button className="btn btn-icon" onClick={() => setInspectorOpen(true)} title="检查面板">
                        <I.Cog size={16}/>
                    </button>
                )}
            </div>

            {/* Canvas */}
            <div ref={containerRef} className="canvas-backdrop center" style={{ flex: 1, position: 'relative', overflow: 'auto' }}>
                {info && info.width > 0 && (
                    <div
                        onMouseDown={(e) => {
                            if (e.button !== 0) return;
                            e.preventDefault();
                            const rect = e.currentTarget.getBoundingClientRect();
                            const startX = (e.clientX - rect.left) / scale;
                            const startY = (e.clientY - rect.top) / scale;
                            dragStart.current = { startX, startY };
                            
                            const move = (ev) => {
                                if (!dragStart.current) return;
                                const curX = (ev.clientX - rect.left) / scale;
                                const curY = (ev.clientY - rect.top) / scale;
                                
                                let x = Math.min(dragStart.current.startX, curX);
                                let y = Math.min(dragStart.current.startY, curY);
                                let w = Math.abs(curX - dragStart.current.startX);
                                let h = Math.abs(curY - dragStart.current.startY);
                                
                                x = Math.max(0, Math.min(info.width, x));
                                y = Math.max(0, Math.min(info.height, y));
                                w = Math.min(info.width - x, w);
                                h = Math.min(info.height - y, h);
                                
                                if (aspectRatio !== 'free') {
                                    let ratio = 1;
                                    if (aspectRatio === '1:1') ratio = 1;
                                    else if (aspectRatio === '4:3') ratio = 4 / 3;
                                    else if (aspectRatio === '3:4') ratio = 3 / 4;
                                    else if (aspectRatio === '16:9') ratio = 16 / 9;
                                    else if (aspectRatio === '9:16') ratio = 9 / 16;
                                    else if (aspectRatio === 'target') {
                                        const refW = w > 20 ? w : info.width * 0.5;
                                        const refH = h > 20 ? h : info.height * 0.5;
                                        const b = calcBucket(refW, refH, targetMP, step);
                                        if (b) ratio = b.w / b.h;
                                    }
                                    
                                    h = w / ratio;
                                    if (y + h > info.height) {
                                        h = info.height - y;
                                        w = h * ratio;
                                    }
                                    if (curX < dragStart.current.startX) {
                                        x = dragStart.current.startX - w;
                                    }
                                    if (curY < dragStart.current.startY) {
                                        y = dragStart.current.startY - h;
                                    }
                                }
                                setDrawingBox({ x, y, w, h });
                            };
                            
                            const up = () => {
                                window.removeEventListener('mousemove', move);
                                window.removeEventListener('mouseup', up);
                                window.removeEventListener('blur', up);

                                // 从 ref 读最新值，避免闭包快照导致永远是 null（或残留旧值）。
                                const box = drawingBoxRef.current;
                                if (dragStart.current && box && box.w > 15 && box.h > 15) {
                                    setCrops(cs => [...cs, {
                                        x: box.x, y: box.y, w: box.w, h: box.h, idx: cs.length,
                                        kind: 'full',
                                    }]);
                                    setActiveIdx(crops.length);
                                }
                                dragStart.current = null;
                                setDrawingBox(null);
                            };

                            window.addEventListener('mousemove', move);
                            window.addEventListener('mouseup', up);
                            // blur 兜底：alt+tab / 窗口失焦时浏览器可能吞掉 mouseup，
                            // 让 dragStart + drawingBox 残留，下次点击就会"凭空"创建框。
                            window.addEventListener('blur', up);
                        }}
                        style={{ position: 'relative', width: info.width * scale, height: info.height * scale, cursor: 'crosshair' }}
                    >
                        <img
                            src={`/api/file/${imgIdx}?s=${sid}`}
                            style={{ width: '100%', height: '100%', display: 'block', userSelect: 'none' }}
                            draggable={false}
                            alt=""
                        />
                        {crops.map((c, i) => (
                            <CropBox
                                key={i}
                                crop={c}
                                color={CROP_COLORS[i % CROP_COLORS.length]}
                                scale={scale}
                                isActive={i === activeIdx}
                                aspectRatio={aspectRatio}
                                activeBucket={bucket}
                                onPick={() => setActiveIdx(i)}
                                onChange={(nc) => updateCrop(i, nc)}
                                onDelete={() => delCrop(i)}
                                onToggleKind={() => toggleKind(i)}
                                onGeometryCommit={() => snapCropIfTexture(i)}
                                imgW={info.width}
                                imgH={info.height}
                            />
                        ))}
                        {drawingBox && (
                            <div style={{
                                position: 'absolute',
                                left: drawingBox.x * scale,
                                top: drawingBox.y * scale,
                                width: drawingBox.w * scale,
                                height: drawingBox.h * scale,
                                border: '2px dashed var(--accent)',
                                background: 'rgba(110, 231, 200, 0.08)',
                                pointerEvents: 'none',
                                zIndex: 100,
                            }}/>
                        )}
                    </div>
                )}
            </div>

            {/* Action bar */}
            <div className="row" style={{
                height: 64, padding: narrow ? '0 12px' : '0 22px', flexShrink: 0,
                borderTop: '1px solid var(--line)',
                background: 'var(--surface-0)',
                gap: 10,
            }}>
                <button className="btn btn-soft" onClick={() => commit('skip')} disabled={busy}>
                    <I.Skip size={14}/> 跳过 <span className="kbd">S</span>
                </button>
                <span className="flex-1"/>
                {!narrow && (
                    <span style={{ fontSize: 12, color: 'var(--text-3)' }}>
                        {info ? `${info.width}×${info.height}` : '—'} · 共 {crops.length} 框
                    </span>
                )}
                <span className="flex-1"/>
                <button className="btn btn-primary btn-lg" onClick={() => commit('accept')} disabled={busy || crops.length === 0}>
                    {busy ? <span className="spin"/> : <I.Check size={14}/>}
                    {narrow ? `加入 (${crops.length})` : <>加入队列 <span className="kbd" style={{ background: 'rgba(0,0,0,.2)', borderColor: 'rgba(0,0,0,.3)' }}>Space</span></>}
                </button>
            </div>
        </main>
    );

    // ---- MOBILE: workspace fills the viewport; filmstrip + inspector are slide-in drawers. ----
    if (narrow) {
        return (
            <div className="col" style={{ flex: 1, minHeight: 0, minWidth: 0, position: 'relative', overflow: 'hidden' }}>
                {workspace}

                {/* Filmstrip drawer */}
                {filmstripOpen && (
                    <>
                        <div className="modal-backdrop" onClick={() => setFilmstripOpen(false)} style={{ zIndex: 240 }}/>
                        <aside style={{
                            position: 'fixed', top: 0, bottom: 56, left: 0,
                            width: 200, zIndex: 250,
                            background: 'var(--surface-0)',
                            borderRight: '1px solid var(--line)',
                            display: 'flex', flexDirection: 'column',
                            boxShadow: '4px 0 16px rgba(0,0,0,.45)',
                            animation: 'slide-in-left .18s var(--ease)',
                        }}>
                            <div className="row" style={{
                                height: 44, padding: '0 8px 0 14px',
                                borderBottom: '1px solid var(--line)',
                            }}>
                                <span className="eyebrow">缩略图</span>
                                <span className="flex-1"/>
                                <button className="btn btn-icon" onClick={() => setFilmstripOpen(false)}><I.Close size={16}/></button>
                            </div>
                            {filmstripBody}
                        </aside>
                    </>
                )}

                {/* Inspector drawer */}
                {inspectorOpen && (
                    <>
                        <div className="modal-backdrop" onClick={() => setInspectorOpen(false)} style={{ zIndex: 240 }}/>
                        <aside style={{
                            position: 'fixed', top: 0, bottom: 56, right: 0,
                            width: 300, maxWidth: '92vw', zIndex: 250,
                            background: 'var(--surface-0)',
                            borderLeft: '1px solid var(--line)',
                            display: 'flex', flexDirection: 'column',
                            overflow: 'auto',
                            boxShadow: '-4px 0 16px rgba(0,0,0,.45)',
                            animation: 'slide-in-right .18s var(--ease)',
                        }}>
                            <div className="row" style={{
                                height: 44, padding: '0 8px 0 14px',
                                borderBottom: '1px solid var(--line)',
                                flexShrink: 0,
                            }}>
                                <span className="eyebrow">检查面板</span>
                                <span className="flex-1"/>
                                <button className="btn btn-icon" onClick={() => setInspectorOpen(false)}><I.Close size={16}/></button>
                            </div>
                            {inspectorBody}
                        </aside>
                    </>
                )}
            </div>
        );
    }

    // ---- DESKTOP: filmstrip | workspace | inspector ----
    // No .row class: that forces align-items:center, which lets the filmstrip's
    // tall thumbnail list overflow up into the TopBar and prevents its inner
    // overflow:auto from finding a bounded box to scroll within.
    return (
        <div style={{ display: 'flex', flex: 1, minHeight: 0, overflow: 'hidden' }}>
            <aside style={{
                width: 92, flexShrink: 0,
                background: 'var(--surface-0)',
                borderRight: '1px solid var(--line)',
                display: 'flex', flexDirection: 'column',
                minHeight: 0,
            }}>
                {filmstripBody}
            </aside>
            {workspace}
            <aside style={{
                width: 280, flexShrink: 0,
                background: 'var(--surface-0)',
                borderLeft: '1px solid var(--line)',
                display: 'flex', flexDirection: 'column',
                overflow: 'auto',
                minHeight: 0,
            }}>
                {inspectorBody}
            </aside>
        </div>
    );
}

window.CropScreen = CropScreen;
