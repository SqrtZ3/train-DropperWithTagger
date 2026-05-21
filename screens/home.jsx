/* ============================================================
   HOME — initialize session, recent folders, workflow shortcuts.
   ============================================================ */

function HomeScreen({ session, onInit, onTab, targetMP, setTargetMP, step, setStep }) {
    const [history, setHistory] = React.useState([]);
    const [inputDir, setInputDir] = React.useState('');
    const [outputDir, setOutputDir] = React.useState('');
    const [busy, setBusy] = React.useState(false);

    React.useEffect(() => {
        api.get('/api/history').then(h => setHistory(Array.isArray(h) ? h : (h.items || []))).catch(() => {});
    }, []);

    React.useEffect(() => {
        if (session?.image_folder && !inputDir) setInputDir(session.image_folder);
        if (session?.output_folder && !outputDir) setOutputDir(session.output_folder);
    }, [session?.image_folder, session?.output_folder]);

    async function go() {
        if (!inputDir.trim()) { ToastBus.emit('请填入输入目录', 'error'); return; }
        setBusy(true);
        try {
            await onInit(inputDir.trim(), outputDir.trim());
        } catch (e) {
            ToastBus.emit(e.message || '初始化失败', 'error');
        } finally { setBusy(false); }
    }

    const workflows = [
        { id: 'crop',  title: '逐张精修',     body: '智能边缘检测 + 多框 + 断点续作。', icon: I.Crop,    needSession: true },
        { id: 'reels', title: '一键全自动',   body: '按当前目标桶最近邻分配 + 最小裁切，直出。', icon: I.Wand,    needSession: true,  highlight: true },
        { id: 'sim',   title: '相似度查重',   body: 'pHash + dHash + 颜色直方图三层校验。', icon: I.Stack,   needSession: true },
        { id: 'video', title: '视频抽帧',     body: '场景检测候选 → 勾选 → 落盘。',     icon: I.Video,   needSession: false },
    ];

    return (
        // No display:flex on the outer — when the screen container above us is
        // align-items:stretch (it must be, to keep the TopBar from being covered),
        // a flex-row outer here stretches the inner .col vertically. The .col is
        // itself flex-direction:column, so its panel children (flex-shrink:1 by
        // default) get squashed and the "开始处理" button at the bottom of panel
        // 01 ends up past the squashed panel's bottom edge. Block layout with
        // margin:0 auto centers cleanly and lets overflow:auto here scroll.
        <div style={{
            flex: 1, overflow: 'auto',
            background: 'var(--bg)',
            padding: '40px 28px 60px',
        }}>
            <div className="col gap-4 fade-in" style={{ maxWidth: 980, width: '100%', margin: '0 auto' }}>

                <div className="col gap-2" style={{ paddingBottom: 4 }}>
                    <div className="eyebrow">Drop Studio · v0.5</div>
                    <h2 style={{
                        margin: 0, fontSize: 38, fontWeight: 600,
                        letterSpacing: '-.025em', lineHeight: 1.05,
                    }}>
                        训练数据管线<span style={{ color: 'var(--text-3)' }}>，一站式。</span>
                    </h2>
                    <p style={{ margin: '4px 0 0', color: 'var(--text-2)', fontSize: 15, maxWidth: 640 }}>
                        裁切 · 分桶 · 去重 · 抽帧。本地运行，支持断点续作；
                        全自动模式可对源目录一键归桶最小裁切，跳过逐张精修。
                    </p>
                </div>

                <Panel eyebrow="01 · 选择目录" title="开启新会话" style={{ padding: 0 }}>
                    <div className="col gap-3" style={{ padding: '18px 20px 20px' }}>
                        <div className="col gap-2">
                            <label className="eyebrow">输入目录</label>
                            <div className="input-row">
                                <I.Folder size={14} style={{ color: 'var(--text-3)' }}/>
                                <input
                                    className="input mono"
                                    placeholder={'D:\\Datasets\\xxx  (支持子目录 + .zip)'}
                                    value={inputDir}
                                    onChange={e => setInputDir(e.target.value)}
                                    onPaste={pasteCleanedPath(setInputDir)}
                                    onBlur={e => setInputDir(cleanPath(e.target.value))}
                                    spellCheck={false}
                                />
                            </div>
                        </div>
                        <div className="col gap-2">
                            <label className="eyebrow">
                                输出目录 <span style={{ color: 'var(--text-4)', textTransform: 'none', letterSpacing: 0 }}>· 可选</span>
                            </label>
                            <div className="input-row">
                                <I.Folder size={14} style={{ color: 'var(--text-3)' }}/>
                                <input
                                    className="input mono"
                                    placeholder="留空则使用 输入目录/output"
                                    value={outputDir}
                                    onChange={e => setOutputDir(e.target.value)}
                                    onPaste={pasteCleanedPath(setOutputDir)}
                                    onBlur={e => setOutputDir(cleanPath(e.target.value))}
                                    spellCheck={false}
                                />
                            </div>
                        </div>

                        <div className="col gap-2">
                            <label className="eyebrow">目标分桶分辨率</label>
                            <BucketResolutionPicker
                                targetMP={targetMP} setTargetMP={setTargetMP}
                                step={step} setStep={setStep}
                                compact
                            />
                        </div>

                        <div className="row gap-2" style={{ paddingTop: 4 }}>
                            <button className="btn btn-primary btn-lg" onClick={go} disabled={busy}>
                                {busy ? <span className="spin"/> : <I.Sparkles size={14}/>}
                                {busy ? '初始化中…' : '开始处理'}
                            </button>
                            <span style={{ color: 'var(--text-3)', fontSize: 12 }}>
                                设置全局生效；后续任何页面都可改
                            </span>
                        </div>
                    </div>
                </Panel>

                {history.length > 0 && (
                    <Panel eyebrow="02 · 最近会话" title={`${history.length} 条历史`}>
                        <div className="col" style={{ maxHeight: 240, overflow: 'auto' }}>
                            {history.map((h, i) => {
                                // 单击 = 仅回填；点右侧 "→" 按钮 = 直接初始化进入 crop。
                                // 拆成两个交互而不是单击直接进，因为有时用户只想看路径再调一下参数。
                                const fillOnly = () => {
                                    setInputDir(h.input || '');
                                    setOutputDir(h.output || '');
                                };
                                const goNow = async (e) => {
                                    e.stopPropagation();
                                    if (busy) return;
                                    setInputDir(h.input || '');
                                    setOutputDir(h.output || '');
                                    setBusy(true);
                                    try {
                                        await onInit((h.input || '').trim(), (h.output || '').trim());
                                    } catch (er) {
                                        ToastBus.emit(er.message || '初始化失败', 'error');
                                    } finally { setBusy(false); }
                                };
                                return (
                                    <div
                                        key={i}
                                        onClick={fillOnly}
                                        style={{
                                            padding: '11px 12px 11px 18px',
                                            borderTop: i === 0 ? 'none' : '1px solid var(--line)',
                                            cursor: 'pointer',
                                            display: 'flex', alignItems: 'center', gap: 14,
                                            color: 'var(--text)',
                                            transition: 'background var(--t-fast) var(--ease)',
                                        }}
                                        onMouseEnter={e => e.currentTarget.style.background = 'var(--surface-1)'}
                                        onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                                    >
                                        <I.Folder size={15} style={{ color: 'var(--text-3)', flexShrink: 0 }}/>
                                        <div className="col flex-1" style={{ gap: 2, minWidth: 0 }}>
                                            <span className="mono" style={{ fontSize: 12, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                                {h.input}
                                            </span>
                                            {h.output && (
                                                <span style={{ color: 'var(--text-3)', fontSize: 11, fontFamily: 'var(--font-mono)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                                    → {h.output}
                                                </span>
                                            )}
                                        </div>
                                        <button
                                            className="btn btn-sm"
                                            onClick={goNow}
                                            disabled={busy}
                                            title="直接用此目录开始处理（跳过回填确认）"
                                            style={{ flexShrink: 0 }}
                                        >
                                            <I.Sparkles size={12}/> 直接进入
                                        </button>
                                    </div>
                                );
                            })}
                        </div>
                        <div style={{ padding: '8px 14px', fontSize: 10, color: 'var(--text-4)', borderTop: '1px solid var(--line)' }}>
                            单击仅回填到上方表单 · 「直接进入」会立刻用当前目录初始化
                        </div>
                    </Panel>
                )}

                <div className="col gap-2" style={{ paddingTop: 8 }}>
                    <div className="eyebrow">03 · 工作流</div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10 }}>
                        {workflows.map(c => {
                            const enabled = !c.needSession || session?.is_initialized;
                            return (
                                <button
                                    key={c.id}
                                    onClick={() => enabled && onTab(c.id)}
                                    disabled={!enabled}
                                    style={{
                                        appearance: 'none',
                                        background: c.highlight ? 'var(--accent-soft)' : 'var(--surface-0)',
                                        border: '1px solid ' + (c.highlight ? 'var(--accent-line)' : 'var(--line)'),
                                        borderRadius: 10,
                                        padding: '14px 16px',
                                        textAlign: 'left',
                                        cursor: enabled ? 'pointer' : 'not-allowed',
                                        color: 'var(--text)',
                                        opacity: enabled ? 1 : .45,
                                        transition: 'border-color var(--t-fast) var(--ease), background var(--t-fast) var(--ease)',
                                    }}
                                    onMouseEnter={e => { if (enabled && !c.highlight) { e.currentTarget.style.borderColor = 'var(--accent-line)'; e.currentTarget.style.background = 'var(--surface-1)'; } }}
                                    onMouseLeave={e => { if (!c.highlight) { e.currentTarget.style.borderColor = 'var(--line)'; e.currentTarget.style.background = 'var(--surface-0)'; } }}
                                >
                                    <div className="row gap-2" style={{ marginBottom: 6 }}>
                                        <c.icon size={15} style={{ color: 'var(--accent)' }}/>
                                        <span style={{ fontWeight: 600, fontSize: 13 }}>{c.title}</span>
                                        {c.highlight && <span className="pill accent" style={{ marginLeft: 4 }}>NEW</span>}
                                    </div>
                                    <div style={{ color: 'var(--text-3)', fontSize: 12, lineHeight: 1.4 }}>{c.body}</div>
                                </button>
                            );
                        })}
                    </div>
                </div>
            </div>
        </div>
    );
}

window.HomeScreen = HomeScreen;
