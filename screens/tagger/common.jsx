/* screens/tagger/common.jsx — 共用小组件 + 默认设置 + 持久化加载
 *
 * 暴露在 window.TG 命名空间下，避免 Slider / Check 这类通用名污染全局。
 *   TG.Slider, TG.Check, TG.Stat, TG.CollapsiblePanel
 *   TG.DEFAULTS, TG.loadStoredSettings()
 */

// 注意：原 tagger_impl.jsx 把这些字符里的 < > 拆成 String.fromCharCode 是为了规避
// 旧的 HTML 内联 <script type="text/babel"> 解析时把 `<o>` 当成 JSX 起始标签。
// 现在是外部 .jsx 文件、由 Babel 单独 transform，普通字符串字面量就能写。
const TAG_DEFAULT_UNDERSCORE_EXCLUDES = (
    "0_0, (o)_(o), +_+, +_-, ._., <o>_<o>, <|>_<|>, =_=, >_<, " +
    "3_3, 6_9, >_o, @_@, ^_^, o_o, u_u, x_x, |_|, ||_||"
);

const TAG_DEFAULT_SETTINGS = {
    // 不再硬写 hf:Camais03/camie-tagger-v2 —— 首次加载时由 sidebar 自动落到「首个已下载」。
    // 这里留空，让前端启动时按 /api/tagger/models 的 downloaded 标志决定。
    model: '',
    threshold: 0.35,
    additional_tags: '',
    exclude_tags: '',
    // 默认排除：画师 / 元数据 / 质量 / 年份 / 模型 —— LoRA 训练时这些通常不需要写进 .txt
    excluded_categories: 'artist, meta, quality, model, year',
    replace_underscore: true,
    replace_underscore_excludes: TAG_DEFAULT_UNDERSCORE_EXCLUDES,
    escape_tag: false,
    sort_alphabetical: false,
    core_tags: '',
    core_tags_weight: 1.0,
    add_confident_as_weight: false,
    use_weight_mapping: false,
    weight_mapping_exponent: 1.0,
    weight_min: 0.9,
    weight_max: 1.1,
    max_tags: 0,
    token_limit: 75,
    tag_precision: 2,
};

function _loadTaggerStoredSettings() {
    try {
        const raw = localStorage.getItem('drop.taggerSettings');
        if (!raw) return { ...TAG_DEFAULT_SETTINGS };
        const parsed = JSON.parse(raw);
        return { ...TAG_DEFAULT_SETTINGS, ...parsed };
    } catch {
        return { ...TAG_DEFAULT_SETTINGS };
    }
}

/* ============================================================
   通用控件
   ============================================================ */
function Slider({ label, min, max, step, value, onChange }) {
    return (
        <div className="col gap-1" style={{ flex: 1 }}>
            <div className="row" style={{ justifyContent: 'space-between' }}>
                <span className="eyebrow">{label}</span>
                <span className="mono" style={{ fontSize: 11, color: 'var(--accent)' }}>
                    {Number(value).toFixed(step < 1 ? 2 : 0)}
                </span>
            </div>
            <input type="range" min={min} max={max} step={step} value={value}
                onChange={e => onChange(parseFloat(e.target.value))}
                style={{ width: '100%' }}/>
        </div>
    );
}

function Check({ label, checked, onChange }) {
    return (
        <label className="row gap-2" style={{ cursor: 'pointer', userSelect: 'none' }}>
            <input type="checkbox" className="check" checked={!!checked}
                onChange={e => onChange(e.target.checked)}/>
            <span style={{ fontSize: 13, color: 'var(--text-2)' }}>{label}</span>
        </label>
    );
}

function Stat({ label, value, accent, danger }) {
    const color = danger ? 'var(--danger)' : (accent ? 'var(--accent)' : 'var(--text)');
    return (
        <div className="col" style={{ alignItems: 'center', gap: 2, flex: 1 }}>
            <span style={{ fontSize: 28, fontWeight: 600, color, fontVariantNumeric: 'tabular-nums' }}>
                {value}
            </span>
            <span className="eyebrow">{label}</span>
        </div>
    );
}

/* 局部可折叠 Panel：shell.jsx 暴露的 Panel 只支持「常开」模式，
   tagger 侧栏需要默认收起的 LoRA 优化 / 预设面板 */
function CollapsiblePanel({ eyebrow, title, action, children, defaultOpen = true, style }) {
    const [open, setOpen] = React.useState(defaultOpen);
    return (
        <section className="col" style={{
            background: 'var(--surface-0)',
            borderBottom: '1px solid var(--line)',
            ...(style || {}),
        }}>
            <header
                onClick={() => setOpen(o => !o)}
                style={{
                    padding: '10px 14px',
                    display: 'flex', alignItems: 'center', gap: 8,
                    cursor: 'pointer', userSelect: 'none',
                    borderBottom: open ? '1px solid var(--line)' : 'none',
                }}
            >
                <div className="col flex-1" style={{ gap: 1, minWidth: 0 }}>
                    {eyebrow && <div className="eyebrow">{eyebrow}</div>}
                    {title && (
                        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>{title}</div>
                    )}
                </div>
                {action}
                <I.ArrowDown size={12} style={{
                    color: 'var(--text-3)',
                    transform: open ? 'rotate(0deg)' : 'rotate(-90deg)',
                    transition: 'transform 140ms var(--ease)',
                }}/>
            </header>
            {open && children}
        </section>
    );
}

window.TG = Object.assign(window.TG || {}, {
    Slider, Check, Stat, CollapsiblePanel,
    DEFAULTS: TAG_DEFAULT_SETTINGS,
    loadStoredSettings: _loadTaggerStoredSettings,
});
