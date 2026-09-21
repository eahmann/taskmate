const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');
const www = path.join(__dirname, '../../custom_components/taskmate/www');
const messages = JSON.parse(readFileSync(path.join(www, 'locales/en.json'), 'utf8'));
const translate = (key, params = {}) => (messages[key] || key).replace(/\{(\w+)\}/g, (_, name) => params[name] ?? '');

function template(strings, ...values) { return { strings, values }; }
function render(value) {
  if (value == null) return '';
  if (Array.isArray(value)) return value.map(render).join('');
  if (typeof value === 'function') return '';
  if (value.strings) return value.strings.reduce((s, part, i) => s + part + render(value.values[i]), '');
  return String(value);
}

function cardHarness(design, picture) {
  class LitElement {
    constructor() { this.style = { setProperty() {}, removeProperty() {} }; }
    requestUpdate() {}
  }
  LitElement.prototype.html = template;
  LitElement.prototype.css = template;
  class View extends LitElement {}
  const elements = new Map([['hui-view', View]]);
  const window = {
    __taskmate_localize: (_, key, params) => translate(key, params),
    __taskmate_chore_visual: c => ({ kind: 'icon', icon: c.icon }),
    __taskmate_design: { apply: () => design },
    __taskmate_is_parent: () => false,
  };
  vm.runInNewContext(readFileSync(path.join(www, 'taskmate-child-card.js'), 'utf8'), {
    window, console, customElements: { get: n => elements.get(n), define: (n, c) => elements.set(n, c) },
    document: { querySelectorAll: () => [] }, URLSearchParams, Date, Intl,
    setTimeout() {}, clearTimeout() {},
  });
  const card = new (elements.get('taskmate-child-card'))();
  card.setConfig({ entity: 'sensor.taskmate_overview', child_id: 'kid', time_category: 'all',
    show_countdown: false, show_swaps: false, show_parent_actions: false,
    pre_reader: picture, pre_reader_labels: true, pre_reader_points: true, card_design: design });
  const chores = ['Teeth', 'Bed', 'Socks'].map((name, i) => ({
    id: String(i), name, points: 2, icon: 'mdi:star', assigned_to: ['kid'], time_category: 'anytime',
    enabled: true, requires_approval: true, task_type: 'standard',
  }));
  const child = { id: 'kid', name: 'Maggie', points: 0, routines: [{ id: 'am', name: 'Morning',
    members: [{ chore_id: '1', required: true }, { chore_id: '0', required: false }],
    bonus_points: 4, required_count: 1, completed_count: 0, pending_count: 0, done: false,
  }] };
  const attrs = { children: [child], chores, points_icon: 'mdi:diamond',
    chore_availability: Object.fromEntries(chores.map(c => [c.id, { kid: true }])), todays_completions: [] };
  const calls = [];
  card.hass = { user: { is_admin: false }, config: { time_zone: 'UTC' }, states: {
    'sensor.taskmate_overview': { attributes: attrs },
  }, async callService(domain, service, data) { calls.push({ domain, service, data }); } };
  return { card, attrs, child, chores, calls, markup: () => render(card.render()) };
}

for (const design of ['classic', 'playroom', 'console', 'cleanpro', 'accessible']) {
  for (const picture of [false, true]) {
    test(`${design}, picture ${picture}: real chore rows group once in routine order`, () => {
      const { markup } = cardHarness(design, picture);
      const html = markup();
      assert.match(html, /data-routine-id="am"/);
      const section = html.slice(html.indexOf('<section class="tm-routine"'), html.indexOf('</section>'));
      assert.match(section, /Morning/);
      assert.match(section, /0 \/ 1 required chores approved/);
      assert.match(section, /Optional: Teeth/);
      // Chore content occurs once; the optional caption names the optional item too.
      assert.equal((html.match(/>Bed</g) || []).length, 1);
      assert.equal((html.match(/>Teeth</g) || []).length, 1);
      assert.ok(section.indexOf('>Bed<') < section.indexOf('>Teeth<'));
      assert.equal((html.match(/>Socks</g) || []).length, 1);
      if (picture) assert.match(section, /pre-tile-points[\s\S]*mdi:diamond/);
    });
  }
}

test('routine items retain completion service, sound and animation', async () => {
  const { card, child, chores, calls } = cardHarness('classic', false);
  const sounds = [];
  card._playSound = sound => sounds.push(sound);
  card._spawnConfetti = () => {};
  await card._handleComplete(chores[1], child);
  assert.equal(calls[0].service, 'complete_chore');
  assert.equal(calls[0].data.chore_id, '1');
  assert.equal(card._celebrating, '1');
  assert.deepEqual(sounds, ['coin']);
});

test('pending approval is distinct from a completed routine', () => {
  const { child, markup } = cardHarness('classic', false);
  child.routines[0].pending_count = 1;
  assert.match(markup(), /1 waiting for parent approval/);
  assert.doesNotMatch(markup(), /Routine complete!/);
  child.routines[0].pending_count = 0;
  child.routines[0].done = true;
  assert.match(markup(), /Routine complete!/);
});

function panelHarness() {
  const elements = new Map();
  vm.runInNewContext(readFileSync(path.join(www, 'taskmate-panel.js'), 'utf8').replaceAll('import.meta.url', '"http://localhost/taskmate-panel.js"'), {
    HTMLElement: class {}, URL, confirm: () => true,
    customElements: { get: n => elements.get(n), define: (n, c) => elements.set(n, c) },
  });
  const panel = Object.create(elements.get('taskmate-panel').prototype);
  panel._t = translate;
  panel._render = () => {};
  panel._syncIconPickers = () => {};
  panel._fetchState = async () => {};
  panel._showToast = () => {};
  panel._state = { children: [{ id: 'kid', name: 'Maggie' }], chores: [], routines: [], settings: {} };
  return panel;
}

test('routine editor exposes full chore creation and preserves its draft on cancel', () => {
  const p = panelHarness();
  p._openRoutineDialog();
  p._dialog.data.name = 'Bedtime';
  p._dialog.data.assigned_to = ['kid'];
  p._dialog.data.time_category = 'night';
  const html = p._renderRoutineDialog();
  assert.match(html, /Create chore in routine/);
  assert.match(html, /Defaults for new chores/);
  p._openRoutineChore();
  assert.equal(p._dialog.kind, 'chore');
  assert.equal(p._dialog.data.time_category, 'night');
  assert.equal(p._dialog.data.assigned_to[0], 'kid');
  p._closeDialog(true);
  assert.equal(p._dialog.kind, 'routine');
  assert.equal(p._dialog.data.name, 'Bedtime');
});

test('new chore saves via ordinary endpoint and links back into the routine draft', async () => {
  const p = panelHarness();
  const calls = [];
  p._callWS = async payload => { calls.push(payload); return { ok: true, res: { id: 'new-chore' } }; };
  p._openRoutineDialog();
  p._dialog.data.name = 'Bedtime';
  p._openRoutineChore();
  p._dialog.data.name = 'Read a story';
  p._dialog.data.require_photo = true;
  await p._doSaveChore();
  assert.equal(calls[0].type, 'taskmate/add_chore');
  assert.equal(calls[0].require_photo, true);
  assert.equal(p._dialog.kind, 'routine');
  assert.equal(p._dialog.data.members[0].chore_id, 'new-chore');
  await p._doSaveRoutine();
  assert.equal(calls[1].type, 'taskmate/save_routine');
  assert.equal(calls[1].members[0].required, true);
});

test('other routines own their chores and member names are escaped', () => {
  const p = panelHarness();
  p._state.chores = [{ id: 'x', name: '<img src=x onerror=alert(1)>' }, { id: 'y', name: 'Evening teeth' }];
  p._state.routines = [{ id: 'other', members: [{ chore_id: 'y', required: true }] }];
  p._openRoutineDialog();
  const html = p._renderRoutineDialog();
  assert.doesNotMatch(html, /Evening teeth/);
  assert.match(html, /&lt;img/);
  assert.doesNotMatch(html, /<img/);
});
