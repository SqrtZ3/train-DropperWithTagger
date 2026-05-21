/* ============================================================
   VIDEO — two-step keyframe extraction.
   Step 1: probe → 后端用场景检测找出候选关键帧，返回缩略图列表
   Step 2: 用户在网格里勾选 → 保存所选的原始帧到 output_dir
   ============================================================ */

function VideoScreen() {
    const [path, setPath] = React.useState(localStorage.getItem('drop.lastVideo') || '');
    const [outDir, setOutDir] = React.useState('');
    const [threshold, setThreshold] = React.useState(parseFloat(localStorage.getItem('drop.videoThr')) || 0.4);
    const [maxCandidates, setMaxCandidates] = React.useState(parseInt(localStorage.getItem('drop.videoMax')) || 0);
    const [sampleRate, setSampleRate] = React.useState(4);

    const [probing, setProbing] = React.useState(false);
    const [saving, setSaving] = React.useState(false);
    const [job, setJob] = React.useState(null);            // {job_id, candidates:[{idx, ts, thumb_url}], ...}
    const [selected, setSelected] = React.useState(new Set());

    const [supported, setSupported] = React.useState(null); // ['.mp4', '.mov', ...]

    React.useEffect(() => {
        api.get('/api/video/supported').then(r => setSupported(r.supported || [])).catch(() => {});
    }, []);

    React.useEffect(() => { localStorage.setItem('drop.videoThr', String(threshold)); }, [threshold]);
    React.useEffect(() => { localStorage.setItem('drop.videoMax', String(maxCandidates)); }, [maxCandidates]);

    // Persist last path for convenience
    React.useEffect(() => {
        if (path) localStorage.setItem('drop.lastVideo', path);
    }, [path]);

    function cleanJob(jid) {
        if (!jid) return;
        api.post(`/api/video/cleanup/${jid}`).catch(() => {});
    }

    async function probe() {
        const v = path.trim().replace(/^"|"$/g, '');
        if (!v) { ToastBus.emit('请填入视频路径', 'error'); return; }
        if (job?.job_id) cleanJob(job.job_id);
        setJob(null);
        setSelected(new Set());
        setProbing(true);
        try {
            const r = await api.post('/api/video/probe', {
                video_path: v,
                scene_threshold: threshold,
                max_candidates: Math.max(0, maxCandidates | 0),
                sample_rate_hz: sampleRate,
            });
            setJob(r);
            // 默认全选
            setSelected(new Set(r.candidates.map(c => c.idx)));
            if (!outDir) setOutDir(r.default_output_dir || '');
            ToastBus.emit(`找到 ${r.candidates.length} 个候选关键帧`, 'ok');
        } catch (e) {
            ToastBus.emit(e.message || '分析失败', 'error');
        } finally {
            setProbing(false);
        }
    }

    async function save() {
        if (!job?.job_id || selected.size === 0) {
            ToastBus.emit('请先勾选要保存的关键帧', 'warn');
            return;
        }
        setSaving(true);
        try {
            const r = await api.post('/api/video/save', {
                job_id: job.job_id,
                indices: Array.from(selected).sort((a, b) => a - b),
                output_dir: outDir.trim() || null,
            });
            ToastBus.emit(`已保存 ${r.saved} 帧 → ${r.output_dir}`, 'ok');
        } catch (e) {
            ToastBus.emit(e.message || '保存失败', 'error');
        } finally {
            setSaving(false);
        }
    }

    function toggle(idx) {
        setSelected(s => {
            const next = new Set(s);
            if (next.has(idx)) next.delete(idx); else next.add(idx);
            return next;
        });
    }

    function selectAll(v) {
        if (!job) return;
        if (v) setSelected(new Set(job.candidates.map(c => c.idx)));
        else setSelected(new Set());
    }

    function selectEvery(n) {
        // 每 n 个候选保留 1 个
        if (!job) return;
        const next = new Set();
        job.candidates.forEach((c, i) => { if (i % n === 0) next.add(c.idx); });
        setSelected(next);
    }

    // Cleanup on unmount
    React.useEffect(() => () => { if (job?.job_id) cleanJob(job.job_id); }, []);

    return (
        <div style={{ flex: 1, overflow: 'auto', padding: 24 }}>
            <div className="col gap-3" style={{ maxWidth: 1100, margin: '0 auto' }}>

                <div className="col gap-1">
                    <div className="eyebrow">Frames</div>
                    <h2 style={{ margin: 0, fontSize: 26, fontWeight: 600, letterSpacing: '-.02em' }}>
                        视频关键帧 · 两步选取
                    </h2>
                    <p style={{ margin: '4px 0 0', color: 'var(--text-2)', fontSize: 14 }}>
                        先分析得到候选关键帧 → 勾选要保留的 → 再写入磁盘。
                    </p>
                </div>

                <Panel eyebrow="01 · 视频源">
                    <div className="col" style={{ padding: 14, gap: 10 }}>
                        <div className="col gap-1">
                            <label className="eyebrow">视频路径</label>
                            <div className="input-row">
                                <I.Video size={14} style={{ color: 'var(--text-3)' }}/>
                                <input className="input mono" value={path}
                                    onChange={e => setPath(e.target.value)}
                                    onPaste={pasteCleanedPath(setPath)}
                                    onBlur={e => setPath(cleanPath(e.target.value))}
                                    placeholder={'D:\\Videos\\xxx.mp4'}/>
                            </div>
                        </div>
                        <div className="col gap-1">
                            <label className="eyebrow">输出目录 <span style={{ color: 'var(--text-4)', textTransform: 'none', letterSpacing: 0 }}>· 可选</span></label>
                            <div className="input-row">
                                <I.Folder size={14} style={{ color: 'var(--text-3)' }}/>
                                <input className="input mono" value={outDir}
                                    onChange={e => setOutDir(e.target.value)}
                                    onPaste={pasteCleanedPath(setOutDir)}
                                    onBlur={e => setOutDir(cleanPath(e.target.value))}
                                    placeholder="留空 → 视频同级 /extracted_frames"/>
                            </div>
                        </div>

                        <div className="row gap-3" style={{ flexWrap: 'wrap' }}>
                            <div className="col gap-1" style={{ flex: 1, minWidth: 240 }}>
                                <label className="eyebrow">场景突变阈值 · {threshold.toFixed(2)}</label>
                                <input type="range" min="0.05" max="0.9" step="0.01"
                                    value={threshold}
                                    onChange={e => setThreshold(parseFloat(e.target.value))}/>
                                <div style={{ fontSize: 11, color: 'var(--text-3)' }}>越低越敏感（候选越多）。动画建议 0.4-0.6，实拍 0.2-0.4。</div>
                            </div>
                            <div className="col gap-1" style={{ width: 140 }}>
                                <label className="eyebrow">最多候选</label>
                                <input className="input mono" type="number" min="0"
                                    value={maxCandidates}
                                    onChange={e => setMaxCandidates(parseInt(e.target.value) || 0)}
                                    placeholder="0 = 不限"/>
                            </div>
                            <div className="col gap-1" style={{ width: 140 }}>
                                <label className="eyebrow">检测频率 / 秒</label>
                                <input className="input mono" type="number" min="1" max="30"
                                    value={sampleRate}
                                    onChange={e => setSampleRate(parseFloat(e.target.value) || 4)}/>
                            </div>
                        </div>

                        <div className="row gap-2" style={{ alignItems: 'flex-start' }}>
                            <button className="btn btn-primary btn-lg" onClick={probe} disabled={probing || saving}>
                                {probing ? <span className="spin"/> : <I.Sparkles size={14}/>}
                                {probing ? '分析中…' : '分析关键帧'}
                            </button>
                            {supported && (
                                <details style={{ flex: 1 }}>
                                    <summary style={{ fontSize: 11, color: 'var(--text-3)', cursor: 'pointer' }}>
                                        支持 {supported.length} 种扩展名
                                    </summary>
                                    <div className="mono" style={{ fontSize: 11, color: 'var(--text-3)', paddingTop: 6 }}>
                                        {supported.join(' · ')}
                                    </div>
                                </details>
                            )}
                        </div>
                    </div>
                </Panel>

                {job && (
                    <>
                        <Panel eyebrow="02 · 候选关键帧" title={`${job.candidates.length} 候选 · 已选 ${selected.size}`} action={
                            <div className="row gap-1">
                                <button className="btn btn-soft btn-sm" onClick={() => selectAll(true)}>全选</button>
                                <button className="btn btn-soft btn-sm" onClick={() => selectAll(false)}>清空</button>
                                <button className="btn btn-soft btn-sm" onClick={() => selectEvery(2)} title="每 2 个候选保留 1 个">每 2</button>
                                <button className="btn btn-soft btn-sm" onClick={() => selectEvery(3)}>每 3</button>
                            </div>
                        }>
                            <div className="row gap-3" style={{ padding: '8px 14px 4px', fontSize: 11, color: 'var(--text-3)', borderBottom: '1px solid var(--line)' }}>
                                <span><I.Film size={11}/> 总帧 <span className="num" style={{ color: 'var(--text-2)' }}>{job.total_frames}</span></span>
                                <span>FPS <span className="num" style={{ color: 'var(--text-2)' }}>{job.fps?.toFixed(2)}</span></span>
                                <span>时长 <span className="num" style={{ color: 'var(--text-2)' }}>{fmtDuration(job.duration)}</span></span>
                                <span>原始分辨率 <span className="num" style={{ color: 'var(--text-2)' }}>{job.width}×{job.height}</span></span>
                            </div>
                            <div style={{
                                padding: 12,
                                display: 'grid',
                                gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
                                gap: 8,
                            }}>
                                {job.candidates.map(c => {
                                    const on = selected.has(c.idx);
                                    return (
                                        <button key={c.idx}
                                            onClick={() => toggle(c.idx)}
                                            style={{
                                                appearance: 'none', padding: 0, cursor: 'pointer',
                                                border: on ? '2px solid var(--accent)' : '1px solid var(--line)',
                                                background: 'var(--surface-1)',
                                                borderRadius: 6, overflow: 'hidden',
                                                position: 'relative',
                                            }}
                                            title={`帧 #${c.idx} · ${fmtDuration(c.ts)}`}
                                        >
                                            <LazyImg src={c.thumb_url} style={{ width: '100%', aspectRatio: '16 / 9' }}/>
                                            <div style={{
                                                position: 'absolute', top: 4, left: 4,
                                                background: 'rgba(0,0,0,.65)',
                                                color: '#fff',
                                                fontFamily: 'var(--font-mono)',
                                                fontSize: 10,
                                                padding: '2px 5px',
                                                borderRadius: 3,
                                            }}>
                                                #{c.idx} · {c.ts.toFixed(2)}s
                                            </div>
                                            <div style={{
                                                position: 'absolute', top: 6, right: 6,
                                                width: 18, height: 18, borderRadius: 4,
                                                background: on ? 'var(--accent)' : 'rgba(0,0,0,.55)',
                                                color: on ? 'var(--accent-ink)' : '#fff',
                                                display: 'grid', placeItems: 'center',
                                                border: '1px solid ' + (on ? 'var(--accent)' : 'rgba(255,255,255,.4)'),
                                                transition: 'background var(--t-fast) var(--ease)',
                                            }}>
                                                {on ? <I.Check size={12} strokeWidth={3}/> : null}
                                            </div>
                                        </button>
                                    );
                                })}
                            </div>
                        </Panel>

                        <div className="row gap-2" style={{ paddingTop: 4 }}>
                            <button className="btn btn-primary btn-lg" onClick={save} disabled={saving || probing || selected.size === 0}>
                                {saving ? <span className="spin"/> : <I.Download size={14}/>}
                                {saving ? '保存中…' : `保存所选 ${selected.size} 帧`}
                            </button>
                            <span style={{ color: 'var(--text-3)', fontSize: 12 }}>
                                目标目录：<span className="mono" style={{ color: 'var(--text-2)' }}>{outDir || '(自动)'}</span>
                            </span>
                        </div>
                    </>
                )}
            </div>
        </div>
    );
}

window.VideoScreen = VideoScreen;
