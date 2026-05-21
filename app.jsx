/* ============================================================
   App — root component & routing.
   Polls /api/session every 8s for the session header, keeps
   target_mp / step in sync between server and client.
   ============================================================ */

// Tagger bundle 是手动 eval 加载的（见 index.html 末尾的 loader），
// 在 App 首次渲染时可能还没就绪。这个 wrapper 监听 'tagger-ready' 事件
// 在 TaggerScreen 写入 window 之后再渲染真组件。
function TaggerHost(props) {
    const [ready, setReady] = React.useState(() => typeof window.TaggerScreen === 'function');
    React.useEffect(() => {
        if (ready) return;
        const onReady = () => setReady(true);
        window.addEventListener('tagger-ready', onReady);
        // Poll fallback — in case the loader finished before this effect attached
        const t = setInterval(() => {
            if (typeof window.TaggerScreen === 'function') {
                setReady(true);
                clearInterval(t);
            }
        }, 200);
        return () => { window.removeEventListener('tagger-ready', onReady); clearInterval(t); };
    }, [ready]);
    const Impl = window.TaggerScreen;
    if (!ready || !Impl) {
        return (
            <div className="center" style={{ flex: 1, color: 'var(--text-3)', gap: 8 }}>
                <span className="spin"/> 正在加载 Tagger 模块…
            </div>
        );
    }
    return <Impl {...props}/>;
}

function App() {
    const [tab, setTab] = React.useState(localStorage.getItem('drop.tab') || 'home');
    const [session, setSession] = React.useState(null);
    const [targetMP, setTargetMP] = React.useState(parseFloat(localStorage.getItem('drop.targetMP')) || 1);
    const [step, setStep] = React.useState(parseInt(localStorage.getItem('drop.step')) || 64);
    // 跟踪客户端最近一次主动修改 targetMP/step 的时间戳。
    // 8s session 轮询发现 server 旧值就不要覆盖 client（避免选 2.25 几秒后跳回 1）。
    const localTouchedRef = React.useRef(0);

    React.useEffect(() => { localStorage.setItem('drop.tab', tab); }, [tab]);

    // targetMP / step 变化时：写 localStorage + POST /api/set_target 让 server 同步
    React.useEffect(() => {
        localStorage.setItem('drop.targetMP', String(targetMP));
        api.post('/api/set_target', { target_mp: targetMP }).catch(() => {});
    }, [targetMP]);
    React.useEffect(() => {
        localStorage.setItem('drop.step', String(step));
        api.post('/api/set_target', { step }).catch(() => {});
    }, [step]);

    const setTargetMPLocal = React.useCallback(v => {
        localTouchedRef.current = Date.now();
        setTargetMP(v);
    }, []);
    const setStepLocal = React.useCallback(v => {
        localTouchedRef.current = Date.now();
        setStep(v);
    }, []);

    const refresh = React.useCallback(() => {
        api.get('/api/session').then(s => {
            setSession(s);
            // 客户端最近 3s 内主动改过 → 不要被 session 旧快照覆盖
            const stale = Date.now() - localTouchedRef.current < 3000;
            if (!stale) {
                if (typeof s.target_mp === 'number') setTargetMP(s.target_mp);
                if (typeof s.step_size === 'number') setStep(s.step_size);
            }
        }).catch(() => setSession({ is_initialized: false }));
    }, []);

    React.useEffect(() => {
        refresh();
        const t = setInterval(refresh, 8000);
        return () => clearInterval(t);
        // eslint-disable-next-line
    }, []);

    async function init(input, output) {
        const r = await api.post('/api/init', {
            input_dir: input,
            output_dir: output || null,
            target_mp: targetMP,
            step: step,
        });
        await refresh();
        if (r.recovered) {
            ToastBus.emit(`从断点恢复，位置 ${(r.current_index ?? 0) + 1} · 队列 ${r.queue_len || 0}`, 'ok');
        } else {
            ToastBus.emit(`已加载 ${r.count || 0} 张图片`, 'ok');
        }
        setTab('crop');
        return r;
    }

    async function shutdown() {
        if (!confirm('关闭服务并清理缓存？\n（关闭后此页面将不可用）')) return;
        try { await api.post('/api/shutdown'); } catch {}
        ToastBus.emit('服务已关闭', 'ok');
        setSession({ is_initialized: false });
        setTab('home');
    }

    async function closeSession() {
        if (!session?.is_initialized) return;
        if (!confirm('关闭当前会话回主页？\n（清掉缓存中的 queue/items；磁盘文件保留；server 不退）')) return;
        try {
            await api.post('/api/reset');
            ToastBus.emit('已关闭当前会话', 'ok');
        } catch (e) {
            ToastBus.emit(e.message || '关闭失败', 'error');
        }
        setSession({ is_initialized: false });
        setTab('home');
        await refresh();
    }

    const props = {
        session, refresh,
        targetMP, setTargetMP: setTargetMPLocal,
        step, setStep: setStepLocal,
        onTab: setTab,
    };
    const screens = {
        home:   <HomeScreen   {...props} onInit={init}/>,
        crop:   <CropScreen   {...props}/>,
        reels:  <ReelsScreen  {...props}/>,
        sim:    <SimScreen    {...props}/>,
        video:  <VideoScreen  {...props}/>,
        tagger: <TaggerHost {...props}/>,
    };

    const tq = session?.tagger_queue;
    const tqBacklog = tq ? (tq.pending || 0) + (tq.processing || 0) : 0;

    const titles = {
        home:   { eyebrow: '主页',  title: 'Drop Studio',  sub: session?.is_initialized ? `· ${basename(session.image_folder || '')}` : '' },
        crop:   { eyebrow: 'Crop',  title: '裁切工作台',   sub: session?.total_count ? `${session.total_count} 张图片` : '' },
        reels:  { eyebrow: 'Export', title: '批处理 / 一键全自动', sub: session?.queue_len ? `队列 ${session.queue_len}` : '' },
        sim:    { eyebrow: 'Sim',   title: '相似度查重',   sub: '' },
        video:  { eyebrow: 'Frames', title: '视频抽帧',    sub: '' },
        tagger: { eyebrow: 'Tagger', title: '反推标签',    sub: tq?.done ? `已标 ${tq.done}` : '' },
    };
    const t = titles[tab] || titles.home;

    return (
        <>
            <Sidebar tab={tab} onTab={setTab} session={session}
                onCloseSession={closeSession} onShutdown={shutdown}/>
            <main className="col app-main" style={{ flex: 1, minWidth: 0 }}>
                <TopBar
                    eyebrow={t.eyebrow}
                    title={t.title}
                    subtitle={t.sub}
                    right={
                        <>
                            {session?.is_initialized && tab !== 'home' && (
                                <span className="pill accent">
                                    <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'currentColor' }}/>
                                    会话进行中
                                </span>
                            )}
                            {tqBacklog > 0 && tab !== 'tagger' && (
                                <button
                                    className="pill"
                                    onClick={() => setTab('tagger')}
                                    title={`tagger 队列：${tq.pending || 0} 待处理 · ${tq.processing || 0} 处理中 · ${tq.done || 0} 已完成`}
                                    style={{ cursor: 'pointer', border: '1px solid var(--accent-line)', color: 'var(--accent)' }}
                                >
                                    <I.Tag size={11}/> {tqBacklog} pending
                                </button>
                            )}
                            <span className="pill" title={`当前目标方桶 ≈ ${targetMP.toFixed(2)} MP · 步长 ${step}px`}>
                                <I.Ratio size={11}/> {Math.max(step, Math.round(Math.sqrt(targetMP * 1024 * 1024) / step) * step)}² · {step}px
                            </span>
                            <button className="btn btn-icon" onClick={refresh} title="刷新会话状态"><I.Refresh size={15}/></button>
                        </>
                    }
                    rightCompact={
                        <button className="btn btn-icon" onClick={refresh} title="刷新会话状态"><I.Refresh size={15}/></button>
                    }
                />
                {/* No .row class: it forces align-items:center which lets tall
                    screens overflow up into the TopBar. Need flex's default stretch. */}
                <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
                    {screens[tab]}
                </div>
            </main>
            <ToastHost/>
        </>
    );
}

const _root_el = document.getElementById('root');
ReactDOM.createRoot(_root_el).render(<App/>);
