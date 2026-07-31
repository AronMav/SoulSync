/**
 * nd-delete.js — Adds "Delete from disk" buttons to Navidrome UI.
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

    async function getSongInfo(songId) {
        const params = authParams({ id: songId });
        const resp = await fetch(`${ND_API}/getSong?${params}`);
        const data = await resp.json();
        return data['subsonic-response']?.song || null;
    }

    async function deleteSong(songId) {
        const song = await getSongInfo(songId);
        if (!song) { alert('Could not resolve song. Are you logged in?'); return; }
        if (!confirm(`Delete from disk?\n\n"${song.title}"\nby ${song.artist}\nfrom "${song.album}"\n\nFile: ${song.path}\n\nIt will also be blacklisted so automation never re-downloads it.`)) return;
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
                setTimeout(() => location.reload(), 1500);
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
        .nd-del-icon {
            cursor: pointer; color: #f44336; background: transparent; border: none;
            padding: 4px; display: inline-flex; align-items: center; opacity: 0.5;
            transition: opacity 0.15s; vertical-align: middle;
        }
        .nd-del-icon:hover { opacity: 1; }
        .nd-del-icon svg { width: 16px; height: 16px; pointer-events: none; }
        .nd-del-player {
            cursor: pointer; color: #f44336; background: transparent !important; border: none !important;
            padding: 6px; display: inline-flex; align-items: center; opacity: 0.6;
            transition: opacity 0.15s; font-size: 1.1rem;
        }
        .nd-del-player:hover { opacity: 1; }
        .nd-del-player svg { width: 20px; height: 20px; pointer-events: none; }
        .nd-del-album {
            cursor: pointer; color: #f44336; background: rgba(244,67,54,0.1);
            border: 1px solid rgba(244,67,54,0.3); border-radius: 4px;
            padding: 6px 14px; font-size: 0.8rem; font-weight: 500;
            display: inline-flex; align-items: center; gap: 6px;
            transition: background 0.15s;
        }
        .nd-del-album:hover { background: rgba(244,67,54,0.2); }
        .nd-del-album svg { width: 16px; height: 16px; }
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

    function getSongId(row) {
        const d = row.querySelector('[data-id]');
        if (d) return d.getAttribute('data-id');
        const links = row.querySelectorAll('a[href*="stream"], a[href*="download"]');
        for (const a of links) {
            const m = (a.getAttribute('href') || '').match(/[?&]id=([^&]+)/);
            if (m) return m[1];
        }
        return getSongIdFromFiber(row);
    }

    // ---- Player delete button ----
    function addDeleteToPlayer() {
        // 0.63 renders the react-jinke bar as `.music-player-panel`; the older
        // `.music-player-controller` class is gone, which left the button with
        // nowhere to attach.
        // Priority matters and querySelector does NOT honour it: given a list it
        // returns the first match in DOCUMENT order, so passing the panel and
        // its inner controls row together lands on the panel and the button is
        // appended past the bottom edge, off-screen. Ask in order instead.
        const player = document.querySelector('.music-player-panel .player-content')
            || document.querySelector('.music-player-panel .music-player-controller')
            || document.querySelector('.music-player-controller, [class*="music-player-controller"], .controller-title')
            || document.querySelector('.music-player-panel');
        if (!player || player.querySelector('.nd-del-player')) return;

        // Get current song ID from the audio element's src. 0.63 streams carry
        // `mediaId=<id>`; older builds used `id=<id>`. Accept either.
        const audio = document.querySelector('.music-player-audio, audio');
        let songId = null;
        if (audio) {
            const src = audio.getAttribute('src') || audio.currentSrc || '';
            const m = src.match(/[?&](?:mediaId|id)=([^&]+)/);
            if (m) songId = decodeURIComponent(m[1]);
        }
        if (!songId) {
            // Walk the player DOM to find the song id via React fiber
            const playerEl = document.querySelector('.music-player, [class*="music-player"], [class*="NDAudioPlayer"]');
            if (playerEl) songId = getSongIdFromFiber(playerEl);
        }
        if (!songId) return;

        // The row is a flat list of `span.group` slots (volume, loop, lyrics,
        // queue, destroy), so match that shape instead of dropping a bare
        // <button> in — otherwise it does not line up with its neighbours.
        const btn = document.createElement('span');
        btn.className = 'group nd-del-player';
        btn.title = 'Delete current song from disk';
        btn.innerHTML = TRASH;
        btn.onclick = (e) => { e.preventDefault(); e.stopPropagation(); deleteSong(songId); };

        // Sit immediately to the right of the lyrics toggle; fall back to the
        // end of the row if that control is absent (older player, or lyrics
        // disabled in settings).
        const lyric = player.querySelector('.lyric-btn, [title*="yric"], [title*="екст"]');
        if (lyric && lyric.parentElement === player) {
            lyric.insertAdjacentElement('afterend', btn);
        } else {
            player.appendChild(btn);
        }
    }

    // The row's React fiber carries the whole record — id, title, artist and
    // path — so a row delete needs no API round-trip at all.
    function getRecordFromFiber(el) {
        const rk = Object.keys(el).find(k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'));
        if (!rk) return null;
        let fiber = el[rk];
        for (let i = 0; i < 30 && fiber; i++) {
            const p = fiber.memoizedProps;
            if (p && p.record && p.record.id && p.record.path) return p.record;
            fiber = fiber.return;
        }
        return null;
    }

    async function deleteRecord(rec) {
        if (!confirm(`Delete from disk?\n\n"${rec.title}"\nby ${rec.artist}\n\nFile: ${rec.path}\n\nIt will also be blacklisted so automation never re-downloads it.`)) return;
        try {
            const resp = await fetch(`${API_BASE}/delete`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: rec.path, artist: rec.artist, title: rec.title })
            });
            const result = await resp.json();
            if (result.success) {
                triggerRescan();
                setTimeout(() => location.reload(), 1500);
            } else {
                alert('Delete failed: ' + (result.error || 'unknown error'));
            }
        } catch (e) { alert('Delete failed: ' + e.message); }
    }

    // ---- Song row delete button ----
    function addDeleteToRow(row) {
        if (row.querySelector('.nd-del-icon')) return;
        const rec = getRecordFromFiber(row);
        const songId = rec ? rec.id : getSongId(row);
        if (!songId) return;

        let container = row.querySelector('[class*="contextMenu"]');
        if (!container) {
            const cells = row.querySelectorAll('td');
            if (cells.length > 0) container = cells[cells.length - 1];
        }
        if (!container) return;

        const btn = document.createElement('button');
        btn.className = 'nd-del-icon';
        btn.title = 'Delete from disk';
        btn.innerHTML = TRASH;
        btn.onclick = (e) => {
            e.preventDefault(); e.stopPropagation();
            if (rec && rec.path) deleteRecord(rec); else deleteSong(songId);
        };
        container.appendChild(btn);
    }

    // ---- Album page delete button ----
    function addDeleteToAlbumPage() {
        // Navidrome routes through the hash (#/album/<id>/show), so the album id
        // is never in location.pathname — which is always /app/.
        const match = (location.hash || location.pathname).match(/\/album\/([^/?#]+)/);
        if (!match) return;
        const albumId = decodeURIComponent(match[1]);
        // MUI emits hashed jss class names now, so the old class-based lookup
        // matches nothing. Anchor on the album's own Shuffle/Play control and
        // use its toolbar; fall back to the card holding the album heading.
        const anchor = document.querySelector('button[aria-label*="huffle"], button[aria-label*="lay"]');
        const actionsArea = (anchor && anchor.parentElement)
            || document.querySelector('[class*="MuiCardContent-root"]');
        if (!actionsArea || actionsArea.querySelector('.nd-del-album')) return;

        const btn = document.createElement('button');
        btn.className = 'nd-del-album';
        btn.innerHTML = TRASH + '<span>Delete album</span>';
        btn.onclick = async (e) => {
            e.preventDefault(); e.stopPropagation();
            const params = authParams({ id: albumId });
            try {
                const resp = await fetch(`${ND_API}/getAlbum?${params}`);
                const data = await resp.json();
                const album = data['subsonic-response']?.album;
                if (!album) return;
                const songs = album.song || [];
                if (!songs.length || !confirm(`Delete entire album from disk?\n\n"${album.name}"\nby ${album.artist}\n${songs.length} track(s)\n\nEvery track will also be blacklisted so automation never re-downloads them.`)) return;
                const firstPath = songs[0].path;
                const dir = firstPath.substring(0, firstPath.lastIndexOf('/'));
                const delResp = await fetch(`${API_BASE}/delete-album`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    // Collected BEFORE the directory goes away — afterwards there
                    // is nothing left to read the track names from.
                    body: JSON.stringify({
                        dir,
                        tracks: songs.map(sg => ({ artist: sg.artist, title: sg.title }))
                    })
                });
                const result = await delResp.json();
                if (result.success) {
                    triggerRescan();
                    setTimeout(() => location.href = '/app/album/all', 1500);
                } else {
                    alert('Delete failed: ' + (result.error || 'unknown error'));
                }
            } catch (e) { alert('Delete failed: ' + e.message); }
        };
        actionsArea.appendChild(btn);
    }

    // ---- Main scan ----
    function processRows() {
        document.querySelectorAll('tbody tr').forEach(row => {
            if (row.querySelector('.songTitle, [class*="songTitle"]') || row.textContent.trim().length > 10) {
                addDeleteToRow(row);
            }
        });
    }

    function scan() {
        processRows();
        addDeleteToPlayer();
        if ((location.hash || location.pathname).includes('/album/')) addDeleteToAlbumPage();
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
