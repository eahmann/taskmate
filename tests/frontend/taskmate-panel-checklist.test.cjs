const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const elements = new Map();
const source = readFileSync(path.join(
  __dirname, '../../custom_components/taskmate/www/taskmate-panel.js'
), 'utf8').replaceAll('import.meta.url', '"http://localhost/taskmate-panel.js?v=test"');
const strings = JSON.parse(readFileSync(path.join(
  __dirname, '../../custom_components/taskmate/www/locales/en.json'
), 'utf8'));
vm.runInNewContext(source, {
  HTMLElement: class {},
  customElements: {
    get: (name) => elements.get(name),
    define: (name, element) => elements.set(name, element),
  },
  URL,
});

function harness(data = {}) {
  const events = [];
  const panel = Object.create(elements.get('taskmate-panel').prototype);
  Object.assign(panel, {
    _state: { children: [], chores: [], settings: {} },
    _hass: { states: {} },
    _t: (key, params = {}) => Object.entries(params).reduce(
      (text, [name, value]) => text.replaceAll(`{${name}}`, value), strings[key] || key
    ),
    _openDialog(dialog) { this._dialog = dialog; },
    _render() { events.push(['render']); },
    querySelectorAll() { return []; },
    _callWS: async (payload) => { events.push(['save', payload]); return { ok: true }; },
    _closeDialog() { events.push(['close']); },
    _fetchState: async () => events.push(['refresh']),
    _showToast: (kind, message) => events.push(['toast', kind, message]),
  });
  panel._openChoreDialog();
  Object.assign(panel._dialog.data, {
    name: 'Morning routine', task_type: 'checklist',
    bonus_subtasks: [{ id: 'brush', name: 'Brush teeth', points: 0, icon: 'mdi:toothbrush' }],
    ...data,
  });
  return { panel, events };
}

test('checklist editor presents required steps and automatic completion bonus', () => {
  const { panel } = harness({ require_photo: true, open_ended: true, daily_limit: 4, assignment_mode: 'alternating' });
  const html = panel._renderChoreDialog();
  assert.match(html, /value="checklist" selected/);
  assert.match(html, /Completion bonus/);
  assert.match(html, /automatically after every step is approved/);
  assert.match(html, /Checklist steps/);
  assert.match(html, /once per day/);
  assert.match(html, /Each assigned child completes their own checklist/);
  assert.match(html, /data-field="checklist_sequential"/);
  assert.match(html, /ha-icon-picker data-field="bonus_subtasks\[0\]\.icon"/);
  assert.match(html, /value="0" data-field="bonus_subtasks\[0\]\.points"/);
  assert.doesNotMatch(html, /data-field="(?:daily_limit|require_photo|open_ended|assignment_mode|manual_start_child_id)"/);
  assert.doesNotMatch(html, /data-section="bonus_subtasks"/);
});

test('standard and timed editors retain optional bonus controls and daily limits', () => {
  for (const task_type of ['standard', 'timed']) {
    const { panel } = harness({ task_type });
    const html = panel._renderChoreDialog();
    assert.match(html, /data-section="bonus_subtasks"/);
    assert.match(html, /data-field="daily_limit"/);
    assert.doesNotMatch(html, /tm-checklist-editor/);
  }
});

test('checklist save keeps zero-point steps, stable IDs, description and icon', async () => {
  const { panel, events } = harness({
    points: 0, daily_limit: 4, checklist_sequential: true, require_photo: true, open_ended: true, assignment_mode: 'alternating',
  });
  panel._onChange({ target: { dataset: { field: 'bonus_subtasks[0].points' }, type: 'number', value: '0' } });
  panel._onInput({ target: { dataset: { field: 'bonus_subtasks[0].name' }, value: '  Brush teeth  ' } });
  panel._onChange({ target: { dataset: { field: 'bonus_subtasks[0].description' }, value: 'Two minutes' } });
  panel._onValueChanged({ target: { dataset: { field: 'bonus_subtasks[0].icon' } }, detail: { value: 'mdi:tooth' } });
  await panel._doSaveChore();
  const payload = events.find(([event]) => event === 'save')[1];
  assert.equal(payload.task_type, 'checklist');
  assert.equal(payload.checklist_sequential, true);
  assert.equal(payload.daily_limit, 1);
  assert.equal(payload.assignment_mode, 'everyone');
  assert.equal(payload.points, 0);
  assert.equal(payload.require_photo, false);
  assert.equal(payload.open_ended, false);
  assert.deepEqual(JSON.parse(JSON.stringify(payload.bonus_subtasks)), [{
    id: 'brush', name: 'Brush teeth', description: 'Two minutes', points: 0, icon: 'mdi:tooth',
  }]);
  assert.equal(Object.keys(panel._dialog.data).some(key => key.startsWith('bonus_subtasks[')), false);
});

test('checklist save refuses an empty or unnamed list before calling the backend', async () => {
  for (const bonus_subtasks of [[], [{ name: '  ', points: 5 }]]) {
    const { panel, events } = harness({ bonus_subtasks });
    await panel._doSaveChore();
    assert.equal(events.some(([event]) => event === 'save'), false);
    assert.ok(events.some(([event, kind, message]) => event === 'toast' && kind === 'err' && message.includes('at least one')));
  }
});

test('moving and removing a checklist step preserves edited picker value and step identity', () => {
  const first = { id: 'brush', name: 'Brush', points: 0 };
  const second = { id: 'dress', name: 'Get dressed', points: 2 };
  const { panel } = harness({ bonus_subtasks: [first, second] });
  panel.querySelectorAll = () => [{ dataset: { field: 'bonus_subtasks[0].icon' }, value: 'mdi:toothbrush' }];
  panel._moveBonusSubtask(0, 1);
  assert.equal(panel._dialog.data.bonus_subtasks[0], second);
  assert.equal(panel._dialog.data.bonus_subtasks[1], first);
  assert.equal(first.icon, 'mdi:toothbrush');
  panel.querySelectorAll = () => [];
  panel._moveBonusSubtask(0, -1);
  assert.equal(panel._dialog.data.bonus_subtasks[0], second);
  panel._removeBonusSubtask(0);
  assert.equal(panel._dialog.data.bonus_subtasks.length, 1);
  assert.equal(panel._dialog.data.bonus_subtasks[0], first);
});

test('optional zero-point bonus remains zero and checklist sequencing defaults off', async () => {
  const { panel, events } = harness({ task_type: 'standard', daily_limit: 3 });
  await panel._doSaveChore();
  const payload = events.find(([event]) => event === 'save')[1];
  assert.equal(payload.bonus_subtasks[0].points, 0);
  assert.equal(payload.daily_limit, 3);
  assert.equal(payload.checklist_sequential, false);
});

test('checklist template preview labels the bonus and keeps the daily limit fixed', () => {
  const { panel } = harness();
  const checklist = { ...panel._dialog.data, _expanded: true, daily_limit: 4 };
  const html = panel._renderTemplateChoreCard(checklist, 0);
  assert.match(html, /Completion bonus/);
  assert.match(html, /automatically after every step is approved/);
  assert.match(html, /data-tpl-field="daily_limit"[^>]*value="1"[^>]*disabled/);
  assert.doesNotMatch(html, /data-tpl-field="assignment_mode"/);
  const standard = panel._renderTemplateChoreCard({ ...checklist, task_type: 'standard' }, 0);
  assert.match(standard, /data-tpl-field="daily_limit"[^>]*value="4"/);
  assert.match(standard, /data-tpl-field="assignment_mode"/);
});

test('checklist template editor labels parent points as the completion bonus', () => {
  const { panel } = harness();
  panel._dialog = {
    kind: 'edit-template',
    data: { name: 'Morning', chores: [panel._dialog.data] },
  };
  const html = panel._renderCreateEditTemplateDialog();
  assert.match(html, /Completion bonus/);
  assert.match(html, /automatically after every step is approved/);
});

for (const method of ['_doApplyTemplate', '_doSaveCreatedTemplate', '_doSaveEditedTemplate']) {
  test(`${method} preserves checklist steps and enforces a single daily completion`, async () => {
    const { panel, events } = harness();
    const checklist = {
      ...panel._dialog.data, points: 0, daily_limit: 9, assignment_mode: 'alternating',
      _expanded: true, require_photo: true, open_ended: true,
    };
    const standard = { name: 'Reading', task_type: 'standard', daily_limit: 3 };
    panel._templateChores = [checklist, standard];
    panel._dialog = { data: { template_id: 'template', name: 'Morning', chores: [checklist, standard] } };
    await panel[method]();
    const payload = events.find(([event]) => event === 'save')[1];
    assert.equal(payload.chores[0].daily_limit, 1);
    assert.equal(payload.chores[0].assignment_mode, 'everyone');
    assert.equal(payload.chores[0].points, 0);
    assert.equal(payload.chores[0].bonus_subtasks[0].id, 'brush');
    assert.equal(payload.chores[0].bonus_subtasks[0].points, 0);
    assert.equal(payload.chores[0].require_photo, false);
    assert.equal(payload.chores[0].open_ended, false);
    assert.equal('_expanded' in payload.chores[0], false);
    assert.equal(payload.chores[1].daily_limit, 3);
    assert.equal(checklist.daily_limit, 9, 'preparing the payload does not mutate the source template');
  });
}

test('template totals include required checklist earnings and exclude optional bonus tasks', () => {
  const { panel } = harness();
  const checklist = {
    ...panel._dialog.data, points: 10,
    bonus_subtasks: [{ name: 'Brush', points: 0 }, { name: 'Dress', points: '5' }],
  };
  const standard = { name: 'Reading', points: '7', bonus_subtasks: [{ name: 'Extra', points: 100 }] };
  const template = { id: 'morning', name: 'Morning', chores: [checklist, standard] };
  panel._t = (key, params = {}) => `${key}:${JSON.stringify(params)}`;
  panel._templateSelected = template;
  panel._templateChores = template.chores;
  assert.match(panel._renderManageTemplateCard(template, false), /template_pts_total:\{"count":2,"points":22\}/);
  assert.match(panel._renderTemplatePickerCard(template), /template_pts_total:\{"count":2,"points":22\}/);
  assert.match(panel._renderTemplatePreview(), /template_confirm_bar:\{"count":2,"points":22\}/);
  panel._state.chores = template.chores;
  panel._saveTemplateDialog = true;
  const saveMarkup = panel._renderSaveTemplateDialog();
  assert.match(saveMarkup, /pts_display:\{"count":15\}/);
  assert.match(saveMarkup, /pts_display:\{"count":7\}/);
});
