// Run groups of browser smokes one after another and summarize.
//
//   node scripts/run-smokes.mjs ui cross-gateway     # what CI runs
//   node scripts/run-smokes.mjs pod-required         # required live-pod room journeys
//   node scripts/run-smokes.mjs all                  # every group, incl. pod + visual
//
// Each smoke starts its own gateway(s), so nothing needs to be running first.
// Exit 1 if any smoke fails. Output of a failing smoke is printed in full.
import { spawn } from 'child_process';
import { resolve } from 'path';
import { fileURLToPath } from 'url';
import { SMOKE_SKIP_EXIT } from './smoke-status.mjs';

const WEB = resolve(fileURLToPath(new URL('..', import.meta.url)));

export const GROUPS = {
  // Single gateway: page boots clean, media path, core journey, rooms, a11y,
  // i18n, first-run setup and settings, the service worker deploy check, and
  // claiming a gateway that requires sign-in.
  ui: ['smoke_browser.mjs', 'smoke_webrtc.mjs', 'smoke_journey.mjs', 'smoke_rooms.mjs', 'smoke_voice_channel.mjs', 'smoke_media_context.mjs', 'smoke_a11y.mjs',
       'smoke_keyboard.mjs', 'smoke_pseudo.mjs', 'smoke_firstrun.mjs', 'smoke_recovery.mjs', 'smoke_sw_update.mjs', 'smoke_owner_claim.mjs'],
  // Two gateways talking to each other: friend requests, E2E DMs, reactions,
  // linked devices, calls, two tabs of one account.
  'cross-gateway': ['smoke_federation.mjs', 'smoke_dm_history.mjs', 'smoke_dm_reaction.mjs',
                    'smoke_multidevice_dm.mjs', 'smoke_call.mjs', 'smoke_pairing.mjs', 'smoke_tabs.mjs'],
  // The gateway-less web build against a local Solid server (fetched with npx).
  pod: ['smoke_web_nogw.mjs', 'smoke_web_signedin.mjs', 'smoke_web_join.mjs', 'smoke_web_join_link.mjs',
        'smoke_room_attachments.mjs', 'smoke_dm_attachments.mjs', 'smoke_asset_inventory.mjs', 'smoke_web_dm.mjs', 'smoke_web_friends.mjs', 'smoke_web_account.mjs', 'smoke_web_rooms.mjs', 'smoke_web_session.mjs',
        'smoke_cross_mode_dm.mjs', 'smoke_cross_mode_rooms.mjs', 'smoke_cross_mode_call.mjs'],
  // A required subset: never pass CI without exercising real pod membership.
  'pod-required': ['smoke_room_attachments.mjs', 'smoke_dm_attachments.mjs', 'smoke_web_rooms.mjs', 'smoke_web_friends.mjs', 'smoke_cross_mode_rooms.mjs', 'smoke_web_join_link.mjs'],
  // ACP is a separate required target, never inferred from the default WAC pod.
  'pod-acp': ['smoke_pod_access.mjs', 'smoke_room_attachments.mjs', 'smoke_dm_attachments.mjs', 'smoke_cross_mode_dm.mjs', 'smoke_cross_mode_rooms.mjs'],
  // Pixel baselines depend on the machine's font rendering, so local only.
  visual: ['smoke_visual.mjs'],
};

export function selectGroups(names) {
  // The required subset is already covered by pod; don't run it twice for all.
  const wanted = names.includes('all') ? Object.keys(GROUPS).filter((g) => g !== 'pod-required') : names;
  if (!wanted.length || wanted.some((g) => !GROUPS[g])) throw new Error(
    `usage: run-smokes.mjs <group...>  groups: ${Object.keys(GROUPS).join(', ')}, all`);
  return [...new Set(wanted)];
}

const TIMEOUT_MS = 10 * 60 * 1000;   // the two-direction pod room smoke alone runs about 6 minutes
export function classifySmokeResult({ code, out = '', timedOut = false, error = null, signal = null }) {
  if (timedOut || error || signal || (code !== 0 && code !== SMOKE_SKIP_EXIT)) return 'failed';
  // Also recognize older self-skipping scripts, including partial-tier skips.
  if (code === SMOKE_SKIP_EXIT || /^\s*SMOKE SKIPPED\b/m.test(out)
      || /^\s*(?:[–-]\s*)?(?:Tier \d+ )?skipped:/im.test(out)) return 'skipped';
  return 'passed';
}

export function runSmoke(file, { required = false, spawnProcess = spawn, timeoutMs = TIMEOUT_MS, env = process.env } = {}) {
  return new Promise((res) => {
    const started = Date.now();
    let out = '';
    let timedOut = false, finished = false, timer;
    const finish = (code, signal = null, error = null) => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      if (error) out += `\n${error.message}`;
      res({ file, status: classifySmokeResult({ code, signal, out, timedOut, error }),
        required, secs: Math.round((Date.now() - started) / 1000), out });
    };
    let p;
    try {
      p = spawnProcess(process.execPath, [file], { cwd: WEB,
        env: { ...env, ...(required ? { PROXION_SMOKE_REQUIRED: '1' } : {}) } });
    } catch (error) { finish(null, null, error); return; }
    p.stdout?.on('data', (d) => { out += d; });
    p.stderr?.on('data', (d) => { out += d; });
    p.on('error', (error) => finish(null, null, error));
    p.on('close', (code, signal) => finish(code, signal));
    timer = setTimeout(() => {
      timedOut = true;
      out += `\n(timed out after ${timeoutMs / 1000}s)`;
      p.kill();
      // A stuck child must not keep the runner waiting for its close event.
      finish(null);
    }, timeoutMs);
  });
}

export async function runGroups(wanted, { run = runSmoke, write = (s) => process.stdout.write(s),
  required = process.env.PROXION_SMOKE_REQUIRED === '1' } = {}) {
  const results = [];
  for (const group of wanted) {
    for (const file of GROUPS[group]) {
      write(`${group.padEnd(14)} ${file.padEnd(28)} `);
      const r = await run(file, { required: required || group === 'pod-required' || group === 'pod-acp',
        ...(group === 'pod-acp' ? { env: { ...process.env, PROXION_CSS_ACP: '1' } } : {}) });
      results.push(r);
      const label = r.status === 'passed' ? 'pass' : (r.status === 'skipped' ? 'SKIP' : 'FAIL');
      write(`${label}${r.required && r.status === 'skipped' ? ' (required)' : ''}  ${r.secs}s\n`);
      if (r.status !== 'passed') write(r.out.split('\n').map((l) => '    | ' + l).join('\n') + '\n');
    }
  }
  const passed = results.filter((r) => r.status === 'passed').length;
  const skipped = results.filter((r) => r.status === 'skipped').length;
  const failures = results.filter((r) => r.status === 'failed' || (r.required && r.status === 'skipped'));
  write(`\n${passed} passed, ${skipped} skipped, ${results.length - passed - skipped} failed.\n`);
  if (failures.length) write('Failed or required checks skipped: ' + failures.map((r) => r.file).join(', ') + '\n');
  return { results, exitCode: failures.length ? 1 : 0 };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { process.exitCode = (await runGroups(selectGroups(process.argv.slice(2)))).exitCode; }
  catch (error) { console.error(error.message); process.exitCode = 2; }
}
