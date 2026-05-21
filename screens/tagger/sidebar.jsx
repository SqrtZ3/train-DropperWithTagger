/* screens/tagger/sidebar.jsx — Tagger 屏左侧 5 个面板：模型/阈值/LoRA/过滤/预设
 *
 * 暴露 window.TG.Sidebar
 */

function TaggerSidebar({
    models, currentModel, modelStatus, onRefreshModels, onUnload, onLoadModel,
    settings, setSettings, categoryInfo, presets, presetName, setPresetName,
    onSavePreset, onLoadPreset, onDeletePreset, onRefreshPresets,
}) {
    const { Slider, Check, CollapsiblePanel } = window.TG;

    const excludedSet = React.useMemo(() => {
        return new Set((settings.excluded_categories || '').split(',').map(s => s.trim()).filter(Boolean));
    }, [settings.excluded_categories]);
    function toggleCategory(cat) {
        const next = new Set(excludedSet);
        if (next.has(cat)) next.delete(cat); else next.add(cat);
        setSettings({ excluded_categories: Array.from(next).join(', ') });
    }
    const chipCats = (categoryInfo.present && categoryInfo.present.length > 0)
        ? categoryInfo.present
        : categoryInfo.all;

    // 当前选中的 model 是否就是后端已加载的；和「是否本地已下载」分开判定
    const selected = models.find(m => m.id === settings.model);
    const isLoaded = settings.model && currentModel === settings.model;
    const isLoading = modelStatus === 'loading';
    const isDownloaded = !!selected?.downloaded;

    return (
        <aside className="col" style={{
            width: 360, flexShrink: 0,
            borderRight: '1px solid var(--line)',
            background: 'var(--surface-0)',
            overflow: 'auto',
        }}>
            {/* 模型 */}
            <Panel eyebrow="01" title="模型">
                <div className="col gap-2" style={{ padding: '10px 14px 14px' }}>
                    <div className="row gap-2">
                        <select className="select" value={settings.model || ''}
                            onChange={e => setSettings({ model: e.target.value })}>
                            {models.length === 0 && <option value="">加载中...</option>}
                            {models.length > 0 && !settings.model && <option value="">— 选择模型 —</option>}
                            {models.map(m => (
                                <option key={m.id} value={m.id}>
                                    {m.downloaded ? '✓ ' : '↓ '}{m.label} {m.kind === 'camie' ? '★' : ''}
                                </option>
                            ))}
                        </select>
                        <button className="btn btn-icon" onClick={onRefreshModels} title="刷新模型列表">
                            <I.Refresh size={14}/>
                        </button>
                    </div>

                    {/* 模型状态条 + 一键加载/卸载 */}
                    <div className="row gap-2" style={{ alignItems: 'center' }}>
                        <span className="pill" style={{
                            border: '1px solid ' + (isLoaded ? 'var(--accent-line)' : 'var(--line)'),
                            color: isLoaded ? 'var(--accent)' : 'var(--text-3)',
                            fontSize: 10,
                        }}>
                            {isLoading
                                ? <><span className="spin"/> 加载中</>
                                : isLoaded
                                    ? <><I.Check size={10}/> 已加载</>
                                    : isDownloaded
                                        ? <>· 本地</>
                                        : <>· 未下载</>}
                        </span>
                        <span style={{ fontSize: 10, color: 'var(--text-4)', flex: 1,
                                       overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                              title={currentModel || '未加载任何模型'}>
                            {currentModel || '未加载'}
                        </span>
                        {settings.model && !isLoaded && (
                            <button className="btn btn-sm" onClick={onLoadModel} disabled={isLoading}
                                title={isDownloaded ? '加载到内存' : '下载并加载（首次需联网）'}>
                                {isLoading
                                    ? <><span className="spin"/> 加载</>
                                    : isDownloaded
                                        ? <><I.Play size={12}/> 加载</>
                                        : <><I.Download size={12}/> 下载</>}
                            </button>
                        )}
                        {isLoaded && (
                            <button className="btn btn-sm btn-warn" onClick={onUnload}>卸载</button>
                        )}
                    </div>
                </div>
            </Panel>

            {/* 阈值与输出 */}
            <Panel eyebrow="02" title="阈值与输出">
                <div className="col gap-3" style={{ padding: '10px 14px 14px' }}>
                    <Slider label="阈值 (threshold)" min={0} max={1} step={0.01}
                        value={settings.threshold} onChange={v => setSettings({ threshold: v })}/>
                    <div className="row gap-2">
                        <div className="col gap-1 flex-1">
                            <span className="eyebrow">Token 限制</span>
                            <select className="select" value={settings.token_limit}
                                onChange={e => setSettings({ token_limit: parseInt(e.target.value) || 0 })}>
                                <option value={0}>无限制</option>
                                <option value={75}>75 (CLIP)</option>
                                <option value={150}>150</option>
                                <option value={225}>225</option>
                            </select>
                        </div>
                        <div className="col gap-1" style={{ width: 90 }}>
                            <span className="eyebrow">最大标签</span>
                            <input className="input mono" type="number" min="0" value={settings.max_tags}
                                onChange={e => setSettings({ max_tags: parseInt(e.target.value) || 0 })}/>
                        </div>
                        <div className="col gap-1" style={{ width: 70 }}>
                            <span className="eyebrow">精度</span>
                            <input className="input mono" type="number" min="0" max="4" value={settings.tag_precision}
                                onChange={e => setSettings({ tag_precision: Math.max(0, Math.min(4, parseInt(e.target.value) || 0)) })}/>
                        </div>
                    </div>
                    <div className="col gap-2">
                        <Check label="替换下划线为空格" checked={settings.replace_underscore}
                            onChange={v => setSettings({ replace_underscore: v })}/>
                        <Check label="转义括号 \\(\\)" checked={settings.escape_tag}
                            onChange={v => setSettings({ escape_tag: v })}/>
                        <Check label="字母排序" checked={settings.sort_alphabetical}
                            onChange={v => setSettings({ sort_alphabetical: v })}/>
                    </div>
                </div>
            </Panel>

            {/* LoRA 优化 */}
            <CollapsiblePanel eyebrow="03" title="LoRA 优化（权重）" defaultOpen={false}>
                <div className="col gap-3" style={{ padding: '10px 14px 14px' }}>
                    <div className="col gap-1">
                        <span className="eyebrow">核心标签 (trigger words)</span>
                        <input className="input mono" value={settings.core_tags}
                            placeholder="1girl, solo"
                            onChange={e => setSettings({ core_tags: e.target.value })}/>
                    </div>
                    <Slider label="核心权重" min={1.0} max={1.5} step={0.05}
                        value={settings.core_tags_weight}
                        onChange={v => setSettings({ core_tags_weight: v })}/>
                    <Check label="启用智能权重映射 (add_confident_as_weight)"
                        checked={settings.add_confident_as_weight}
                        onChange={v => setSettings({ add_confident_as_weight: v })}/>
                    <Check label="使用 use_weight_mapping (覆盖)"
                        checked={settings.use_weight_mapping}
                        onChange={v => setSettings({ use_weight_mapping: v })}/>
                    <div className="row gap-2">
                        <Slider label="权重下限" min={0.5} max={1.0} step={0.05}
                            value={settings.weight_min} onChange={v => setSettings({ weight_min: v })}/>
                        <Slider label="权重上限" min={1.0} max={1.5} step={0.05}
                            value={settings.weight_max} onChange={v => setSettings({ weight_max: v })}/>
                    </div>
                    <Slider label="曲线指数" min={0.1} max={2.0} step={0.1}
                        value={settings.weight_mapping_exponent}
                        onChange={v => setSettings({ weight_mapping_exponent: v })}/>
                </div>
            </CollapsiblePanel>

            {/* 标签过滤 */}
            <Panel eyebrow="04" title="标签过滤">
                <div className="col gap-3" style={{ padding: '10px 14px 14px' }}>
                    <div className="col gap-1">
                        <span className="eyebrow">按类别排除（点选即排）</span>
                        <div className="row gap-1" style={{ flexWrap: 'wrap' }}>
                            {chipCats.map(cat => {
                                const on = excludedSet.has(cat);
                                const label = categoryInfo.labels?.[cat] || cat;
                                return (
                                    <button key={cat}
                                        className={`pill ${on ? 'danger' : ''}`}
                                        onClick={() => toggleCategory(cat)}
                                        title={on ? `点击恢复 ${cat}` : `点击排除 ${cat} 类全部标签`}
                                        style={{
                                            cursor: 'pointer',
                                            opacity: on ? 1 : 0.55,
                                            textDecoration: on ? 'line-through' : 'none',
                                            border: '1px solid ' + (on ? 'var(--danger)' : 'var(--line)'),
                                        }}>
                                        {label}
                                        <span style={{ opacity: 0.6, fontSize: 9 }}>· {cat}</span>
                                    </button>
                                );
                            })}
                        </div>
                        <span style={{ fontSize: 10, color: 'var(--text-3)' }}>
                            红色（划掉）= 排除该类所有标签。常用：LoRA 训练时排掉 artist / meta / quality / year。
                        </span>
                    </div>
                    <div className="col gap-1">
                        <span className="eyebrow">附加标签（开头）</span>
                        <input className="input mono" value={settings.additional_tags}
                            placeholder="masterpiece, best quality"
                            onChange={e => setSettings({ additional_tags: e.target.value })}/>
                    </div>
                    <div className="col gap-1">
                        <span className="eyebrow">排除标签（按名字）</span>
                        <input className="input mono" value={settings.exclude_tags}
                            placeholder="watermark, signature"
                            onChange={e => setSettings({ exclude_tags: e.target.value })}/>
                    </div>
                    <div className="col gap-1">
                        <span className="eyebrow">下划线替换排除（emoticons）</span>
                        <textarea className="input mono" rows="2"
                            value={settings.replace_underscore_excludes}
                            onChange={e => setSettings({ replace_underscore_excludes: e.target.value })}/>
                    </div>
                </div>
            </Panel>

            {/* 预设 */}
            <CollapsiblePanel eyebrow="05" title="预设" defaultOpen={false}>
                <div className="col gap-2" style={{ padding: '10px 14px 14px' }}>
                    <div className="row gap-2">
                        <select className="select" value=""
                            onChange={e => { if (e.target.value) onLoadPreset(e.target.value); }}>
                            <option value="">— 加载预设 —</option>
                            {presets.map(p => <option key={p} value={p}>{p}</option>)}
                        </select>
                        <button className="btn btn-icon" onClick={onRefreshPresets}
                            title="刷新预设列表">
                            <I.Refresh size={14}/>
                        </button>
                    </div>
                    <div className="row gap-2">
                        <input className="input mono" value={presetName}
                            placeholder="新预设名称"
                            onChange={e => setPresetName(e.target.value)}/>
                        <button className="btn btn-primary btn-sm" onClick={onSavePreset}>保存</button>
                    </div>
                    {presets.length > 0 && (
                        <div className="row gap-2" style={{ flexWrap: 'wrap' }}>
                            {presets.map(p => (
                                <span key={p} className="pill" style={{ cursor: 'pointer' }}
                                    onClick={() => onLoadPreset(p)}
                                    onDoubleClick={() => onDeletePreset(p)}
                                    title="单击加载 / 双击删除">
                                    {p}
                                </span>
                            ))}
                        </div>
                    )}
                </div>
            </CollapsiblePanel>
        </aside>
    );
}

window.TG = Object.assign(window.TG || {}, { Sidebar: TaggerSidebar });
