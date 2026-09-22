const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const elements = new Map();
const source = readFileSync(path.join(
  __dirname, '../../custom_components/taskmate/www/taskmate-panel.js'
), 'utf8').replaceAll('import.meta.url', '"http://localhost/taskmate-panel.js?v=test"');
const locale = JSON.parse(readFileSync(path.join(
  __dirname, '../../custom_components/taskmate/www/locales/en.json'
), 'utf8'));
vm.runInNewContext(source, {
  HTMLElement: class {},
  customElements: {
    get: (name) => elements.get(name),
    define: (name, element) => elements.set(name, element),
  },
  URL,
  document: { createElement: () => ({ style: {}, setAttribute() {}, offsetWidth: 200, offsetHeight: 200 }) },
  window: { innerWidth: 1000, innerHeight: 1000, addEventListener() {} },
});

const plain = (value) => JSON.parse(JSON.stringify(value));

function harness(chore) {
  const panel = Object.create(elements.get('taskmate-panel').prototype);
  const requests = [], toasts = [];
  Object.assign(panel, {
    _state: { chores: chore ? [chore] : [], children: [], settings: {} },
    _render() {},
    querySelector: () => null,
    appendChild() {},
    _syncIconPickers() {},
    _closeDialog() {},
    _fetchState: async () => {},
    _t: (key, params = {}) => (locale[key] || key).replace(/\{(\w+)\}/g, (_, name) => params[name] ?? `{${name}}`),
    _callWS: async (request) => { requests.push(request); return { ok: true, res: { id: 'new-chore' } }; },
    _showToast: (kind, message) => toasts.push({ kind, message }),
  });
  panel._openChoreDialog(chore?.id);
  return { panel, requests, toasts };
}

function change(panel, field, value, event = 'change') {
  const target = { dataset: { field, rerender: field === 'task_type' ? 'true' : '' }, type: 'text', value };
  panel[event === 'input' ? '_onInput' : '_onChange']({ target });
}

test('selecting Checklist starts one item and disables an existing open-ended setting', () => {
  const { panel } = harness();
  panel._dialog.data.open_ended = true;
  change(panel, 'task_type', 'checklist', 'input');
  const firstId = panel._dialog.data.checklist_items[0].id;
  change(panel, 'task_type', 'checklist');
  assert.equal(panel._dialog.data.open_ended, false);
  assert.equal(panel._dialog.data.checklist_items.length, 1);
  assert.equal(panel._dialog.data.checklist_items[0].id, firstId);
  assert.ok(firstId.length > 0 && firstId.length <= 64);
  const markup = panel._renderChoreDialog();
  assert.match(markup, /<option value="checklist" selected>/);
  assert.match(markup, /data-field="checklist_items\[0\]\.name"/);
  assert.match(markup, /Items earn no points on their own/);
  assert.match(markup, /<ha-switch disabled aria-label=/);
  assert.doesNotMatch(markup, /data-field="open_ended"/);
  assert.doesNotMatch(panel._renderChecklistEditor(panel._dialog.data), /type="number"/);
});

test('checklist edit and reorder preserve IDs without changing the saved chore before Save', async () => {
  const chore = {
    id: 'dress', name: 'Get dressed', task_type: 'checklist', points: 2,
    checklist_items: [{ id: 'clothes', name: 'Put daytime clothes on' }, { id: 'pajamas', name: 'Put pajamas away' }],
  };
  const { panel, requests } = harness(chore);
  change(panel, 'checklist_items[0].name', ' Put clothes on ', 'input');
  // Browsers also fire change on blur; it must update the nested item, not create a stray field.
  change(panel, 'checklist_items[0].name', ' Put daytime clothes on ');
  panel._moveChecklistItem(1, -1);
  assert.equal(chore.checklist_items[0].id, 'clothes');
  assert.equal(chore.checklist_items[0].name, 'Put daytime clothes on');
  assert.equal(panel._dialog.data['checklist_items[0].name'], undefined);
  await panel._doSaveChore();
  assert.equal(requests[0].type, 'taskmate/update_chore');
  assert.equal(requests[0].points, 2);
  assert.deepEqual(plain(requests[0].checklist_items), [
    { id: 'pajamas', name: 'Put pajamas away' }, { id: 'clothes', name: 'Put daytime clothes on' },
  ]);
});

test('Checklist saves one fixed award and preserves normal chore options', async () => {
  const { panel, requests } = harness();
  change(panel, 'task_type', 'checklist');
  Object.assign(panel._dialog.data, {
    name: 'Get dressed', points: 2, open_ended: true, requires_approval: true,
    require_photo: true, assigned_to: ['maggie', 'ellie'], due_days: ['mon'],
    depends_on: ['wake-up'], bonus_subtasks: [{ id: 'extra', name: 'Help sibling', points: 1 }],
  });
  change(panel, 'checklist_items[0].name', 'Put clothes on');
  panel._addChecklistItem();
  change(panel, 'checklist_items[1].name', 'Put pajamas away');
  await panel._doSaveChore();
  const saved = plain(requests[0]);
  assert.equal(saved.type, 'taskmate/add_chore');
  assert.equal(saved.task_type, 'checklist');
  assert.equal(saved.points, 2);
  assert.equal(saved.open_ended, false);
  assert.equal(saved.requires_approval, true);
  assert.equal(saved.require_photo, true);
  assert.deepEqual(saved.assigned_to, ['maggie', 'ellie']);
  assert.deepEqual(saved.due_days, ['mon']);
  assert.deepEqual(saved.depends_on, ['wake-up']);
  assert.equal(saved.bonus_subtasks[0].id, 'extra');
  assert.equal(new Set(saved.checklist_items.map(item => item.id)).size, 2);
  assert.ok(saved.checklist_items.every(item => Object.keys(item).sort().join(',') === 'id,name'));
});

for (const items of [[], [{ id: 'blank', name: '   ' }], [{ id: 'long', name: 'x'.repeat(201) }],
  Array.from({ length: 31 }, (_, i) => ({ id: `id-${i}`, name: `Item ${i}` }))]) {
  test(`invalid checklist cannot save (${items.length} items, first name length ${items[0]?.name.length || 0})`, async () => {
    const { panel, requests, toasts } = harness();
    Object.assign(panel._dialog.data, { name: 'Get dressed', task_type: 'checklist', checklist_items: items });
    await panel._doSaveChore();
    assert.equal(requests.length, 0);
    assert.equal(toasts[0].kind, 'err');
    assert.match(toasts[0].message, /1–30 checklist items/);
  });
}

test('add limit, removing an item, and invalid move controls preserve remaining identities', () => {
  const { panel } = harness();
  change(panel, 'task_type', 'checklist');
  for (let i = 0; i < 35; i++) panel._addChecklistItem();
  const items = panel._dialog.data.checklist_items;
  assert.equal(items.length, 30);
  const ids = items.map(item => item.id);
  panel._moveChecklistItem(0, -1);
  panel._moveChecklistItem(29, 1);
  panel._moveChecklistItem(-1, 1);
  assert.deepEqual(plain(items.map(item => item.id)), plain(ids));
  panel._removeChecklistItem(10);
  assert.deepEqual(plain(items.map(item => item.id)), plain(ids.filter((_, i) => i !== 10)));
});

test('switching away and back keeps the draft; saving another type clears checklist definition', async () => {
  const { panel, requests } = harness();
  change(panel, 'task_type', 'checklist');
  change(panel, 'checklist_items[0].name', 'Put clothes on');
  const itemId = panel._dialog.data.checklist_items[0].id;
  change(panel, 'task_type', 'timed');
  change(panel, 'task_type', 'checklist');
  assert.equal(panel._dialog.data.checklist_items[0].id, itemId);
  assert.equal(panel._dialog.data.checklist_items[0].name, 'Put clothes on');
  change(panel, 'task_type', 'standard');
  panel._dialog.data.name = 'Get dressed';
  await panel._doSaveChore();
  assert.deepEqual(plain(requests[0].checklist_items), []);
});

test('routine Add chore links a saved Checklist using the normal chore save flow', async () => {
  const { panel, requests } = harness();
  panel._routineReturn = { dialog: { data: { members: [] } } };
  change(panel, 'task_type', 'checklist');
  panel._dialog.data.name = 'Get dressed';
  change(panel, 'checklist_items[0].name', 'Put clothes on');
  await panel._doSaveChore();
  assert.equal(requests[0].task_type, 'checklist');
  assert.deepEqual(plain(panel._routineReturn.dialog.data.members), [{ chore_id: 'new-chore', required: true }]);
});

test('parent row menu cannot bypass required checklist items', () => {
  for (const task_type of ['standard', 'checklist']) {
    const { panel } = harness({ id: 'dress', name: 'Get dressed', task_type });
    panel._state.parent_completable = { dress: true };
    panel._openRowMenu({ setAttribute() {}, getBoundingClientRect: () => ({ right: 300, bottom: 200 }) }, 'dress');
    assert.equal(panel._rowMenuEl.innerHTML.includes('parent-complete-chore'), task_type === 'standard');
    assert.match(panel._rowMenuEl.innerHTML, /clone-chore/);
  }
});
