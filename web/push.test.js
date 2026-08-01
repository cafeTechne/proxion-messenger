// push.test.js — closed-app push reachability status (R78 L1). Pure helpers.
import { describe, it, expect } from 'vitest';
import { isPrivateHost, closedAppPushStatus } from './push.js';

describe('isPrivateHost', () => {
    it('treats loopback, private, and mDNS hosts as unreachable', () => {
        for (const h of ['localhost', 'x.local', '127.0.0.1', '::1', '[::1]',
            '10.0.0.5', '192.168.1.20', '172.16.4.1', '172.31.255.1', '169.254.1.1', '0.0.0.0']) {
            expect(isPrivateHost(h)).toBe(true);
        }
    });
    it('treats public hostnames and IPs as reachable', () => {
        for (const h of ['proxion.example', 'pod.alice.net', '8.8.8.8', '172.32.0.1', '192.167.0.1']) {
            expect(isPrivateHost(h)).toBe(false);
        }
    });
});

describe('closedAppPushStatus', () => {
    it('is off without notification permission', () => {
        expect(closedAppPushStatus({ origin: 'https://proxion.example', permission: 'default' })).toBe('off');
        expect(closedAppPushStatus({ origin: 'https://proxion.example', permission: 'denied' })).toBe('off');
    });
    it('is in-app-only when granted but served from a private origin', () => {
        expect(closedAppPushStatus({ origin: 'http://localhost:8080', permission: 'granted' })).toBe('in-app-only');
        expect(closedAppPushStatus({ origin: 'http://192.168.1.10:8080', permission: 'granted' })).toBe('in-app-only');
    });
    it('is on when granted and served from a public origin', () => {
        expect(closedAppPushStatus({ origin: 'https://proxion.example', permission: 'granted' })).toBe('on');
    });
    it('is off when the origin is unparseable', () => {
        expect(closedAppPushStatus({ origin: 'not a url', permission: 'granted' })).toBe('off');
    });
});
