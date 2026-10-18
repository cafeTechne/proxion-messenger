import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// main.js is the composition root and cannot be imported in the node test env,
// so these guards read its source (same approach as main-selector.test.js).
const src = readFileSync(fileURLToPath(new URL('./main.js', import.meta.url)), 'utf8');

function body(name, next) {
    const fn = src.slice(src.indexOf(`async function ${name}(`));
    return fn.slice(0, fn.indexOf(next));
}

describe('pod sign-out and delete identity', () => {
    it('pod sign-out runs the full solidLogout purge, not a bare session logout', () => {
        const b = body('_signOutOfPod', 'async function _resetIdentity(');
        expect(b).toContain('await solidLogout()');
        expect(b).not.toContain('solidSession.logout');
    });

    it('delete identity erases the account on the gateway before wiping the browser', () => {
        const b = body('_resetIdentity', "attachListener('#settings-pod-logout-btn'");
        const send = b.indexOf("cmd: 'delete_account', confirm: 'delete'");
        expect(send).toBeGreaterThan(-1);
        expect(send).toBeLessThan(b.indexOf('localStorage.clear()'));
        expect(b).toContain('await solidLogout()');
        expect(src).toMatch(/case "account_deleted":\s*_accountDeleteWaiter\?\.\(true\);/);
    });
});
