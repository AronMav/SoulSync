// ── Deleted-track blacklist ("Deleted") modal ───────────────────────────
// Tracks removed from the library with the trash button in the Navidrome
// player are remembered by name so automation never fetches them again.
// Unlike the ignore-list this never expires — deleting is a permanent
// instruction — so this modal is the only way back.
// Mirrors openWishlistIgnoreModal() in downloads.js.
//
// Rows are built with DOM nodes rather than an innerHTML template: entry
// values are arbitrary track titles, and the shared escapeHtml() helper
// (textContent -> innerHTML) does NOT escape quotes, so a title like
// He Said "Yes" would break straight out of an HTML attribute.

async function openDeletedBlacklistModal() {
    let modal = document.getElementById('deleted-blacklist-modal');
    if (modal) modal.remove();
    modal = document.createElement('div');
    modal.id = 'deleted-blacklist-modal';
    modal.className = 'modal-overlay';
    modal.style.cssText = 'display:flex;position:fixed;inset:0;z-index:10050;align-items:center;justify-content:center;background:rgba(0,0,0,0.6);';
    modal.innerHTML = `
        <div class="playlist-modal-content" style="max-width:560px;width:90%;max-height:80vh;display:flex;flex-direction:column;">
            <div class="playlist-modal-header">
                <h2 style="margin:0;">🗑️ Deleted Tracks</h2>
                <button class="playlist-modal-btn playlist-modal-btn-secondary" onclick="closeDeletedBlacklistModal()">Close</button>
            </div>
            <p style="opacity:0.7;font-size:13px;margin:8px 16px 0;">Tracks you deleted from the library are never re-downloaded by automation. Un-block one to allow it back (manual downloads were never affected).</p>
            <div id="deleted-blacklist-list" class="playlist-tracks-scroll" style="flex:1;overflow-y:auto;padding:12px 16px;">
                <div class="loading-indicator">Loading...</div>
            </div>
        </div>`;
    document.body.appendChild(modal);
    modal.addEventListener('click', (e) => { if (e.target === modal) closeDeletedBlacklistModal(); });
    await loadDeletedBlacklist();
}

function closeDeletedBlacklistModal() {
    const modal = document.getElementById('deleted-blacklist-modal');
    if (modal) modal.remove();
}

function _deletedBlacklistRow(entry) {
    const row = document.createElement('div');
    row.className = 'deleted-blacklist-row';
    row.style.cssText = 'display:flex;align-items:center;gap:10px;padding:8px 4px;border-bottom:1px solid rgba(255,255,255,0.06);';

    const text = document.createElement('div');
    text.style.cssText = 'flex:1;min-width:0;';

    const title = document.createElement('div');
    title.style.cssText = 'font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;';
    title.textContent = entry.title || 'Unknown';

    const meta = document.createElement('div');
    meta.style.cssText = 'opacity:0.6;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;';
    const when = (entry.created_at || '').slice(0, 10);
    const artist = entry.artist || '';
    meta.textContent = artist + (artist && when ? ' · ' : '') + when;

    text.appendChild(title);
    text.appendChild(meta);

    const btn = document.createElement('button');
    btn.className = 'playlist-modal-btn playlist-modal-btn-secondary';
    btn.style.flexShrink = '0';
    btn.textContent = 'Un-block';
    btn.addEventListener('click', () => unblockDeletedTrack(artist, entry.title || ''));

    row.appendChild(text);
    row.appendChild(btn);
    return row;
}

async function loadDeletedBlacklist() {
    const list = document.getElementById('deleted-blacklist-list');
    if (!list) return;
    try {
        const resp = await fetch('/api/nd/blacklist');
        const data = await resp.json();
        const entries = (data && data.entries) || [];
        list.textContent = '';
        if (!entries.length) {
            const empty = document.createElement('div');
            empty.className = 'playlist-empty-state';
            empty.style.cssText = 'text-align:center;opacity:0.6;padding:30px 0;';
            empty.textContent = 'Nothing blocked.';
            list.appendChild(empty);
            return;
        }
        entries.forEach(e => list.appendChild(_deletedBlacklistRow(e)));
    } catch (err) {
        list.textContent = '';
        const error = document.createElement('div');
        error.className = 'playlist-empty-state';
        error.style.cssText = 'text-align:center;opacity:0.6;padding:30px 0;';
        error.textContent = 'Error loading deleted tracks';
        list.appendChild(error);
    }
}

async function unblockDeletedTrack(artist, title) {
    if (!title) return;
    try {
        const resp = await fetch('/api/nd/blacklist/remove', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ artist: artist || '', title }),
        });
        const data = await resp.json();
        if (data && data.success) {
            showToast('Un-blocked — automation may fetch this track again.', 'success');
            await loadDeletedBlacklist();
        } else {
            showToast(`Un-block failed: ${(data && data.error) || 'unknown'}`, 'error');
        }
    } catch (err) {
        showToast(`Un-block failed: ${err.message}`, 'error');
    }
}
