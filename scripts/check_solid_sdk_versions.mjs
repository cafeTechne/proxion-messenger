#!/usr/bin/env node
/**
 * Build gate: validate Solid SDK dependency policy.
 *
 * Reads web/package.json, parses the solidSdkPolicy section, and verifies:
 *   1. All required packages are declared.
 *   2. No forbidden packages are present.
 *   3. Declared versions satisfy the allowed range.
 *
 * Writes machine-readable results to artifacts/solid-sdk-check.json.
 * Exits non-zero on any violation.
 *
 * Usage:
 *   node scripts/check_solid_sdk_versions.mjs
 *   npm run check:solid-sdk   (from web/)
 */

import { readFileSync, mkdirSync, writeFileSync } from "fs";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, "..");
const webDir = resolve(repoRoot, "web");

function loadJson(filePath) {
  try {
    return JSON.parse(readFileSync(filePath, "utf8"));
  } catch (err) {
    console.error(`Failed to read ${filePath}: ${err.message}`);
    process.exit(1);
  }
}

const pkg = loadJson(resolve(webDir, "package.json"));
const policy = pkg.proxion?.solidSdkPolicy;

if (!policy) {
  console.error("ERROR: package.json missing proxion.solidSdkPolicy section");
  process.exit(1);
}

const allDeclared = {
  ...pkg.dependencies,
  ...pkg.devDependencies,
  ...(pkg.peerDependencies ?? {}),
};

const violations = [];
const results = {
  required: {},
  forbidden: {},
  rangeChecks: {},
  passed: true,
};

// 1. Required packages
for (const req of policy.required ?? []) {
  if (!allDeclared[req]) {
    violations.push(`MISSING required package: ${req}`);
    results.required[req] = { status: "missing" };
  } else {
    results.required[req] = { status: "present", version: allDeclared[req] };
  }
}

// 2. Forbidden packages
for (const forbidden of policy.forbidden ?? []) {
  if (allDeclared[forbidden]) {
    violations.push(`FORBIDDEN package present: ${forbidden} (${allDeclared[forbidden]})`);
    results.forbidden[forbidden] = { status: "present", version: allDeclared[forbidden] };
  } else {
    results.forbidden[forbidden] = { status: "absent" };
  }
}

// 3. Range checks (simple semver prefix validation)
for (const [pkg_name, minRange] of Object.entries(policy.allowedRanges ?? {})) {
  const declared = allDeclared[pkg_name];
  if (!declared) continue;
  const minMajor = parseInt(minRange.replace(/[^0-9]/, ""), 10);
  const declaredMajor = parseInt(declared.replace(/[^0-9]/, ""), 10);
  if (isNaN(minMajor) || isNaN(declaredMajor)) {
    results.rangeChecks[pkg_name] = { status: "unknown", declared, minRange };
    continue;
  }
  if (declaredMajor < minMajor) {
    violations.push(`RANGE VIOLATION: ${pkg_name} declares ${declared}, need ${minRange}`);
    results.rangeChecks[pkg_name] = { status: "violation", declared, minRange };
  } else {
    results.rangeChecks[pkg_name] = { status: "ok", declared, minRange };
  }
}

results.violations = violations;
results.passed = violations.length === 0;

// Write machine-readable report
const artifactsDir = resolve(repoRoot, "artifacts");
mkdirSync(artifactsDir, { recursive: true });
const reportPath = resolve(artifactsDir, "solid-sdk-check.json");
writeFileSync(reportPath, JSON.stringify(results, null, 2));
console.log(`Solid SDK check report written to ${reportPath}`);

if (violations.length > 0) {
  console.error("\nSOLID SDK POLICY VIOLATIONS:");
  for (const v of violations) console.error(`  ✗ ${v}`);
  process.exit(1);
} else {
  console.log("✓ All Solid SDK dependency policy checks passed.");
  process.exit(0);
}
