// settings-panel.js: the settings dialog's section nav and its autosave helper.
//
// Desktop shows a left nav of section buttons beside one scrolling pane; a click
// scrolls the pane to that section and a scroll-spy keeps aria-current on the
// section in view. Mobile hides the nav, but showSection() still works there
// (onboarding uses it to jump to the pod controls), expanding the Advanced fold
// first when the target lives inside it.

// Pure: which section is current for a pane scrolled to scrollTop.
// sections: [{ id, top }] in document order, top measured from the pane's
// content origin. The last section wins once the pane is scrolled to the end,
// since a short final section can never reach the top of the pane.
export function activeSectionId(sections, scrollTop, { offset = 32, atEnd = false } = {}) {
    if (!sections.length) return null;
    if (atEnd) return sections[sections.length - 1].id;
    let active = sections[0].id;
    for (const s of sections) {
        if (s.top - offset <= scrollTop) active = s.id;
        else break;
    }
    return active;
}

// A save callback run after `delay` ms of quiet (schedule), or right away
// (flush). The callback decides itself whether anything changed.
export function createDebouncedSaver(save, delay = 800, timers = globalThis) {
    let handle = null;
    const clear = () => {
        if (handle !== null) { timers.clearTimeout(handle); handle = null; }
    };
    return {
        schedule() {
            clear();
            handle = timers.setTimeout(() => { handle = null; save(); }, delay);
        },
        flush() { clear(); save(); },
        cancel: clear,
        get pending() { return handle !== null; },
    };
}

export function initSettingsNav(doc = document) {
    const pane = doc.getElementById('settings-content');
    const items = Array.from(doc.querySelectorAll('.settings-nav-item[data-settings-target]'));
    if (!pane || !items.length) return { reset() {}, showSection() {} };

    const setCurrent = (id) => {
        for (const b of items) {
            if (b.dataset.settingsTarget === id) b.setAttribute('aria-current', 'true');
            else b.removeAttribute('aria-current');
        }
    };
    const visibleSections = () => items
        .map((b) => doc.getElementById(b.dataset.settingsTarget))
        .filter((s) => s && s.offsetParent !== null);

    // A click sets the current item directly. The scroll it causes must not
    // override that choice when the target cannot reach the top of the pane.
    let pinned = null;
    pane.addEventListener('scroll', () => {
        if (pinned) return;
        const atEnd = pane.scrollTop + pane.clientHeight >= pane.scrollHeight - 2;
        const id = activeSectionId(
            visibleSections().map((s) => ({ id: s.id, top: s.offsetTop })),
            pane.scrollTop, { atEnd });
        if (id) setCurrent(id);
    }, { passive: true });

    function showSection(id, { focus = true } = {}) {
        const sec = doc.getElementById(id);
        if (!sec) return;
        const adv = doc.getElementById('settings-advanced');
        if (adv && adv.contains(sec) && adv.style.display === 'none') {
            doc.getElementById('settings-advanced-toggle')?.click();
        }
        pinned = id;
        pane.scrollTop = Math.max(0, sec.offsetTop - 8);
        setCurrent(id);
        if (focus) sec.focus({ preventScroll: true });
        // Release after the scroll event for this jump has been dispatched.
        const release = () => { if (pinned === id) pinned = null; };
        if (typeof requestAnimationFrame === 'function') requestAnimationFrame(() => requestAnimationFrame(release));
        else setTimeout(release, 50);
    }

    for (const b of items) {
        b.addEventListener('click', () => showSection(b.dataset.settingsTarget));
    }

    return {
        reset() {
            pinned = null;
            pane.scrollTop = 0;
            setCurrent(items[0].dataset.settingsTarget);
        },
        showSection,
    };
}
