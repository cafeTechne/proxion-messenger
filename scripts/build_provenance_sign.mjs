#!/usr/bin/env node
/**
 * R15: Build provenance manifest generator.
 *
 * Hashes critical runtime files, records git commit + build metadata,
 * writes artifacts/provenance.json + artifacts/provenance.sig.
 *
 * Usage:
 *   node scripts/build_provenance_sign.mjs
 *
 * Env:
 *   PROXION_PROVENANCE_FILES  — colon-separated list of paths relative to repo root
 *                               (default: the list below)
 */

import { createHash } from "crypto";
import { execSync } from "child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "fs";
import { join, resolve } from "path";

const REPO_ROOT = resolve(new URL(".", import.meta.url).pathname, "..");

const DEFAULT_PROTECTED_FILES = [
  "proxion-core/src/proxion_messenger_core/gateway.py",
  "proxion-core/src/proxion_messenger_core/security_policy.py",
  "proxion-core/src/proxion_messenger_core/local_store.py",
  "proxion-core/src/proxion_messenger_core/solid_client.py",
  "web/src/solid/notifications_adapter.js",
  "web/src/solid/access_grants_adapter.js",
  "web/main.js",
];

function sha256File(absPath) {
  try {
    const data = readFileSync(absPath);
    return createHash("sha256").update(data).digest("hex");
  } catch {
    return null;
  }
}

function getGitCommit() {
  try {
    return execSync("git rev-parse HEAD", { cwd: REPO_ROOT, encoding: "utf8" }).trim();
  } catch {
    return "unknown";
  }
}

function getToolchainVersions() {
  const versions = {};
  try {
    versions.node = process.version;
  } catch {}
  try {
    versions.python = execSync("python --version", { encoding: "utf8" }).trim();
  } catch {}
  return versions;
}

const fileList = process.env.PROXION_PROVENANCE_FILES
  ? process.env.PROXION_PROVENANCE_FILES.split(":")
  : DEFAULT_PROTECTED_FILES;

const files = {};
for (const rel of fileList) {
  const abs = join(REPO_ROOT, rel);
  const hash = sha256File(abs);
  if (hash !== null) {
    files[rel] = hash;
  } else {
    console.warn(`[provenance] Warning: could not hash ${rel}`);
  }
}

const manifest = {
  commit: getGitCommit(),
  built_at: new Date().toISOString(),
  toolchain: getToolchainVersions(),
  files,
};

const artifactsDir = join(REPO_ROOT, "artifacts");
mkdirSync(artifactsDir, { recursive: true });

const manifestJson = JSON.stringify(manifest, null, 2);
const manifestPath = join(artifactsDir, "provenance.json");
const sigPath = join(artifactsDir, "provenance.sig");

writeFileSync(manifestPath, manifestJson, "utf8");

const sig = createHash("sha256").update(manifestJson).digest("hex");
writeFileSync(sigPath, sig, "utf8");

console.log(`[provenance] Manifest written to ${manifestPath}`);
console.log(`[provenance] Signature: ${sig.slice(0, 16)}...`);
console.log(`[provenance] Files hashed: ${Object.keys(files).length}`);
