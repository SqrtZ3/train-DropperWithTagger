/* ============================================================
   Shell — sidebar, topbar, and panel chrome.
   ============================================================ */

const NAV = [
    { id: 'home',    label: '主页',     short: 'Home',   icon: I.Home,    hint: '会话与目录' },
    { id: 'crop',    label: '裁切',     short: 'Crop',   icon: I.Crop,    hint: '逐张精修' },
    { id: 'reels',   label: '批处理',   short: 'Export', icon: I.Layers,  hint: '一键全自动 / 队列导出' },
    { id: 'sim',     label: '查重',     short: 'Sim',    icon: I.Stack,   hint: '相似度聚类' },
    { id: 'video',   label: '抽帧',     short: 'Frames', icon: I.Video,   hint: '关键帧两步选取' },
    { id: 'tagger',  label: '反推',     short: 'Tagger', icon: I.Tag,     hint: '反推标签 / 联动自动标注' },
];

function CloseMenu({ session, onCloseSession, onShutdownServer }) {
    // 拆开「关闭当前会话」与「关闭服务」。
    // 关闭会话 = POST /api/reset，前端跳回主页，server 还活着 → 可以马上换一个目录开新会话。
    // 关闭服务 = POST /api/shutdown，os._exit，整个 WebUI 不可用。
    const [open, setOpen] = React.useState(false);
    const ref = React.useRef(null);
    React.useEffect(() => {
        if (!open) return;
        const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
        document.addEventListener('mousedown', onDoc);
        return () => document.removeEventListener('mousedown', onDoc);
    }, [open]);
    return (
        <div ref={ref} style={{ position: 'relative', margin: '0 auto' }}>
            <button className="btn-icon" onClick={() => setOpen(o => !o)}
                title={session?.is_initialized ? '会话进行中 · 点击操作' : '会话未启动'}>
                <I.Power size={16}/>
            </button>
            {open && (
                <div style={{
                    position: 'absolute', bottom: 'calc(100% + 8px)', left: '50%', transform: 'translateX(-50%)',
                    background: 'var(--surface-1)', border: '1px solid var(--line)',
                    borderRadius: 8, padding: 4, minWidth: 180,
                    boxShadow: '0 12px 32px rgba(0,0,0,.45)',
                    zIndex: 300, display: 'flex', flexDirection: 'column', gap: 2,
                }}>
                    <button onClick={() => { setOpen(false); onCloseSession?.(); }}
                        disabled={!session?.is_initialized}
                        style={menuItemStyle(false)}>
                        <I.Undo size={13}/>
                        <div className="col" style={{ alignItems: 'flex-start', gap: 1 }}>
                            <span style={{ fontSize: 12, fontWeight: 500 }}>关闭当前会话</span>
                            <span style={{ fontSize: 10, color: 'var(--text-3)' }}>清缓存回主页，server 不退</span>
                        </div>
                    </button>
                    <button onClick={() => { setOpen(false); onShutdownServer?.(); }}
                        style={menuItemStyle(true)}>
                        <I.Power size={13}/>
                        <div className="col" style={{ alignItems: 'flex-start', gap: 1 }}>
                            <span style={{ fontSize: 12, fontWeight: 500 }}>关闭服务</span>
                            <span style={{ fontSize: 10, color: 'var(--text-3)' }}>整个 WebUI 退出</span>
                        </div>
                    </button>
                </div>
            )}
        </div>
    );
}

function menuItemStyle(danger) {
    return {
        appearance: 'none', border: 'none', cursor: 'pointer',
        textAlign: 'left', padding: '8px 10px', borderRadius: 6,
        background: 'transparent', color: danger ? 'var(--danger)' : 'var(--text)',
        display: 'flex', alignItems: 'center', gap: 10,
        transition: 'background var(--t-fast) var(--ease)',
    };
}

function Sidebar({ tab, onTab, session, onCloseSession, onShutdown }) {
    const narrow = useIsNarrow();
    if (narrow) return <SidebarBottom tab={tab} onTab={onTab}/>;

    return (
        <nav style={{
            width: 72, flexShrink: 0,
            background: 'var(--surface-0)',
            borderRight: '1px solid var(--line)',
            display: 'flex', flexDirection: 'column',
            alignItems: 'stretch',
        }}>
            <div style={{
                height: 64, display: 'flex',
                alignItems: 'center', justifyContent: 'center',
                borderBottom: '1px solid var(--line)',
            }}>
                <div style={{
                    width: 36, height: 36,
                    background: 'var(--accent)',
                    color: 'var(--accent-ink)',
                    borderRadius: 8,
                    display: 'grid', placeItems: 'center',
                    fontFamily: 'var(--font-mono)',
                    fontWeight: 700,
                    fontSize: 15,
                    boxShadow: '0 0 0 1px rgba(255,255,255,.04) inset',
                }}>D</div>
            </div>

            <div className="col" style={{ padding: '10px 0', gap: 2, flex: 1 }}>
                {NAV.map(n => {
                    const active = tab === n.id;
                    const Icn = n.icon;
                    return (
                        <button
                            key={n.id}
                            onClick={() => onTab(n.id)}
                            title={`${n.label} · ${n.hint}`}
                            style={{
                                appearance: 'none', border: 'none',
                                background: active ? 'var(--surface-2)' : 'transparent',
                                color: active ? 'var(--text)' : 'var(--text-3)',
                                margin: '0 10px',
                                padding: '10px 0',
                                borderRadius: 8,
                                cursor: 'pointer',
                                display: 'flex', flexDirection: 'column',
                                alignItems: 'center', gap: 4,
                                position: 'relative',
                                transition: 'background var(--t-fast) var(--ease), color var(--t-fast) var(--ease)',
                            }}
                            onMouseEnter={e => { if (!active) e.currentTarget.style.color = 'var(--text)'; }}
                            onMouseLeave={e => { if (!active) e.currentTarget.style.color = 'var(--text-3)'; }}
                        >
                            {active && (
                                <span style={{
                                    position: 'absolute',
                                    left: -10, top: '50%', transform: 'translateY(-50%)',
                                    width: 3, height: 18,
                                    background: 'var(--accent)',
                                    borderRadius: '0 2px 2px 0',
                                }}/>
                            )}
                            <Icn size={18}/>
                            <span style={{ fontSize: 10, fontWeight: 500, letterSpacing: '.02em' }}>{n.short}</span>
                        </button>
                    );
                })}
            </div>

            <div className="col" style={{ padding: 10, gap: 6, borderTop: '1px solid var(--line)' }}>
                <div title={session?.is_initialized ? '会话进行中' : '未初始化'} style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    height: 26,
                }}>
                    <span style={{
                        width: 7, height: 7, borderRadius: '50%',
                        background: session?.is_initialized ? 'var(--accent)' : 'var(--text-4)',
                        boxShadow: session?.is_initialized ? '0 0 8px var(--accent)' : 'none',
                    }}/>
                </div>
                <CloseMenu session={session} onCloseSession={onCloseSession} onShutdownServer={onShutdown}/>
            </div>
        </nav>
    );
}

// Mobile bottom-nav variant. Sticks to the viewport bottom; <main.app-main>
// reserves 56px of bottom padding so screen content doesn't slide underneath.
// Shutdown / session-status indicator drop here — they live on Home instead.
function SidebarBottom({ tab, onTab }) {
    return (
        <nav style={{
            position: 'fixed', left: 0, right: 0, bottom: 0,
            height: 56, zIndex: 200,
            background: 'var(--surface-0)',
            borderTop: '1px solid var(--line)',
            display: 'flex', flexDirection: 'row',
            justifyContent: 'space-around', alignItems: 'stretch',
        }}>
            {NAV.map(n => {
                const active = tab === n.id;
                const Icn = n.icon;
                return (
                    <button
                        key={n.id}
                        onClick={() => onTab(n.id)}
                        title={n.label}
                        style={{
                            appearance: 'none', border: 'none',
                            background: 'transparent',
                            color: active ? 'var(--text)' : 'var(--text-3)',
                            flex: 1, padding: '6px 2px 4px',
                            cursor: 'pointer',
                            display: 'flex', flexDirection: 'column',
                            alignItems: 'center', justifyContent: 'center', gap: 3,
                            position: 'relative',
                            transition: 'color var(--t-fast) var(--ease)',
                        }}
                    >
                        {active && (
                            <span style={{
                                position: 'absolute',
                                top: 0, left: '25%', right: '25%',
                                height: 2.5,
                                background: 'var(--accent)',
                                borderRadius: '0 0 2px 2px',
                            }}/>
                        )}
                        <Icn size={18}/>
                        <span style={{ fontSize: 10, fontWeight: 500, letterSpacing: '.02em', lineHeight: 1 }}>{n.short}</span>
                    </button>
                );
            })}
        </nav>
    );
}

function TopBar({ title, subtitle, eyebrow, right, rightCompact }) {
    const narrow = useIsNarrow();
    return (
        <header style={{
            height: 64, flexShrink: 0,
            display: 'flex', alignItems: 'center',
            padding: narrow ? '0 14px' : '0 22px',
            borderBottom: '1px solid var(--line)',
            background: 'var(--surface-0)',
            gap: narrow ? 12 : 22,
        }}>
            <div className="col" style={{ flex: 1, minWidth: 0, gap: 2 }}>
                {!narrow && eyebrow && (
                    <div className="eyebrow" style={{ lineHeight: 1.3, fontSize: 11 }}>{eyebrow}</div>
                )}
                <div className="row gap-3" style={{ minWidth: 0 }}>
                    <h1 style={{
                        margin: 0,
                        fontSize: narrow ? 18 : 22,
                        fontWeight: 700,
                        letterSpacing: '-.015em',
                        color: 'var(--text)',
                        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                        lineHeight: 1.5,
                    }}>{title}</h1>
                    {!narrow && subtitle && (
                        <span style={{ color: 'var(--text-3)', fontSize: 13, whiteSpace: 'nowrap',
                                       lineHeight: 1.5 }}>
                            {subtitle}
                        </span>
                    )}
                </div>
            </div>
            <div className="row gap-2">{narrow ? (rightCompact ?? null) : right}</div>
        </header>
    );
}

function Panel({ title, eyebrow, action, children, style }) {
    return (
        <section className="col" style={{
            background: 'var(--surface-0)',
            border: '1px solid var(--line)',
            borderRadius: 10,
            overflow: 'hidden',
            ...style,
        }}>
            {(title || eyebrow || action) && (
                <header style={{
                    padding: '10px 14px',
                    borderBottom: '1px solid var(--line)',
                    display: 'flex', alignItems: 'center', gap: 8,
                }}>
                    <div className="col flex-1" style={{ gap: 1, minWidth: 0 }}>
                        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
                        {title && (
                            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                {title}
                            </div>
                        )}
                    </div>
                    {action}
                </header>
            )}
            {children}
        </section>
    );
}

function InspectorRow({ label, value, mono = true, accent = false }) {
    return (
        <div className="row" style={{ justifyContent: 'space-between', padding: '7px 14px', gap: 12 }}>
            <span style={{ color: 'var(--text-3)', fontSize: 12, letterSpacing: '.02em' }}>{label}</span>
            <span style={{
                color: accent ? 'var(--accent)' : 'var(--text)',
                fontSize: 12, fontWeight: 500,
                fontFamily: mono ? 'var(--font-mono)' : 'var(--font-ui)',
                fontVariantNumeric: 'tabular-nums',
                whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                maxWidth: '60%',
            }} title={value}>
                {value}
            </span>
        </div>
    );
}

// Shared bucket-resolution picker. Used in CropScreen sidebar and ReelsScreen.
// 协议层仍是 MP（target_mp 浮点），UI 改成以边长 px 为主，更直观。
function _pxToMp(px) {
    const v = Math.max(64, Math.round(parseFloat(px) || 0));
    return (v * v) / (1024 * 1024);
}
function _mpToPx(mp, step = 64) {
    const baseSide = Math.sqrt(Math.max(0.0025, parseFloat(mp) || 0) * 1024 * 1024);
    return Math.max(step, Math.round(baseSide / step) * step);
}

function BucketResolutionPicker({ targetMP, setTargetMP, step, setStep, compact = false }) {
    // 当前等效边长（按 step 对齐）
    const baseSide = _mpToPx(targetMP, step);
    // 预设方桶（边长 px）
    const presetsPx = [512, 768, 1024, 1280, 1536];
    // chip 命中判定：边长差 < step 视为命中
    const matchPx = (px) => Math.abs(baseSide - px) < Math.max(1, step / 2);
    // 自定义输入用本地 state 让用户可以临时输入半成品再失焦应用
    const [pxDraft, setPxDraft] = React.useState(String(baseSide));
    React.useEffect(() => { setPxDraft(String(baseSide)); }, [baseSide]);

    const commitPx = (v) => {
        const px = Math.max(64, parseInt(v) || 1024);
        const aligned = Math.round(px / step) * step;
        setTargetMP(_pxToMp(aligned));
        setPxDraft(String(aligned));
    };

    return (
        <div className="col" style={{ gap: compact ? 8 : 12, padding: compact ? '6px 14px 12px' : 14 }}>
            <div className="seg" style={{ width: '100%' }}>
                {presetsPx.map(px => (
                    <button key={px}
                        className={matchPx(px) ? 'on' : ''}
                        onClick={() => setTargetMP(_pxToMp(px))}
                        style={{ flex: 1 }}
                        title={`目标方桶 ${px}² (${((px * px) / (1024 * 1024)).toFixed(2)} MP)`}
                    >
                        {px}²
                    </button>
                ))}
            </div>
            <div className="row gap-2">
                <div className="col gap-1" style={{ flex: 1 }}>
                    <span className="eyebrow">自定义边长 PX</span>
                    <input className="input mono" type="number" min="64" step={step}
                        value={pxDraft}
                        onChange={e => setPxDraft(e.target.value)}
                        onBlur={e => commitPx(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter') e.target.blur(); }}
                    />
                </div>
                <div className="col gap-1" style={{ width: 88 }}>
                    <span className="eyebrow">步长 PX</span>
                    <input className="input mono" type="number" min="8" step="8"
                        value={step}
                        onChange={e => setStep(Math.max(8, parseInt(e.target.value) || 64))}
                    />
                </div>
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-3)', lineHeight: 1.5 }}>
                目标方桶 <span className="num" style={{ color: 'var(--text-2)' }}>{baseSide}²</span> px
                <span style={{ color: 'var(--text-4)' }}> ≈ {targetMP.toFixed(2)} MP</span>
                <span style={{ display: 'block' }}>等比缩到此面积后按宽高比落到最近的桶。常用 1024² 或 1536²。</span>
            </div>
        </div>
    );
}

Object.assign(window, { Sidebar, TopBar, Panel, InspectorRow, BucketResolutionPicker, NAV });
