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

    function getCredentials() {
        const u = localStorage.getItem('nd-username') || 'admin';
        const p = localStorage.getItem('nd-salted-pass') || '';
        return { u, p };
    }

    async function getSongInfo(songId) {
        const creds = getCredentials();
        const params = new URLSearchParams({
            u: creds.u, p: creds.p, v: '1.16.1', c: 'nd-delete', f: 'json', id: songId
        });
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
        const creds = getCredentials();
        const params = new URLSearchParams({ u: creds.u, p: creds.p, v: '1.16.1', c: 'nd-delete' });
        fetch(`${ND_API}/startScan?${params}`).catch(() => {});
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
        const player = document.querySelector('.music-player-controller, [class*="music-player-controller"], .controller-title');
        if (!player || player.querySelector('.nd-del-player')) return;

        // Get current song ID from the audio element's src
        const audio = document.querySelector('.music-player-audio, audio');
        let songId = null;
        if (audio) {
            const src = audio.getAttribute('src') || audio.currentSrc || '';
            const m = src.match(/[?&]id=([^&]+)/);
            if (m) songId = m[1];
        }
        if (!songId) {
            // Walk the player DOM to find the song id via React fiber
            const playerEl = document.querySelector('.music-player, [class*="music-player"], [class*="NDAudioPlayer"]');
            if (playerEl) songId = getSongIdFromFiber(playerEl);
        }
        if (!songId) return;

        const btn = document.createElement('button');
        btn.className = 'nd-del-player';
        btn.title = 'Delete current song from disk';
        btn.innerHTML = TRASH;
        btn.onclick = (e) => { e.preventDefault(); e.stopPropagation(); deleteSong(songId); };
        player.appendChild(btn);
    }

    // ---- Song row delete button ----
    function addDeleteToRow(row) {
        if (row.querySelector('.nd-del-icon')) return;
        const songId = getSongId(row);
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
        btn.onclick = (e) => { e.preventDefault(); e.stopPropagation(); deleteSong(songId); };
        container.appendChild(btn);
    }

    // ---- Album page delete button ----
    function addDeleteToAlbumPage() {
        const match = location.pathname.match(/\/album\/(.+)/);
        if (!match) return;
        const albumId = decodeURIComponent(match[1]);
        const actionsArea = document.querySelector('[class*="albumShow"] [class*="actions"], [class*="albumDetail"] [class*="actions"]');
        if (!actionsArea || actionsArea.querySelector('.nd-del-album')) return;

        const btn = document.createElement('button');
        btn.className = 'nd-del-album';
        btn.innerHTML = TRASH + '<span>Delete album</span>';
        btn.onclick = async (e) => {
            e.preventDefault(); e.stopPropagation();
            const creds = getCredentials();
            const params = new URLSearchParams({ u: creds.u, p: creds.p, v: '1.16.1', c: 'nd-delete', f: 'json', id: albumId });
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
        if (location.pathname.includes('/album/')) addDeleteToAlbumPage();
    }

    // ---- MutationObserver ----
    let debounce = null;
    new MutationObserver(() => {
        if (debounce) return;
        debounce = setTimeout(() => { debounce = null; scan(); }, 200);
    }).observe(document.body, { childList: true, subtree: true });

    // ---- Route change detection (SPA) ----
    let lastUrl = location.href;
    setInterval(() => {
        if (location.href !== lastUrl) {
            lastUrl = location.href;
            setTimeout(scan, 500);
        }
    }, 300);

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', scan);
    } else {
        scan();
    }
    console.log('[nd-delete] loaded');
})();
