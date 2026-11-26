export async function nativeNavigationJourney({ sender, receiver, sourceGateway, openSender, online }) {
    await sender.bringToFront(); await openSender();
    const name = 'native-nav-' + Date.now() + '.txt';
    const hash = await sender.evaluate(async name => {
        const bytes = new Uint8Array(600 * 1024); for (let n = 0; n < bytes.length; n++) bytes[n] = n % 251;
        const transfer = new DataTransfer(); transfer.items.add(new File([bytes], name, { type: 'text/plain' }));
        const input = document.getElementById('file-input'); input.files = transfer.files; input.dispatchEvent(new Event('change', { bubbles: true }));
        return [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(n => n.toString(16).padStart(2, '0')).join('');
    }, name);
    await sender.waitForSelector('#file-confirm-modal', { visible: true }); await sender.click('#file-confirm-ok');
    await receiver.bringToFront(); await receiver.waitForFunction(async name => (await (await import('./dmhistory.js')).dmHistorySearch(name)).some(row => row.file), { timeout: 30000 }, name);
    await sender.bringToFront(); await sender.waitForFunction(async name => (await (await import('./dmhistory.js')).dmHistorySearch(name)).some(row => row.file), { timeout: 30000 }, name);
    const file = await sender.evaluate(async name => (await (await import('./dmhistory.js')).dmHistorySearch(name))[0], name);
    const bookmark = async id => {
        await sender.waitForSelector(`[id="msg-${id}"]`, { visible: true, timeout: 15000 });
        await sender.evaluate(id => document.getElementById('msg-' + id)?.scrollIntoView({ block: 'center' }), id);
        await sender.focus(`[id="msg-${id}"]`); await sender.keyboard.down('Shift'); await sender.keyboard.press('F10'); await sender.keyboard.up('Shift');
        await sender.waitForSelector('#ctx-bookmark', { visible: true });
        // Wait for openMenu's deferred first-item focus before choosing an item.
        await sender.waitForFunction(() => document.getElementById('ctx-menu')?.contains(document.activeElement));
        await sender.focus('#ctx-bookmark'); await sender.keyboard.press('Enter');
        await sender.waitForFunction(async id => (await (await import('./saved.js')).listSaved()).some(row => row.id === id), { timeout: 10000 }, id);
    };
    const savedJump = async id => {
        await sender.click('#saved-panel-btn');
        const selector = `[data-saved-action="jump"][data-message-id="${id}"]`;
        await sender.waitForSelector(selector, { visible: true }); await sender.focus(selector); await sender.keyboard.press('Enter');
    };
    const focused = async id => {
        await sender.waitForFunction(id => document.activeElement?.dataset.messageId === id
            && !!document.getElementById('msg-' + id)?.getClientRects().length, { timeout: 15000 }, id);
        await sender.evaluate(() => new Promise(resolve => setTimeout(resolve, 250)));
        if (!await sender.evaluate(id => document.activeElement?.dataset.messageId === id, id)) throw Error('background history replaced reference focus');
    };
    const closeSaved = async () => sender.evaluate(() => document.getElementById('saved-panel-close').click());
    console.log('  · navigation file ready; bookmarking real attachment and text');
    await bookmark(file.message_id);
    const text = 'plain-nav-' + Date.now(); await sender.focus('#message-input'); await sender.type('#message-input', text); await sender.keyboard.press('Enter');
    await sender.waitForFunction(async text => (await (await import('./dmhistory.js')).dmHistorySearch(text)).some(row => row.content === text), { timeout: 15000 }, text);
    const ordinary = await sender.evaluate(async text => (await (await import('./dmhistory.js')).dmHistorySearch(text))[0], text);
    await sender.waitForFunction(async id => !(await (await import('./e2e.js')).listDmSendJobs()).some(job => job.id === id), { timeout: 45000 }, ordinary.message_id);
    await bookmark(ordinary.message_id);
    console.log('  · navigation bookmarks saved; creating second conversation');
    const roomName = 'reference-destination-' + Date.now();
    await sender.click('#create-room-btn'); await sender.waitForSelector('#room-name-input', { visible: true }); await sender.type('#room-name-input', roomName); await sender.click('#room-create-submit');
    await sender.waitForSelector('#room-create-done-btn', { visible: true }); await sender.click('#room-create-done-btn');
    const roomId = await sender.evaluate(roomName => [...document.querySelectorAll('#room-list li')].find(row => row.dataset.name === roomName)?.id.slice(4), roomName);
    if (!roomId) throw Error('navigation room fixture missing');
    const openRoom = async () => { await sender.click(`[id="nav-${roomId}"]`); await sender.waitForFunction(name => document.getElementById('chat-header-name')?.textContent.includes(name), { timeout: 10000 }, roomName); };
    await openRoom();
    const roomText = 'room-nav-' + Date.now(); await sender.focus('#message-input'); await sender.type('#message-input', roomText); await sender.keyboard.press('Enter');
    await sender.waitForFunction(text => [...document.querySelectorAll('#message-feed .message')].some(row => row.textContent.includes(text)), { timeout: 15000 }, roomText);
    const roomMessage = await sender.evaluate(text => [...document.querySelectorAll('#message-feed .message')].find(row => row.textContent.includes(text))?.dataset.messageId, roomText);
    await bookmark(roomMessage);
    console.log('  · navigation room bookmark ready; opening original references');
    await savedJump(ordinary.message_id); await focused(ordinary.message_id);
    await savedJump(roomMessage); await focused(roomMessage);
    console.log('  · Saved opens the original DM/room and focuses its actual ordinary message');
    await openSender();
    await sender.evaluate(async ({ file, run }) => {
        const history = await import('./dmhistory.js'), self = localStorage.getItem('proxion_identity_did');
        for (let n = 0; n < 550; n++) {
            if (await history.dmHistorySave({ message_id: `nav-filler-${run}-${n}`, thread_id: file.thread_id, from_webid: self, content: `navigation filler ${n}`,
                timestamp: new Date(Date.now() + 60000 + n).toISOString() }) !== true) throw Error('fixture cache write failed');
        }
    }, { file, run: name });
    await sender.reload({ waitUntil: 'load' }); await online(sender); await openSender();
    await sender.waitForSelector(`[id="msg-nav-filler-${name}-549"]`, { timeout: 15000 });
    if (await sender.$(`[id="msg-${file.message_id}"]`)) throw Error('navigation fixture did not fall outside the retained history window');
    await openRoom(); await sender.setOfflineMode(true); await sourceGateway.pause();
    await sender.waitForFunction(() => !document.querySelector('.dot')?.classList.contains('online'), { timeout: 10000 });
    await savedJump(file.message_id); await focused(file.message_id);
    const restored = await sender.evaluate(async id => {
        const record = await (await import('./dmhistory.js')).dmHistoryGet(id), bytes = Uint8Array.from(atob(record?.file?.data_b64 || ''), c => c.charCodeAt(0));
        return { size: bytes.length, hash: [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(n => n.toString(16).padStart(2, '0')).join('') };
    }, file.message_id);
    if (restored.size !== 600 * 1024 || restored.hash !== hash) throw Error('older offline navigation lost retained Blob bytes');
    await openRoom(); await sender.focus('#search-input'); await sender.type('#search-input', name);
    await sender.waitForSelector('.search-result', { visible: true, timeout: 15000 }); await sender.focus('.search-result'); await sender.keyboard.press('Enter'); await focused(file.message_id);
    console.log('  · older native Blob is outside the 500-row cache load, yet Saved/search reveal its exact bytes and keyboard focus offline');
    await openRoom();
    await sender.evaluate(() => {
        const read = Blob.prototype.arrayBuffer;
        window.__navBlobHeld = false;
        Blob.prototype.arrayBuffer = function (...args) {
            if (this.size === 600 * 1024 && !window.__navBlobHeld) {
                window.__navBlobHeld = true; const blob = this;
                return new Promise(resolve => { window.__releaseNavBlob = async () => { Blob.prototype.arrayBuffer = read; resolve(await read.apply(blob, args)); }; });
            }
            return read.apply(this, args);
        };
    });
    await savedJump(file.message_id); await sender.waitForFunction(() => window.__navBlobHeld, { timeout: 10000 });
    await openRoom(); await sender.focus('#message-input'); await sender.evaluate(() => window.__releaseNavBlob());
    await sender.evaluate(() => new Promise(resolve => setTimeout(resolve, 300)));
    if (await sender.evaluate(id => !!document.getElementById('msg-' + id), file.message_id)) throw Error('stale native lookup populated a replacement view');
    if (!await sender.$eval('#chat-header-name', (node, name) => node.textContent.includes(name), roomName)) throw Error('stale lookup stole the conversation');
    await closeSaved(); await savedJump(file.message_id); await focused(file.message_id);
    await sender.click(`[id="msg-${file.message_id}"]`, { button: 'right' }); await sender.waitForSelector('#ctx-delete-me', { visible: true }); await sender.click('#ctx-delete-me');
    await sender.waitForFunction(async id => !(await (await import('./dmhistory.js')).dmHistoryGet(id)), { timeout: 10000 }, file.message_id);
    await openRoom(); await savedJump(file.message_id);
    await sender.waitForFunction(() => [...document.querySelectorAll('.toast')].some(node => /not available|unavailable/i.test(node.textContent)), { timeout: 10000 });
    if (await sender.$(`[id="msg-${file.message_id}"]`)) throw Error('deleted Saved reference recreated a file bubble');
    await closeSaved();
    await sender.evaluate(async () => (await import('./saved.js')).upsertSaved({ id: 'missing-navigation-reference', thread_id: 'removed-navigation-room', thread_type: 'local_room', content: 'missing original conversation' }));
    await openRoom(); await savedJump('missing-navigation-reference');
    if (!await sender.$eval('#chat-header-name', (node, name) => node.textContent.includes(name), roomName)) throw Error('missing conversation guessed another destination');
    await closeSaved(); await sender.setOfflineMode(false); await sourceGateway.resume(); await online(sender); await openSender();
    console.log('  ✓ Saved/search cross conversation jumps preserve focus and older offline Blob bytes; stale view completion, deleted originals and missing conversations cannot reveal substitute messages');
}
