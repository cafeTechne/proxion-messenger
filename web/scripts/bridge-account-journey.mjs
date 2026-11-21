// A real credential replacement must not reauthor a saved text envelope.
export async function bridgeAccountJourney({ sender, receiver, originalActor, replacementActor,
    setAccount, openSender, openReceiver }) {
    await openSender(); await openReceiver();
    const text = 'bridge-owner-recovery-' + Date.now();
    const writes = [], cdp = await sender.createCDPSession(); await cdp.send('Network.enable');
    cdp.on('Network.webSocketFrameSent', event => {
        try {
            const command = JSON.parse(event.response.payloadData);
            if (command.cmd === 'pod_fetch' && command.method === 'POST') writes.push(command);
        } catch { /* unrelated frame */ }
    });
    await sender.evaluate(() => {
        const io = window.proxionCrossMode.bridge.io, original = io.podDropDm.bind(io);
        io.podDropDm = async () => false;
        window.__restoreActorDrop = () => { io.podDropDm = original; };
    });
    await sender.focus('#message-input'); await sender.type('#message-input', text); await sender.keyboard.press('Enter');
    await sender.waitForFunction(async text => (await (await import('./e2e.js')).listDmSendJobs()).some(job => job.record.content === text && job.frames), { timeout: 20000, polling: 250 }, text);
    const original = await sender.evaluate(async text => (await (await import('./e2e.js')).listDmSendJobs()).find(job => job.record.content === text), text);
    if (original.podActor !== originalActor) throw Error('bridge text did not freeze its source account');
    await setAccount('replacement');
    await sender.waitForFunction(actor => window.proxionCrossMode?.isReady() && window.proxionCrossMode.state().webid === actor,
        { timeout: 40000, polling: 250 }, replacementActor);
    await sender.evaluate(() => window.__restoreActorDrop());
    await sender.reload({ waitUntil: 'networkidle2' });
    await sender.waitForFunction(actor => window.proxionCrossMode?.isReady() && window.proxionCrossMode.state().webid === actor,
        { timeout: 40000, polling: 250 }, replacementActor);
    await new Promise(resolve => setTimeout(resolve, 5500)); // include the automatic recovery scan
    const held = await sender.evaluate(async id => (await (await import('./e2e.js')).listDmSendJobs()).find(job => job.id === id), original.id);
    if (!held || held.podActor !== originalActor || held.peerConfirmed || JSON.stringify(held.frames) !== JSON.stringify(original.frames)) throw Error('replacement account changed or discarded the saved send');
    const matching = () => writes.filter(command => { try { return JSON.parse(command.body).message_id === original.id; } catch { return false; } });
    if (matching().length) throw Error('original-account text was published while replacement credentials were active');
    await setAccount('original');
    await sender.waitForFunction(actor => window.proxionCrossMode?.isReady() && window.proxionCrossMode.state().webid === actor,
        { timeout: 40000, polling: 250 }, originalActor);
    await sender.waitForFunction(async id => !(await (await import('./e2e.js')).listDmSendJobs()).some(job => job.id === id),
        { timeout: 30000, polling: 250 }, original.id);
    await openReceiver();
    for (let attempt = 0; attempt < 40; attempt++) {
        await receiver.evaluate(() => window.proxionWebDm?.drainOnce());
        if (await receiver.evaluate(text => document.getElementById('message-feed')?.textContent.includes(text), text)) break;
        await new Promise(resolve => setTimeout(resolve, 300));
    }
    const count = await receiver.evaluate(async text => (await (await import('./dmhistory.js')).dmHistorySearch(text)).filter(row => row.content === text).length, text);
    if (count !== 1) throw Error('original-account recovery did not leave one readable message: ' + count);
    const posted = matching(); if (!posted.length) throw Error('no bridge publication evidence');
    const prepared = original.frames[0];
    for (const command of posted) {
        const envelope = JSON.parse(command.body);
        if (command.expected_webid !== originalActor || envelope.from_webid !== originalActor
            || envelope.content !== prepared.content || envelope.nonce !== prepared.nonce || envelope.msg_num !== prepared.msg_num) throw Error('recovery changed actor or ciphertext');
    }
    await cdp.detach(); await openSender();
    console.log('  ✓ bridge text stays bound to its original pod through credential replacement/reload and resumes exact ciphertext once when that account returns');
}
