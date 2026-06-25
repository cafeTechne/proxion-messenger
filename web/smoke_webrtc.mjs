// Automated WebRTC media smoke test — replaces the manual "does a voice call
// actually work" browser check for the parts nothing else covers.
//
// WHAT THIS COVERS (and why it's a loopback, not a two-peer call):
// Proxion is one-gateway-per-user (a user's address IS their gateway's DID), so
// two distinct identities require two federated gateways — too heavy/fragile for
// a smoke test. But the thing the manual test really guards is the WebRTC *media
// path* in the deployed environment: that the served page's CSP permits
// RTCPeerConnection, that getUserMedia works, and that ICE/DTLS negotiate and
// carry a live media track. We validate all of that by running a real
// peer-connection loopback (two RTCPeerConnections + fake mic) INSIDE the actual
// served app page, so its real CSP/origin/secure-context apply. The app's own
// offer/answer/ICE signaling glue is covered by voice.js unit tests; a full
// cross-gateway live call between two installs remains a brief manual check.
//
// Prereq: gateway running (python run_gateway.py). Chrome/Edge installed.
// Usage:  node web/smoke_webrtc.mjs   [PROXION_SMOKE_URL=... PROXION_CHROME=...]
// Exit 0 = media connected over a real PeerConnection; 1 = failed.

import { existsSync } from 'fs';
import puppeteer from 'puppeteer-core';

const URL = process.env.PROXION_SMOKE_URL || 'https://localhost:8080/';
const CHROME = [
  process.env.PROXION_CHROME,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/google-chrome', '/usr/bin/chromium',
].filter(Boolean).find(p => existsSync(p));
if (!CHROME) { console.error('No Chrome/Edge found; set PROXION_CHROME.'); process.exit(2); }

// Runs in the page: a real RTCPeerConnection loopback with a fake mic track.
async function loopback() {
  const result = { gum: false, iceA: '', iceB: '', remoteTracks: 0, error: '' };
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    result.gum = stream.getAudioTracks().length > 0;

    const pcA = new RTCPeerConnection();
    const pcB = new RTCPeerConnection();
    pcA.onicecandidate = (e) => { if (e.candidate) pcB.addIceCandidate(e.candidate).catch(() => {}); };
    pcB.onicecandidate = (e) => { if (e.candidate) pcA.addIceCandidate(e.candidate).catch(() => {}); };

    const gotTrack = new Promise((resolve) => {
      pcB.ontrack = (e) => { result.remoteTracks++; if (e.track) resolve(); };
    });

    stream.getTracks().forEach((t) => pcA.addTrack(t, stream));

    const offer = await pcA.createOffer();
    await pcA.setLocalDescription(offer);
    await pcB.setRemoteDescription(offer);
    const answer = await pcB.createAnswer();
    await pcB.setLocalDescription(answer);
    await pcA.setRemoteDescription(answer);

    let aOk = false, bOk = false;
    const isUp = (s) => s === 'connected' || s === 'completed';
    const connected = new Promise((resolve, reject) => {
      const check = (pc, who) => () => {
        const s = pc.iceConnectionState;
        result['ice' + who] = s;
        if (isUp(s)) { if (who === 'A') aOk = true; else bOk = true; }
        if (s === 'failed') reject(new Error('ICE failed on ' + who));
        if (aOk && bOk) resolve();   // require BOTH peers up
      };
      pcA.oniceconnectionstatechange = check(pcA, 'A');
      pcB.oniceconnectionstatechange = check(pcB, 'B');
    });

    await Promise.race([
      Promise.all([connected, gotTrack]),
      new Promise((_, rej) => setTimeout(() => rej(new Error('timeout: ICE/track did not complete')), 15000)),
    ]);
    result.iceA = pcA.iceConnectionState;
    result.iceB = pcB.iceConnectionState;
    pcA.close(); pcB.close();
    stream.getTracks().forEach((t) => t.stop());
  } catch (e) {
    result.error = String(e && e.message || e);
  }
  return result;
}

let browser;
try {
  browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new',
    args: [
      '--ignore-certificate-errors', '--no-sandbox', '--disable-gpu',
      '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream',
      '--autoplay-policy=no-user-gesture-required',
    ],
  });
  const page = await browser.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message));

  // Load the REAL served app page so its CSP / origin / secure-context apply.
  await page.goto(URL, { waitUntil: 'load', timeout: 20000 });
  await new Promise((r) => setTimeout(r, 1500));

  const r = await page.evaluate(loopback);
  console.log(`Loaded ${URL}`);
  console.log('getUserMedia (fake mic):', r.gum);
  console.log('ICE states:', `A=${r.iceA || '(none)'} B=${r.iceB || '(none)'}`);
  console.log('remote media tracks received:', r.remoteTracks);
  if (r.error) console.log('error:', r.error);
  if (pageErrors.length) console.log('page errors:', pageErrors.slice(0, 3));

  const connected = (r.iceA === 'connected' || r.iceA === 'completed') &&
                    (r.iceB === 'connected' || r.iceB === 'completed');
  if (r.gum && connected && r.remoteTracks > 0 && !r.error) {
    console.log('\n✓ WebRTC media path works in the served app environment ' +
                '(getUserMedia + RTCPeerConnection + ICE + live remote track).');
    process.exitCode = 0;
  } else {
    console.error('\n✗ WebRTC media smoke test FAILED.');
    process.exitCode = 1;
  }
} catch (e) {
  console.error('\n✗ WebRTC media smoke test errored:', e.message);
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
}
