/**
 * nd-delete.js — Adds a "Delete from disk" button to the Navidrome player.
 * Injected via nginx sub_filter. Talks to SoulSync API at /nd-del/.
 */
(function () {
    'use strict';
    if (window.__ndDeleteLoaded) return;
    window.__ndDeleteLoaded = true;

    const API_BASE = '/nd-del';
    const ND_API = '/rest';

    // Navidrome 0.63 stores the salted-token trio; older builds stored a
    // pre-salted password. Support both so this keeps working either way.
    function authParams(extra) {
        const params = new URLSearchParams(Object.assign(
            { v: '1.16.1', c: 'nd-delete', f: 'json' }, extra || {}));
        const token = localStorage.getItem('subsonic-token');
        const salt = localStorage.getItem('subsonic-salt');
        const user = localStorage.getItem('username')
            || localStorage.getItem('nd-username') || 'admin';
        params.set('u', user);
        if (token && salt) {
            params.set('t', token);
            params.set('s', salt);
        } else {
            params.set('p', localStorage.getItem('nd-salted-pass') || '');
        }
        return params;
    }

    // ---- Confirmation dialog (styled like Navidrome, not a browser popup) ----
    function ndConfirm({ title, lines, confirmText }) {
        return new Promise(resolve => {
            const overlay = document.createElement('div');
            overlay.className = 'nd-del-overlay';

            const paper = document.createElement('div');
            paper.className = 'nd-del-dialog';

            const h = document.createElement('h2');
            h.className = 'nd-del-dialog-title';
            h.textContent = title;
            paper.appendChild(h);

            const body = document.createElement('div');
            body.className = 'nd-del-dialog-body';
            lines.forEach(line => {
                const row = document.createElement('div');
                row.className = line.muted ? 'nd-del-line nd-del-line-muted' : 'nd-del-line';
                row.textContent = line.text;   // textContent: titles are arbitrary
                body.appendChild(row);
            });
            paper.appendChild(body);

            const actions = document.createElement('div');
            actions.className = 'nd-del-dialog-actions';
            const cancel = document.createElement('button');
            cancel.className = 'nd-del-btn';
            cancel.textContent = 'Cancel';
            const ok = document.createElement('button');
            ok.className = 'nd-del-btn nd-del-btn-danger';
            ok.textContent = confirmText || 'Delete';
            actions.appendChild(cancel);
            actions.appendChild(ok);
            paper.appendChild(actions);

            overlay.appendChild(paper);
            document.body.appendChild(overlay);

            const close = (answer) => {
                document.removeEventListener('keydown', onKey);
                overlay.remove();
                resolve(answer);
            };
            const onKey = (e) => {
                if (e.key === 'Escape') close(false);
                if (e.key === 'Enter') close(true);
            };
            cancel.onclick = () => close(false);
            ok.onclick = () => close(true);
            overlay.onclick = (e) => { if (e.target === overlay) close(false); };
            document.addEventListener('keydown', onKey);
            ok.focus();
        });
    }

    function ndToast(message) {
        const el = document.createElement('div');
        el.className = 'nd-del-toast';
        el.textContent = message;
        document.body.appendChild(el);
        setTimeout(() => { el.classList.add('nd-del-toast-out'); }, 2600);
        setTimeout(() => { el.remove(); }, 3100);
    }

    // Navidrome's own API returns the REAL file path. The Subsonic `path` is
    // synthesised from tags (artist/album/NN - title), so deleting by it fails
    // with "File not found in library" whenever the file does not happen to sit
    // where the tags imply — which is most of the library.
    async function fetchRealSong(songId) {
        const jwt = localStorage.getItem('token');
        if (jwt) {
            try {
                const resp = await fetch(`/api/song/${encodeURIComponent(songId)}`, {
                    headers: { 'x-nd-authorization': 'Bearer ' + jwt }
                });
                if (resp.ok) {
                    const song = await resp.json();
                    if (song && song.path) return song;
                }
            } catch (e) { /* fall through to Subsonic */ }
        }
        return await getSongInfo(songId);
    }

    async function getSongInfo(songId) {
        const params = authParams({ id: songId });
        const resp = await fetch(`${ND_API}/getSong?${params}`);
        const data = await resp.json();
        return data['subsonic-response']?.song || null;
    }

    async function deleteSong(songId) {
        const song = await fetchRealSong(songId);
        if (!song) {
            alert('Could not find that track in Navidrome — it may have been removed already, or the session expired. Try reloading the page.');
            return;
        }
        const okToDelete = await ndConfirm({
            title: 'Delete from disk?',
            lines: [
                { text: song.title || '' },
                { text: (song.artist || '') + (song.album ? ' — ' + song.album : ''), muted: true },
                { text: song.path || '', muted: true },
                { text: 'It will also be blacklisted so automation never re-downloads it.', muted: true },
            ],
            confirmText: 'Delete',
        });
        if (!okToDelete) return;
        try {
            const resp = await fetch(`${API_BASE}/delete`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                // artist/title travel with the path: the blacklist matches by
                // name, since Navidrome knows no provider id for the track.
                body: JSON.stringify({ path: song.path, artist: song.artist, title: song.title })
            });
            const result = await resp.json();
            if (result.success) {
                triggerRescan();
                ndToast('Deleted — blacklisted so it will not come back.');
            } else {
                alert('Delete failed: ' + (result.error || 'unknown error'));
            }
        } catch (e) { alert('Delete failed: ' + e.message); }
    }

    function triggerRescan() {
        fetch(`${ND_API}/startScan?${authParams()}`).catch(() => {});
    }

    // ---- CSS ----
    const css = document.createElement('style');
    css.textContent = `
        .nd-del-player {
            cursor: pointer; color: #f44336; background: transparent !important; border: none !important;
            padding: 6px; display: inline-flex; align-items: center; opacity: 0.6;
            transition: opacity 0.15s; font-size: 1.1rem;
        }
        .nd-del-player:hover { opacity: 1; }
        .nd-del-player svg { width: 20px; height: 20px; pointer-events: none; }
        /* Fallback placement: a player markup we do not recognise (theme or a
           new Navidrome layout) still gets a visible control, floating above
           the player bar instead of vanishing. */
        .nd-del-floating {
            position: fixed; right: 24px; bottom: 96px; z-index: 15000;
            background: rgba(48, 48, 48, 0.95) !important; border-radius: 50%;
            padding: 12px; box-shadow: 0 2px 10px rgba(0, 0, 0, 0.5); opacity: 0.85;
        }
        .nd-del-floating:hover { opacity: 1; }
        .nd-del-floating svg { width: 22px; height: 22px; }
        .nd-del-overlay {
            position: fixed; inset: 0; z-index: 20000; display: flex;
            align-items: center; justify-content: center;
            background: rgba(0,0,0,0.6);
        }
        .nd-del-dialog {
            background: #303030; color: #fff; border-radius: 4px;
            box-shadow: 0 11px 15px -7px rgba(0,0,0,.2), 0 24px 38px 3px rgba(0,0,0,.14);
            min-width: 320px; max-width: min(560px, 92vw); padding: 0 0 8px;
            font-family: Roboto, Helvetica, Arial, sans-serif;
        }
        .nd-del-dialog-title {
            margin: 0; padding: 16px 24px 8px; font-size: 1.25rem; font-weight: 500;
        }
        .nd-del-dialog-body { padding: 0 24px 12px; }
        .nd-del-line {
            font-size: 1rem; margin: 2px 0; word-break: break-word;
        }
        .nd-del-line-muted { font-size: .82rem; opacity: .65; }
        .nd-del-dialog-actions {
            display: flex; justify-content: flex-end; gap: 8px; padding: 8px 16px 8px;
        }
        .nd-del-btn {
            background: transparent; border: none; border-radius: 4px; cursor: pointer;
            color: #fff; font-size: .875rem; font-weight: 500; letter-spacing: .02em;
            text-transform: uppercase; padding: 8px 12px;
        }
        .nd-del-btn:hover { background: rgba(255,255,255,0.08); }
        .nd-del-btn-danger { color: #f44336; }
        .nd-del-btn-danger:hover { background: rgba(244,67,54,0.12); }
        .nd-del-toast {
            position: fixed; left: 50%; bottom: 96px; transform: translateX(-50%);
            z-index: 20001; background: #323232; color: #fff; border-radius: 4px;
            padding: 12px 18px; font-size: .875rem; max-width: 92vw;
            font-family: Roboto, Helvetica, Arial, sans-serif;
            box-shadow: 0 3px 5px -1px rgba(0,0,0,.2), 0 6px 10px 0 rgba(0,0,0,.14);
            opacity: 1; transition: opacity .4s ease;
        }
        .nd-del-toast-out { opacity: 0; }
    `;
    document.head.appendChild(css);

    const TRASH = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>';

    // ---- Song ID extraction ----
    function getSongIdFromFiber(el) {
        const rk = Object.keys(el).find(k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'));
        if (!rk) return null;
        let fiber = el[rk];
        for (let i = 0; i < 30 && fiber; i++) {
            const p = fiber.memoizedProps;
            if (p) {
                if (p.record && p.record.id) return p.record.id;
                if (p.audioId) return p.audioId;
                if (p.songId) return p.songId;
                if (p.id && typeof p.id === 'string' && p.id.length > 8) return p.id;
            }
            fiber = fiber.return;
        }
        return null;
    }

    function currentPlayingId() {
        const audio = document.querySelector('.music-player-audio, audio');
        if (audio) {
            const src = audio.getAttribute('src') || audio.currentSrc || '';
            const m = src.match(/[?&](?:mediaId|id)=([^&]+)/);
            if (m) return decodeURIComponent(m[1]);
        }
        const playerEl = document.querySelector('.music-player, [class*="music-player"], [class*="NDAudioPlayer"]');
        return playerEl ? getSongIdFromFiber(playerEl) : null;
    }

    // ---- Player delete button ----
    // The row that actually holds the transport controls. Navidrome 0.64 renders
    // `.music-player-panel` with a `.panel-content` body (0.6x used
    // `.player-content`), and the mini bar only has `.music-player-controller`.
    // Ask for each in turn and take the first that contains real controls.
    function findControlRow() {
        const panel = document.querySelector('.music-player-panel')
            || document.querySelector('[class*="music-player"]');
        if (!panel) return null;
        const candidates = [
            panel.querySelector('.player-content'),
            panel.querySelector('.panel-content'),
            panel.querySelector('section.audio-main'),
            panel.querySelector('.music-player-controller'),
        ].filter(Boolean);
        for (const row of candidates) {
            if (row.querySelector('button, .group, svg')) return row;
        }
        // Nothing recognised: any element that already holds several buttons.
        const rows = Array.from(panel.querySelectorAll('div, section'))
            .filter(el => el.querySelectorAll('button').length >= 3);
        return rows.length ? rows[rows.length - 1] : panel;
    }

    function buildDeleteButton(floating) {
        const btn = document.createElement('span');
        btn.className = floating ? 'group nd-del-player nd-del-floating' : 'group nd-del-player';
        btn.title = 'Delete current song from disk';
        btn.innerHTML = TRASH;
        btn.onclick = (e) => {
            e.preventDefault(); e.stopPropagation();
            const playingNow = currentPlayingId();
            if (!playingNow) { ndToast('Nothing is playing right now.'); return; }
            deleteSong(playingNow);
        };
        return btn;
    }

    function addDeleteToPlayer() {
        if (document.querySelector('.nd-del-player')) return;

        const row = findControlRow();
        if (row) {
            const btn = buildDeleteButton(false);
            // Sit next to the lyrics toggle when there is one, otherwise after the
            // last control in the row, so it lines up with its neighbours.
            const lyric = row.querySelector('.lyric-btn, [title*="yric"], [title*="екст"]');
            if (lyric && lyric.parentElement) {
                lyric.insertAdjacentElement('afterend', btn);
            } else {
                const groups = row.querySelectorAll('.group');
                const last = groups.length ? groups[groups.length - 1] : null;
                if (last) last.insertAdjacentElement('afterend', btn);
                else row.appendChild(btn);
            }
            return;
        }

        // Last resort: the player markup is unrecognisable (a theme or a new
        // Navidrome layout). Float the control above the player bar so the
        // feature never silently disappears.
        const bar = document.querySelector('.music-player-panel, [class*="music-player"]');
        if (!bar) return;
        document.body.appendChild(buildDeleteButton(true));
    }

    // ---- Main scan ----
    function scan() {
        addDeleteToPlayer();
    }

    // ---- MutationObserver ----
    // The script is injected into </head>, so it runs BEFORE <body> exists.
    // Observing document.body here threw "parameter 1 is not of type 'Node'",
    // which aborted everything below it — observer, DOMContentLoaded hook and
    // the first scan — so no button ever appeared anywhere. Start only once
    // the document has a body.
    let debounce = null;
    function startObserving() {
        new MutationObserver(() => {
            if (debounce) return;
            debounce = setTimeout(() => { debounce = null; scan(); }, 200);
        }).observe(document.body, { childList: true, subtree: true });
        scan();
    }

    // ---- Route change detection (SPA) ----
    let lastUrl = location.href;
    setInterval(() => {
        if (location.href !== lastUrl) {
            lastUrl = location.href;
            setTimeout(scan, 500);
        }
    }, 300);

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', startObserving);
    } else {
        startObserving();
    }
    console.log('[nd-delete] loaded');
})();
