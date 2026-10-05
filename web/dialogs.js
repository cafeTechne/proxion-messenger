// dialogs.js: the shared confirm and prompt dialogs, plus overlay and header
// close-button dismissal for dialogs marked data-dismissable.
//
// showConfirm(message, onConfirm, onCancel, opts) fills the static
// #confirm-modal in index.html. opts:
//   title         heading text (default "Are you sure?")
//   confirmLabel  verb for the primary button (default "Confirm")
//   cancelLabel   label for the secondary button (default "Cancel")
//   danger        true paints the primary button as a solid destructive action
//   checkLabel    when set, shows a checkbox that must be ticked before the
//                 primary button enables (e.g. "I have a backup")
//
// showPromptModal(message, opts) resolves the entered string, or null on
// cancel/Escape. It builds #prompt-modal on first use.
//
// `doc` is injectable for tests (default: the page document).
import { t } from './i18n.js';

export function showConfirm(message, onConfirm, onCancel, opts = {}, doc = document) {
    const { title, confirmLabel, cancelLabel, danger = false, checkLabel } = opts || {};
    const $ = (id) => doc.getElementById(id);
    const modal = $('confirm-modal');
    const okBtn = $('confirm-ok');
    const cancelBtn = $('confirm-cancel');
    if (!modal || !okBtn || !cancelBtn) return;
    const titleEl = $('confirm-title');
    if (titleEl) titleEl.textContent = title || t('confirm.title');
    const msgEl = $('confirm-msg');
    if (msgEl) msgEl.textContent = message;
    okBtn.textContent = confirmLabel || t('btn.confirm');
    cancelBtn.textContent = cancelLabel || t('btn.cancel');
    okBtn.classList.toggle('btn--danger', !!danger);
    okBtn.classList.toggle('btn--accent', !danger);

    const checkRow = $('confirm-check-row');
    const check = $('confirm-check');
    const checkText = $('confirm-check-label');
    if (checkRow) checkRow.hidden = !checkLabel;
    if (checkText) checkText.textContent = checkLabel || '';
    if (check) {
        check.checked = false;
        check.onchange = () => { okBtn.disabled = !check.checked; };
    }
    okBtn.disabled = !!(checkLabel && check);

    const close = () => { modal.style.display = 'none'; };
    cancelBtn.onclick = () => { close(); if (onCancel) onCancel(); };
    okBtn.onclick = () => {
        if (okBtn.disabled) return;
        close();
        if (onConfirm) onConfirm();
    };
    modal.style.display = 'flex';
}

function _buildPromptModal(doc) {
    const modal = doc.createElement('div');
    modal.id = 'prompt-modal';
    modal.className = 'modal modal--stacked';
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.setAttribute('aria-labelledby', 'prompt-title');
    modal.style.display = 'none';
    modal.innerHTML =
        '<div class="modal__panel modal__panel--sm">' +
        '<form id="prompt-form" novalidate>' +
        '<h3 id="prompt-title" class="modal__title"></h3>' +
        '<label id="prompt-msg" for="prompt-input" class="field-label"></label>' +
        '<input id="prompt-input" class="input" autocomplete="off">' +
        '<div class="btn-row modal__footer">' +
        '<button type="button" id="prompt-cancel" class="btn btn--slate btn--lg" data-modal-cancel></button>' +
        '<button type="submit" id="prompt-ok" class="btn btn--accent btn--lg"></button>' +
        '</div></form></div>';
    doc.body.appendChild(modal);
    return modal;
}

export function showPromptModal(message, opts = {}, doc = document) {
    const { type = 'text', placeholder = '', title = '', confirmLabel = '', value = '' } = opts || {};
    return new Promise((resolve) => {
        const modal = doc.getElementById('prompt-modal') || _buildPromptModal(doc);
        const $ = (id) => doc.getElementById(id);
        const input = $('prompt-input');
        const titleEl = $('prompt-title');
        // Without a separate title the message itself names the dialog.
        titleEl.textContent = title;
        titleEl.hidden = !title;
        modal.setAttribute('aria-labelledby', title ? 'prompt-title' : 'prompt-msg');
        $('prompt-msg').textContent = message;
        $('prompt-cancel').textContent = t('btn.cancel');
        $('prompt-ok').textContent = confirmLabel || t('btn.continue');
        input.type = type; input.placeholder = placeholder; input.value = value;
        const form = $('prompt-form');
        const done = (val) => {
            modal.style.display = 'none';
            input.onkeydown = null;
            form.onsubmit = null;
            $('prompt-cancel').onclick = null;
            resolve(val);
        };
        $('prompt-cancel').onclick = () => done(null);
        // Enter submits the form; this is the only submit path, so it cannot fire twice.
        form.onsubmit = (e) => { e.preventDefault(); done(input.value); };
        input.onkeydown = (e) => {
            if (e.key === 'Escape') { e.stopPropagation(); done(null); }
        };
        modal.style.display = 'flex';
        setTimeout(() => input.focus(), 50);
    });
}

// ---- Dismissal for data-dismissable dialogs ----
//
// A press that starts AND ends on the overlay (not the panel) cancels the
// dialog, so a text selection dragged out of the panel does not close it.
// The header close button (.modal__close) also routes through the dialog's
// own cancel control, so cancel callbacks and cleanup run exactly as they do
// for the footer Cancel/Close button and for Escape.

const defaultShown = (el) => el.offsetParent !== null;

export function cancelDialog(dialog, shown = defaultShown) {
    const cancels = Array.from(dialog.querySelectorAll('[data-modal-cancel]'))
        .filter((b) => !b.classList.contains('modal__close') && shown(b));
    if (cancels.length) cancels[0].click();
    else dialog.style.display = 'none';
}

function isDismissOverlay(el) {
    return !!(el && typeof el.hasAttribute === 'function'
        && el.hasAttribute('data-dismissable') && el.getAttribute('role') === 'dialog');
}

let _dismissInstalled = false;

export function installDialogDismiss(doc = document, shown = defaultShown) {
    if (typeof document !== 'undefined' && doc === document) {
        if (_dismissInstalled) return;
        _dismissInstalled = true;
    }
    let downOn = null;
    doc.addEventListener('mousedown', (e) => {
        downOn = isDismissOverlay(e.target) ? e.target : null;
    });
    doc.addEventListener('mouseup', (e) => {
        const d = downOn;
        downOn = null;
        if (d && e.target === d) cancelDialog(d, shown);
    });
    doc.addEventListener('click', (e) => {
        const x = e.target && e.target.closest ? e.target.closest('.modal__close') : null;
        if (!x) return;
        const dlg = x.closest('[role="dialog"]');
        if (!dlg) return;
        e.preventDefault();
        cancelDialog(dlg, shown);
    });
}

// ---- Inline field errors ----
//
// The error <p>s under modal inputs are role=alert and named by the input's
// aria-describedby. fieldInvalidState decides aria-invalid from the error
// element; installFieldErrorSync keeps it in step with whatever code writes
// the error text (many call sites set errEl.textContent directly).

// A non-empty message is an error unless the code styled it as a success
// note (add-peer shows "Found ..." in the same slot with an inline color).
export function fieldInvalidState(errEl) {
    return !!(errEl && (errEl.textContent || '').trim() && !errEl.style.color);
}

function _syncInvalid(doc, errEl) {
    const inputs = doc.querySelectorAll(`[aria-describedby~="${errEl.id}"]`);
    const bad = fieldInvalidState(errEl);
    inputs.forEach((inp) => {
        if (bad) inp.setAttribute('aria-invalid', 'true');
        else inp.removeAttribute('aria-invalid');
    });
}

export function installFieldErrorSync(doc = document) {
    if (typeof MutationObserver === 'undefined') return;
    doc.querySelectorAll('.field-error[id]').forEach((errEl) => {
        if (errEl._fieldSync) return;
        errEl._fieldSync = true;
        new MutationObserver(() => _syncInvalid(doc, errEl))
            .observe(errEl, { childList: true, characterData: true, subtree: true, attributes: true, attributeFilter: ['style'] });
        _syncInvalid(doc, errEl);
    });
}

// Show (or clear, with an empty msg) an inline error under `input`, creating
// the role=alert element on first use and wiring aria-describedby/invalid.
export function setFieldError(input, msg, doc = document) {
    if (!input) return;
    const id = `${input.id}-error`;
    let errEl = doc.getElementById(id);
    if (!errEl) {
        if (!msg) return;
        errEl = doc.createElement('p');
        errEl.id = id;
        errEl.className = 'field-error';
        errEl.setAttribute('role', 'alert');
        input.insertAdjacentElement('afterend', errEl);
        const prev = input.getAttribute('aria-describedby');
        input.setAttribute('aria-describedby', prev ? `${prev} ${id}` : id);
    }
    errEl.textContent = msg || '';
    if (msg) input.setAttribute('aria-invalid', 'true');
    else input.removeAttribute('aria-invalid');
}

// Header close button markup (x-mark, 18px). aria-label is filled from i18n.
export function closeButtonHtml() {
    return '<button type="button" class="modal__close" data-modal-cancel aria-label="' +
        t('btn.close').replace(/"/g, '&quot;') + '">' +
        '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" ' +
        'stroke="currentColor" aria-hidden="true" focusable="false" width="18" height="18">' +
        '<path stroke-linecap="round" stroke-linejoin="round" d="M6 18 18 6M6 6l12 12"/></svg></button>';
}
