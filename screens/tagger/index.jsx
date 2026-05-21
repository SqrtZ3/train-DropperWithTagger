/* screens/tagger/index.jsx — TaggerScreen 组装根
 *
 * 依赖：window.TG.{Sidebar, SingleMode, BatchMode, QueueMode, ConfidencePanel,
 *                  GroupedConfidencePanel, Slider, Check, Stat, CollapsiblePanel,
 *                  DEFAULTS, loadStoredSettings}
 *
 * 主要修复：
 * - 不再走 tagger.jsx → fetch + Babel.transform + script 注入的 loader shim：
 *   各子模块都是常规 <script type="text/babel">，直接由 babel-standalone 处理。
 * - 默认模型不再硬写 "hf:Camais03/camie-tagger-v2"。如果用户存的 settings.model
 *   不在「已下载」名单里，自动落到首个已下载的本地模型，避免开页就触发几百 MB 下载。
 * - 新增 /api/tagger/preload 的轮询入口（POST /load_model + GET /models 来判定加载状态），
 *   sidebar 可以提前点「加载」按钮，无需先选图。
 */

function TaggerScreen({ session, refresh, onTab }) {
    const [models, setModels] = React.useState([]);
    const [currentModel, setCurrentModel] = React.useState('');
    const [modelStatus, setModelStatus] = React.useState('idle');   // idle | loading
    const [settings, _setSettings] = React.useState(window.TG.loadStoredSettings);
    // 默认从 batch 进 —— 这是日常最常用的模式。
    // 老用户的 localStorage 里如果还存着 'single'，也会照常进单图（用户选过的）。
    const [mode, setMode] = React.useState(localStorage.getItem('drop.taggerMode') || 'batch');
    const [presets, setPresets] = React.useState([]);
    const [presetName, setPresetName] = React.useState('');
    const [categoryInfo, setCategoryInfo] = React.useState({
        all: ['general','character','copyright','artist','meta','model','quality','year'],
        present: null,
        labels: { general:'一般', character:'角色', copyright:'版权', artist:'画师',
                  meta:'元数据', model:'模型', quality:'质量', year:'年份' },
    });

    React.useEffect(() => { localStorage.setItem('drop.taggerMode', mode); }, [mode]);

    const setSettings = (patch) => {
        _setSettings(prev => {
            const next = typeof patch === 'function' ? patch(prev) : { ...prev, ...patch };
            try { localStorage.setItem('drop.taggerSettings', JSON.stringify(next)); } catch {}
            return next;
        });
    };

    // ============== 数据拉取 ==============
    const loadModels = React.useCallback(() => {
        api.get('/api/tagger/models')
            .then(r => {
                const list = r.models || [];
                setModels(list);
                setCurrentModel(r.current || '');
                // 默认模型：用户存的 settings.model 缺失或指向「未下载」的远程模型时，
                // 落到首个已下载本地模型，避免开页就触发几百 MB 下载阻塞 UI。
                _setSettings(prev => {
                    const selected = list.find(m => m.id === prev.model);
                    if (prev.model && selected?.downloaded) return prev;       // 当前选择有效
                    if (prev.model && selected && !selected.downloaded && r.current === prev.model) {
                        return prev; // 远程模型但已加载了，保持
                    }
                    // 落到 downloaded 名单的首个
                    const localFirst = list.find(m => m.downloaded);
                    if (!localFirst) return prev;  // 一个本地都没有 → 保持原状（用户得手动选）
                    const next = { ...prev, model: localFirst.id };
                    try { localStorage.setItem('drop.taggerSettings', JSON.stringify(next)); } catch {}
                    return next;
                });
            })
            .catch(e => ToastBus.emit(e.message || '加载模型失败', 'error'));
    }, []);

    const loadPresets = React.useCallback(() => {
        api.get('/api/tagger/presets')
            .then(r => setPresets(r.presets || []))
            .catch(() => {});
    }, []);

    const loadCategories = React.useCallback(() => {
        api.get('/api/tagger/categories')
            .then(r => setCategoryInfo(c => ({ ...c, ...r })))
            .catch(() => {});
    }, []);

    React.useEffect(() => { loadModels(); loadPresets(); loadCategories(); },
        [loadModels, loadPresets, loadCategories]);
    React.useEffect(() => { loadCategories(); }, [currentModel, loadCategories]);

    // ============== 模型加载/卸载 ==============
    async function loadModel() {
        if (!settings.model) {
            ToastBus.emit('请先选择模型', 'error');
            return;
        }
        // 立即给 toast：点了按钮但模型加载实际要几秒到几分钟，没有这个反馈
        // 看起来就是「按了没反应」（即使按钮已经进 loading 态用户也可能没注意到）。
        // selected.downloaded 决定文案——本地模型只是「加载到显存」；未下载的远程
        // 模型还要先做几百 MB 的 HTTP 下载，得明确提示。
        const selected = models.find(m => m.id === settings.model);
        const isLocal = !!selected?.downloaded;
        ToastBus.emit(
            isLocal ? `正在加载 ${settings.model}…` : `正在下载并加载 ${settings.model}（首次需联网，可能几分钟）…`,
            'warn', 3500,
        );
        setModelStatus('loading');
        try {
            const r = await api.post('/api/tagger/preload', { model: settings.model });
            if (r.success) {
                ToastBus.emit(`模型已加载: ${settings.model}`, 'ok');
                setCurrentModel(settings.model);
                loadModels();   // 刷新 downloaded 标志
            } else {
                throw new Error(r.error || '加载失败');
            }
        } catch (e) {
            // 后端会把 download / load 的具体原因塞进 e.message（含建议、镜像设置等）。
            // 详细版本也打到 console 方便复制；toast 显示原文（含换行）8 秒。
            console.error('[tagger] preload 失败:', e);
            ToastBus.emit(e.message || '加载失败', 'error', 8000);
        } finally {
            setModelStatus('idle');
        }
    }

    async function unload() {
        try {
            await api.post('/api/tagger/unload');
            ToastBus.emit('已卸载模型', 'ok');
            setCurrentModel('');
            loadModels();
        } catch (e) { ToastBus.emit(e.message || '卸载失败', 'error'); }
    }

    // ============== 预设 ==============
    async function savePreset() {
        const name = (presetName || '').trim();
        if (!name) { ToastBus.emit('预设名称不能为空', 'error'); return; }
        try {
            await api.post('/api/tagger/presets', { name, data: settings });
            ToastBus.emit(`已保存预设 ${name}`, 'ok');
            loadPresets();
        } catch (e) { ToastBus.emit(e.message || '保存失败', 'error'); }
    }

    async function loadPreset(name) {
        if (!name) return;
        try {
            const r = await api.get(`/api/tagger/presets/${encodeURIComponent(name)}`);
            if (r.success && r.data) {
                const { name: _n, version: _v, ...rest } = r.data;
                setSettings(s => ({ ...s, ...rest }));
                ToastBus.emit(`已加载预设 ${name}`, 'ok');
            }
        } catch (e) { ToastBus.emit(e.message || '加载失败', 'error'); }
    }

    async function deletePreset(name) {
        if (!name) return;
        if (!confirm(`删除预设 "${name}"？`)) return;
        try {
            await fetch(`/api/tagger/presets/${encodeURIComponent(name)}`, { method: 'DELETE' });
            ToastBus.emit(`已删除 ${name}`, 'ok');
            loadPresets();
        } catch (e) { ToastBus.emit(e.message || '删除失败', 'error'); }
    }

    const { Sidebar, SingleMode, BatchMode, QueueMode } = window.TG;

    return (
        <div className="row" style={{ flex: 1, minHeight: 0 }}>
            <Sidebar
                models={models}
                currentModel={currentModel}
                modelStatus={modelStatus}
                onRefreshModels={loadModels}
                onUnload={unload}
                onLoadModel={loadModel}
                settings={settings}
                setSettings={setSettings}
                categoryInfo={categoryInfo}
                presets={presets}
                presetName={presetName}
                setPresetName={setPresetName}
                onSavePreset={savePreset}
                onLoadPreset={loadPreset}
                onDeletePreset={deletePreset}
                onRefreshPresets={loadPresets}
            />
            <main className="col" style={{ flex: 1, minWidth: 0, background: 'var(--bg)' }}>
                <div style={{ padding: '14px 22px 0', display: 'flex', gap: 12, alignItems: 'center' }}>
                    <div className="seg">
                        <button className={mode === 'batch' ? 'on' : ''} onClick={() => setMode('batch')}
                            title="给整个目录的图片打标（常用）">批量目录</button>
                        <button className={mode === 'queue' ? 'on' : ''} onClick={() => setMode('queue')}
                            title="Drop Studio 批处理写出 PNG 时自动入队打标">联动队列</button>
                        <button className={mode === 'single' ? 'on' : ''} onClick={() => setMode('single')}
                            title="只对一张图推理，用来测参数/看置信度">测试单图</button>
                    </div>
                    <span style={{ fontSize: 11, color: 'var(--text-3)' }}>
                        {mode === 'batch' && '指一个目录，给所有图生成 .txt'}
                        {mode === 'queue' && '与 Drop Studio 输出联动，写完一张自动打标'}
                        {mode === 'single' && '调试用：单张图，看每个标签的置信度'}
                    </span>
                </div>
                <div style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: 22 }}>
                    {mode === 'single' && <SingleMode
                        session={session}
                        settings={settings} currentModel={currentModel}
                        modelStatus={modelStatus} onLoadModel={loadModel}/>}
                    {mode === 'batch'  && <BatchMode  session={session} settings={settings}/>}
                    {mode === 'queue'  && <QueueMode  session={session} refresh={refresh}
                                                       settings={settings} setSettings={setSettings}
                                                       onTab={onTab}/>}
                </div>
            </main>
        </div>
    );
}

window.TaggerScreen = TaggerScreen;
