import { describe, it, expect } from 'vitest';
import { injectWebHead, WEB_CSP } from './scripts/build-web.mjs';

describe('injectWebHead', () => {
    it('adds the web-mode meta so detectMode picks web', () => {
        const out = injectWebHead('<head>\n<title>x</title>\n</head>');
        expect(out).toMatch(/<meta name="proxion-mode" content="web">/);
    });

    it('adds a Content-Security-Policy meta', () => {
        const out = injectWebHead('<head></head>');
        expect(out).toContain('http-equiv="Content-Security-Policy"');
        expect(out).toContain(WEB_CSP);
    });

    it('inserts right after <head>, keeping existing head content', () => {
        const out = injectWebHead('<head>\n    <title>Proxion</title>\n</head>');
        expect(out.indexOf('proxion-mode')).toBeLessThan(out.indexOf('<title>'));
        expect(out).toContain('<title>Proxion</title>');
    });

    it('tolerates attributes on the head tag', () => {
        const out = injectWebHead('<head data-x="1">\n</head>');
        expect(out).toMatch(/<head data-x="1">\s*\n\s*<meta name="proxion-mode"/);
    });

    it('is idempotent', () => {
        const once = injectWebHead('<head></head>');
        expect(injectWebHead(once)).toBe(once);
    });

    it('CSP restricts default-src to self and allows pod https/wss', () => {
        expect(WEB_CSP).toContain("default-src 'self'");
        expect(WEB_CSP).toContain('connect-src');
        expect(WEB_CSP).toContain('wss:');
    });
});
