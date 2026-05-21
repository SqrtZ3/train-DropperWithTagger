// Tiny API client + helpers shared across screens.
// Backend stays the same; we just wrap fetch.

const api = {
    async get(path) {
        const r = await fetch(path);
        if (!r.ok) {
            let msg = `${r.status}`;
            try { const j = await r.json(); if (j.error) msg = j.error; } catch {}
            throw new Error(msg);
        }
        return r.json();
    },
    async post(path, body) {
        const r = await fetch(path, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: body == null ? null : JSON.stringify(body),
        });
        if (!r.ok) {
            let msg = `${r.status}`;
            try { const j = await r.json(); if (j.error) msg = j.error; } catch {}
            throw new Error(msg);
        }
        return r.json();
    },
};

// Toast singleton used across the app.
const ToastBus = (() => {
    const listeners = new Set();
    return {
        emit(msg, kind = 'ok', ttl = 2400) { listeners.forEach(fn => fn({ msg, kind, ttl, id: Math.random() })); },
        on(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    };
})();

function ToastHost() {
    const [t, setT] = React.useState(null);
    React.useEffect(() => ToastBus.on(({ msg, kind, ttl, id }) => {
        setT({ msg, kind, id });
        clearTimeout(ToastHost._h);
        ToastHost._h = setTimeout(() => setT(s => s && s.id === id ? null : s), ttl);
    }), []);
    return (
        <div className={`toast ${t ? 'show' : ''} ${t?.kind || ''}`}>
            {t?.kind === 'error' ? <I.Info size={14}/> : t?.kind === 'warn' ? <I.Info size={14}/> : <I.Check size={14}/>}
            <span>{t?.msg || ''}</span>
        </div>
    );
}

function fmtDuration(sec) {
    if (sec == null || isNaN(sec)) return '—';
    sec = Math.max(0, Math.round(sec));
    if (sec < 60) return `${sec}s`;
    const m = Math.floor(sec / 60), s = sec % 60;
    return `${m}m ${s}s`;
}

function fmtPct(n) { return `${(n * 100).toFixed(0)}%`; }

function basename(p) {
    if (!p) return '';
    return p.split(/[\\/]/).pop() || p;
}

// 把从资源管理器复制来的路径处理干净：
//   "D:\Datasets\foo"    → D:\Datasets\foo   （去首尾引号）
//   D:/Datasets/foo/     → D:\Datasets\foo    （末尾斜杠丢掉，正斜杠转反斜杠）
//   file:///D:/a%20b     → D:\a b             （file:// + URL 解码）
// Windows 用户用得多，但 macOS/Linux 也兼容。
function cleanPath(s) {
    if (!s) return '';
    let v = String(s).trim();
    // file:// URI
    if (/^file:\/\/\//i.test(v)) {
        try { v = decodeURIComponent(v.replace(/^file:\/\/\//i, '')); } catch {}
    }
    // 首尾引号（单/双/中文「」"」）
    v = v.replace(/^["'「『]+|["'」』]+$/g, '');
    // 末尾分隔符
    v = v.replace(/[\\/]+$/, '');
    return v;
}

// 给 <input> 装一个 onPaste：粘贴时自动 cleanPath。setter 直接接 useState 的 setter。
function pasteCleanedPath(setter) {
    return (e) => {
        const t = (e.clipboardData || window.clipboardData)?.getData('text');
        if (!t) return;
        const cleaned = cleanPath(t);
        if (cleaned !== t) {
            e.preventDefault();
            setter(cleaned);
        }
    };
}

// Intersection-observer-driven lazy image with retry-on-error.
// objectFit:'cover' (default) crops the image to fill a square container — fine
// for filmstrip-style pickers. Pass 'contain' for screens like Sim where the
// user is comparing full image content and any crop hides information.
// onLoadError: fired after the retry budget is exhausted. Useful for parents
// to detect e.g. a stale server-side snapshot and trigger cleanup.
function LazyImg({ src, alt = '', className = '', style, onLoad, onClick, onLoadError, draggable = false, maxRetries = 2, objectFit = 'cover' }) {
    const [shown, setShown] = React.useState(false);
    const [loaded, setLoaded] = React.useState(false);
    const [attempt, setAttempt] = React.useState(0);
    const [failed, setFailed] = React.useState(false);
    const ref = React.useRef(null);

    React.useEffect(() => {
        setLoaded(false); setAttempt(0); setFailed(false);
    }, [src]);

    // Notify parent once after retries are exhausted.
    React.useEffect(() => {
        if (failed && onLoadError) onLoadError();
    }, [failed]);

    React.useEffect(() => {
        if (!ref.current) return;
        const el = ref.current;
        const io = new IntersectionObserver(entries => {
            entries.forEach(e => { if (e.isIntersecting) { setShown(true); io.disconnect(); } });
        }, { rootMargin: '300px' });
        io.observe(el);
        return () => io.disconnect();
    }, []);

    const onErr = () => {
        if (attempt >= maxRetries) { setFailed(true); return; }
        setTimeout(() => setAttempt(a => a + 1), 300 * Math.pow(2, attempt));
    };

    const finalSrc = attempt > 0
        ? `${src}${src.includes('?') ? '&' : '?'}_r=${attempt}_${Date.now()}`
        : src;

    return (
        <div ref={ref} className={className} style={{
            background: 'var(--surface-1)',
            position: 'relative',
            overflow: 'hidden',
            ...style,
        }} onClick={onClick}>
            {shown && !failed && (
                <img
                    src={finalSrc}
                    alt={alt}
                    draggable={draggable}
                    onLoad={() => { setLoaded(true); onLoad?.(); }}
                    onError={onErr}
                    style={{
                        width: '100%', height: '100%', objectFit,
                        opacity: loaded ? 1 : 0,
                        transition: 'opacity 200ms var(--ease)',
                        display: 'block',
                    }}
                />
            )}
            {failed && (
                <div style={{
                    position: 'absolute', inset: 0,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    color: 'var(--text-4)', fontSize: 11, fontFamily: 'var(--font-mono)',
                }}>—</div>
            )}
        </div>
    );
}

// Hook: track key without modifiers being held / pressed.
function useKey(key, fn, deps = []) {
    React.useEffect(() => {
        function onKey(e) {
            const t = e.target;
            if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
            if (typeof key === 'function') {
                if (key(e)) fn(e);
            } else if (e.key === key) {
                fn(e);
            }
        }
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, deps);
}

// Hook: viewport-driven layout switch. Single breakpoint at 768px — matches the
// mobile rules in tokens.css. Components branch on this only when the React
// subtree must change shape (Sidebar rail → bottom bar, etc.); pure styling
// should stay in @media in tokens.css.
const _narrowMQL = typeof window !== 'undefined' && window.matchMedia
    ? window.matchMedia('(max-width: 767px)')
    : null;
function useIsNarrow() {
    const [narrow, setNarrow] = React.useState(() => !!(_narrowMQL && _narrowMQL.matches));
    React.useEffect(() => {
        if (!_narrowMQL) return;
        const on = (e) => setNarrow(e.matches);
        _narrowMQL.addEventListener('change', on);
        return () => _narrowMQL.removeEventListener('change', on);
    }, []);
    return narrow;
}

// Calculate the best bucket (w,h) for a given source dimension at target MP + step.
// Returns the bucket with closest aspect ratio.
function calcBucket(w, h, mp = 1, step = 64) {
    if (!w || !h) return { w: step, h: step };
    const targetArea = mp * 1024 * 1024;
    const root = Math.floor(Math.sqrt(targetArea));
    const base = Math.round(root / step) * step;
    const buckets = [[base, base]];
    for (let i = 1; i < 64; i++) {
        const bw = base + i * step;
        const bh = Math.round((targetArea / bw) / step) * step;
        if (bw * bh > 0) {
            buckets.push([bw, bh]);
            buckets.push([bh, bw]);
        }
        if (bw / Math.max(1, bh) > 4.5) break;
    }
    const ar = w / h;
    let best = buckets[0];
    let bestDiff = Math.abs(best[0]/best[1] - ar);
    for (let i = 1; i < buckets.length; i++) {
        const d = Math.abs(buckets[i][0]/buckets[i][1] - ar);
        if (d < bestDiff) { bestDiff = d; best = buckets[i]; }
    }
    return { w: best[0], h: best[1] };
}

Object.assign(window, {
    api, ToastBus, ToastHost, LazyImg, fmtDuration, fmtPct, basename,
    cleanPath, pasteCleanedPath,
    useKey, useIsNarrow, calcBucket,
});
