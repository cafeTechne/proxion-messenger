import { describe, it, expect, vi } from 'vitest';
import { activeSectionId, createDebouncedSaver } from './settings-panel.js';

const SECTIONS = [
    { id: 'profile', top: 0 },
    { id: 'privacy', top: 300 },
    { id: 'pod', top: 700 },
    { id: 'danger', top: 1200 },
];

describe('activeSectionId', () => {
    it('returns null with no sections', () => {
        expect(activeSectionId([], 0)).toBeNull();
    });

    it('picks the first section at the top', () => {
        expect(activeSectionId(SECTIONS, 0)).toBe('profile');
    });

    it('picks the last section whose top has scrolled past the offset', () => {
        expect(activeSectionId(SECTIONS, 280, { offset: 32 })).toBe('privacy');
        expect(activeSectionId(SECTIONS, 650, { offset: 32 })).toBe('privacy');
        expect(activeSectionId(SECTIONS, 680, { offset: 32 })).toBe('pod');
    });

    it('picks the last section once the pane is scrolled to the end', () => {
        expect(activeSectionId(SECTIONS, 900, { atEnd: true })).toBe('danger');
    });
});

describe('createDebouncedSaver', () => {
    function fakeTimers() {
        let next = 1;
        const pending = new Map();
        return {
            setTimeout(fn) { const id = next++; pending.set(id, fn); return id; },
            clearTimeout(id) { pending.delete(id); },
            runAll() { const fns = [...pending.values()]; pending.clear(); fns.forEach((f) => f()); },
            get size() { return pending.size; },
        };
    }

    it('runs once after repeated schedules', () => {
        const timers = fakeTimers();
        const save = vi.fn();
        const s = createDebouncedSaver(save, 800, timers);
        s.schedule(); s.schedule(); s.schedule();
        expect(timers.size).toBe(1);
        expect(s.pending).toBe(true);
        timers.runAll();
        expect(save).toHaveBeenCalledTimes(1);
        expect(s.pending).toBe(false);
    });

    it('flush saves now and drops the pending timer', () => {
        const timers = fakeTimers();
        const save = vi.fn();
        const s = createDebouncedSaver(save, 800, timers);
        s.schedule();
        s.flush();
        expect(save).toHaveBeenCalledTimes(1);
        expect(timers.size).toBe(0);
        timers.runAll();
        expect(save).toHaveBeenCalledTimes(1);
    });

    it('cancel drops the pending save', () => {
        const timers = fakeTimers();
        const save = vi.fn();
        const s = createDebouncedSaver(save, 800, timers);
        s.schedule();
        s.cancel();
        timers.runAll();
        expect(save).not.toHaveBeenCalled();
    });
});
