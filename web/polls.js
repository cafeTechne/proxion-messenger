// polls.js — R59F: lightweight polls with ZERO backend changes. A poll is a
// structured plain-text message (readable as-is on any client, upgraded or
// not, local or federated); votes are ordinary keycap-emoji reactions, so the
// existing reaction pipeline carries counts, live updates, and federation for
// free. The poll card's option buttons cast them (one vote per person).
//
// Format (parse + format are pure and round-trip):
//   📊 <question>
//   1️⃣ <option one>
//   2️⃣ <option two>
//   …up to 5 options.

import { t } from './i18n.js';

export const POLL_EMOJI = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣'];
export const POLL_MARKER = '\u{1F4CA}';   // 📊
export const MAX_POLL_OPTIONS = 5;

export function formatPoll(question, options) {
    const q = (question || '').trim();
    const opts = (options || []).map(o => (o || '').trim()).filter(Boolean).slice(0, MAX_POLL_OPTIONS);
    if (!q || opts.length < 2) return null;
    return `${POLL_MARKER} ${q}\n` + opts.map((o, i) => `${POLL_EMOJI[i]} ${o}`).join('\n');
}

// Single-choice voting on top of reactions: picking an option you already
// chose clears it; picking another moves your vote. reacts is the message's
// {emoji: [webid…]} map. Returns {add: emoji|null, remove: [emoji…]}.
export function pollVoteChange(reacts, emoji, self) {
    const mine = POLL_EMOJI.filter(e => (reacts?.[e] || []).includes(self));
    if (mine.includes(emoji)) return { add: null, remove: [emoji] };
    return { add: emoji, remove: mine };
}

// {question, options: [{emoji, text}]} for a well-formed poll, else null.
export function parsePoll(content) {
    if (!content || !content.startsWith(POLL_MARKER + ' ')) return null;
    const lines = content.split('\n');
    const question = lines[0].slice(POLL_MARKER.length + 1).trim();
    if (!question) return null;
    const options = [];
    for (let i = 1; i < lines.length; i++) {
        const line = lines[i].trim();
        if (!line) continue;
        const emoji = POLL_EMOJI[options.length];
        if (!emoji || !line.startsWith(emoji)) return null;
        const text = line.slice(emoji.length).trim();
        if (!text) return null;
        options.push({ emoji, text });
    }
    return options.length >= 2 ? { question, options } : null;
}

export function createPolls({ showToast }) {

    function openPollModal() {
        const modal = document.getElementById('poll-modal');
        if (!modal) return;
        document.getElementById('poll-question').value = '';
        for (let i = 1; i <= MAX_POLL_OPTIONS; i++) {
            const el = document.getElementById(`poll-opt-${i}`);
            if (el) el.value = '';
        }
        modal.style.display = 'flex';
        setTimeout(() => document.getElementById('poll-question')?.focus(), 50);
    }

    function closePollModal() {
        const modal = document.getElementById('poll-modal');
        if (modal) modal.style.display = 'none';
    }

    function submitPoll() {
        const question = document.getElementById('poll-question')?.value || '';
        const options = [];
        for (let i = 1; i <= MAX_POLL_OPTIONS; i++) {
            options.push(document.getElementById(`poll-opt-${i}`)?.value || '');
        }
        const content = formatPoll(question, options);
        if (!content) { showToast(t('poll.needTwo')); return; }
        closePollModal();

        // Ride the normal composer pipeline (E2E, fanout, optimistic render,
        // dedupe) by submitting the form with the poll as its content.
        const input = document.getElementById('message-input');
        input.value = content;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        document.getElementById('message-form')?.dispatchEvent(
            new Event('submit', { bubbles: true, cancelable: true }));

    }

    function wirePolls() {
        document.getElementById('poll-btn')?.addEventListener('click', openPollModal);
        document.getElementById('poll-cancel')?.addEventListener('click', closePollModal);
        // The modal body is a <form>: the Create button and Enter in any field
        // both submit it, once.
        document.getElementById('poll-form')?.addEventListener('submit', (e) => {
            e.preventDefault();
            submitPoll();
        });
    }

    return { openPollModal, closePollModal, submitPoll, wirePolls };
}
