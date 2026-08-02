// callquality.js — video quality profiles and the per-sender bitrate/framerate cap.
// Pure, no I/O. Every value is a CEILING; WebRTC still adapts down under congestion.
//
// The cap matters most in a group mesh: you send your camera to every other
// participant, so your uplink carries (participants - 1) copies. We divide the budget
// so a bigger call does not saturate the link, while 1:1 and small calls stay crisp.

// Face-camera ceilings per profile.
export const QUALITY_PROFILES = Object.freeze({
    high: { maxBitrate: 2_500_000, maxFramerate: 30, maxHeight: 720 },
    standard: { maxBitrate: 1_200_000, maxFramerate: 30, maxHeight: 540 },
    saver: { maxBitrate: 300_000, maxFramerate: 20, maxHeight: 360 },
});

// Screen share wants resolution for text but tolerates a low framerate, the opposite
// trade-off from a face.
export const SCREEN_PROFILES = Object.freeze({
    high: { maxBitrate: 3_000_000, maxFramerate: 8, maxHeight: 1080 },
    standard: { maxBitrate: 1_500_000, maxFramerate: 6, maxHeight: 900 },
    saver: { maxBitrate: 600_000, maxFramerate: 4, maxHeight: 720 },
});

export const QUALITY_CHOICES = Object.freeze(['auto', 'high', 'standard', 'saver']);

// Never drop a sender below this, or video becomes unusable.
const BITRATE_FLOOR = 150_000;

/** Resolve 'auto' to a concrete profile: generous for 1:1, moderate for a group. */
export function resolveProfile(name, isGroup) {
    if (name === 'high' || name === 'standard' || name === 'saver') return name;
    return isGroup ? 'standard' : 'high';   // auto
}

/**
 * The encoding cap for one sender given the chosen profile and call context.
 * `kind` is 'camera' or 'screen'. Group calls divide the bitrate budget across the
 * other participants (with a floor); 1:1 keeps the full profile ceiling.
 * Returns { maxBitrate, maxFramerate, maxHeight }.
 */
export function senderCap({ profile = 'auto', isGroup = false, participantCount = 2, kind = 'camera' } = {}) {
    const table = kind === 'screen' ? SCREEN_PROFILES : QUALITY_PROFILES;
    const p = table[resolveProfile(profile, isGroup)];
    let maxBitrate = p.maxBitrate;
    if (isGroup) {
        const others = Math.max(1, (participantCount || 2) - 1);
        maxBitrate = Math.max(BITRATE_FLOOR, Math.round(maxBitrate / others));
    }
    return { maxBitrate, maxFramerate: p.maxFramerate, maxHeight: p.maxHeight };
}
