// callstats.test.js — R81 S: call-health classification. Pure.
import { describe, it, expect } from 'vitest';
import { classifyConnection, deriveStats } from './callstats.js';

describe('classifyConnection', () => {
    it('is good on a clean link', () => {
        expect(classifyConnection({ rttMs: 40, lossFrac: 0 })).toBe('good');
    });
    it('is fair on moderate latency or loss', () => {
        expect(classifyConnection({ rttMs: 250, lossFrac: 0 })).toBe('fair');
        expect(classifyConnection({ rttMs: 40, lossFrac: 0.05 })).toBe('fair');
    });
    it('is poor on high latency or heavy loss', () => {
        expect(classifyConnection({ rttMs: 500, lossFrac: 0 })).toBe('poor');
        expect(classifyConnection({ rttMs: 40, lossFrac: 0.2 })).toBe('poor');
    });
});

describe('deriveStats', () => {
    const report = [
        { type: 'candidate-pair', nominated: true, currentRoundTripTime: 0.12 },
        { type: 'inbound-rtp', packetsLost: 10, packetsReceived: 990 },
        { type: 'inbound-rtp', packetsLost: 0, packetsReceived: 500 },
    ];
    it('extracts rtt in ms and totals packets', () => {
        const d = deriveStats(report, null);
        expect(d.rttMs).toBe(120);
        expect(d.packetsLost).toBe(10);
        expect(d.packetsReceived).toBe(1490);
        expect(d.lossFrac).toBe(0);          // no previous snapshot
    });
    it('computes loss over the interval from the previous snapshot', () => {
        const prev = { packetsLost: 10, packetsReceived: 1490 };
        const next = [
            { type: 'candidate-pair', nominated: true, currentRoundTripTime: 0.05 },
            { type: 'inbound-rtp', packetsLost: 20, packetsReceived: 1580 },   // +10 lost, +90 recv
        ];
        const d = deriveStats(next, prev);
        expect(d.lossFrac).toBeCloseTo(10 / 100, 3);
        expect(classifyConnection(d)).toBe('poor');   // 10% loss
    });
});
