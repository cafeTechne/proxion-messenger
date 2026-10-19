import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { ROOM_FILE_BYTES } from './media-resize.js';

// main.js cannot be imported in the node test env; read its source instead.
const src = readFileSync(fileURLToPath(new URL('./main.js', import.meta.url)), 'utf8');
const en = JSON.parse(readFileSync(fileURLToPath(new URL('./locales/en.json', import.meta.url)), 'utf8'));

describe('oversized room file message', () => {
    it('names the room limit instead of "large files only in DMs"', () => {
        expect(en['file.roomTooLarge']).toContain('{mb} MB');
        const check = src.slice(src.indexOf('function attachmentBlockReason('));
        expect(check.slice(0, check.indexOf('\n        }\n'))).toContain("if (!isDm) return 'file.roomTooLarge';");
        expect(src).toContain("showToast(t(blocked, { mb: ROOM_FILE_MB }))");
        expect(src).toContain("showToast(t('file.roomTooLarge', { mb: ROOM_FILE_MB }))");
        expect(ROOM_FILE_BYTES / (1024 * 1024)).toBe(2);
    });
});
