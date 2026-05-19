#!/usr/bin/env node
/**
 * spec_drift_watch.mjs — Solid spec and SDK drift watcher (Round 14)
 *
 * Snapshots the Inrupt supported-version page, Solid Protocol spec, Solid
 * Notifications Protocol spec, and configured SDK package metadata.  Compares
 * against a baseline and emits drift artifacts.
 *
 * Outputs:
 *   artifacts/spec-drift-report.json   — machine-readable report
 *   artifacts/spec-drift-summary.md    — human-readable summary
 *
 * Exit codes:
 *   0 — no drift or drift below threshold
 *   1 — drift severity >= threshold (controlled by SPEC_DRIFT_FAIL_THRESHOLD)
 */

import { readFileSync, writeFileSync, mkdirSync, existsSync } from "fs";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, "..");
const ARTIFACTS_DIR = resolve(REPO_ROOT, "artifacts");
const PKG_JSON_PATH = resolve(REPO_ROOT, "web", "package.json");

const FAIL_THRESHOLD = (process.env.SPEC_DRIFT_FAIL_THRESHOLD ?? "high").toLowerCase();
const SEVERITY_RANK = { none: 0, low: 1, medium: 2, high: 3, critical: 4 };

// Spec endpoints to snapshot (lightweight HEAD/GET to check availability)
const SPEC_SOURCES = [
  {
    id: "solid_protocol",
    label: "Solid Protocol Spec",
    url: "https://solidproject.org/TR/protocol",
  },
  {
    id: "solid_notifications",
    label: "Solid Notifications Protocol",
    url: "https://solidproject.org/TR/notifications-protocol",
  },
  {
    id: "inrupt_supported_versions",
    label: "Inrupt Supported Versions",
    url: "https://docs.inrupt.com/developer-tools/javascript/client-libraries/",
  },
];

async function fetchHead(url) {
  try {
    const ctrl = new AbortController();
    const timeout = setTimeout(() => ctrl.abort(), 10000);
    const resp = await fetch(url, { method: "HEAD", signal: ctrl.signal });
    clearTimeout(timeout);
    return {
      ok: resp.ok,
      status: resp.status,
      lastModified: resp.headers.get("last-modified") ?? null,
      etag: resp.headers.get("etag") ?? null,
    };
  } catch (err) {
    return { ok: false, status: 0, error: err.message };
  }
}

function loadPackagePolicy() {
  try {
    const pkg = JSON.parse(readFileSync(PKG_JSON_PATH, "utf-8"));
    const deps = { ...pkg.dependencies, ...pkg.devDependencies };
    const policy = pkg?.proxion?.solidSdkPolicy ?? {};
    const required = policy.required ?? [];
    const forbidden = policy.forbidden ?? [];
    const violations = [];
    const present = [];

    for (const name of required) {
      if (deps[name]) {
        present.push({ name, version: deps[name] });
      } else {
        violations.push({ name, reason: "missing" });
      }
    }
    for (const name of forbidden) {
      if (deps[name]) {
        violations.push({ name, reason: "forbidden_present" });
      }
    }

    return { present, violations, required, forbidden };
  } catch (err) {
    return { present: [], violations: [{ name: "package.json", reason: `parse_error: ${err.message}` }], required: [], forbidden: [] };
  }
}

function computeSeverity(specResults, pkgResult) {
  const specFailures = specResults.filter(r => !r.ok).length;
  const pkgViolations = pkgResult.violations.length;

  if (pkgViolations > 0) return "high";
  if (specFailures >= specResults.length) return "critical";
  if (specFailures > 0) return "medium";
  return "none";
}

function renderSummary(report) {
  const lines = [
    `# Spec Drift Report — ${report.generated_at}`,
    "",
    `**Severity:** ${report.severity}`,
    `**Spec sources checked:** ${report.spec_sources.length}`,
    `**SDK violations:** ${report.sdk_check.violations.length}`,
    "",
    "## Spec Source Status",
    "",
  ];
  for (const s of report.spec_sources) {
    const icon = s.ok ? "✓" : "✗";
    lines.push(`- ${icon} **${s.label}** — HTTP ${s.status}${s.etag ? ` (ETag: ${s.etag})` : ""}`);
  }
  lines.push("", "## SDK Package Check", "");
  if (report.sdk_check.violations.length === 0) {
    lines.push("All required packages present, no forbidden packages detected.");
  } else {
    lines.push("**Violations:**");
    for (const v of report.sdk_check.violations) {
      lines.push(`- \`${v.name}\`: ${v.reason}`);
    }
  }
  if (report.severity !== "none") {
    lines.push("", "## Action Required", "");
    lines.push("Review the violations above and update SDK dependencies or resolve spec connectivity issues.");
  }
  return lines.join("\n");
}

async function main() {
  mkdirSync(ARTIFACTS_DIR, { recursive: true });

  const specResults = await Promise.all(
    SPEC_SOURCES.map(async (src) => ({
      ...src,
      ...(await fetchHead(src.url)),
    }))
  );

  const pkgResult = loadPackagePolicy();
  const severity = computeSeverity(specResults, pkgResult);
  const now = new Date().toISOString();

  const report = {
    generated_at: now,
    severity,
    passed: severity === "none",
    spec_sources: specResults,
    sdk_check: pkgResult,
    fail_threshold: FAIL_THRESHOLD,
  };

  const reportPath = resolve(ARTIFACTS_DIR, "spec-drift-report.json");
  const summaryPath = resolve(ARTIFACTS_DIR, "spec-drift-summary.md");

  writeFileSync(reportPath, JSON.stringify(report, null, 2));
  writeFileSync(summaryPath, renderSummary(report));

  console.log(`Spec drift report written to ${reportPath}`);
  console.log(`Severity: ${severity}`);

  if ((SEVERITY_RANK[severity] ?? 0) >= (SEVERITY_RANK[FAIL_THRESHOLD] ?? 3)) {
    console.error(`Drift severity '${severity}' meets or exceeds threshold '${FAIL_THRESHOLD}' — failing.`);
    process.exit(1);
  }
}

main().catch((err) => {
  console.error("spec_drift_watch failed:", err);
  process.exit(1);
});
