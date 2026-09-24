const assert = require('node:assert/strict');
const { test } = require('node:test');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const { harness, rendered } = require('./card-harness.cjs');
const www = path.join(__dirname, '../../custom_components/taskmate/www');

test('child color inherits, supports card overrides, ignores malformed backups and resets', () => {
  const window = {};
  vm.runInNewContext(readFileSync(path.join(www, 'taskmate-design.js'), 'utf8'), {
    window, document: { getElementById: () => true },
  });
  const design = window.__taskmate_design;
  const child = { id: 'kid', color: '#4ec9bb' };
  const hass = { states: { sensor: { attributes: { children: [child] } } } };
  assert.equal(design.childColor(child), '#4ec9bb');
  assert.equal(design.childColor(child, '#b885e3'), '#b885e3');
  assert.equal(design.childColor({ color: 'red;display:none' }, '', '#123456'), '#123456');
  const tokens = new Map();
  const host = { setAttribute() {}, toggleAttribute() {}, style: { setProperty: (k, v) => tokens.set(k, v), removeProperty: k => tokens.delete(k) } };
  design.apply(host, hass, { entity: 'sensor', child_id: 'kid' }, 'sensor');
  assert.equal(tokens.get('--tmd-accent'), '#4ec9bb');
  child.color = '';
  design.apply(host, hass, { entity: 'sensor', child_id: 'kid' }, 'sensor');
  assert.equal(tokens.size, 0);
  assert.equal(design.onColor('#ffffff'), '#111111');
  assert.equal(design.onColor('#000000'), '#ffffff');
});

function grouped() {
  const h = harness('child', { config: { app_layout: true, card_design: 'playroom', dependency_mode: 'show' } });
  h.attrs.chores = ['Teeth', 'Pajamas', 'Set table', 'Wipe table', 'Match socks'].map((name, i) => ({
    ...h.chore, id: String(i), name, bonus_subtasks: [], display_category: i === 4 ? 'Laundry' : '',
  }));
  h.attrs.chore_availability = Object.fromEntries(h.attrs.chores.map(c => [c.id, { kid: true }]));
  h.child.routines = [{ id: 'evening', name: 'Evening', required_count: 2, completed_count: 0, pending_count: 0, bonus_points: 5,
    members: [{ chore_id: '0', required: true }, { chore_id: '1', required: true }] },
  { id: 'dinner', name: 'Dinner', required_count: 2, completed_count: 1, pending_count: 0, bonus_points: 2,
    members: [{ chore_id: '2', required: true }, { chore_id: '3', required: true }] }];
  return h;
}

test('all routines and categories appear once; filter retains real chore controls', async () => {
  const h = grouped();
  let view = h.view();
  assert.match(view.markup, /Evening/);
  assert.match(view.markup, /Dinner/);
  assert.match(view.markup, /0 \/ 2 required chores approved/);
  assert.match(view.markup, /1 \/ 2 required chores approved/);
  assert.equal((view.markup.match(/>Match socks</g) || []).length, 1);
  await view.buttons.find(b => b.content === 'Laundry').click();
  view = h.view();
  assert.doesNotMatch(view.markup, /data-routine-id/);
  assert.match(view.markup, /Match socks/);
  const rowDone = view.buttons.find(b => b.content.includes('Done'));
  h.card._playSound = () => {};
  h.card._spawnConfetti = () => {};
  await rowDone.click();
  assert.equal(h.calls[0].service, 'complete_chore');
  assert.equal(h.calls[0].data.chore_id, '4');
});

test('overview next skips submitted, blocked and unavailable tasks independently per routine', () => {
  const h = grouped();
  h.attrs.todays_completions = [{ chore_id: '0', child_id: 'kid', approved: false, completed_at: new Date().toISOString() }];
  h.attrs.chores[1].depends_on = ['0'];
  h.child.routines[0].pending_count = 1;
  h.attrs.chore_availability['1'].kid = false;
  let data = h.card.overviewData(h.child);
  assert.equal(data.routines.length, 2);
  assert.equal(data.routines[0].next, undefined);
  assert.equal(data.routines[0].pending_count, 1);
  assert.equal(data.routines[1].next.id, '2');
  assert.deepEqual(Array.from(data.other, c => c.name), ['Match socks']);
  assert.equal(h.attrs.chores[1]._isDependencyBlocked, undefined, 'overview must not annotate shared entity state');
  h.attrs.todays_completions[0].approved = true;
  h.attrs.chore_availability['1'].kid = true;
  data = h.card.overviewData(h.child);
  assert.equal(data.routines[0].next.id, '1');
  h.attrs.chores[4].enabled = false;
  assert.equal(h.card.overviewData(h.child).other.length, 0);
});

test('vanished category falls back to All without hiding remaining routines', async () => {
  const h = grouped();
  await h.view().buttons.find(b => b.content === 'Laundry').click();
  h.attrs.chores[4].enabled = false;
  assert.match(h.view().markup, /data-routine-id="evening"/);
  assert.equal(h.card._displayFilter, 'all');
});

test('family page links to the configured PIN route and routine, with spendable balance', () => {
  const h = grouped();
  const template = (strings, ...values) => ({ strings, values });
  class Lit { requestUpdate() {} }
  Lit.prototype.html = template; Lit.prototype.css = template;
  class View extends Lit {}
  const registry = new Map([['hui-view', View], ['taskmate-child-card', true]]);
  const window = { location: { origin: 'http://ha.local' }, __taskmate_localize: h.window.__taskmate_localize };
  vm.runInNewContext(readFileSync(path.join(www, 'taskmate-family-page-card.js'), 'utf8'), {
    window, URL, document: { createElement: () => h.card },
    customElements: { get: n => registry.get(n), define: (n, c) => registry.set(n, c), whenDefined: () => Promise.resolve() },
  });
  const card = new (registry.get('taskmate-family-page-card'))();
  const config = { entity: 'sensor.taskmate_overview', parents_path: '/family/parents',
    child_pages: [{ child_id: 'kid', chores_path: '/family/kid?example=1', rewards_path: '/family/rewards' }] };
  card.setConfig(config); card.hass = h.card.hass; h.child.spendable_balance = 3;
  const markup = rendered(card.render()).markup;
  assert.match(markup, /href=\/family\/parents/);
  assert.match(markup, /href=\/family\/kid\?example=1&routine=evening/);
  assert.match(markup, /href=\/family\/kid\?example=1&routine=dinner/);
  assert.match(markup, /3 <small>Stars/);
  assert.equal(h.calls.length, 0);
  assert.throws(() => card.setConfig({ ...config, parents_path: '//evil.example' }), /local dashboard path/);
  assert.throws(() => card.setConfig({ ...config, child_pages: [...config.child_pages, ...config.child_pages] }), /unique/);
});
