// emoji.js — R59C: composer emoji. Two features, zero dependencies:
//   1. `:shortcode:` autocomplete in the message input (clone of the
//      mentions.js dropdown pattern — shared-state factory, attach(inputEl)).
//   2. An insert-picker panel on a composer button (distinct from the
//      per-message REACTION picker, which stays as-is).
// Pure helpers (matchShortcodes, findShortcodeStart, applyShortcode) are
// exported for tests.

// ~160 common emoji, Discord/Slack-compatible names. Names match
// /^[a-z0-9_+-]+$/ (the autocomplete trigger charset).
export const EMOJI_MAP = {
    grinning: '😀', smiley: '😃', smile: '😄', grin: '😁', laughing: '😆',
    sweat_smile: '😅', joy: '😂', rofl: '🤣', slight_smile: '🙂', upside_down: '🙃',
    wink: '😉', blush: '😊', innocent: '😇', heart_eyes: '😍', star_struck: '🤩',
    kissing_heart: '😘', yum: '😋', stuck_out_tongue: '😛', zany: '🤪', money_mouth: '🤑',
    hugging: '🤗', shushing: '🤫', thinking: '🤔', zipper_mouth: '🤐', neutral_face: '😐',
    expressionless: '😑', smirk: '😏', unamused: '😒', roll_eyes: '🙄', grimacing: '😬',
    relieved: '😌', pensive: '😔', sleepy: '😪', sleeping: '😴', mask: '😷',
    thermometer_face: '🤒', nauseated: '🤢', sneezing: '🤧', hot_face: '🥵', cold_face: '🥶',
    woozy: '🥴', dizzy_face: '😵', exploding_head: '🤯', cowboy: '🤠', partying: '🥳',
    sunglasses: '😎', nerd: '🤓', monocle: '🧐', confused: '😕', worried: '😟',
    frowning: '🙁', open_mouth: '😮', astonished: '😲', flushed: '😳', pleading: '🥺',
    cry: '😢', sob: '😭', scream: '😱', confounded: '😖', persevere: '😣',
    disappointed: '😞', sweat: '😓', weary: '😩', tired_face: '😫', yawning: '🥱',
    triumph: '😤', rage: '😡', angry: '😠', cursing: '🤬', smiling_imp: '😈',
    skull: '💀', poop: '💩', clown: '🤡', ghost: '👻', alien: '👽',
    robot: '🤖', wave: '👋', raised_hand: '✋', ok_hand: '👌', pinched_fingers: '🤌',
    v: '✌️', crossed_fingers: '🤞', love_you: '🤟', metal: '🤘', call_me: '🤙',
    point_left: '👈', point_right: '👉', point_up: '👆', point_down: '👇', middle_finger: '🖕',
    '+1': '👍', thumbsup: '👍', '-1': '👎', thumbsdown: '👎', fist: '✊',
    clap: '👏', raised_hands: '🙌', open_hands: '👐', handshake: '🤝', pray: '🙏',
    muscle: '💪', eyes: '👀', eye: '👁️', brain: '🧠', tongue: '👅',
    heart: '❤️', orange_heart: '🧡', yellow_heart: '💛', green_heart: '💚', blue_heart: '💙',
    purple_heart: '💜', black_heart: '🖤', white_heart: '🤍', broken_heart: '💔', two_hearts: '💕',
    sparkling_heart: '💖', heartpulse: '💗', fire: '🔥', sparkles: '✨', star: '⭐',
    star2: '🌟', zap: '⚡', boom: '💥', dizzy: '💫', sweat_drops: '💦',
    dash: '💨', hole: '🕳️', bomb: '💣', zzz: '💤', tada: '🎉',
    confetti: '🎊', balloon: '🎈', gift: '🎁', trophy: '🏆', medal: '🏅',
    crown: '👑', gem: '💎', dog: '🐶', cat: '🐱', mouse: '🐭',
    rabbit: '🐰', fox: '🦊', bear: '🐻', panda: '🐼', koala: '🐨',
    lion: '🦁', pig: '🐷', frog: '🐸', monkey: '🐵', see_no_evil: '🙈',
    hear_no_evil: '🙉', speak_no_evil: '🙊', chicken: '🐔', penguin: '🐧', bird: '🐦',
    duck: '🦆', owl: '🦉', unicorn: '🦄', bee: '🐝', bug: '🐛',
    butterfly: '🦋', snail: '🐌', turtle: '🐢', snake: '🐍', octopus: '🐙',
    crab: '🦀', whale: '🐳', dolphin: '🐬', fish: '🐟', shark: '🦈',
    rose: '🌹', sunflower: '🌻', tree: '🌳', cactus: '🌵', four_leaf_clover: '🍀',
    sun: '☀️', moon: '🌙', earth: '🌍', rainbow: '🌈', cloud: '☁️',
    snowflake: '❄️', umbrella: '☔', coffee: '☕', tea: '🍵', beer: '🍺',
    beers: '🍻', wine: '🍷', cocktail: '🍸', pizza: '🍕', hamburger: '🍔',
    fries: '🍟', hotdog: '🌭', taco: '🌮', burrito: '🌯', ramen: '🍜',
    sushi: '🍣', bento: '🍱', rice: '🍚', bread: '🍞', cheese: '🧀',
    egg: '🥚', bacon: '🥓', pancakes: '🥞', cake: '🍰', birthday: '🎂',
    cookie: '🍪', chocolate: '🍫', candy: '🍬', lollipop: '🍭', popcorn: '🍿',
    doughnut: '🍩', ice_cream: '🍦', apple: '🍎', banana: '🍌', watermelon: '🍉',
    grapes: '🍇', strawberry: '🍓', peach: '🍑', cherries: '🍒', pineapple: '🍍',
    avocado: '🥑', eggplant: '🍆', carrot: '🥕', corn: '🌽', hot_pepper: '🌶️',
    rocket: '🚀', airplane: '✈️', car: '🚗', bike: '🚲', train: '🚆',
    ship: '🚢', anchor: '⚓', house: '🏠', office: '🏢', hospital: '🏥',
    school: '🏫', church: '⛪', mountain: '⛰️', beach: '🏖️', desert: '🏜️',
    island: '🏝️', volcano: '🌋', camping: '🏕️', clock: '🕐', hourglass: '⌛',
    watch: '⌚', alarm_clock: '⏰', phone: '📱', computer: '💻', keyboard: '⌨️',
    printer: '🖨️', tv: '📺', camera: '📷', video_camera: '📹', movie_camera: '🎥',
    microphone: '🎤', headphones: '🎧', radio: '📻', bell: '🔔', loudspeaker: '📢',
    mega: '📣', book: '📖', books: '📚', newspaper: '📰', pencil: '✏️',
    pen: '🖊️', paintbrush: '🖌️', crayon: '🖍️', memo: '📝', briefcase: '💼',
    folder: '📁', calendar: '📅', clipboard: '📋', pushpin: '📌', paperclip: '📎',
    scissors: '✂️', lock: '🔒', unlock: '🔓', key: '🔑', hammer: '🔨',
    wrench: '🔧', gear: '⚙️', link: '🔗', chains: '⛓️', syringe: '💉',
    pill: '💊', money: '💰', dollar: '💵', credit_card: '💳', chart: '📈',
    chart_down: '📉', email: '📧', envelope: '✉️', package: '📦', label: '🏷️',
    check: '✅', x: '❌', warning: '⚠️', question: '❓', exclamation: '❗',
    no_entry: '⛔', recycle: '♻️', infinity: '♾️', music: '🎵', notes: '🎶',
    art: '🎨', game: '🎮', dice: '🎲', dart: '🎯', bowling: '🎳',
    soccer: '⚽', basketball: '🏀', football: '🏈', baseball: '⚾', tennis: '🎾',
    eight_ball: '🎱', ping_pong: '🏓', flag: '🚩', hundred: '💯', ok: '🆗',
    new: '🆕', free: '🆓', sos: '🆘', wc: '🚾', shrug: '🤷',
    facepalm: '🤦',
};

// Prefix matches first (alphabetical), then substring matches — deterministic.
export function matchShortcodes(query, limit = 8) {
    const q = (query || '').toLowerCase();
    if (!q) return [];
    const names = Object.keys(EMOJI_MAP).sort();
    const prefix = names.filter(n => n.startsWith(q));
    const infix = names.filter(n => !n.startsWith(q) && n.includes(q));
    return prefix.concat(infix).slice(0, limit).map(n => ({ name: n, emoji: EMOJI_MAP[n] }));
}

// Scan back from the caret for a `:query` trigger (colon at start or after
// whitespace, ≥2 query chars typed). Returns the colon index, or -1.
export function findShortcodeStart(text, caret) {
    for (let i = caret - 1; i >= 0; i--) {
        const c = text[i];
        if (c === ':') {
            if (i > 0 && !/\s/.test(text[i - 1])) return -1;
            return (caret - i - 1) >= 2 ? i : -1;
        }
        if (!/[a-z0-9_+-]/i.test(c)) return -1;
    }
    return -1;
}

// Replace text[colonStart..caret) with the emoji (plus a trailing space).
export function applyShortcode(text, caret, colonStart, emoji) {
    const before = text.slice(0, colonStart);
    const after = text.slice(caret);
    const inserted = emoji + ' ';
    return { text: before + inserted + after, caret: colonStart + inserted.length };
}

export function createEmoji({ getCustomEmoji } = {}) {
    const state = { colonStart: -1, focusIdx: 0 };
    let inputEl = null;
    let _panelOpen = false;

    function _dd() { return document.getElementById('emoji-dropdown'); }

    function closeEmojiDropdown() {
        const dd = _dd();
        if (dd) dd.style.display = 'none';
        state.colonStart = -1;
    }

    function _render(matches) {
        const dd = _dd();
        if (!dd) return;
        state.focusIdx = 0;
        dd.innerHTML = '';
        matches.forEach((m, i) => {
            const row = document.createElement('div');
            row.className = 'mention-option' + (i === 0 ? ' focused' : '');
            row.dataset.idx = String(i);
            row.dataset.emoji = m.emoji;
            const glyph = document.createElement('span');
            glyph.textContent = m.emoji;
            glyph.style.marginRight = '8px';
            row.appendChild(glyph);
            const label = document.createElement('span');
            label.textContent = ':' + m.name + ':';
            row.appendChild(label);
            dd.appendChild(row);
        });
        dd.style.display = matches.length ? 'block' : 'none';
    }

    function _selectEmoji(emoji) {
        const r = applyShortcode(inputEl.value, inputEl.selectionStart, state.colonStart, emoji);
        inputEl.value = r.text;
        inputEl.setSelectionRange(r.caret, r.caret);
        closeEmojiDropdown();
        inputEl.focus();
        inputEl.dispatchEvent(new Event('input', { bubbles: true }));
    }

    function _insertAtCaret(emoji) {
        const caret = inputEl?.selectionStart ?? 0;
        const val = inputEl?.value ?? '';
        inputEl.value = val.slice(0, caret) + emoji + val.slice(caret);
        const pos = caret + emoji.length;
        inputEl.setSelectionRange(pos, pos);
        inputEl.focus();
    }

    // ── Composer insert-picker panel ────────────────────────────────────────
    function _panel() { return document.getElementById('composer-emoji-panel'); }

    function openPanel() {
        const panel = _panel();
        if (!panel) return;
        if (!panel.querySelector('button:not(.custom-entry)')) {
            for (const [name, emoji] of Object.entries(EMOJI_MAP)) {
                if (name === 'thumbsup' || name === 'thumbsdown') continue;   // dupes of +1/-1
                const b = document.createElement('button');
                b.type = 'button';
                b.textContent = emoji;
                b.setAttribute('aria-label', name);
                b.addEventListener('click', () => { _insertAtCaret(emoji); });
                panel.appendChild(b);
            }
        }
        // R59G: the active room's custom emoji, rebuilt each open (room-scoped).
        panel.querySelectorAll('.custom-entry').forEach(el => el.remove());
        const custom = getCustomEmoji?.() || {};
        const names = Object.keys(custom).sort().reverse();   // prepend keeps a→z order
        for (const name of names) {
            const b = document.createElement('button');
            b.type = 'button';
            b.className = 'custom-entry';
            b.setAttribute('aria-label', ':' + name + ':');
            const img = document.createElement('img');
            img.className = 'custom-emoji';
            img.src = `data:${custom[name].mime};base64,${custom[name].data_b64}`;
            img.alt = '';
            b.appendChild(img);
            b.addEventListener('click', () => { _insertAtCaret(':' + name + ':'); });
            panel.prepend(b);
        }
        _panelOpen = true;
        panel.style.display = 'grid';
        document.getElementById('composer-emoji-btn')?.setAttribute('aria-expanded', 'true');
        panel.querySelector('button')?.focus();
    }

    function closePanel() {
        const panel = _panel();
        if (panel) panel.style.display = 'none';
        _panelOpen = false;
        document.getElementById('composer-emoji-btn')?.setAttribute('aria-expanded', 'false');
    }

    function togglePanel() { _panelOpen ? closePanel() : openPanel(); }

    function attach(el) {
        inputEl = el;

        el.addEventListener('input', () => {
            const val = inputEl.value;
            const caret = inputEl.selectionStart;
            // Exact `:name:` just closed → expand immediately.
            const exact = /(^|\s):([a-z0-9_+-]{2,}):$/.exec(val.slice(0, caret));
            if (exact && EMOJI_MAP[exact[2]]) {
                state.colonStart = caret - exact[2].length - 2;
                _selectEmoji(EMOJI_MAP[exact[2]]);
                return;
            }
            const colonStart = findShortcodeStart(val, caret);
            if (colonStart === -1) { closeEmojiDropdown(); return; }
            const matches = matchShortcodes(val.slice(colonStart + 1, caret));
            if (!matches.length) { closeEmojiDropdown(); return; }
            state.colonStart = colonStart;
            _render(matches);
        });

        el.addEventListener('keydown', (e) => {
            const dd = _dd();
            if (!dd || dd.style.display === 'none') return;
            const items = dd.querySelectorAll('.mention-option');
            if (!items.length) return;
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                state.focusIdx = Math.min(state.focusIdx + 1, items.length - 1);
                items.forEach((el2, i) => el2.classList.toggle('focused', i === state.focusIdx));
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                state.focusIdx = Math.max(state.focusIdx - 1, 0);
                items.forEach((el2, i) => el2.classList.toggle('focused', i === state.focusIdx));
            } else if (e.key === 'Enter' || e.key === 'Tab') {
                const emoji = items[state.focusIdx]?.dataset.emoji;
                if (emoji) { e.preventDefault(); _selectEmoji(emoji); }
            } else if (e.key === 'Escape') {
                e.preventDefault();
                closeEmojiDropdown();
            }
        });

        // Dropdown click-select (delegation, like the mention dropdown's).
        _dd()?.addEventListener('click', (e) => {
            const row = e.target.closest('.mention-option');
            if (row?.dataset.emoji) _selectEmoji(row.dataset.emoji);
        });

        // Panel wiring
        document.getElementById('composer-emoji-btn')?.addEventListener('click', togglePanel);
        // Escape closes the panel from anywhere (focus may be back in the
        // input after an insert — the panel stays open for multi-insert).
        document.addEventListener('keydown', (e) => {
            if (_panelOpen && e.key === 'Escape') {
                e.stopPropagation();
                closePanel();
                document.getElementById('composer-emoji-btn')?.focus();
            }
        }, true);
        document.addEventListener('click', (e) => {
            if (!_panelOpen) return;
            if (!_panel()?.contains(e.target) && !e.target.closest('#composer-emoji-btn')) closePanel();
        });
    }

    return { attach, closeEmojiDropdown, openPanel, closePanel, togglePanel, state };
}
