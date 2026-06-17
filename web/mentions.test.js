import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createMentions } from './mentions.js';

let els;
function mkEl(over = {}) {
  const children = [];
  return {
    style: {}, value: '', innerHTML: '', textContent: '', className: '',
    dataset: {}, _children: children, selectionStart: 0,
    appendChild: (c) => children.push(c),
    addEventListener(type, fn) { this[`_on_${type}`] = fn; },
    setSelectionRange() {}, focus() {},
    querySelectorAll: () => [],
    classList: { toggle() {} },
    ...over,
  };
}
beforeEach(() => {
  els = {};
  global.document = {
    getElementById: (id) => (els[id] ||= mkEl()),
    createElement: () => mkEl(),
  };
});

function make(members = []) {
  const mentions = createMentions({ getCurrentRoomMembers: () => members });
  return { mentions };
}

describe('_renderMentionDropdown', () => {
  it('renders a row per match and reveals the dropdown', () => {
    const dd = mkEl();
    els['mention-dropdown'] = dd;
    const { mentions } = make();
    mentions._renderMentionDropdown([
      { display_name: 'Bob', webid: 'did:key:zBob' },
      { display_name: 'Carol', webid: 'did:key:zCarol', status: 'online' },
    ]);
    expect(dd._children).toHaveLength(2);
    expect(dd.style.display).toBe('block');
    expect(mentions.state.mentionFocusIdx).toBe(0);
  });
});

describe('closeMentionDropdown', () => {
  it('hides the dropdown and resets the cursor', () => {
    const dd = mkEl({ style: { display: 'block' } });
    els['mention-dropdown'] = dd;
    const { mentions } = make();
    mentions.state.mentionStart = 5;
    mentions.closeMentionDropdown();
    expect(dd.style.display).toBe('none');
    expect(mentions.state.mentionStart).toBe(-1);
  });
});

describe('_selectMention', () => {
  it('splices the @name into the input at the mention start', () => {
    const input = mkEl({ value: 'hey @bo', selectionStart: 7 });
    els['mention-dropdown'] = mkEl();
    const { mentions } = make();
    mentions.attach(input);
    mentions.state.mentionStart = 4; // position of '@'
    mentions._selectMention('Bob');
    expect(input.value).toBe('hey @Bob ');
  });
});

describe('attach (input listener)', () => {
  it('opens the dropdown when an @query matches a member', () => {
    const dd = mkEl();
    els['mention-dropdown'] = dd;
    const input = mkEl({ value: '@bo', selectionStart: 3 });
    const { mentions } = make([{ display_name: 'Bob', webid: 'did:key:zBob' }]);
    mentions.attach(input);
    input._on_input(); // fire the input listener
    expect(mentions.state.mentionStart).toBe(0);
    expect(dd.style.display).toBe('block');
  });
  it('closes the dropdown when there is no @ token', () => {
    const dd = mkEl({ style: { display: 'block' } });
    els['mention-dropdown'] = dd;
    const input = mkEl({ value: 'plain text', selectionStart: 10 });
    const { mentions } = make([{ display_name: 'Bob', webid: 'did:key:zBob' }]);
    mentions.attach(input);
    input._on_input();
    expect(dd.style.display).toBe('none');
  });
});
