// Run groups of browser smokes one after another and summarize.
//
//   node scripts/run-smokes.mjs ui cross-gateway     # what CI runs
//   node scripts/run-smokes.mjs all                  # every group, incl. pod + visual
//
// Each smoke starts its own gateway(s), so nothing needs to be running first.
// Exit 1 if any smoke fails. Output of a failing smoke is printed in full.
import { spawn } from 'child_process';
import { resolve } from 'path';
import { fileURLToPath } from 'url';

const WEB = resolve(fileURLToPath(new URL('..', import.meta.url)));

const GROUPS = {
  // Single gateway: page boots clean, media path, core journey, rooms, a11y,
  // i18n, first-run setup and settings.
  ui: ['smoke_browser.mjs', 'smoke_webrtc.mjs', 'smoke_journey.mjs', 'smoke_rooms.mjs', 'smoke_a11y.mjs',
       'smoke_keyboard.mjs', 'smoke_pseudo.mjs', 'smoke_firstrun.mjs'],
  // Two gateways talking to each other: friend requests, E2E DMs, reactions,
  // linked devices, calls.
  'cross-gateway': ['smoke_federation.mjs', 'smoke_dm_history.mjs', 'smoke_dm_reaction.mjs',
                    'smoke_multidevice_dm.mjs', 'smoke_call.mjs', 'smoke_pairing.mjs'],
  // The gateway-less web build against a local Solid server (fetched with npx).
  pod: ['smoke_web_nogw.mjs', 'smoke_web_signedin.mjs', 'smoke_web_join.mjs'],
  // Pixel baselines depend on the machine's font rendering, so local only.
  visual: ['smoke_visual.mjs'],
};

const names = process.argv.slice(2);
const wanted = names.includes('all') ? Object.keys(GROUPS) : names;
const unknown = wanted.filter((g) => !GROUPS[g]);
if (!wanted.length || unknown.length) {
  console.error(`usage: run-smokes.mjs <group...>  groups: ${Object.keys(GROUPS).join(', ')}, all`);
  process.exit(2);
}

const TIMEOUT_MS = 6 * 60 * 1000;
function run(file) {
  return new Promise((res) => {
    const started = Date.now();
    const p = spawn(process.execPath, [file], { cwd: WEB, env: process.env });
    let out = '';
    p.stdout.on('data', (d) => { out += d; });
    p.stderr.on('data', (d) => { out += d; });
    const timer = setTimeout(() => { out += `\n(timed out after ${TIMEOUT_MS / 1000}s)`; p.kill(); }, TIMEOUT_MS);
    p.on('close', (code) => {
      clearTimeout(timer);
      res({ file, ok: code === 0, secs: Math.round((Date.now() - started) / 1000), out });
    });
  });
}

const results = [];
for (const group of wanted) {
  for (const file of GROUPS[group]) {
    process.stdout.write(`${group.padEnd(14)} ${file.padEnd(28)} `);
    const r = await run(file);
    results.push(r);
    console.log(`${r.ok ? 'pass' : 'FAIL'}  ${r.secs}s`);
    if (!r.ok) console.log(r.out.split('\n').map((l) => '    | ' + l).join('\n'));
  }
}
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} smokes passed.`);
if (failed.length) {
  console.log('Failed: ' + failed.map((r) => r.file).join(', '));
  process.exitCode = 1;
}
