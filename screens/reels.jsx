/* ============================================================
   REELS — batch processing screen.
   Two modes:
     - "auto"     ← 新增：一键全自动 (源目录全部图片，最近邻桶 + 最小裁切)
     - "queue"    ← 沿用：把 Crop 页加入队列的项导出
   ============================================================ */

function ReelsScreen({ session, refresh, targetMP, setTargetMP, step, setStep }) {
    const [mode, setMode] = React.useState(localStorage.getItem('drop.reelsMode') || 'auto');
    const [busy, setBusy] = React.useState(false);
    const [result, setResult] = React.useState(null);

    // queue-mode-only options
    const [minBucket, setMinBucket] = React.useState(parseInt(localStorage.getItem('drop.minBucket')) || 4);

    // auto-mode-only options
    const [includeProcessed, setIncludeProcessed] = React.useState(false);
    const [subdir, setSubdir] = React.useState('');

    React.useEffect(() => { localStorage.setItem('drop.reelsMode', mode); }, [mode]);
    React.useEffect(() => { localStorage.setItem('drop.minBucket', String(minBucket)); }, [minBucket]);

    if (!session?.is_initialized) {
        return <div className="center" style={{ flex: 1, color: 'var(--text-3)' }}>请先在主页初始化会话</div>;
    }

    const totalSource = session.total_count || 0;
    const processed = session.processed_count || 0;
    const pending = Math.max(0, totalSource - processed);
    const queueLen = session.queue_len || 0;

    async function run() {
        if (busy) return;
        setResult(null);
        setBusy(true);
        try {
            if (mode === 'auto') {
                const r = await api.post('/api/auto_bucket_all', {
                    target_mp: targetMP, step,
                    include_processed: includeProcessed,
                    output_subdir: subdir.trim() || null,
                });
                setResult({ ...r, mode: 'auto' });
                if (r.status === 'empty') {
                    ToastBus.emit(r.message || '没有可处理的源图', 'warn');
                } else {
                    ToastBus.emit(`完成：${r.processed} 张 → ${r.output_folder}`, 'ok');
                }
            } else {
                const r = await api.post('/api/process_batch', {
                    target_mp: targetMP, step,
                    min_bucket_size: Math.max(1, minBucket | 0),
                });
                if (r.status === 'empty') {
                    ToastBus.emit('队列为空', 'warn');
                } else {
                    setResult({ ...r, mode: 'queue' });
                    ToastBus.emit(`完成：写入 ${r.processed} 张到 ${r.output_folder}`, 'ok');
                }
            }
            refresh();
        } catch (e) {
            ToastBus.emit(e.message || '处理失败', 'error');
        } finally {
            setBusy(false);
        }
    }

    return (
        <div style={{ flex: 1, overflow: 'auto', padding: 24 }}>
            <div className="col gap-3" style={{ maxWidth: 820, margin: '0 auto' }}>

                <div className="col gap-1">
                    <div className="eyebrow">Export</div>
                    <h2 style={{ margin: 0, fontSize: 26, fontWeight: 600, letterSpacing: '-.02em' }}>
                        批处理与导出
                    </h2>
                    <p style={{ margin: '4px 0 0', color: 'var(--text-2)', fontSize: 14 }}>
                        新底模放宽了严格分桶要求 —— 优先用「一键全自动」对源目录直接最近邻分桶 + 最小裁切。
                    </p>
                </div>

                <Panel eyebrow="模式">
                    <div className="col" style={{ padding: 14, gap: 10 }}>
                        {[
                            { id: 'auto',  icon: I.Wand,  title: '一键全自动', desc: `对源目录全部 ${pending} 张未处理图按最近桶居中最小裁切，跳过 Crop。`, accent: true },
                            { id: 'queue', icon: I.Files, title: '导出队列',    desc: `把已加入 Crop 队列的 ${queueLen} 项裁切结果写出。` },
                        ].map(o => {
                            const on = mode === o.id;
                            return (
                                <button
                                    key={o.id}
                                    onClick={() => setMode(o.id)}
                                    style={{
                                        appearance: 'none', textAlign: 'left',
                                        background: on ? 'var(--surface-2)' : 'var(--surface-1)',
                                        border: '1px solid ' + (on ? 'var(--accent-line)' : 'var(--line)'),
                                        borderRadius: 8, padding: '12px 14px',
                                        cursor: 'pointer', color: 'var(--text)',
                                        display: 'flex', alignItems: 'flex-start', gap: 12,
                                    }}
                                >
                                    <o.icon size={18} style={{ color: on ? 'var(--accent)' : 'var(--text-3)', marginTop: 2 }}/>
                                    <div className="col" style={{ gap: 2, flex: 1 }}>
                                        <span style={{ fontWeight: 600, fontSize: 13 }}>{o.title}</span>
                                        <span style={{ fontSize: 12, color: 'var(--text-3)' }}>{o.desc}</span>
                                    </div>
                                    <span style={{
                                        width: 16, height: 16, borderRadius: '50%',
                                        background: on ? 'var(--accent)' : 'transparent',
                                        border: '1.5px solid ' + (on ? 'var(--accent)' : 'var(--line-strong)'),
                                    }}/>
                                </button>
                            );
                        })}
                    </div>
                </Panel>

                <Panel eyebrow="目标分桶">
                    <BucketResolutionPicker
                        targetMP={targetMP} setTargetMP={setTargetMP}
                        step={step} setStep={setStep}
                    />
                </Panel>

                {mode === 'auto' && (
                    <Panel eyebrow="一键全自动 · 选项">
                        <div className="col" style={{ padding: 14, gap: 12 }}>
                            <label className="row gap-2" style={{ cursor: 'pointer' }}>
                                <input type="checkbox" className="check"
                                    checked={includeProcessed}
                                    onChange={e => setIncludeProcessed(e.target.checked)}/>
                                <span style={{ fontSize: 13 }}>包括已处理 / 已跳过的图片</span>
                                <span style={{ fontSize: 11, color: 'var(--text-3)' }}>
                                    （默认仅处理未触碰过的源图）
                                </span>
                            </label>
                            <div className="col gap-1">
                                <span className="eyebrow">输出子目录</span>
                                <input className="input mono"
                                    placeholder="留空 → 直接写到当前输出目录"
                                    value={subdir}
                                    onChange={e => setSubdir(e.target.value)}
                                />
                            </div>
                            <div style={{ fontSize: 11, color: 'var(--text-3)', lineHeight: 1.6 }}>
                                算法：按图片原始宽高比从桶列表中找最近邻 → 按目标桶比例居中最小裁切 → resize 到桶大小 → PNG 输出。
                                没有上采样（除非你启用了 drop.py 的 RealESRGAN）。
                            </div>
                        </div>
                    </Panel>
                )}

                {mode === 'queue' && (
                    <Panel eyebrow="导出队列 · 选项">
                        <div className="col" style={{ padding: 14, gap: 10 }}>
                            <div className="row gap-2">
                                <span style={{ fontSize: 12, color: 'var(--text-3)', width: 120 }}>最小成桶数量</span>
                                <input className="input mono" type="number" min="1"
                                    value={minBucket}
                                    onChange={e => setMinBucket(Math.max(1, parseInt(e.target.value) || 1))}
                                    style={{ width: 96 }}/>
                                <span style={{ fontSize: 11, color: 'var(--text-3)', flex: 1, lineHeight: 1.4 }}>
                                    数量少于此值的「孤儿桶」会被合并到最近的合法桶。新底模可设为 1（不合并）。
                                </span>
                            </div>
                        </div>
                    </Panel>
                )}

                <Panel eyebrow="本次概况">
                    <InspectorRow label="目标桶" value={`${targetMP} MP · ${step}px`} accent/>
                    <InspectorRow label="源目录文件" value={`${totalSource}`}/>
                    <InspectorRow label="已处理 / 已跳过" value={`${processed}`}/>
                    <InspectorRow label="未处理" value={`${pending}`}/>
                    <InspectorRow label="队列项" value={`${queueLen}`}/>
                    <InspectorRow label="输出目录" value={session.output_folder || '—'} mono={false}/>
                </Panel>

                <div className="row gap-2" style={{ paddingTop: 4 }}>
                    <button className="btn btn-primary btn-lg" onClick={run} disabled={busy}>
                        {busy ? <span className="spin"/> : (mode === 'auto' ? <I.Wand size={14}/> : <I.Zap size={14}/>)}
                        {busy ? '处理中…' : (mode === 'auto' ? '一键全自动导出' : '导出队列')}
                    </button>
                    <span style={{ color: 'var(--text-3)', fontSize: 12 }}>
                        {mode === 'auto'
                            ? `将处理 ${includeProcessed ? totalSource : pending} 张`
                            : `将处理 ${queueLen} 项`}
                    </span>
                </div>

                {result && (
                    <Panel eyebrow="处理结果" title={result.mode === 'auto' ? '一键全自动' : '导出队列'}>
                        <div className="col" style={{ padding: 14, gap: 10 }}>
                            <div className="row gap-3">
                                <Stat label="写入" value={result.processed || 0} accent/>
                                {result.mode === 'auto' && <Stat label="跳过" value={result.skipped || 0}/>}
                                {result.mode === 'auto' && <Stat label="错误" value={result.error_count || 0} danger={(result.error_count || 0) > 0}/>}
                                {result.mode === 'queue' && <Stat label="桶数" value={result.bucket_count || 0}/>}
                                {result.mode === 'queue' && <Stat label="合并" value={result.merged_count || 0}/>}
                            </div>
                            <div style={{ fontSize: 12, color: 'var(--text-2)', fontFamily: 'var(--font-mono)' }}>
                                → {result.output_folder}
                            </div>
                            {Array.isArray(result.buckets) && result.buckets.length > 0 && (
                                <div className="col gap-1" style={{ paddingTop: 6, borderTop: '1px solid var(--line)' }}>
                                    <div className="eyebrow">分桶分布</div>
                                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 6 }}>
                                        {result.buckets.slice(0, 16).map(b => (
                                            <div key={`${b.w}x${b.h}`} className="row gap-2" style={{
                                                padding: '6px 10px',
                                                background: 'var(--surface-1)',
                                                border: '1px solid var(--line)',
                                                borderRadius: 6,
                                            }}>
                                                <span className="mono" style={{ fontSize: 12 }}>{b.w}×{b.h}</span>
                                                <span className="flex-1"/>
                                                <span className="num" style={{ fontSize: 12, color: 'var(--accent)' }}>{b.count}</span>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            )}
                            {Array.isArray(result.errors) && result.errors.length > 0 && (
                                <details style={{ paddingTop: 6 }}>
                                    <summary style={{ cursor: 'pointer', color: 'var(--danger)', fontSize: 12 }}>
                                        查看 {result.error_count} 个错误
                                    </summary>
                                    <div className="col gap-1" style={{ paddingTop: 6, fontFamily: 'var(--font-mono)', fontSize: 11 }}>
                                        {result.errors.map((er, i) => (
                                            <div key={i} style={{ color: 'var(--text-3)' }}>
                                                <span style={{ color: 'var(--danger)' }}>{er.error}</span> · {er.file}
                                            </div>
                                        ))}
                                    </div>
                                </details>
                            )}
                        </div>
                    </Panel>
                )}
            </div>
        </div>
    );
}

function Stat({ label, value, accent, danger }) {
    return (
        <div className="col" style={{
            flex: 1,
            background: 'var(--surface-1)',
            border: '1px solid var(--line)',
            borderRadius: 8,
            padding: '10px 12px',
            gap: 2,
        }}>
            <span className="eyebrow">{label}</span>
            <span className="num" style={{
                fontSize: 22, fontWeight: 600,
                color: danger ? 'var(--danger)' : accent ? 'var(--accent)' : 'var(--text)',
            }}>{value}</span>
        </div>
    );
}

window.ReelsScreen = ReelsScreen;
