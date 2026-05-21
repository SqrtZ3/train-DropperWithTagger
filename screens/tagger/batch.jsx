/* screens/tagger/batch.jsx — 批量目录模式
 * 暴露 window.TG.BatchMode
 */

function BatchMode({ session, settings }) {
    const [inputPath, setInputPath] = React.useState(() => {
        const cached = localStorage.getItem('drop.taggerBatchInput');
        if (cached) return cached;
        return session?.output_folder || session?.image_folder || '';
    });
    const [outputPath, setOutputPath] = React.useState(() => {
        const cached = localStorage.getItem('drop.taggerBatchOutput');
        if (cached !== null) return cached;
        return '';
    });
    const [recursive, setRecursive] = React.useState(false);
    const [conflict, setConflict] = React.useState('ignore');
    const [job, setJob] = React.useState(null);
    const pollRef = React.useRef(null);

    React.useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);
    React.useEffect(() => { localStorage.setItem('drop.taggerBatchInput', inputPath); }, [inputPath]);
    React.useEffect(() => { localStorage.setItem('drop.taggerBatchOutput', outputPath); }, [outputPath]);

    const { Check } = window.TG;

    async function start() {
        if (!inputPath.trim()) { ToastBus.emit('请填入输入目录', 'error'); return; }
        if (!settings.model) { ToastBus.emit('请先在左侧选择模型', 'error'); return; }
        try {
            const body = {
                input_path: inputPath.trim(),
                output_path: outputPath.trim() || null,
                recursive,
                output_format: '[name].txt',
                batch_size: 8,
                conflict_mode: conflict,
                ...settings,
            };
            const r = await api.post('/api/tagger/batch', body);
            if (!r.success) throw new Error(r.error || '启动失败');
            setJob({ job_id: r.job_id, status: 'queued', processed: 0, total: 0 });
            pollProgress(r.job_id);
        } catch (e) { ToastBus.emit(e.message || '启动失败', 'error'); }
    }

    function pollProgress(jobId) {
        if (pollRef.current) clearInterval(pollRef.current);
        pollRef.current = setInterval(async () => {
            try {
                const r = await api.get(`/api/tagger/batch/${jobId}`);
                if (r.success) setJob(r);
                if (r.status === 'done' || r.status === 'failed' || r.status === 'cancelled') {
                    clearInterval(pollRef.current); pollRef.current = null;
                    ToastBus.emit(
                        `批处理 ${r.status}：成功 ${r.result?.success_count || 0} · 错 ${r.result?.error_count || 0}`,
                        r.status === 'done' ? 'ok' : 'warn');
                }
            } catch {}
        }, 800);
    }

    async function cancel() {
        if (!job) return;
        try {
            await api.post(`/api/tagger/batch/${job.job_id}/cancel`);
            ToastBus.emit('已发送取消信号', 'warn');
        } catch (e) { ToastBus.emit(e.message || '取消失败', 'error'); }
    }

    const pct = job && job.total > 0 ? (job.processed / job.total) : 0;
    const running = job && (job.status === 'queued' || job.status === 'running');

    return (
        <div className="col gap-4" style={{ maxWidth: 1080, margin: '0 auto' }}>
            <Panel eyebrow="INPUT" title="批量目录">
                <div className="col gap-3" style={{ padding: 14 }}>
                    <div className="col gap-1">
                        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
                            <span className="eyebrow">输入目录</span>
                            <div className="row gap-2">
                                {session?.output_folder && (
                                    <button className="btn btn-sm btn-soft" style={{ padding: '2px 8px', fontSize: 10 }}
                                        onClick={() => setInputPath(session.output_folder)}
                                        title={`导入当前裁切输出目录：${session.output_folder}`}>
                                        导入当前裁切目录
                                    </button>
                                )}
                                {session?.image_folder && (
                                    <button className="btn btn-sm btn-soft" style={{ padding: '2px 8px', fontSize: 10 }}
                                        onClick={() => setInputPath(session.image_folder)}
                                        title={`导入当前原图输入目录：${session.image_folder}`}>
                                        导入原图输入目录
                                    </button>
                                )}
                            </div>
                        </div>
                        <div className="input-row">
                            <I.Folder size={14} style={{ color: 'var(--text-3)' }}/>
                            <input className="input mono" value={inputPath}
                                placeholder={'D:\\Datasets\\xxx'}
                                onChange={e => setInputPath(e.target.value)}
                                onPaste={pasteCleanedPath(setInputPath)}
                                onBlur={e => setInputPath(cleanPath(e.target.value))}/>
                        </div>
                    </div>
                    <div className="col gap-1">
                        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
                            <span className="eyebrow">
                                输出目录 <span style={{ color: 'var(--text-4)', textTransform: 'none' }}>· 可选，留空则与图片同目录</span>
                            </span>
                            <div className="row gap-2">
                                {session?.output_folder && (
                                    <button className="btn btn-sm btn-soft" style={{ padding: '2px 8px', fontSize: 10 }}
                                        onClick={() => setOutputPath(session.output_folder)}
                                        title={`导入当前会话输出目录：${session.output_folder}`}>
                                        导入当前输出目录
                                    </button>
                                )}
                                <button className="btn btn-sm btn-soft" style={{ padding: '2px 8px', fontSize: 10 }}
                                    onClick={() => setOutputPath('')}
                                    title="清空输出目录以在图片旁边生成 .txt 文件">
                                    留空 (同目录)
                                </button>
                            </div>
                        </div>
                        <div className="input-row">
                            <I.Folder size={14} style={{ color: 'var(--text-3)' }}/>
                            <input className="input mono" value={outputPath}
                                placeholder="留空则在每张图片旁边生成 .txt"
                                onChange={e => setOutputPath(e.target.value)}
                                onPaste={pasteCleanedPath(setOutputPath)}
                                onBlur={e => setOutputPath(cleanPath(e.target.value))}/>
                        </div>
                    </div>
                    <div className="row gap-3" style={{ flexWrap: 'wrap' }}>
                        <Check label="递归子目录" checked={recursive} onChange={setRecursive}/>
                        <div className="row gap-2">
                            <span className="eyebrow">冲突</span>
                            <select className="select" style={{ width: 120 }} value={conflict}
                                onChange={e => setConflict(e.target.value)}>
                                <option value="ignore">跳过</option>
                                <option value="overwrite">覆盖</option>
                                <option value="append">追加</option>
                                <option value="prepend">前置</option>
                            </select>
                        </div>
                    </div>
                    <div className="row gap-2">
                        <button className="btn btn-primary" onClick={start} disabled={running}>
                            {running ? <><span className="spin"/> 运行中</> : <><I.Play size={14}/> 启动</>}
                        </button>
                        <button className="btn btn-warn" onClick={cancel} disabled={!running}>
                            <I.StopCircle size={14}/> 取消
                        </button>
                    </div>
                </div>
            </Panel>

            {job && (
                <Panel eyebrow="PROGRESS" title={`Job ${job.job_id} · ${job.status}`}>
                    <div className="col gap-3" style={{ padding: 14 }}>
                        <div className="bar"><div className="fill" style={{ width: `${pct * 100}%` }}/></div>
                        <div className="row" style={{ justifyContent: 'space-between' }}>
                            <span className="mono" style={{ color: 'var(--text-2)', fontSize: 12 }}>
                                {job.processed} / {job.total || '?'}
                            </span>
                            <span className="mono" style={{ color: 'var(--text-3)', fontSize: 12,
                                                            maxWidth: 360, overflow: 'hidden',
                                                            textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                {job.current_file || '—'}
                            </span>
                            <span className="pill">{job.error_count || 0} 错</span>
                        </div>
                        {job.errors && job.errors.length > 0 && (
                            <div className="col" style={{ maxHeight: 180, overflowY: 'auto',
                                                          border: '1px solid var(--line)',
                                                          borderRadius: 'var(--r-2)' }}>
                                {job.errors.map((er, i) => (
                                    <div key={i} className="row mono" style={{ padding: '4px 10px', gap: 8,
                                                                                fontSize: 11, color: 'var(--danger)' }}>
                                        <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis' }}>{er.file}</span>
                                        <span style={{ color: 'var(--text-3)' }}>{er.error}</span>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                </Panel>
            )}
        </div>
    );
}

window.TG = Object.assign(window.TG || {}, { BatchMode });
