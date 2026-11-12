import { describe, expect, it, vi } from 'vitest';
import { EventEmitter } from 'node:events';
import { classifySmokeResult, runGroups, runSmoke, selectGroups, GROUPS } from './scripts/run-smokes.mjs';
import { smokeSkipExit } from './scripts/smoke-status.mjs';

describe('smoke prerequisite results', () => {
  it('keeps optional skips distinct from success and fails required skips', () => {
    expect(smokeSkipExit({ required: false })).toBe(77);
    expect(smokeSkipExit({ required: true })).toBe(1);
    expect(smokeSkipExit({ required: false, failed: true })).toBe(1);
    expect(classifySmokeResult({ code: 77 })).toBe('skipped');
    expect(classifySmokeResult({ code: 0, out: 'SMOKE SKIPPED: CSS unavailable' })).toBe('skipped');
    expect(classifySmokeResult({ code: 0, out: '  – skipped: no CSS pod available' })).toBe('skipped');
    expect(classifySmokeResult({ code: 0, out: '  - Tier 2 skipped: offline\nSMOKE PASSED' })).toBe('skipped');
    expect(classifySmokeResult({ code: 1, out: 'SMOKE SKIPPED: CSS unavailable' })).toBe('failed');
    expect(classifySmokeResult({ code: 0, timedOut: true })).toBe('failed');
    expect(classifySmokeResult({ code: 0, out: 'SMOKE PASSED' })).toBe('passed');
  });

  it('runs every live-pod room journey as required and refuses a skipped result', async () => {
    const run = vi.fn(async (file, { required }) => ({ file, required, status: 'skipped', secs: 0, out: 'missing pod' }));
    const output = [];
    const { exitCode } = await runGroups(['pod-required'], { run, write: (s) => output.push(s), required: false });
    expect(run.mock.calls).toEqual(GROUPS['pod-required'].map((file) => [file, { required: true }]));
    expect(exitCode).toBe(1);
    expect(output.join('')).toContain(`0 passed, ${GROUPS['pod-required'].length} skipped, 0 failed.`);
    expect(output.join('')).toContain('SKIP (required)');
  });

  it('reports optional skips without counting them as passed', async () => {
    const run = vi.fn(async (file, { required }) => ({ file, required, status: 'skipped', secs: 0, out: 'missing pod' }));
    const output = [];
    const result = await runGroups(['pod'], { run, write: (s) => output.push(s), required: false });
    expect(result.exitCode).toBe(0);
    expect(output.join('')).toContain(`0 passed, ${GROUPS.pod.length} skipped, 0 failed.`);
  });
  it('requires ACP checks with the explicit ACP server configuration', async () => {
    const run = vi.fn(async (file, { required }) => ({ file, required, status: 'skipped', secs: 0, out: 'missing ACP server' }));
    const result = await runGroups(['pod-acp'], { run, write: () => {}, required: false });
    expect(result.exitCode).toBe(1);
    expect(run).toHaveBeenCalledTimes(GROUPS['pod-acp'].length);
    for (const [, options] of run.mock.calls) {
      expect(options.required).toBe(true);
      expect(options.env.PROXION_CSS_ACP).toBe('1');
    }
  });

  it('runs the required subset only once when all groups are selected', () => {
    expect(selectGroups(['all'])).toContain('pod');
    expect(selectGroups(['all'])).not.toContain('pod-required');
    expect(() => selectGroups(['unknown'])).toThrow(/usage:/);
  });
});

function childHarness(start) {
  const child = new EventEmitter();
  child.stdout = new EventEmitter();
  child.stderr = new EventEmitter();
  child.kill = vi.fn();
  const spawnProcess = vi.fn(() => { queueMicrotask(() => start(child)); return child; });
  return { child, spawnProcess };
}

describe('smoke child process results', () => {
  it('propagates required mode to the child and captures an explicit skip', async () => {
    const h = childHarness((child) => {
      child.stdout.emit('data', 'SMOKE SKIPPED: CSS unavailable\n');
      child.emit('close', 77, null);
    });
    const result = await runSmoke('fixture.mjs', { required: true, spawnProcess: h.spawnProcess, env: { PATH: 'test-path' } });
    expect(result).toMatchObject({ required: true, status: 'skipped' });
    expect(h.spawnProcess.mock.calls[0][2].env).toEqual({ PATH: 'test-path', PROXION_SMOKE_REQUIRED: '1' });
  });

  it('reports startup errors as failures instead of waiting forever', async () => {
    const h = childHarness((child) => child.emit('error', new Error('executable unavailable')));
    const result = await runSmoke('fixture.mjs', { spawnProcess: h.spawnProcess });
    expect(result.status).toBe('failed');
    expect(result.out).toContain('executable unavailable');
  });

  it('fails a timeout even if the child reports success while being killed', async () => {
    vi.useFakeTimers();
    try {
      const h = childHarness(() => {});
      h.child.kill.mockImplementation(() => h.child.emit('close', 0, null));
      const running = runSmoke('fixture.mjs', { spawnProcess: h.spawnProcess, timeoutMs: 10 });
      await vi.advanceTimersByTimeAsync(10);
      const result = await running;
      expect(result.status).toBe('failed');
      expect(result.out).toContain('timed out');
      expect(h.child.kill).toHaveBeenCalledOnce();
    } finally { vi.useRealTimers(); }
  });
});
