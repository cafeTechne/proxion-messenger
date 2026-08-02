// devices.test.js — R81 R1: device enumeration + constraint helpers. Pure.
import { describe, it, expect } from 'vitest';
import { mapDevices, preferredDeviceId, mediaConstraints } from './devices.js';

describe('mapDevices', () => {
    it('splits cameras and mics, ignoring other kinds', () => {
        const { cameras, mics } = mapDevices([
            { kind: 'videoinput', deviceId: 'c1', label: 'Cam' },
            { kind: 'audioinput', deviceId: 'm1', label: 'Mic' },
            { kind: 'audiooutput', deviceId: 's1', label: 'Speaker' },
            null,
        ]);
        expect(cameras).toEqual([{ id: 'c1', label: 'Cam' }]);
        expect(mics).toEqual([{ id: 'm1', label: 'Mic' }]);
    });
});

describe('preferredDeviceId', () => {
    const list = [{ id: 'a' }, { id: 'b' }];
    it('keeps the saved id when still present', () => {
        expect(preferredDeviceId(list, 'b')).toBe('b');
    });
    it('falls back to the first when the saved id is gone', () => {
        expect(preferredDeviceId(list, 'gone')).toBe('a');
    });
    it('is empty for an empty list', () => {
        expect(preferredDeviceId([], 'x')).toBe('');
    });
});

describe('mediaConstraints', () => {
    it('audio-only by default with DSP', () => {
        const c = mediaConstraints({ micId: '' });
        expect(c.video).toBe(false);
        expect(c.audio.echoCancellation).toBe(true);
    });
    it('pins exact devices when ids are given', () => {
        const c = mediaConstraints({ cameraId: 'c1', micId: 'm1', video: true });
        expect(c.audio.deviceId).toEqual({ exact: 'm1' });
        expect(c.video.deviceId).toEqual({ exact: 'c1' });
    });
    it('uses default camera when no id and video requested', () => {
        expect(mediaConstraints({ video: true }).video).toBe(true);
    });
});
