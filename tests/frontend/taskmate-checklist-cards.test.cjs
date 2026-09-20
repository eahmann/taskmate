const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const www = path.join(__dirname, '../../custom_components/taskmate/www');
const messages = JSON.parse(readFileSync(path.join(www, 'locales/en.json'), 'utf8'));

// Keep Lit's actual template structure and event bindings. This small harness
// renders that tree and exercises its button handlers without an HA instance
// or installing a browser/DOM framework. Disabled buttons do not dispatch clicks.
function template(strings, ...values) { return { strings, values }; }
function rendered(tree) {
  const handlers = [];
  function serialize(value) {
    if (value == null) return '';
    if (Array.isArray(value)) return value.map(serialize).join('');
    if (typeof value === 'function') return `event_${handlers.push(value) - 1}`;
    if (value.strings) return value.strings.reduce((out, str, i) => out + str + serialize(value.values[i]), '');
    return String(value);
  }
  const markup = serialize(tree);
  const buttons = Array.from(markup.matchAll(/<button\b([^>]*?)>([\s\S]*?)<\/button>/g), match => {
    const attrs = match[1];
    const disabled = /\?disabled\s*=\s*["']?true\b/.test(attrs) || /(?:^|\s)disabled(?:\s|$)/.test(attrs);
    const event = attrs.match(/@click\s*=\s*["']?event_(\d+)/);
    const step = attrs.match(/data-step-id="([^"]+)"/);
    return {
      attrs, content: match[2], disabled, step: step?.[1],
      async click() { if (!disabled && event) await handlers[Number(event[1])]({ stopPropagation() {} }); },
    };
  });
  return { markup, buttons, step: id => buttons.find(button => button.step === id) };
}

function harness(kind, options = {}) {
  let now = options.now ? new Date(options.now).getTime() : Date.now();
  class ClockDate extends Date {
    constructor(...args) { super(...(args.length ? args : [now])); }
    static now() { return now; }
  }
  class LitElement {
    constructor() {
      this.style = { setProperty() {}, removeProperty() {} };
      this.events = [];
    }
    requestUpdate() {}
    dispatchEvent(event) { this.events.push(event); }
    disconnectedCallback() {}
  }
  LitElement.prototype.html = template;
  LitElement.prototype.css = template;
  class View extends LitElement {}
  const elements = new Map([['hui-view', View]]);
  const window = {
    __taskmate_localize: (_hass, key, params = {}) => (messages[key] || key).replace(/\{(\w+)\}/g, (_, name) => params[name] ?? ''),
    __taskmate_chore_visual: chore => chore.icon ? { kind: 'icon', icon: chore.icon } : { kind: 'none' },
    __taskmate_design: { apply: (_card, _hass, config) => config.card_design || 'classic' },
    __taskmate_is_parent: () => options.parent === true,
  };
  const timeouts = [];
  const timers = [];
  const context = {
    window, console: { info() {}, error() {} },
    customElements: { get: name => elements.get(name), define: (name, element) => elements.set(name, element) },
    document: { querySelectorAll: () => [] },
    CustomEvent: class { constructor(type, init) { this.type = type; Object.assign(this, init); } },
    URLSearchParams, Date: ClockDate, Intl,
    setTimeout: (fn, delay) => { timers.push({ fn, delay }); return timeouts.push(fn); },
    clearTimeout() {},
  };
  if (kind === 'bonuses' || kind === 'penalties') {
    const base = readFileSync(path.join(www, 'taskmate-incentive-card.js'), 'utf8').replace('export function', 'function');
    const wrapper = readFileSync(path.join(www, `taskmate-${kind}-card.js`), 'utf8').replace(/^import .*;$/m, '');
    vm.runInNewContext(base + '\n' + wrapper, context);
  } else {
    vm.runInNewContext(readFileSync(path.join(www, `taskmate-${kind}-card.js`), 'utf8'), context);
  }
  const card = new (elements.get(`taskmate-${kind}-card`))();
  card.setConfig({
    entity: 'sensor.taskmate_overview', child_id: 'kid', time_category: 'all',
    show_countdown: false, show_swaps: false, ...options.config,
  });
  const child = { id: 'kid', name: 'Ari', points: 5 };
  const chore = {
    id: 'ready', name: 'Get ready', task_type: 'checklist', points: 4,
    icon: 'mdi:weather-sunny', assigned_to: ['kid'], time_category: 'anytime',
    enabled: true, requires_approval: true,
    bonus_subtasks: [
      { id: 'teeth', name: 'Brush teeth', icon: 'mdi:toothbrush', points: 2 },
      { id: 'dress', name: 'Get dressed', icon: 'mdi:tshirt-crew', points: 3 },
    ],
    ...options.chore,
  };
  const attrs = { children: [child], chores: [chore], chore_availability: { ready: { kid: true } }, todays_completions: [] };
  const calls = [];
  card.hass = {
    user: { id: 'family-display', is_admin: options.admin === true },
    config: { time_zone: options.timeZone || 'UTC' }, states: { 'sensor.taskmate_overview': { attributes: attrs } },
    async callService(domain, service, data) { calls.push({ domain, service, data }); },
  };
  return { card, attrs, child, chore, calls, timeouts, timers, window,
    setNow: value => { now = new Date(value).getTime(); }, view: () => rendered(card.render()) };
}

for (const design of ['classic', 'playroom', 'console', 'cleanpro', 'accessible']) {
  test(`${design}: shared parent account can finish checklist items but cannot undo them`, async () => {
    const { card, attrs, view, chore, child, calls } = harness('child', {
      parent: true, config: { card_design: design, show_parent_actions: false },
    });
    await view().step('teeth').click();
    assert.equal(calls.length, 1);
    attrs.todays_completions = [completion('teeth'), completion('dress')];
    assert.equal(view().step('teeth').disabled, true);
    await view().step('teeth').click();
    await card._handleUndoBonusSubtask(chore, chore.bonus_subtasks[0], child, attrs.todays_completions);
    await card._handleUndo(chore, child, attrs.todays_completions);
    assert.equal(calls.length, 1, 'hidden and stale handlers cannot call parent services');
    assert.doesNotMatch(rendered(card._designUndoChip({ onAct() {} }, 'Done')).markup, /button|undo/);
  });

  for (const kind of ['parent-dashboard', 'approvals', 'points']) {
    test(`${design}: ${kind} removes parent controls when hidden`, async () => {
      const { card, view, calls } = harness(kind, { parent: true, config: { card_design: design, show_parent_actions: false } });
      assert.equal(view().markup, '');
      const actions = {
        'parent-dashboard': [['_handleApprove', 'x'], ['_handleReject', 'x'], ['_handleApproveReward', 'x'], ['_handleRejectReward', 'x'], ['_handleSkip', 'x'], ['_handlePoints', 'kid', 5], ['_handleCompleteOnBehalf', 'ready', 'kid']],
        approvals: [['_callService', 'approve_chore', 'x'], ['_callClaimService', 'approve_reward', 'x'], ['_callMissService', 'dismiss_mandatory_chore', 'x'], ['_handleApproveAll', [{ completion_id: 'x' }]]],
        points: [['_quickAdjust', { id: 'kid' }, 'add', 5], ['_confirmAction', { id: 'kid' }, 'add']],
      };
      for (const [method, ...args] of actions[kind]) await card[method](...args);
      assert.equal(calls.length, 0);
    });
  }
}

for (const kind of ['bonuses', 'penalties']) {
  test(`${kind}: display override also hides management for an administrator`, async () => {
    const { card, calls } = harness(kind, { parent: true, admin: true, config: { show_parent_actions: false } });
    assert.equal(card._canApply(), false);
    assert.equal(card._canManage(), false);
    await card._applyBonus({ id: 'kindness', points: 3 });
    assert.equal(calls.length, 0);
    card.config.show_parent_actions = true;
    assert.equal(card._canApply(), true);
    assert.equal(card._canManage(), true);
  });
}

test('activity and overview hide and refuse parent actions without hiding ordinary content', async () => {
  const activity = harness('activity', { parent: true, config: { show_parent_actions: false } });
  activity.card._confirm = { kind: 'txn', id: 'x' };
  assert.equal(rendered(activity.card._renderUndoButton('txn', 'x', 'Award', 'Ari', 2)).markup, '');
  assert.equal(rendered(activity.card._designUndoBtn({ id: 'x' }, 'Undo')).markup, '');
  assert.equal(rendered(activity.card._renderConfirmDialog()).markup, '');
  await activity.card._doUndo();
  assert.equal(activity.calls.length, 0);
  const overview = harness('overview', { parent: true, config: { show_parent_actions: false } });
  overview.card._expanded = { kid: true };
  assert.doesNotMatch(overview.view().markup, /tm-expandable|tm-outstanding-hdr/);
  assert.match(overview.view().markup, /Ari/);
  await overview.card._handleCompleteOnBehalf('ready', 'kid');
  assert.equal(overview.calls.length, 0);
});

function completion(step, approved = true, extras = {}) {
  return { completion_id: `done_${step}`, chore_id: 'ready', child_id: 'kid', bonus_subtask_id: step,
    approved, completed_at: new Date().toISOString(), ...extras };
}

for (const design of ['classic', 'playroom', 'console', 'cleanpro', 'accessible']) {
  for (const preReader of [false, true]) {
    test(`${design}, pre-reader ${preReader}: grouped steps submit without a parent claim`, async () => {
      const { card, view, calls, chore, child } = harness('child', { config: { card_design: design, pre_reader: preReader } });
      const tree = view();
      assert.match(tree.markup, /class="checklist-group/);
      assert.match(tree.markup, /Get ready/);
      assert.match(tree.markup, /0 of 2 steps/);
      assert.match(tree.markup, /mdi:toothbrush/);
      assert.match(tree.markup, /mdi:tshirt-crew/);
      assert.match(tree.markup, /Finish every step/);
      assert.equal(tree.buttons.filter(button => button.step).length, 2);
      if (preReader) assert.doesNotMatch(tree.step('teeth').content, /checklist-step-name/);
      await tree.step('teeth').click();
      assert.equal(calls.length, 1);
      assert.equal(calls[0].service, 'complete_bonus_subtask');
      assert.equal(calls[0].data.bonus_subtask_id, 'teeth');
      assert.match(view().step('teeth').attrs, /pending/);
      assert.match(view().step('teeth').attrs, /step-celebrating/);
      assert.doesNotMatch(view().markup, /class="celebration-overlay"/);
      assert.equal(view().step('teeth').disabled, true);
      await view().step('teeth').click();
      await card._handleComplete(chore, child);
      assert.equal(calls.length, 1, 'no duplicate step or direct parent completion');
    });
  }
}

test('sequential checklist waits for approval; sibling records never unlock a step', async () => {
  const { attrs, view, calls } = harness('child', { chore: { checklist_sequential: true } });
  assert.equal(view().step('dress').disabled, true);
  await view().step('teeth').click();
  attrs.todays_completions = [completion('teeth', false), completion('teeth', true, { child_id: 'sibling' })];
  assert.equal(view().step('dress').disabled, true);
  assert.match(view().markup, /Waiting for approval/);
  attrs.todays_completions[0].approved = true;
  assert.equal(view().step('dress').disabled, false);
  await view().step('dress').click();
  assert.equal(calls.length, 2);
});

test('availability and dependency gates disable checklist steps even in show mode', async () => {
  const { card, attrs, chore, child, view, calls } = harness('child', { config: { dependency_mode: 'show' }, chore: { depends_on: ['breakfast'] } });
  let tree = view();
  assert.equal(tree.step('teeth').disabled, true);
  await tree.step('teeth').click();
  chore.depends_on = [];
  attrs.chore_availability.ready.kid = false;
  tree = view();
  assert.equal(tree.step('teeth').disabled, true);
  await card._handleCompleteBonusSubtask(chore, chore.bonus_subtasks[0], child);
  assert.equal(calls.length, 0);
});

test('completed checklist keeps its progress and bonus; parents can undo a step', async () => {
  const { attrs, calls, view } = harness('child', { parent: true });
  attrs.todays_completions = [completion('teeth'), completion('dress'), completion('', true, { points: 4 })];
  attrs.chore_availability.ready.kid = false;
  const tree = view();
  assert.match(tree.markup, /2 of 2 steps/);
  assert.match(tree.markup, /4 bonus points earned/);
  assert.equal(tree.step('teeth').disabled, false);
  await tree.step('teeth').click();
  assert.equal(calls[0].service, 'reject_chore');
  assert.equal(calls[0].data.completion_id, 'done_teeth');
});

test('failed step submission clears optimism and shows the error', async () => {
  const { card, view } = harness('child');
  card.hass.callService = async () => { throw new Error('Try again'); };
  await view().step('teeth').click();
  assert.equal(view().step('teeth').disabled, false);
  assert.doesNotMatch(view().step('teeth').attrs, /step-celebrating/);
  assert.doesNotMatch(view().markup, /class="celebration-overlay"/);
  assert.equal(card._confetti.length, 0);
  assert.match(card.events.at(-1).detail.message, /Try again/);
});

test('a parent rejection immediately releases a previously observed child submission', async () => {
  const { attrs, view } = harness('child');
  await view().step('teeth').click();
  attrs.todays_completions = [completion('teeth', false)];
  assert.equal(view().step('teeth').disabled, true);
  attrs.todays_completions = [];
  assert.equal(view().step('teeth').disabled, false);
});

test('zero-point checklist steps celebrate locally without advertising a bonus award', async () => {
  const { card, chore, view, timers } = harness('child');
  chore.bonus_subtasks[0].points = 0;
  const sounds = [];
  card._playSound = sound => sounds.push(sound);
  await view().step('teeth').click();
  assert.match(view().step('teeth').attrs, /step-celebrating/);
  assert.equal(sounds.length, 1);
  assert.equal(card._celebrating, null);
  assert.equal(card._confetti.length, 0);
  for (const timer of timers.filter(timer => timer.delay < 30000)) timer.fn();
  assert.doesNotMatch(view().step('teeth').attrs, /step-celebrating/);
  assert.equal(view().step('teeth').disabled, true, 'ending feedback must not undo the submitted step');
});

for (const bonus of [7, 0]) {
  test(`checklist celebration waits for approval and shows the saved parent bonus ${bonus} exactly once`, async () => {
    const { card, attrs, chore, view, timers } = harness('child');
    await view().step('teeth').click();
    await view().step('dress').click();
    attrs.todays_completions = [completion('teeth', false), completion('dress', false)];
    assert.doesNotMatch(view().markup, /class="celebration-overlay"/);
    // Parent approval can happen much later than the optimistic submission UI.
    for (const timer of [...timers]) timer.fn();
    chore.points = 99;
    attrs.todays_completions = [
      completion('', true, { points: bonus }),
      completion('teeth', true, { points: 2 }),
      completion('dress', true, { points: 3 }),
    ];
    const tree = view();
    assert.match(tree.markup, /class="celebration-overlay"/);
    assert.equal(card._celebrationPoints, bonus, 'use parent award, not current chore value or last step');
    assert.ok(card._confetti.length > 0);
    const confetti = card._confetti;
    view();
    assert.equal(card._confetti, confetti, 'a repeat HA update cannot restart confetti');
    card._closeCelebration();
    assert.equal(card._celebrationPoints, null);
    assert.doesNotMatch(view().markup, /class="celebration-overlay"/);
  });
}

test('checklist parent state published before service response celebrates only after success', async () => {
  const { card, attrs, view } = harness('child', { chore: { requires_approval: false } });
  attrs.todays_completions = [completion('teeth')];
  let resolve;
  card.hass.callService = () => new Promise(done => { resolve = done; });
  const submission = view().step('dress').click();
  attrs.todays_completions.push(completion('dress'), completion('', true, { points: 4 }));
  assert.doesNotMatch(view().markup, /class="celebration-overlay"/);
  resolve();
  await submission;
  assert.equal(card._celebrating, 'ready');
  assert.equal(card._celebrationPoints, 4);
  assert.match(view().markup, /class="celebration-overlay"/);
});

test('overlapping step responses cannot celebrate the same awarded checklist twice', async () => {
  const { card, attrs, view } = harness('child', { chore: { requires_approval: false } });
  const finish = [];
  card.hass.callService = () => new Promise(resolve => finish.push(resolve));
  const first = view().step('teeth').click();
  const last = view().step('dress').click();
  attrs.todays_completions = [completion('teeth'), completion('dress'), completion('', true, { points: 4 })];
  finish[0]();
  await first;
  assert.match(view().markup, /class="celebration-overlay"/);
  card._closeCelebration();
  finish[1]();
  await last;
  assert.doesNotMatch(view().markup, /class="celebration-overlay"/);
});

test('undo and a fresh local completion celebrate the newly awarded parent again', async () => {
  const { card, attrs, view } = harness('child', { parent: true });
  attrs.todays_completions = [completion('teeth')];
  await view().step('dress').click();
  attrs.todays_completions.push(completion('dress'), completion('', true, { points: 4 }));
  assert.match(view().markup, /class="celebration-overlay"/);
  await view().step('dress').click();
  attrs.todays_completions = [completion('teeth')];
  assert.doesNotMatch(view().markup, /class="celebration-overlay"/);
  await view().step('dress').click();
  attrs.todays_completions.push(
    completion('dress', true, { completion_id: 'done_dress_again' }),
    completion('', true, { completion_id: 'done_parent_again', points: 6 }),
  );
  assert.match(view().markup, /class="celebration-overlay"/);
  assert.equal(card._celebrationPoints, 6);
});

test('a delayed HA parent update triggers checklist celebration through shouldUpdate', async () => {
  const { card, attrs, view, window } = harness('child');
  await view().step('dress').click();
  attrs.todays_completions = [completion('teeth'), completion('dress'), completion('', true, { points: 4 })];
  window.__taskmate_hasChanged = () => false;
  assert.equal(card.shouldUpdate(new Map([['hass', card.hass]])), true);
  assert.equal(card._celebrating, 'ready');
});

test('already completed checklists and remote-only completions do not replay celebrations', () => {
  const { card, attrs, view } = harness('child');
  attrs.todays_completions = [completion('teeth'), completion('dress'), completion('', true, { points: 4 })];
  assert.doesNotMatch(view().markup, /class="celebration-overlay"/);
  assert.doesNotMatch(view().step('teeth').attrs, /step-celebrating/);
  attrs.todays_completions = [];
  view();
  attrs.todays_completions = [completion('', true, { points: 4, completion_id: 'remote_new' })];
  assert.doesNotMatch(view().markup, /class="celebration-overlay"/);
  assert.equal(card._confetti.length, 0);
});

test('sibling, shared-parent, and yesterday records cannot award the child checklist celebration', async () => {
  const { card, attrs, view } = harness('child', { now: '2026-09-19T12:00:00Z' });
  await view().step('teeth').click();
  attrs.todays_completions = [
    completion('', true, { child_id: 'other', points: 40, completed_at: '2026-09-19T12:00:00Z' }),
    completion('', true, { child_id: '__parent__', points: 30, completed_at: '2026-09-19T12:00:00Z' }),
    completion('', true, { points: 20, completed_at: '2026-09-18T12:00:00Z' }),
  ];
  assert.doesNotMatch(view().markup, /class="celebration-overlay"/);
  assert.equal(card._confetti.length, 0);
  attrs.todays_completions.push(completion('', true, { points: 4, completed_at: '2026-09-19T12:00:00Z' }));
  assert.match(view().markup, /class="celebration-overlay"/);
  assert.equal(card._celebrationPoints, 4);
});

test('switching the selected child discards checklist feedback and pending celebration', async () => {
  const { card, attrs, chore, view } = harness('child');
  await view().step('teeth').click();
  attrs.children.push({ id: 'other', name: 'Bea', points: 0 });
  chore.assigned_to.push('other');
  attrs.chore_availability.ready.other = true;
  card.setConfig({ ...card.config, child_id: 'other' });
  assert.doesNotMatch(view().step('teeth').attrs, /step-celebrating/);
  attrs.todays_completions = [completion('', true, { points: 4, child_id: 'other' })];
  assert.doesNotMatch(view().markup, /class="celebration-overlay"/);
  card.setConfig({ ...card.config, child_id: 'kid' });
  attrs.todays_completions.push(completion('', true, { points: 4 }));
  assert.doesNotMatch(view().markup, /class="celebration-overlay"/);
});

test('HA local midnight clears checklist feedback and old callbacks cannot erase new feedback', async () => {
  const { card, attrs, view, timers, setNow } = harness('child', {
    now: '2026-09-19T04:59:00Z', timeZone: 'America/Chicago',
  });
  await view().step('teeth').click();
  const oldTimers = [...timers];
  setNow('2026-09-19T05:01:00Z');
  assert.doesNotMatch(view().step('teeth').attrs, /step-celebrating/);
  attrs.todays_completions = [completion('', true, { points: 4, completed_at: '2026-09-19T05:00:00Z' })];
  assert.doesNotMatch(view().markup, /class="celebration-overlay"/);
  attrs.todays_completions = [];
  await view().step('teeth').click();
  for (const timer of oldTimers) timer.fn();
  assert.match(view().step('teeth').attrs, /step-celebrating/);
  assert.equal(card._celebrating, null);
});

test('disconnecting clears step feedback and cannot celebrate an approval after reconnect', async () => {
  const { card, attrs, view } = harness('child');
  await view().step('teeth').click();
  card.disconnectedCallback();
  assert.doesNotMatch(view().step('teeth').attrs, /step-celebrating/);
  attrs.todays_completions = [completion('', true, { points: 4 })];
  assert.doesNotMatch(view().markup, /class="celebration-overlay"/);
  assert.equal(card._confetti.length, 0);
});

test('a step response after disconnect cannot restore celebrations or sound', async () => {
  const { card, attrs, view } = harness('child');
  const sounds = [];
  card._playSound = sound => sounds.push(sound);
  let resolve;
  card.hass.callService = () => new Promise(done => { resolve = done; });
  const submission = view().step('teeth').click();
  card.disconnectedCallback();
  attrs.todays_completions = [completion('teeth'), completion('dress'), completion('', true, { points: 4 })];
  resolve();
  await submission;
  assert.doesNotMatch(view().step('teeth').attrs, /step-celebrating/);
  assert.doesNotMatch(view().markup, /class="celebration-overlay"/);
  assert.equal(sounds.length, 0);
});

test('a delayed undo for the old child cannot cancel the selected child celebration', async () => {
  const { card, attrs, chore, view } = harness('child', { parent: true });
  attrs.todays_completions = [completion('teeth')];
  let finishUndo;
  card.hass.callService = async (_domain, service) => {
    if (service === 'reject_chore') await new Promise(resolve => { finishUndo = resolve; });
  };
  const oldUndo = view().step('teeth').click();
  attrs.children.push({ id: 'other', name: 'Bea', points: 0 });
  chore.assigned_to.push('other');
  attrs.chore_availability.ready.other = true;
  card.setConfig({ ...card.config, child_id: 'other' });
  await view().step('teeth').click();
  attrs.todays_completions.push(completion('', true, { child_id: 'other', points: 4 }));
  assert.match(view().markup, /class="celebration-overlay"/);
  finishUndo();
  await oldUndo;
  assert.match(view().markup, /class="celebration-overlay"/);
});

test('picture checklist labels and exact point values remain configurable', () => {
  const { view } = harness('child', { config: { pre_reader: true, pre_reader_labels: true, pre_reader_points: true } });
  assert.match(view().step('teeth').content, /checklist-step-name.*Brush teeth/);
  assert.match(view().step('teeth').content, /\+2/);
  assert.match(view().step('dress').content, /\+3/);
});

test('standard optional bonuses still require parent completion', () => {
  const { card, child, chore } = harness('child', { chore: { task_type: 'standard' } });
  assert.equal(card._renderBonusSubtasks(chore, child, 'mdi:star', []), '');
  const tree = rendered(card._renderBonusSubtasks(chore, child, 'mdi:star', [completion('')]));
  assert.match(tree.markup, /bonus-subtask/);
  assert.doesNotMatch(tree.markup, /checklist-group/);
});

test('routine renders step buttons and cannot manually claim the parent', async () => {
  const { card, calls, chore, view } = harness('routine');
  const tree = view();
  assert.match(tree.markup, /rt-checklist/);
  assert.equal(tree.buttons.filter(button => button.step).length, 2);
  assert.equal(tree.buttons.filter(button => /rt-done/.test(button.attrs)).length, 0);
  await tree.step('teeth').click();
  await card._complete(chore);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].service, 'complete_bonus_subtask');
  assert.equal(view().step('teeth').disabled, true);
});

test('routine sequential steps wait for approval and display completion bonus once', async () => {
  const { card, attrs, calls, view } = harness('routine', { chore: { checklist_sequential: true } });
  assert.equal(view().step('dress').disabled, true);
  await view().step('teeth').click();
  attrs.todays_completions = [completion('teeth', false, { points: 2 })];
  assert.equal(view().step('dress').disabled, true);
  attrs.todays_completions[0].approved = true;
  assert.equal(view().step('dress').disabled, false);
  await view().step('dress').click();
  assert.equal(card._finished, true);
  assert.equal(calls.length, 2);
  attrs.todays_completions.push(completion('dress', true, { points: 3 }), completion('', true, { points: 4 }));
  attrs.chore_availability.ready.kid = false;
  const finish = view();
  assert.match(finish.markup, /9/);
  assert.equal(card._runCompleted.get('ready').points, 9);
  assert.equal(card._runCompleted.get('ready').pendingPoints, 0);
});

test('routine reopens when a later HA update unlocks the next dependency', async () => {
  const { card, attrs, chore, view } = harness('routine', { chore: { task_type: 'standard', requires_approval: false } });
  attrs.chores.push({ id: 'school', name: 'Pack school bag', time_category: 'anytime', task_type: 'standard', points: 2 });
  attrs.chore_availability.school = { kid: false };
  await card._complete(chore);
  assert.equal(card._finished, true);
  attrs.todays_completions = [completion('')];
  attrs.chore_availability.ready.kid = false;
  attrs.chore_availability.school.kid = true;
  const tree = view();
  assert.equal(card._finished, false);
  assert.match(tree.markup, /Pack school bag/);
  assert.equal(card._tasks()[card._index].id, 'school');
});

test('routine totals exclude steps completed before this run and preserve saved awards', async () => {
  const { card, attrs, view } = harness('routine');
  attrs.todays_completions = [completion('teeth', true, { points: 2 })];
  await view().step('dress').click();
  attrs.todays_completions.push(completion('dress', true, { points: 7 }), completion('', true, { points: 6 }));
  view();
  assert.equal(card._runCompleted.get('ready').points, 13);
  assert.equal(card._runCompleted.get('ready').pendingPoints, 0);
});

test('routine rejection reopens a checklist after previously submitted steps', async () => {
  const { card, attrs, view } = harness('routine');
  await view().step('teeth').click();
  attrs.todays_completions = [completion('teeth', false)];
  await view().step('dress').click();
  attrs.todays_completions.push(completion('dress', false));
  view(); // observe the persisted submissions
  assert.equal(card._finished, true);
  attrs.todays_completions = [completion('dress', false)];
  const tree = view();
  assert.equal(card._finished, false);
  assert.equal(tree.step('teeth').disabled, false);
});

test('child checklist optimism follows Home Assistant local midnight, not UTC midnight', async () => {
  const { card, attrs, view, setNow, window, timeouts } = harness('child', {
    now: '2026-09-18T23:59:00Z', timeZone: 'America/Chicago',
  });
  await view().step('teeth').click();
  const oldTimeout = timeouts.at(-1);
  attrs.todays_completions = [completion('teeth', true, { completed_at: '2026-09-18T23:59:00Z' })];
  view();
  const key = 'ready_bonus_teeth_kid';
  assert.equal(card._optimisticCompletions[key].confirmed, true);
  setNow('2026-09-19T00:01:00Z');
  view();
  assert.equal(card._optimisticCompletions[key].confirmed, true, 'UTC midnight is still the same local day');
  setNow('2026-09-19T05:01:00Z');
  attrs.todays_completions = [];
  window.__taskmate_hasChanged = () => false;
  assert.equal(card.shouldUpdate(new Map([['hass', card.hass]])), true, 'day changes bypass unchanged-entity optimization');
  assert.equal(view().step('teeth').disabled, false);
  assert.equal(Object.keys(card._optimisticCompletions).length, 0);
  await view().step('teeth').click();
  oldTimeout();
  assert.ok(card._optimisticCompletions[key], 'yesterday timeout cannot clear today submission');
});

test('routine resets daily progress, skipped tasks and point totals at local midnight', async () => {
  const { card, attrs, view, setNow, window } = harness('routine', {
    now: '2026-09-19T04:59:00Z', timeZone: 'America/Chicago',
  });
  await view().step('teeth').click();
  await view().step('dress').click();
  attrs.todays_completions = ['teeth', 'dress', ''].map(step => completion(step, true, {
    completed_at: '2026-09-19T04:59:00Z', points: 20,
  }));
  view();
  card._skipped.add('old');
  assert.equal(card._runCompleted.get('ready').points, 60);
  setNow('2026-09-19T05:01:00Z');
  window.__taskmate_hasChanged = () => false;
  assert.equal(card.shouldUpdate(new Map([['hass', card.hass]])), true);
  const tree = view();
  assert.equal(tree.step('teeth').disabled, false, 'yesterday sensor records cannot block today');
  assert.equal(card._runCompleted.size, 0);
  assert.equal(card._runSteps.size, 0);
  assert.equal(card._skipped.size, 0);
  assert.equal(card._finished, false);
  await tree.step('teeth').click();
  await view().step('dress').click();
  assert.equal(card._runCompleted.get('ready').points, 0);
  assert.equal(card._runCompleted.get('ready').pendingPoints, 9);
});

test('routine selected-child change starts a fresh run without retaining another child points', async () => {
  const { card, attrs, chore, view } = harness('routine');
  await view().step('teeth').click();
  await view().step('dress').click();
  card._skipped.add('old');
  attrs.children.push({ id: 'other', name: 'Bea', points: 0 });
  chore.assigned_to.push('other');
  attrs.chore_availability.ready.other = true;
  card.setConfig({ ...card.config, child_id: 'other' });
  assert.equal(card._runCompleted.size, 0);
  assert.equal(card._runSteps.size, 0);
  assert.equal(card._skipped.size, 0);
  assert.equal(card._finished, false);
  assert.equal(view().step('teeth').disabled, false);
});

for (const kind of ['child', 'routine']) {
  test(`${kind} ignores a service response from yesterday while a new submission is in flight`, async () => {
    const { card, setNow, view } = harness(kind, { now: '2026-09-18T23:59:00Z' });
    const finish = [];
    card.hass.callService = () => new Promise(resolve => finish.push(resolve));
    const yesterday = view().step('teeth').click();
    setNow('2026-09-19T00:01:00Z');
    assert.equal(view().step('teeth').disabled, false);
    const today = view().step('teeth').click();
    finish[0]();
    await yesterday;
    assert.equal(view().step('teeth').disabled, true);
    if (kind === 'routine') assert.equal(card._busy, true);
    finish[1]();
    await today;
    assert.equal(view().step('teeth').disabled, true);
    if (kind === 'routine') assert.equal(card._runSteps.get('ready').size, 1);
  });

  test(`${kind} advertises effective completion bonus while retaining saved awarded bonus`, () => {
    const { attrs, card, chore, view } = harness(kind, { chore: { effective_points: 8 } });
    assert.match(view().markup, /Finish every step for \+8 bonus points/);
    attrs.todays_completions = [completion('teeth'), completion('dress'), completion('', true, { points: 5 })];
    const awarded = kind === 'routine' ? rendered(card._renderChecklist(chore, 'mdi:star')) : view();
    assert.match(awarded.markup, /5 bonus points earned/);
  });
}

test('routine advances on a delayed all-steps server update without a second tap', async () => {
  const { card, attrs, view } = harness('routine');
  // A different screen submits the first step while this screen submits the
  // last step. Its completion arrives only after this service promise resolves.
  await view().step('dress').click();
  assert.equal(card._finished, false);
  attrs.chores.push({ id: 'school', name: 'Pack school bag', task_type: 'standard', points: 2, time_category: 'anytime' });
  attrs.chore_availability.school = { kid: true };
  attrs.todays_completions = [completion('teeth'), completion('dress')];
  const tree = view();
  assert.equal(card._runCompleted.has('ready'), true);
  assert.equal(card._tasks()[card._index].id, 'school');
  assert.match(tree.markup, /Pack school bag/);
  assert.equal(tree.step('dress'), undefined);
});

test('routine finishes on a delayed all-steps update when no other task remains', async () => {
  const { card, attrs, view } = harness('routine');
  await view().step('dress').click();
  attrs.todays_completions = [completion('teeth'), completion('dress'), completion('', true, { points: 4 })];
  const tree = view();
  assert.equal(card._finished, true);
  assert.match(tree.markup, /class="finished"/);
});

for (const kind of ['child', 'routine']) {
  for (const savedBonus of [10, 0]) {
    test(`${kind} preserves saved pending bonus ${savedBonus} after the chore bonus changes to 25`, async () => {
      const { card, chore, attrs, view } = harness(kind, { now: '2026-09-19T12:00:00Z' });
      await view().step('teeth').click();
      await view().step('dress').click();
      // Deliberately unsorted: choose the latest matching snapshot, not the
      // first/last array entry or a snapshot belonging to another child/chore.
      attrs.todays_completions = [
        completion('dress', false, { points: 3, checklist_bonus_points: savedBonus, completed_at: '2026-09-19T12:00:00Z' }),
        completion('teeth', false, { points: 2, checklist_bonus_points: 2, completed_at: '2026-09-19T11:00:00Z' }),
        completion('dress', false, { child_id: 'other', checklist_bonus_points: 90, completed_at: '2026-09-19T12:01:00Z' }),
        completion('dress', false, { chore_id: 'other', checklist_bonus_points: 80, completed_at: '2026-09-19T12:02:00Z' }),
      ];
      chore.points = 25;
      chore.effective_points = 25;
      view();
      const pending = kind === 'routine' ? rendered(card._renderChecklist(chore, 'mdi:star')) : view();
      assert.match(pending.markup, new RegExp(`Finish every step for \\+${savedBonus} bonus points`));
      if (kind === 'routine') {
        assert.equal(card._runCompleted.get('ready').pendingPoints, savedBonus + 5);
        assert.equal(card._runCompleted.get('ready').points, 0);
      }
      // A final approved parent record is authoritative, including any saved
      // award that differs from the earlier pending promise.
      attrs.todays_completions[0].approved = true;
      attrs.todays_completions[1].approved = true;
      attrs.todays_completions.push(completion('', true, { points: 7, completed_at: '2026-09-19T12:03:00Z' }));
      view();
      const awarded = kind === 'routine' ? rendered(card._renderChecklist(chore, 'mdi:star')) : view();
      assert.match(awarded.markup, /7 bonus points earned/);
      if (kind === 'routine') {
        assert.equal(card._runCompleted.get('ready').pendingPoints, 0);
        assert.equal(card._runCompleted.get('ready').points, 12);
      }
    });
  }
}
