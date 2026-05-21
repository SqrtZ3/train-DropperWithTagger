/* screens/tagger/queue.jsx — 联动队列模式：自动标注 Drop Studio 的输出
 * 暴露 window.TG.QueueMode
 */

function QueueMode({ session, refresh, settings, setSettings, onTab }) {
    const [snap, setSnap] = React.useState(null);
    const [busy, setBusy] = React.useState(false);
    const pollRef = React.useRef(null);

    const fetchSnap = React.useCallback(() => {
        api.get('/api/tagger/queue').then(setSnap).catch(() => {});
    }, []);

    React.useEffect(() => {
        fetchSnap();
        pollRef.current = setInterval(fetchSnap, 1500);
        return () => { if (pollRef.current) clearInterval(pollRef.current); };
    }, [fetchSnap]);

    // 当 settings 变化时把当前后处理参数推到后端（300ms 防抖）。
    // 不把 settings 直接放进 deps 数组（引用每次都不同），用 ref 跟踪上次推送过的版本。
    const lastPushedRef = React.useRef('');
    React.useEffect(() => {
        const fp = JSON.stringify(settings);
        if (fp === lastPushedRef.current) return;
        const t = setTimeout(() => {
            lastPushedRef.current = fp;
            api.post('/api/tagger/queue/config', settings).catch(() => {});
        }, 300);
        return () => clearTimeout(t);
    });

    async function toggleAutoTag(v) {
        try {
            await api.post('/api/session/auto_tag', { enabled: v });
            ToastBus.emit(v ? '已开启自动标注' : '已关闭自动标注', 'ok');
            refresh?.();
        } catch (e) { ToastBus.emit(e.message || '操作失败', 'error'); }
    }

    async function tagCurrent() {
        if (!session?.is_initialized) {
            ToastBus.emit('请先在主页初始化会话', 'error');
            onTab?.('home');
            return;
        }
        setBusy(true);
        try {
            const r = await api.post('/api/session/tag_output');
            if (r.error) throw new Error(r.error);
            ToastBus.emit(`已入队 ${r.added} 张（扫到 ${r.total_scanned}）`, 'ok');
            fetchSnap();
        } catch (e) { ToastBus.emit(e.message || '入队失败', 'error'); }
        finally { setBusy(false); }
    }

    async function clear() {
        if (!confirm('清空 pending 队列并把完成/跳过/失败计数清零？\n（磁盘上已写出的 .txt 不会被删除）')) return;
        try { await api.post('/api/tagger/queue/clear'); fetchSnap(); }
        catch (e) { ToastBus.emit(e.message || '清空失败', 'error'); }
    }

    const { Check, Stat } = window.TG;

    const auto = !!session?.auto_tag_enabled;
    const pending = snap?.pending || 0;
    const processing = snap?.processing || 0;
    const done = snap?.done || 0;
    const failed = snap?.failed || 0;
    const skipped = snap?.skipped || 0;
    const recent = snap?.recent || [];

    return (
        <div className="col gap-4" style={{ maxWidth: 1080, margin: '0 auto' }}>
            <Panel eyebrow="01" title="联动控制">
                <div className="col gap-3" style={{ padding: 14 }}>
                    <div className="row" style={{ justifyContent: 'space-between', gap: 14 }}>
                        <div className="col gap-1" style={{ flex: 1, minWidth: 0 }}>
                            <span style={{ fontSize: 13, fontWeight: 600 }}>自动标注 Drop Studio 输出</span>
                            <span style={{ fontSize: 11, color: 'var(--text-3)' }}>
                                打开后：批处理 / 一键全自动 每写完一张 PNG 就自动入队推理，结果写到同名 .txt
                            </span>
                            <span style={{ fontSize: 11, color: 'var(--text-3)' }}>
                                当前会话：<span className="mono" style={{ color: 'var(--text-2)' }}>{session?.output_folder || '—'}</span>
                            </span>
                        </div>
                        <button
                            className={`btn ${auto ? 'btn-primary' : 'btn-soft'}`}
                            onClick={() => toggleAutoTag(!auto)}
                            disabled={!session?.is_initialized}
                        >
                            {auto ? '已开启' : '已关闭'}
                        </button>
                    </div>
                    <div className="row gap-2">
                        <Check label="覆盖已有 .txt（默认 skip-if-exists）"
                            checked={!!settings.overwrite_existing_txt}
                            onChange={v => setSettings({ overwrite_existing_txt: v })}/>
                    </div>
                    <div className="row gap-2">
                        <button className="btn" onClick={tagCurrent} disabled={busy || !session?.is_initialized}>
                            {busy ? <><span className="spin"/> 入队中</> : <><I.Sparkles size={13}/> 一键标注当前 output</>}
                        </button>
                        <button className="btn btn-warn" onClick={clear} disabled={!pending && !processing}>
                            <I.Trash size={13}/> 清空队列
                        </button>
                    </div>
                </div>
            </Panel>

            <Panel eyebrow="02" title="状态">
                <div className="row" style={{ padding: 14, gap: 8, justifyContent: 'space-between' }}>
                    <Stat label="pending" value={pending}/>
                    <Stat label="处理中" value={processing} accent={processing > 0}/>
                    <Stat label="完成"   value={done} accent={done > 0}/>
                    <Stat label="跳过"   value={skipped}/>
                    <Stat label="失败"   value={failed} danger={failed > 0}/>
                </div>
                {snap?.last_error && (
                    <div className="row mono" style={{ padding: '4px 14px 12px', fontSize: 11, color: 'var(--danger)' }}>
                        最近错误: {snap.last_error}
                    </div>
                )}
            </Panel>

            <Panel eyebrow="03" title="最近事件">
                <div className="col" style={{ maxHeight: 360, overflowY: 'auto' }}>
                    {recent.length === 0 && (
                        <div style={{ padding: 14, color: 'var(--text-3)', fontSize: 12 }}>暂无事件</div>
                    )}
                    {recent.map((e, i) => (
                        <div key={i} className="row mono" style={{
                            padding: '5px 14px', gap: 8, borderTop: '1px solid var(--line)',
                            fontSize: 11, alignItems: 'center',
                        }}>
                            <span className={`pill ${e.status === 'done' ? 'accent' : e.status === 'fail' ? 'danger' : ''}`}
                                  style={{ minWidth: 36, justifyContent: 'center' }}>
                                {e.status}
                            </span>
                            <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis',
                                           whiteSpace: 'nowrap', color: 'var(--text-2)' }}>
                                {e.name}
                            </span>
                            <span style={{ color: 'var(--text-3)' }}>{e.reason}</span>
                        </div>
                    ))}
                </div>
            </Panel>
        </div>
    );
}

window.TG = Object.assign(window.TG || {}, { QueueMode });
