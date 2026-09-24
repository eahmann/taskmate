const assert = require('node:assert/strict');
const { test } = require('node:test');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { harness, rendered } = require('./card-harness.cjs');
const www = path.join(__dirname, '../../custom_components/taskmate/www');
const messages = JSON.parse(readFileSync(path.join(www, 'locales/en.json'), 'utf8'));

function pageHarness() {
  const template = (strings, ...values) => ({ strings, values });
  class LitElement {}
  LitElement.prototype.html = template;
  LitElement.prototype.css = template;
  class View extends LitElement {}
  class Content { setConfig(value) { this.config = value; } }
  const registry = new Map([['hui-view', View], ['taskmate-child-card', Content], ['taskmate-rewards-card', Content]]);
  const navigation = [];
  const window = {
    __taskmate_localize: (_, key) => messages[key] || key,
    location: { pathname: '/kids/maggie' },
    history: { pushState: (...args) => navigation.push(args) },
    dispatchEvent: event => navigation.push(event.type),
  };
  vm.runInNewContext(readFileSync(path.join(www, 'taskmate-child-page-card.js'), 'utf8'), {
    window, customElements: { get: tag => registry.get(tag), define: (tag, cls) => registry.set(tag, cls) },
    document: { createElement: tag => Object.assign(new Content(), { localName: tag }) },
    CustomEvent: class { constructor(type) { this.type = type; } },
  });
  const page = new (registry.get('taskmate-child-page-card'))();
  const config = { entity: 'sensor.taskmate_overview', child_id: 'maggie',
    chores_path: '/kids/maggie', rewards_path: '/kids/maggie-rewards' };
  page.setConfig(config);
  const children = [{ id: 'maggie', name: 'Maggie', points: 15, spendable_balance: 5, committed_points: 10 },
    { id: 'ellie', name: 'Ellie', points: 99 }];
  page.hass = { states: { 'sensor.taskmate_overview': { attributes: { children, points_name: 'Stars' } } } };
  return { page, config, children, navigation, view: () => rendered(page.render()).markup };
}

test('workspace pins both bodies to the selected child and forwards fresh state', () => {
  const { page, config } = pageHarness();
  page.updated(new Map([['hass', null]]));
  assert.equal(page._content.hass, page.hass);
  assert.equal(page._content.localName, 'taskmate-child-card');
  page.setConfig({ ...config, view: 'rewards', reward_options: { child_id: 'ellie', entity: 'wrong', app_layout: false } });
  assert.equal(page._content.localName, 'taskmate-rewards-card');
  assert.equal(page._content.config.child_id, 'maggie');
  assert.equal(page._content.config.entity, config.entity);
  assert.equal(page._content.config.app_layout, true);
  assert.equal(page._content.hass, page.hass);
  page.setConfig({ ...config, chore_options: { show_parent_actions: true } });
  assert.equal(page._content.config.show_parent_actions, false);
});

test('header uses spendable Stars and does not fall back to another child', () => {
  const { page, children, view } = pageHarness();
  assert.match(view(), /aria-label=5 Stars/);
  assert.match(view(), /10 Stars · Awaiting parent approval/i);
  assert.doesNotMatch(view(), /99/);
  children.splice(0, 1);
  assert.match(view(), /information is unavailable/);
  assert.doesNotMatch(view(), /Ellie|99/);
  delete page.hass.states['sensor.taskmate_overview'];
  assert.match(view(), /information is unavailable/);
});

test('page tabs have real links and preserve modified clicks and browser navigation', () => {
  const { page, config, view, navigation } = pageHarness();
  assert.match(view(), /href=\/kids\/maggie aria-current=page/);
  let prevented = 0;
  const event = { button: 0, preventDefault: () => prevented++ };
  page._navigate({ ...event, ctrlKey: true }, config.rewards_path);
  assert.equal(prevented, 0);
  page._navigate(event, config.rewards_path);
  assert.equal(prevented, 1);
  assert.equal(navigation[0][2], config.rewards_path);
  assert.equal(navigation[1], 'location-changed');
  for (const invalid of ['//example.com', 'javascript:alert(1)', '/\\example.com']) {
    assert.throws(() => page.setConfig({ ...config, chores_path: invalid }), /local dashboard path/);
  }
});

test('app chore rows retain completion, celebration and child undo', async () => {
  const { card, attrs, child, chore, calls, view, touch } = harness('child', {
    config: { app_layout: true, card_design: 'playroom', show_description: true },
    chore: { bonus_subtasks: [], points: 0, description: 'Put your pajamas on.' },
  });
  card._playSound = () => {};
  card._spawnConfetti = () => {};
  const complete = card._handleComplete.bind(card);
  let completion;
  card._handleComplete = (...args) => { completion = complete(...args); return completion; };
  assert.doesNotMatch(view().markup, /<details/);
  assert.match(view().markup, /Put your pajamas on/);
  const done = view().buttons.find(b => b.content === 'Done');
  assert.ok(done);
  await done.click();
  await completion;
  assert.equal(calls[0].service, 'complete_chore');
  assert.equal(calls[0].data.child_id, child.id);
  assert.equal(card._celebrating, chore.id);
  attrs.settings = { child_undo_window_seconds: 60 };
  attrs.todays_completions = [{ id: 'submitted', child_id: child.id, chore_id: chore.id,
    approved: false, child_undo_pending: true, completed_at: new Date().toISOString() }];
  touch();
  const undo = view().buttons.find(b => b.attrs.includes('tmd-undochip'));
  assert.ok(undo);
  await undo.click();
  assert.equal(calls.at(-1).service, 'undo_chore');
  assert.equal(calls.at(-1).data.completion_id, 'submitted');
});

test('app rewards retain affordability and pending-claim guards', () => {
  const { attrs, view, touch } = harness('rewards', { config: { app_layout: true, card_design: 'playroom' } });
  attrs.rewards = [{ id: 'music', name: 'Music', cost: 10, assigned_to: ['kid'], description: 'Choose a song.' }];
  attrs.reward_claims = [];
  assert.match(view().markup, /Choose a song/);
  assert.doesNotMatch(view().markup, /<details/);
  assert.equal(view().buttons.find(b => b.content === 'Claim reward').disabled, true);
  attrs.children[0].points = 15;
  assert.equal(view().buttons.find(b => b.content === 'Claim reward').disabled, false);
  attrs.pending_reward_claims = [{ reward_id: 'music', child_id: 'kid', approved: false }];
  touch();
  assert.match(view().markup, /Awaiting parent approval/i);
  assert.equal(view().buttons.some(b => b.content === 'Claim reward'), false);
});
