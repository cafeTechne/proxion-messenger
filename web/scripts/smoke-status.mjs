// Prerequisites that are optional locally must be explicit in runner results.
// CI sets PROXION_SMOKE_REQUIRED=1 so an unavailable dependency fails the gate.
export const SMOKE_SKIP_EXIT = 77;

export function smokeSkipExit({ required = process.env.PROXION_SMOKE_REQUIRED === '1', failed = false } = {}) {
  return required || failed ? 1 : SMOKE_SKIP_EXIT;
}

export function reportSmokeSkip(reason) {
  console.log(`SMOKE SKIPPED: ${reason}`);
  process.exitCode = smokeSkipExit({ failed: !!process.exitCode && process.exitCode !== SMOKE_SKIP_EXIT });
  return process.exitCode;
}

export function skipSmoke(reason) {
  process.exit(reportSmokeSkip(reason));
}
