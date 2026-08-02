// devices.js — media device enumeration helpers (R81 R1). Pure, no I/O.

/** Split enumerateDevices() output into cameras and microphones. */
export function mapDevices(infos) {
    const cameras = [];
    const mics = [];
    for (const d of infos || []) {
        if (!d) continue;
        if (d.kind === 'videoinput') cameras.push({ id: d.deviceId || '', label: d.label || '' });
        else if (d.kind === 'audioinput') mics.push({ id: d.deviceId || '', label: d.label || '' });
    }
    return { cameras, mics };
}

/** The saved device if still present, else the first available, else ''. */
export function preferredDeviceId(list, savedId) {
    if (savedId && (list || []).some(d => d.id === savedId)) return savedId;
    return (list && list[0] && list[0].id) || '';
}

/** getUserMedia constraints honoring a chosen device id (empty = default device). */
export function mediaConstraints({ cameraId = '', micId = '', video = false } = {}) {
    const audio = micId
        ? { deviceId: { exact: micId }, noiseSuppression: true, echoCancellation: true, autoGainControl: true }
        : { noiseSuppression: true, echoCancellation: true, autoGainControl: true };
    if (!video) return { audio, video: false };
    return { audio, video: cameraId ? { deviceId: { exact: cameraId } } : true };
}
