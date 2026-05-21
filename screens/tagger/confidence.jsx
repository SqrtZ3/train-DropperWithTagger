/* screens/tagger/confidence.jsx — 置信度面板
 *
 * 暴露 window.TG.ConfidencePanel 和 window.TG.GroupedConfidencePanel。
 * 后者按类别分组，并把已排除类别画灰加删除线。
 */

const CATEGORY_LABEL_ZH = {
    general: '一般', character: '角色', copyright: '版权', artist: '画师',
    meta: '元数据', model: '模型', quality: '质量', year: '年份', unknown: '未分类',
};

function _ConfidenceRow({ k, v, struck }) {
    return (
        <div className="row" style={{
            padding: '5px 14px', gap: 8, justifyContent: 'space-between',
            borderTop: '1px solid var(--line)',
            opacity: struck ? 0.4 : 1,
        }}>
            <span className="mono" style={{
                fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                textDecoration: struck ? 'line-through' : 'none',
            }}>
                {k}
            </span>
            <span style={{
                position: 'relative', minWidth: 56, height: 14,
                background: 'var(--surface-2)', borderRadius: 4,
                overflow: 'hidden', flexShrink: 0,
            }}>
                <span style={{
                    position: 'absolute', left: 0, top: 0, bottom: 0,
                    width: `${Math.min(1, v) * 100}%`,
                    background: struck ? 'var(--text-4)' : 'var(--accent)',
                }}/>
                <span style={{
                    position: 'absolute', inset: 0,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--text)',
                    textShadow: '0 0 2px rgba(0,0,0,0.6)',
                }}>
                    {(v * 100).toFixed(1)}%
                </span>
            </span>
        </div>
    );
}

function ConfidencePanel({ title, data }) {
    const entries = Object.entries(data || {}).sort((a, b) => b[1] - a[1]);
    return (
        <Panel eyebrow="" title={title} style={{ flex: 1, minWidth: 0 }}>
            <div className="col" style={{ maxHeight: 280, overflowY: 'auto' }}>
                {entries.length === 0 && (
                    <div style={{ padding: 14, color: 'var(--text-3)', fontSize: 12 }}>暂无</div>
                )}
                {entries.map(([k, v]) => <_ConfidenceRow key={k} k={k} v={v}/>)}
            </div>
        </Panel>
    );
}

function GroupedConfidencePanel({ title, data, categories, highlight }) {
    const groups = React.useMemo(() => {
        const out = {};
        for (const [k, v] of Object.entries(data || {})) {
            const cat = categories[k] || 'unknown';
            (out[cat] = out[cat] || []).push([k, v]);
        }
        for (const cat of Object.keys(out)) out[cat].sort((a, b) => b[1] - a[1]);
        return out;
    }, [data, categories]);
    const cats = Object.keys(groups);
    if (cats.length === 0) {
        return (
            <Panel eyebrow="" title={title} style={{ flex: 1, minWidth: 0 }}>
                <div style={{ padding: 14, color: 'var(--text-3)', fontSize: 12 }}>暂无</div>
            </Panel>
        );
    }
    const priority = ['general', 'character', 'copyright', 'artist', 'meta', 'quality', 'model', 'year', 'unknown'];
    cats.sort((a, b) => priority.indexOf(a) - priority.indexOf(b));
    const excludedSet = new Set(highlight || []);
    return (
        <Panel eyebrow="" title={title} style={{ flex: 1, minWidth: 0 }}>
            <div className="col" style={{ maxHeight: 380, overflowY: 'auto' }}>
                {cats.map(cat => {
                    const items = groups[cat];
                    const struck = excludedSet.has(cat);
                    return (
                        <div key={cat} className="col">
                            <div className="row" style={{
                                padding: '6px 14px', gap: 8, alignItems: 'center',
                                background: 'var(--surface-1)',
                                borderTop: '1px solid var(--line)',
                                position: 'sticky', top: 0, zIndex: 1,
                            }}>
                                <span className="eyebrow" style={{ color: struck ? 'var(--danger)' : 'var(--text-2)' }}>
                                    {CATEGORY_LABEL_ZH[cat] || cat}
                                </span>
                                <span className="mono" style={{ fontSize: 10, color: 'var(--text-4)' }}>{cat}</span>
                                <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--text-3)' }}>{items.length}</span>
                                {struck && <span className="pill danger" style={{ fontSize: 9 }}>已排除</span>}
                            </div>
                            {items.map(([k, v]) => <_ConfidenceRow key={k} k={k} v={v} struck={struck}/>)}
                        </div>
                    );
                })}
            </div>
        </Panel>
    );
}

window.TG = Object.assign(window.TG || {}, {
    ConfidencePanel, GroupedConfidencePanel, CATEGORY_LABEL_ZH,
});
