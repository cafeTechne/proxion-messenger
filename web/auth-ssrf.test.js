import { describe, it, expect } from 'vitest';
import { isPrivatePodHost, isPeerPodRootAllowed } from './ssrf.js';

describe('isPrivatePodHost', () => {
    it('flags loopback / private / link-local hosts', () => {
        for (const u of [
            'https://127.0.0.1/', 'http://localhost/', 'https://app.localhost/',
            'https://10.0.0.5/', 'https://192.168.1.9/', 'https://172.16.0.1/',
            'https://169.254.10.10/', 'https://0.0.0.0/',
            'https://[::1]/', 'https://[fe80::1]/', 'https://[fc00::1]/', 'https://[fd12::1]/',
            'https://[::ffff:127.0.0.1]/',
        ]) expect(isPrivatePodHost(u), u).toBe(true);
    });
    it('allows public hosts', () => {
        for (const u of [
            'https://pod.example.com/', 'https://storage.inrupt.com/abc/',
            'https://8.8.8.8/', 'https://172.15.0.1/', 'https://172.32.0.1/',
        ]) expect(isPrivatePodHost(u), u).toBe(false);
    });
    it('treats an unparseable URL as unsafe', () => {
        expect(isPrivatePodHost('not a url')).toBe(true);
    });
});

describe('isPeerPodRootAllowed', () => {
    it('allows any public https peer (cross-pod federation)', () => {
        expect(isPeerPodRootAllowed('https://bob.pod.example/', 'https://me.pod.example/')).toBe(true);
    });
    it('blocks a private peer when it is not our own pod origin', () => {
        expect(isPeerPodRootAllowed('https://127.0.0.1/', 'https://me.pod.example/')).toBe(false);
        expect(isPeerPodRootAllowed('https://[::1]:3000/', null)).toBe(false);
    });
    it('allows a private peer that shares our own pod origin (local/dev)', () => {
        expect(isPeerPodRootAllowed('http://localhost:3000/bob/', 'http://localhost:3000/me/')).toBe(true);
    });
    it('refuses an empty root', () => {
        expect(isPeerPodRootAllowed('', 'https://me.pod.example/')).toBe(false);
    });
});
