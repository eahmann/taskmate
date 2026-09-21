const assert = require('node:assert/strict');
const { test } = require('node:test');
const { harness, rendered } = require('./card-harness.cjs');

const now = '2026-09-21T12:00:00Z';
function completion(extra = {}) {
  return { completion_id: 'done', chore_id: 'ready', child_id: 'kid', chore_name: 'Get ready',
    bonus_subtask_id: '', completed_at: now, approved: true,
    child_undo_until: '2026-09-21T12:00:30Z', ...extra };
}

for (const design of ['classic', 'playroom', 'console', 'cleanpro', 'accessible']) {
  for (const picture of [false, true]) {
    test(`${design}, picture ${picture}: hidden parent actions still allow bounded child undo`, async () => {
      const h = harness('child', { now, parent: true,
        config: { card_design: design, pre_reader: picture, show_parent_actions: false } });
      h.card._playSound = () => {};
      h.attrs.todays_completions = [completion()];
      // Routines preserve the same row actions as standalone chores.
      h.child.routines = [{ id: 'am', name: 'Morning', members: [{ chore_id: 'ready', required: true }],
        required_count: 1, completed_count: 1, bonus_points: 4, done: true }];
      let view = h.view();
      assert.match(view.markup, /tm-child-undo/);
      assert.match(view.markup, /data-routine-id="am"/);
      const undo = view.buttons.find(b => b.content.includes('Undo Get ready'));
      assert.ok(undo);
      await undo.click();
      assert.equal(h.calls.length, 1);
      assert.equal(h.calls[0].service, 'undo_chore');
      assert.equal(h.calls[0].data.completion_id, 'done');
      assert.equal(h.card._canUndoCompletions([completion()]), true);
      h.setNow('2026-09-21T12:00:30Z');
      view = h.view();
      assert.equal(view.buttons.some(b => b.content.includes('Undo Get ready')), false);
      assert.equal(h.card._canUndoCompletions([completion()]), false);
      await h.card._handleUndo(h.chore, h.child, [completion()]);
      assert.equal(h.calls.length, 1);
    });
  }
}

test('parent approval removes undo from a previously pending submission', async () => {
  const h = harness('child', { now, config: { show_parent_actions: false } });
  h.attrs.todays_completions = [completion({ approved: false, child_undo_until: undefined, child_undo_pending: true })];
  assert.match(h.view().markup, /Undo Get ready/);
  h.attrs.todays_completions = [completion({ child_undo_until: undefined })];
  h.touch();
  assert.doesNotMatch(h.view().markup, /Undo Get ready/);
  await h.card._handleUndo(h.chore, h.child, h.attrs.todays_completions);
  assert.equal(h.calls.length, 0);
});

test('older pending submission remains withdrawable after the chore leaves today’s list', async () => {
  const h = harness('child', { now, config: { show_parent_actions: false } });
  h.attrs.chore_completions = [completion({ approved: false, completed_at: '2026-09-19T12:00:00Z',
    child_undo_until: undefined, child_undo_pending: true })];
  h.attrs.chore_availability = { ready: { kid: false } };
  assert.match(h.view().markup, /Undo Get ready/);
  h.setNow('2026-09-22T12:00:00Z');
  assert.match(h.view().markup, /Undo Get ready/);
});

test('timed expiry schedules a render without needing an HA state change', () => {
  const h = harness('child', { now });
  h.attrs.todays_completions = [completion()];
  h.card.updated(new Map());
  const timer = h.timeouts.find(t => t.delay === 30001);
  assert.ok(timer);
  h.setNow('2026-09-21T12:00:30.001Z');
  timer.fn();
  assert.ok(h.card.updates > 0);
  assert.doesNotMatch(h.view().markup, /Undo Get ready/);
});

test('parent corrections remain available after expiry, but the quick undo uses the restricted route', async () => {
  const h = harness('child', { now, parent: true });
  h.card._playSound = () => {};
  h.attrs.todays_completions = [completion()];
  await h.view().buttons.find(b => b.content.includes('Undo Get ready')).click();
  assert.equal(h.calls[0].service, 'undo_chore');
  h.setNow('2026-09-21T12:01:00Z');
  await h.card._handleUndo(h.chore, h.child, [completion()]);
  assert.equal(h.calls[1].service, 'reject_chore');
});

test('repeat click is blocked while undo is in flight, and failed undo keeps optimistic state', async () => {
  const h = harness('child', { now, config: { show_parent_actions: false } });
  h.card._optimisticCompletions = { ready_kid: { count: 1 } };
  let release;
  h.card.hass.callService = async () => { await new Promise(resolve => { release = resolve; }); throw new Error('reviewed'); };
  const first = h.card._handleUndo(h.chore, h.child, [completion()]);
  await h.card._handleUndo(h.chore, h.child, [completion()]);
  assert.equal(h.card._loading.ready, true);
  release();
  await first;
  assert.ok(h.card._optimisticCompletions.ready_kid);
  assert.equal(h.card._loading.ready, false);
  assert.match(h.card.events[0].detail.message, /reviewed/);
});

test('bonus undo preserves optimistic completion of main chore and other bonuses', async () => {
  const h = harness('child', { now, config: { show_parent_actions: false } });
  h.card._playSound = () => {};
  h.card._optimisticCompletions = { ready_kid: {}, ready_bonus_teeth_kid: {}, ready_bonus_dress_kid: {} };
  await h.card._handleUndoBonusSubtask(h.chore, { id: 'teeth' }, h.child, [completion({ bonus_subtask_id: 'teeth' })]);
  assert.equal(h.calls[0].service, 'undo_chore');
  assert.ok(h.card._optimisticCompletions.ready_kid);
  assert.ok(h.card._optimisticCompletions.ready_bonus_dress_kid);
  assert.equal(h.card._optimisticCompletions.ready_bonus_teeth_kid, undefined);
});

test('guided routine finish screen supports undo and returns the task to the run', async () => {
  const h = harness('routine', { now });
  h.attrs.todays_completions = [completion()];
  h.card._runCompleted.set('ready', { points: 4, pending: false });
  h.card._finished = true;
  const button = h.view().buttons.find(b => b.content.includes('Undo Get ready'));
  assert.ok(button);
  await button.click();
  assert.equal(h.calls[0].service, 'undo_chore');
  assert.equal(h.card._finished, false);
  assert.equal(h.card._runCompleted.has('ready'), false);
  h.attrs.todays_completions = [];
  h.touch();
  assert.match(h.view().markup, /Get ready/);
  assert.ok(h.view().buttons.some(b => b.content.includes('Done')));
});

test('a pending sensor snapshot cannot override a reviewed today record', () => {
  const h = harness('child', { now });
  h.attrs.chore_completions = [completion({ child_undo_pending: true })];
  h.attrs.todays_completions = [completion({ child_undo_until: undefined })];
  assert.equal(h.window.__taskmate_chore_undo.candidates(h.attrs, 'kid').length, 0);
  assert.equal(h.window.__taskmate_chore_undo.candidates({ todays_completions: [completion()] }, 'sibling').length, 0);
});
