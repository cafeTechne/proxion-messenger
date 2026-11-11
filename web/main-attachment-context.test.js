import { describe, it, expect, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { captureMediaTarget } from './media.js';
const source = readFileSync(new URL('./main.js', import.meta.url), 'utf8');
function declaration(name) {
    const start = source.indexOf('async function ' + name + '(');
    const tail = source.slice(start), end = /^        }\r?$/m.exec(tail);
    return tail.slice(0, end.index + end[0].length);
}
function fixture() {
    let session = 'one';
    const readers = [];
    const deps = {
        activeView: { type: 'local_room', id: 'first' }, socket: { readyState: 1 },
        _isPodRoomView: () => false, _roomMedia: { send: vi.fn() },
        _isPodDmView: () => false, _dmMedia: { send: vi.fn() },
        _webRoomSessionId: () => session, _conversationSupports: () => true,
        captureMediaTarget, showToast: vi.fn(), t: key => key,
        needsDownscale: () => false, downscaleImage: vi.fn(), measureMedia: vi.fn(async () => null),
        secondary: { mustSeal: () => false, sealFile: vi.fn() }, inlineFileLimit: () => 2 * 1024 * 1024,
        ROOM_FILE_MB: 2, FILE_TAG_BYTES: 16, fileTransfer: { sendFileChunked: vi.fn() },
        WebSocket: { OPEN: 1 }, SEALED_FILE_NAME: 'sealed', SEALED_FILE_MIME: 'application/octet-stream',
        _u8ToB64: () => 'QUJD', sendCmd: vi.fn(), podUploadFile: vi.fn(async () => null),
        attachmentBlockReason: () => null, confirmAttachment: vi.fn(async () => ({ ok: true })),
        crypto: { randomUUID: () => 'message-1' },
        FileReader: class { readAsDataURL() { readers.push(this); } },
    };
    const app = new Function(...Object.keys(deps), `${declaration('sendAttachmentFile')}\n${declaration('confirmAndSend')}\nreturn {
        sendAttachmentFile, confirmAndSend, setView(view) { activeView = view; }, setSocket(value) { socket = value; }
    };`)(...Object.values(deps));
    const file = { name: 'photo.png', type: 'image/png', size: 10 };
    return { app, deps, readers, file, changeSession: () => { session = 'two'; }, finishRead: () => {
        readers[0].result = 'data:image/png;base64,QUJD'; readers[0].onload();
    } };
}

describe('attachment destination across asynchronous preparation', () => {
    it('sends and archives to the confirmed room', async () => {
        const h = fixture(); await h.app.sendAttachmentFile(h.file); h.finishRead();
        expect(h.deps.sendCmd).toHaveBeenCalledWith('send_file', expect.objectContaining({ room_id: 'first', data_b64: 'QUJD' }));
        expect(h.deps.podUploadFile).toHaveBeenCalledWith('first', 'message-1', 'photo.png', h.file);
    });
    it.each(['view', 'mutated view', 'session', 'socket'])('cancels an inline file after a changed %s', async change => {
        const h = fixture(); await h.app.sendAttachmentFile(h.file);
        if (change === 'view') h.app.setView({ type: 'local_room', id: 'second' });
        if (change === 'mutated view') h.deps.activeView.id = 'second';
        if (change === 'session') h.changeSession();
        if (change === 'socket') h.app.setSocket({ readyState: 1 });
        h.finishRead();
        expect(h.deps.sendCmd).not.toHaveBeenCalled(); expect(h.deps.podUploadFile).not.toHaveBeenCalled();
        expect(h.deps.showToast).toHaveBeenCalledWith('media.conversationChanged', 'error');
    });
    it('keeps the target selected before the confirmation dialog', async () => {
        const h = fixture(); let confirm;
        h.deps.confirmAttachment.mockImplementation(() => new Promise(resolve => { confirm = resolve; }));
        const sending = h.app.confirmAndSend(h.file);
        h.app.setView({ type: 'local_room', id: 'second' }); confirm({ ok: true }); await sending;
        expect(h.readers).toHaveLength(0); expect(h.deps.sendCmd).not.toHaveBeenCalled();
    });
    it('cancels when media measurement finishes in a different room', async () => {
        const h = fixture(); let measured;
        h.deps.measureMedia.mockImplementation(() => new Promise(resolve => { measured = resolve; }));
        const sending = h.app.sendAttachmentFile(h.file);
        h.app.setView({ type: 'local_room', id: 'second' }); measured(null); await sending;
        expect(h.readers).toHaveLength(0); expect(h.deps.sendCmd).not.toHaveBeenCalled();
    });
    it('does not send encrypted bytes after the account changes during sealing', async () => {
        const h = fixture(); let sealed;
        h.deps.secondary.mustSeal = () => true;
        h.file.arrayBuffer = async () => new Uint8Array([1]).buffer;
        h.deps.secondary.sealFile.mockImplementation(() => new Promise(resolve => { sealed = resolve; }));
        const sending = h.app.sendAttachmentFile(h.file);
        await vi.waitFor(() => expect(sealed).toBeTypeOf('function'));
        h.changeSession(); sealed({ ct: new Uint8Array([1]), hdr: {} }); await sending;
        expect(h.deps.sendCmd).not.toHaveBeenCalled();
    });
});
