/* ============================================================
   SIM — similarity (dedup) screen.
   Backed by existing endpoints:
     POST /api/analyze_similarity
     GET  /api/similarity_progress
     POST /api/cancel_similarity
     POST /api/delete_files
   ============================================================ */

const SIM_PRESETS = {
    strict:   { threshold: 4,  ar_tolerance: 0.05, require_dual_hash: true,  label: '严格', desc: '只合并几乎一致的图（连拍 / 重复存档）' },
    balanced: { threshold: 6,  ar_tolerance: 0.15, require_dual_hash: true,  label: '平衡', desc: '推荐。允许构图微调与轻度后期差异' },
    loose:    { threshold: 10, ar_tolerance: 0.25, require_dual_hash: false, label: '宽松', desc: '同一场景多角度拍摄。误判率会上升' },
};

// Module-level cache so scan results survive when SimScreen unmounts (tab
// switch). Also persisted to localStorage so it survives page reloads.
// Window-scoped so it also survives Babel hot-reloads in the same tab.
// Keys are derived from (scope, folder, preset) — clicking 开始扫描 with a
// matching key skips the API and reuses the cached groups; "重新计算" forces
// a fresh run. After a server restart the in-memory _SNAPSHOTS map is empty,
// so we ping the snap on mount and clear the cache if it returns 410.
const SIM_LS_KEY = 'drop.sim.cache.v1';
const SIM_CACHE_INIT = (() => {
    const empty = { groups: [], snapId: null, scanRoot: '', deleted: [], ignored: [], scanKey: null };
    if (typeof window === 'undefined') return empty;
    if (window._dropSimCache) return window._dropSimCache;
    try {
        const raw = localStorage.getItem(SIM_LS_KEY);
        if (raw) {
            const obj = JSON.parse(raw);
            if (obj && typeof obj === 'object' && Array.isArray(obj.groups)) {
                return {
                    groups: obj.groups || [],
                    snapId: obj.snapId || null,
                    scanRoot: obj.scanRoot || '',
                    deleted: obj.deleted || [],
                    // Accept old `sessionDeleted` schema for migration — same meaning.
                    ignored: obj.ignored || obj.sessionDeleted || [],
                    scanKey: obj.scanKey || null,
                };
            }
        }
    } catch {}
    return empty;
})();
const SIM_CACHE = window._dropSimCache = SIM_CACHE_INIT;
function _persistSimCache() {
    try { localStorage.setItem(SIM_LS_KEY, JSON.stringify(SIM_CACHE)); } catch {}
}

function SimScreen({ session, refresh }) {
    const narrow = useIsNarrow();
    const [scope, setScope] = React.useState('source');           // 'source' | 'output' | 'custom'
    const [customFolder, setCustomFolder] = React.useState('');
    const [preset, setPreset] = React.useState(localStorage.getItem('drop.simPreset') || 'balanced');
    const [running, setRunning] = React.useState(false);
    const [progress, setProgress] = React.useState({ phase: '', current: 0, total: 0 });
    const [scanRoot, setScanRoot] = React.useState(SIM_CACHE.scanRoot);
    const [snapId, setSnapId] = React.useState(SIM_CACHE.snapId);
    const [groups, setGroups] = React.useState(SIM_CACHE.groups);
    const [deleted, setDeleted] = React.useState(() => new Set(SIM_CACHE.deleted || []));
    // `ignored` = filepaths the user has decided to hide from Sim results.
    // Soft-delete: no disk file is touched, just filtered at render time. Set
    // persists across rescans (and across page reloads via localStorage), so
    // a user's "I've dealt with these" decisions stick. Stable item keys keep
    // surviving thumbs from remounting / flashing gray on hide.
    const [ignored, setIgnored] = React.useState(() => new Set(SIM_CACHE.ignored || []));
    const [previewItem, setPreviewItem] = React.useState(null);

    React.useEffect(() => { localStorage.setItem('drop.simPreset', preset); }, [preset]);

    // Mirror state into the module cache (+ localStorage) so a remount or full
    // page reload can rehydrate. Persisting on every state change is fine —
    // the cache is bounded (snapshot has ≤thousands of items).
    React.useEffect(() => { SIM_CACHE.groups = groups; _persistSimCache(); }, [groups]);
    React.useEffect(() => { SIM_CACHE.snapId = snapId; _persistSimCache(); }, [snapId]);
    React.useEffect(() => { SIM_CACHE.scanRoot = scanRoot; _persistSimCache(); }, [scanRoot]);
    React.useEffect(() => { SIM_CACHE.deleted = [...deleted]; _persistSimCache(); }, [deleted]);
    React.useEffect(() => { SIM_CACHE.ignored = [...ignored]; _persistSimCache(); }, [ignored]);

    // After a server restart the in-memory _SNAPSHOTS map is wiped; old snap_id
    // URLs return 410. Two ways to catch this:
    //   1) On mount with cached groups, ping once.
    //   2) On *any* thumbnail load failure (via LazyImg's onLoadError),
    //      re-ping — this covers the rarer case where the server restarts
    //      while we're already viewing the Sim screen.
    // Both paths flow through revalidateSnap(); a ref de-dupes concurrent
    // probes so a wall of failing thumbs doesn't trigger N requests.
    const revalidatingRef = React.useRef(false);
    const revalidateSnap = React.useCallback(() => {
        if (revalidatingRef.current) return;
        if (!snapId || groups.length === 0) return;
        revalidatingRef.current = true;
        fetch(`/api/snap/${snapId}/thumb/0`, { method: 'GET', cache: 'no-store' })
            .then(r => {
                if (r.status === 410) {
                    ToastBus.emit('扫描快照已失效（服务器重启或缓存过期），结果已清空', 'warn');
                    setGroups([]); setSnapId(null); setScanRoot(''); setDeleted(new Set());
                    // Keep `ignored` — persistent user decisions about specific
                    // filepaths, independent of any one snapshot.
                    SIM_CACHE.scanKey = null; _persistSimCache();
                }
            }).catch(() => {})
            .finally(() => { revalidatingRef.current = false; });
    }, [snapId, groups.length]);

    const validatedRef = React.useRef(false);
    React.useEffect(() => {
        if (validatedRef.current) return;
        if (!snapId || groups.length === 0) return;
        validatedRef.current = true;
        revalidateSnap();
    }, [snapId, groups.length, revalidateSnap]);

    // Identifies "what would a scan with the current UI selections compute" —
    // used to decide whether the cached groups still apply.
    function currentScanKey() {
        const folder = scope === 'source' ? (session?.image_folder || '')
                     : scope === 'output' ? (session?.output_folder || '')
                     : customFolder.trim();
        return `${scope}|${folder}|${preset}`;
    }
    const cachedForCurrent = groups.length > 0 && SIM_CACHE.scanKey === currentScanKey();

    // ESC closes the preview modal.
    React.useEffect(() => {
        if (!previewItem) return;
        const onKey = (e) => { if (e.key === 'Escape') setPreviewItem(null); };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [previewItem]);

    // If the previewed item gets deleted (e.g. user marked it in the modal
    // then triggered the delete batch), close the modal — its image source
    // would otherwise 500 and show a broken/gray view.
    React.useEffect(() => {
        if (previewItem && ignored.has(previewItem.filepath)) {
            setPreviewItem(null);
        }
    }, [ignored, previewItem]);

    if (!session?.is_initialized) {
        return <div className="center" style={{ flex: 1, color: 'var(--text-3)' }}>请先在主页初始化会话</div>;
    }

    const cfg = SIM_PRESETS[preset];

    async function scan(force = false) {
        if (running) return;
        const key = currentScanKey();
        // Cache hit: identical (scope, folder, preset) AND we still have groups.
        // No API call — just confirm with a toast. Force=true (from 重新计算) bypasses.
        if (!force && key === SIM_CACHE.scanKey && SIM_CACHE.groups.length > 0) {
            ToastBus.emit(`使用缓存结果（${SIM_CACHE.groups.length} 组）。点"重新计算"以重新扫描。`, 'ok');
            return;
        }
        setRunning(true);
        setGroups([]); setDeleted(new Set()); setSnapId(null);
        // Keep `ignored` across rescans — those are persistent user decisions.
        setProgress({ phase: 'hashing', current: 0, total: 0 });
        const poll = setInterval(() => {
            api.get('/api/similarity_progress').then(p => {
                setProgress(p);
                if (!p.running && p.phase === 'done') clearInterval(poll);
            }).catch(() => {});
        }, 500);
        try {
            const r = await api.post('/api/analyze_similarity', {
                threshold: cfg.threshold,
                ar_tolerance: cfg.ar_tolerance,
                require_dual_hash: cfg.require_dual_hash,
                method: 'phash',
                scope,
                folder: scope === 'custom' ? customFolder.trim() : null,
            });
            setGroups(r.groups || []);
            setSnapId(r.snap_id || null);
            setScanRoot(r.scan_root || '');
            SIM_CACHE.scanKey = key;
            const count = (r.groups || []).reduce((s, g) => s + g.length, 0);
            ToastBus.emit(`扫描完成：${r.groups?.length || 0} 组 / ${count} 张`, 'ok');
        } catch (e) {
            ToastBus.emit(e.message || '扫描失败', 'error');
        } finally {
            clearInterval(poll);
            setRunning(false);
            setProgress({ phase: 'done', current: 0, total: 0 });
        }
    }

    async function cancel() {
        try { await api.post('/api/cancel_similarity'); } catch {}
    }

    function toggleDelete(fp) {
        setDeleted(s => {
            const next = new Set(s);
            if (next.has(fp)) next.delete(fp); else next.add(fp);
            return next;
        });
    }

    function autoMarkExceptLargest() {
        // 每组保留最大尺寸的一张，其它的全部标记删除
        const marked = new Set();
        groups.forEach(g => {
            if (g.length < 2) return;
            let bestI = 0, bestArea = 0;
            g.forEach((it, i) => {
                const a = (it.width || 0) * (it.height || 0);
                if (a > bestArea) { bestArea = a; bestI = i; }
            });
            g.forEach((it, i) => { if (i !== bestI) marked.add(it.filepath); });
        });
        setDeleted(marked);
        ToastBus.emit(`已标记 ${marked.size} 张待删除（保留每组最大）`, 'ok');
    }

    // Soft "ignore": hide the marked items from Sim results AND sync to the
    // server so Crop / Reels also stop showing them. Files stay on disk; the
    // server just filters them out of session.files via add_to_ignored.
    // Optimistic UI: state updates immediately, server sync fires in the
    // background; on failure we toast a warning but keep the local state.
    function doIgnore() {
        if (deleted.size === 0) return;
        const toIgnore = new Set(deleted);
        const paths = [...toIgnore];
        setIgnored(s => {
            const next = new Set(s);
            toIgnore.forEach(fp => next.add(fp));
            return next;
        });
        setDeleted(new Set());
        ToastBus.emit(`已忽略 ${paths.length} 张（文件保留，Crop 视图同步隐藏）`, 'ok');
        api.post('/api/ignore_files', { filepaths: paths })
            .then(() => refresh())
            .catch(e => ToastBus.emit('Crop 同步失败：' + (e.message || ''), 'warn'));
    }

    async function doPhysicalDelete() {
        const paths = [...deleted];
        if (paths.length === 0) return;
        const msg = `⚠️ 警告！您将要对选择的 ${paths.length} 个重复文件执行【物理删除】。\n\n这会直接从磁盘删除这些图片及其对应的 .txt 标签文件（如果存在），此操作不可逆！\n\n确定要继续删除吗？`;
        if (!confirm(msg)) return;
        
        try {
            const r = await api.post('/api/delete_files', {
                filepaths: paths,
                snap_id: snapId
            });
            
            setIgnored(prev => {
                const next = new Set(prev);
                paths.forEach(fp => next.add(fp));
                return next;
            });
            setDeleted(new Set());
            
            if (r.failed && r.failed.length > 0) {
                ToastBus.emit(`删除完成：成功 ${r.deleted} 个 · 失败 ${r.failed.length} 个`, 'warn');
            } else {
                ToastBus.emit(`成功物理删除 ${r.deleted} 个重复文件（及其 .txt 标签）！`, 'ok');
            }
            refresh();
        } catch (e) {
            ToastBus.emit(e.message || '物理删除失败', 'error');
        }
    }

    // Restore everything that was previously ignored. Asks the server to
    // rebuild session.files so Crop sees the files again.
    function clearAllIgnored() {
        const paths = [...ignored];
        setIgnored(new Set());
        if (paths.length === 0) return;
        api.post('/api/unignore_files', { filepaths: paths })
            .then(() => refresh())
            .catch(e => ToastBus.emit('Crop 同步失败：' + (e.message || ''), 'warn'));
    }

    return (
        <div className="col" style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
            <div style={{ padding: 22, borderBottom: '1px solid var(--line)' }}>
                <div className="row gap-3" style={{ alignItems: 'flex-start', flexWrap: 'wrap' }}>
                    <Panel eyebrow="扫描范围" title={null} style={{ flex: 1, minWidth: 280 }}>
                        <div className="col" style={{ padding: 14, gap: 10 }}>
                            <div className="seg" style={{ alignSelf: 'flex-start' }}>
                                <button className={scope === 'source' ? 'on' : ''} onClick={() => setScope('source')}>源目录</button>
                                <button className={scope === 'output' ? 'on' : ''} onClick={() => setScope('output')}>输出目录</button>
                                <button className={scope === 'custom' ? 'on' : ''} onClick={() => setScope('custom')}>自定义</button>
                            </div>
                            {scope === 'custom' && (
                                <div className="input-row">
                                    <I.Folder size={14} style={{ color: 'var(--text-3)' }}/>
                                    <input className="input mono" placeholder="自定义目录路径"
                                        value={customFolder}
                                        onChange={e => setCustomFolder(e.target.value)}
                                        onPaste={pasteCleanedPath(setCustomFolder)}
                                        onBlur={e => setCustomFolder(cleanPath(e.target.value))}/>
                                </div>
                            )}
                            <div style={{ fontSize: 11, color: 'var(--text-3)' }}>
                                {scope === 'source' && '扫描当前会话输入目录里的所有图片（含 zip）'}
                                {scope === 'output' && '扫描当前会话输出目录里的所有图片'}
                                {scope === 'custom' && '扫描任意磁盘目录（自动跳过看起来像旧 output 的子目录）'}
                            </div>
                        </div>
                    </Panel>
                    <Panel eyebrow="灵敏度" title={null} style={{ flex: 1, minWidth: 280 }}>
                        <div className="col" style={{ padding: 14, gap: 10 }}>
                            <div className="seg" style={{ alignSelf: 'flex-start' }}>
                                {Object.entries(SIM_PRESETS).map(([k, v]) => (
                                    <button key={k} className={preset === k ? 'on' : ''} onClick={() => setPreset(k)}>{v.label}</button>
                                ))}
                            </div>
                            <div style={{ fontSize: 12, color: 'var(--text-2)' }}>{cfg.desc}</div>
                            <div style={{ fontSize: 11, color: 'var(--text-3)', fontFamily: 'var(--font-mono)' }}>
                                threshold {cfg.threshold} · ar_tol {cfg.ar_tolerance} · dual {cfg.require_dual_hash ? 'on' : 'off'}
                            </div>
                        </div>
                    </Panel>
                </div>

                <div className="row gap-2" style={{ marginTop: 14, flexWrap: 'wrap' }}>
                    {!running ? (
                        <>
                            <button className="btn btn-primary btn-lg" onClick={() => scan(false)}>
                                <I.Search size={14}/> 开始扫描
                            </button>
                            {cachedForCurrent && (
                                <button className="btn btn-soft btn-lg" onClick={() => scan(true)}
                                    title="忽略缓存，强制重新计算（用于文件已变动的情形）">
                                    <I.Refresh size={14}/> 重新计算
                                </button>
                            )}
                        </>
                    ) : (
                        <button className="btn btn-soft btn-lg" onClick={cancel}>
                            <I.Pause size={14}/> 取消扫描
                        </button>
                    )}
                    {groups.length > 0 && (
                        <>
                            <button className="btn btn-soft" onClick={autoMarkExceptLargest}>
                                <I.Wand size={14}/> 标记重复（保留最大）
                            </button>
                            <button className="btn btn-soft" onClick={() => setDeleted(new Set())} disabled={deleted.size === 0}>
                                <I.Close size={14}/> 清空标记
                            </button>
                            {ignored.size > 0 && (
                                <button className="btn btn-soft" onClick={clearAllIgnored}
                                    title="重新显示之前被忽略的图片，并把它们恢复到 Crop / Reels 队列里">
                                    <I.Refresh size={14}/> 取消所有忽略 ({ignored.size})
                                </button>
                            )}
                            <span className="flex-1"/>
                            <button className="btn btn-warn" onClick={doIgnore} disabled={deleted.size === 0}
                                title="仅从结果中隐藏（写入缓存），不删除磁盘文件">
                                <I.Filter size={14}/> 忽略已标记 ({deleted.size})
                            </button>
                            <button className="btn btn-danger" onClick={doPhysicalDelete} disabled={deleted.size === 0}
                                title="物理删除标记的图片及其对应的 .txt 标签文件">
                                <I.Trash size={14}/> 物理删除已标记 ({deleted.size})
                            </button>
                        </>
                    )}
                </div>

                {running && (
                    <div className="col gap-2" style={{ marginTop: 14 }}>
                        <div className="row gap-2">
                            <span className="spin"/>
                            <span style={{ fontSize: 12, color: 'var(--text-2)' }}>
                                {progress.phase === 'hashing' ? '计算哈希…' :
                                 progress.phase === 'clustering' ? '聚类…' : '处理中…'}
                            </span>
                            <span className="flex-1"/>
                            <span className="num" style={{ fontSize: 12, color: 'var(--text-3)' }}>
                                {progress.current}/{progress.total}
                            </span>
                        </div>
                        <div className="bar">
                            <div className="fill" style={{ width: progress.total ? `${progress.current / progress.total * 100}%` : '0%' }}/>
                        </div>
                    </div>
                )}

                {scanRoot && (
                    <div style={{ marginTop: 10, fontSize: 11, color: 'var(--text-3)', fontFamily: 'var(--font-mono)' }}>
                        扫描根：{scanRoot}
                        {groups.length > 0 && !cachedForCurrent && (
                            <span style={{ color: 'var(--warn)', marginLeft: 10 }}>
                                · 上次参数与当前不同，点"开始扫描"将重新计算
                            </span>
                        )}
                    </div>
                )}
            </div>

            {/* Results: on narrow viewports remove the inner overflow so the
                whole page scrolls as one — otherwise the config block above
                pins to the top and the user can't reach the images. */}
            <div style={narrow
                ? { padding: 22 }
                : { flex: 1, padding: 22, overflow: 'auto' }
            }>
                {groups.length === 0 && !running && (
                    <div className="center col gap-2" style={{ padding: '60px 0', color: 'var(--text-3)' }}>
                        <I.Stack size={32} style={{ color: 'var(--text-4)' }}/>
                        <div style={{ fontSize: 14 }}>尚未扫描或没有发现重复项</div>
                    </div>
                )}
                <div className="col gap-3">
                    {groups.map((g, gi) => {
                        // Filter out items that were deleted from disk in this
                        // visit. We don't mutate `groups` itself — that would
                        // shift React keys and remount LazyImg, flashing the
                        // surviving thumbs gray.
                        const visible = g.filter(it => !ignored.has(it.filepath));
                        if (visible.length < 2) return null;
                        return (
                            <div key={gi} className="card" style={{ padding: 14 }}>
                                <div className="row gap-2" style={{ marginBottom: 10 }}>
                                    <span className="eyebrow">组 {gi + 1}</span>
                                    <span className="pill">{visible.length} 项</span>
                                    <span className="flex-1"/>
                                    <span style={{ fontSize: 11, color: 'var(--text-3)' }}>点缩略图标记 / 取消标记删除</span>
                                </div>
                                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 8 }}>
                                    {visible.map((it) => {
                                        const mark = deleted.has(it.filepath);
                                        return (
                                            <div key={it.filepath} style={{
                                                position: 'relative',
                                                background: 'var(--surface-1)',
                                                border: mark ? '2px solid var(--danger)' : '1px solid var(--line)',
                                                borderRadius: 6, overflow: 'hidden',
                                            }}>
                                                <button
                                                    onClick={() => toggleDelete(it.filepath)}
                                                    style={{
                                                        appearance: 'none', cursor: 'pointer',
                                                        background: 'transparent', border: 'none',
                                                        padding: 0, display: 'block', width: '100%',
                                                        color: 'var(--text-3)',
                                                    }}
                                                    title={`点击切换标记 · ${it.filepath}`}
                                                >
                                                    <LazyImg src={it.thumb_url}
                                                        objectFit="contain"
                                                        onLoadError={revalidateSnap}
                                                        style={{ width: '100%', aspectRatio: '1 / 1' }}/>
                                                    <div style={{
                                                        padding: '6px 8px', fontFamily: 'var(--font-mono)', fontSize: 10,
                                                        textAlign: 'left',
                                                        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                                                    }}>
                                                        {it.width}×{it.height} · {basename(it.filename)}
                                                    </div>
                                                </button>
                                                <button
                                                    onClick={() => setPreviewItem(it)}
                                                    title="预览大图"
                                                    style={{
                                                        position: 'absolute', top: 6, left: 6,
                                                        width: 26, height: 26, padding: 0,
                                                        border: 'none', borderRadius: 4,
                                                        background: 'rgba(0,0,0,.55)',
                                                        color: '#fff', cursor: 'pointer',
                                                        display: 'grid', placeItems: 'center',
                                                    }}
                                                >
                                                    <I.Eye size={14}/>
                                                </button>
                                                {mark && (
                                                    <div style={{
                                                        position: 'absolute', top: 6, right: 6,
                                                        background: 'var(--danger)', color: '#fff',
                                                        borderRadius: '50%', width: 22, height: 22,
                                                        display: 'grid', placeItems: 'center',
                                                        pointerEvents: 'none',
                                                    }}>
                                                        <I.Trash size={12} strokeWidth={3}/>
                                                    </div>
                                                )}
                                            </div>
                                        );
                                    })}
                                </div>
                            </div>
                        );
                    })}
                </div>
            </div>

            {previewItem && (
                <div className="modal-backdrop" onClick={() => setPreviewItem(null)}>
                    <div
                        onClick={(e) => e.stopPropagation()}
                        style={{
                            background: 'var(--surface-0)',
                            border: '1px solid var(--line)',
                            borderRadius: 10,
                            padding: 14,
                            maxWidth: '92vw', maxHeight: '92vh',
                            display: 'flex', flexDirection: 'column', gap: 10,
                            boxShadow: '0 24px 64px rgba(0,0,0,.5)',
                        }}
                    >
                        <div className="row gap-3" style={{ minWidth: 0 }}>
                            <span className="eyebrow">预览</span>
                            <span className="mono" style={{
                                fontSize: 11, color: 'var(--text-2)',
                                whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                                minWidth: 0, flex: 1,
                            }} title={previewItem.filepath}>
                                {previewItem.width}×{previewItem.height} · {basename(previewItem.filename)}
                            </span>
                            <button className="btn btn-icon" onClick={() => setPreviewItem(null)} title="关闭 (Esc)">
                                <I.Close size={16}/>
                            </button>
                        </div>
                        <img
                            src={previewItem.url || previewItem.thumb_url}
                            alt=""
                            draggable={false}
                            style={{
                                maxWidth: '88vw', maxHeight: '74vh',
                                objectFit: 'contain', display: 'block',
                                background: 'var(--surface-1)',
                                borderRadius: 6,
                            }}
                        />
                        <div className="row gap-2">
                            <span className="flex-1"/>
                            <button
                                className={deleted.has(previewItem.filepath) ? 'btn btn-soft' : 'btn btn-danger'}
                                onClick={() => toggleDelete(previewItem.filepath)}
                            >
                                {deleted.has(previewItem.filepath)
                                    ? <><I.Close size={14}/> 取消标记删除</>
                                    : <><I.Trash size={14}/> 标记删除</>}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

window.SimScreen = SimScreen;
