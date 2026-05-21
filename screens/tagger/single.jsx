/* screens/tagger/single.jsx — 单图模式：拖一张图、看推理结果
 * 暴露 window.TG.SingleMode
 */

function SingleMode({ session, settings, currentModel, modelStatus, onLoadModel }) {
    const [file, setFile] = React.useState(null);
    const [previewURL, setPreviewURL] = React.useState('');
    const [busy, setBusy] = React.useState(false);
    const [result, setResult] = React.useState(null);
    const inputRef = React.useRef(null);

    React.useEffect(() => () => { if (previewURL) URL.revokeObjectURL(previewURL); }, [previewURL]);

    function onFile(f) {
        if (!f) return;
        if (previewURL) URL.revokeObjectURL(previewURL);
        setFile(f);
        setPreviewURL(URL.createObjectURL(f));
        setResult(null);
    }

    async function loadActiveCropImage() {
        if (!session || typeof session.current_index !== 'number') return;
        setBusy(true);
        try {
            const idx = session.current_index;
            const infoRes = await fetch(`/api/image/${idx}`);
            if (!infoRes.ok) throw new Error('无法获取图片元数据');
            const info = await infoRes.json();
            const origPath = info.filepath || 'image.png';
            const filename = origPath.split(/[\\/]/).pop() || 'image.png';

            const res = await fetch(`/api/file/${idx}`);
            if (!res.ok) throw new Error('无法拉取当前图片文件');
            const blob = await res.blob();
            
            const f = new File([blob], filename, { type: blob.type });
            onFile(f);
            ToastBus.emit(`成功载入裁切图片: ${filename}`, 'ok');
        } catch (e) {
            ToastBus.emit(e.message || '加载图片失败', 'error');
        } finally {
            setBusy(false);
        }
    }

    const modelReady = settings.model && currentModel === settings.model && modelStatus !== 'loading';

    async function run() {
        if (!file) { ToastBus.emit('请先选择图片', 'error'); return; }
        if (!settings.model) { ToastBus.emit('请先在左侧选择模型', 'error'); return; }
        if (!modelReady) {
            ToastBus.emit('正在加载模型，首次可能需要几分钟…', 'warn', 4000);
        }
        setBusy(true);
        try {
            const fd = new FormData();
            fd.append('image', file);
            fd.append('model', settings.model);
            const send = (k, v) => fd.append(k, v == null ? '' : String(v));
            send('threshold', settings.threshold);
            send('additional_tags', settings.additional_tags);
            send('exclude_tags', settings.exclude_tags);
            send('excluded_categories', settings.excluded_categories || '');
            send('replace_underscore', settings.replace_underscore);
            send('replace_underscore_excludes', settings.replace_underscore_excludes);
            send('escape_tag', settings.escape_tag);
            send('sort_alphabetical', settings.sort_alphabetical);
            send('core_tags', settings.core_tags);
            send('core_tags_weight', settings.core_tags_weight);
            send('add_confident_as_weight', settings.add_confident_as_weight);
            send('use_weight_mapping', settings.use_weight_mapping);
            send('weight_mapping_exponent', settings.weight_mapping_exponent);
            send('weight_min', settings.weight_min);
            send('weight_max', settings.weight_max);
            send('max_tags', settings.max_tags);
            send('token_limit', settings.token_limit);
            send('tag_precision', settings.tag_precision);
            const r = await fetch('/api/tagger/interrogate', { method: 'POST', body: fd });
            const j = await r.json();
            if (!r.ok || !j.success) throw new Error(j.error || `${r.status}`);
            setResult(j);
            ToastBus.emit(`识别到 ${j.count} 个标签`, 'ok');
        } catch (e) {
            ToastBus.emit(e.message || '识别失败', 'error');
        } finally { setBusy(false); }
    }

    function copyTags() {
        if (!result?.tags) return;
        navigator.clipboard.writeText(result.tags).then(
            () => ToastBus.emit('已复制', 'ok'),
            () => ToastBus.emit('复制失败', 'error'),
        );
    }

    const { ConfidencePanel, GroupedConfidencePanel } = window.TG;

    return (
        <div className="col gap-4" style={{ maxWidth: 1280, margin: '0 auto' }}>
            <div
                onDragOver={e => e.preventDefault()}
                onDrop={e => { e.preventDefault();
                    const f = e.dataTransfer.files?.[0]; if (f) onFile(f); }}
                onClick={() => inputRef.current?.click()}
                style={{
                    padding: 22,
                    border: '2px dashed var(--line-strong)',
                    borderRadius: 'var(--r-3)',
                    background: previewURL ? 'transparent' : 'var(--surface-0)',
                    cursor: 'pointer',
                    minHeight: previewURL ? 280 : 180,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    overflow: 'hidden',
                }}
            >
                <input ref={inputRef} type="file" accept="image/*" style={{ display: 'none' }}
                    onChange={e => onFile(e.target.files?.[0])}/>
                {previewURL ? (
                    <img src={previewURL} alt="preview"
                        style={{ maxWidth: '100%', maxHeight: 520, objectFit: 'contain' }}/>
                ) : (
                    <div className="col gap-2" style={{ alignItems: 'center', color: 'var(--text-3)' }}>
                        <I.Image size={28}/>
                        <span style={{ fontSize: 13 }}>点击或拖入一张图片</span>
                        <span style={{ fontSize: 11 }}>支持 PNG / JPG / WEBP</span>
                    </div>
                )}
            </div>

            <div className="row gap-2" style={{ justifyContent: 'space-between' }}>
                <span className="pill" title={modelReady ? '已就绪' : '模型尚未加载，可点击左侧「加载」按钮预加载，或直接点开始（首次会自动下载）'}>
                    <I.Cpu size={11}/>
                    {modelReady ? (currentModel || settings.model) : (settings.model ? `${settings.model} · 未加载` : '未选模型')}
                </span>
                <div className="row gap-2">
                    {file && <span className="pill mono" title={file.name}
                        style={{ maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {file.name}
                    </span>}
                    {session?.is_initialized && typeof session.current_index === 'number' && (
                        <button className="btn btn-soft" onClick={loadActiveCropImage} disabled={busy}>
                            <I.Image size={14}/> 载入当前裁切图片
                        </button>
                    )}
                    {settings.model && !modelReady && (
                        <button className="btn" onClick={onLoadModel} disabled={modelStatus === 'loading'}
                            title="先把模型加载好；模型首次会自动下载">
                             {modelStatus === 'loading'
                                 ? <><span className="spin"/> 加载模型</>
                                 : <><I.Download size={13}/> 预加载模型</>}
                        </button>
                    )}
                    <button className="btn btn-primary" onClick={run} disabled={busy || !file}>
                        {busy ? <><span className="spin"/> 推理中</> : <><I.Sparkles size={14}/> 开始识别</>}
                    </button>
                </div>
            </div>

            {result && (
                <div className="col gap-3">
                    <Panel eyebrow="OUTPUT" title="生成标签 (.txt 实际内容)"
                        action={<button className="btn btn-sm" onClick={copyTags}><I.Copy size={12}/> 复制</button>}>
                        <textarea readOnly className="input mono"
                            style={{ minHeight: 120, padding: 14, border: 'none', background: 'var(--surface-1)' }}
                            value={result.tags || ''}/>
                    </Panel>
                    <div className="row gap-3" style={{ alignItems: 'stretch', flexWrap: 'wrap' }}>
                        <ConfidencePanel title="评级 (Ratings)" data={result.ratings}/>
                        <GroupedConfidencePanel
                            title="标签置信度（按类别分组）"
                            data={result.raw_tags}
                            categories={result.tag_categories || {}}
                            highlight={(settings.excluded_categories || '').split(',').map(s => s.trim()).filter(Boolean)}
                        />
                        {result.meta && Object.keys(result.meta).length > 0 && (
                            <ConfidencePanel title="元数据 (year / meta / quality)" data={result.meta}/>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}

window.TG = Object.assign(window.TG || {}, { SingleMode });
