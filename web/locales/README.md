# Localization (`web/locales/`)

Proxion's web client is fully internationalized. Every user-facing string is
externalized into a per-locale JSON file and resolved at runtime by
[`web/i18n.js`](../i18n.js).

## Files

| File | Role |
|------|------|
| `en.json` | **Canonical.** The single source of truth — every key lives here first. |
| `es/de/fr/ar.json` | Shippable drafts (`_meta.draft: true`). May lag `en.json`; missing keys fall back to English per-key. |
| `qps.json` | **Generated, test-only** pseudo-locale. Do not edit by hand — run `npm run gen:pseudo`. Never listed in `SUPPORTED_LOCALES`, so it's only reachable via an explicit `localStorage.proxion_locale = "qps"`. |

## Key conventions

- **Flat dot-keys**, grouped by a module/domain prefix: `toast.*`, `error.*`,
  `error.fr.*` (friend-request codes), `onboarding.*`, `sidebar.*`, `msg.*`,
  `voice.*`, `file.*`, `modal.*`, `kbd.*`, `nat.*`, `pod.*`, `conn.*`, `ui.*`
  (static HTML attributes), `heading.*` / `label.* `/ `btn.*` (static text
  nodes), `time.*`.
- **Interpolation**: `{name}` placeholders, filled by `t('key', { name })`.
- **Plurals**: keys end in `.one` / `.other` (etc.) and are selected by
  `tn('key', count)` via `Intl.PluralRules`.

## The no-markup rule (security)

`t()` / `tn()` return **plain text**. Callers escape at the HTML sink exactly as
they did before i18n — the translation layer must never become an
HTML-injection path. Therefore **a locale value may not contain markup**: any
value containing `<` fails `i18n_check`. When a surface genuinely needs markup
(e.g. the NAT-warning banner), compose it in JS — keep the HTML skeleton in
code and interpolate plain-text `t()` fragments (see `status-banners.js`).

## Adding or updating a locale

1. Add the language to `LOCALE_META` in `web/i18n.js` (code → `{ name, draft }`;
   `name` is the endonym, e.g. `"Deutsch"`). It appears in the settings picker
   automatically.
2. Create/extend `web/locales/<code>.json`. Copy keys from `en.json`; leave out
   any you haven't translated (they fall back to English).
3. Add `<code>.json` to the `SHELL` list in `web/sw.js` and bump `CACHE`.
4. Run the checks below.

## Checks

```
npm run check:i18n     # keys referenced ⇔ present; no markup; qps fresh
npm run gen:pseudo     # regenerate qps.json after any en.json change
node web/scripts/gen_pseudo_locale.mjs --check   # CI freshness gate
npm run smoke:pseudo   # runtime: no un-externalized strings, no overflow, RTL
```

`i18n_check` **fails** on a referenced key missing from `en.json`, a locale
value containing markup, or a stale `qps.json`; it **warns** on dead keys,
non-`en` gaps, and any remaining hardcoded sink strings.
