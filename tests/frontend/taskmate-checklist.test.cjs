const assert = require('node:assert/strict');
const { test } = require('node:test');
const { harness } = require('./card-harness.cjs');

const now = '2026-09-22T12:00:00Z';
function setup(kind = 'child', config = {}, extra = {}) {
  const h = harness(kind, { now, config: { show_parent_actions: false, ...config }, chore: {
    name: 'Get dressed', task_type: 'checklist', points: 2, bonus_subtasks: [],
    checklist_items: [{ id: 'clothes', name: 'Put daytime clothes on' }, { id: 'pajamas', name: 'Put pajamas away' }], ...extra,
  } });
  h.child.checklist_progress = { ready: {
    occurrence_id: 'today:0', completion_id: '', items: h.chore.checklist_items.map(item => ({ ...item, checked: false })),
  } };
  h.sounds = [];
  h.card._playSound = value => h.sounds.push(value);
  h.card._spawnConfetti = () => {};
  h.card.hass.callService = async (domain, service, data, target, notifyOnError, responseRequested) => {
    h.calls.push({ domain, service, data, responseRequested });
    if (service !== 'set_checklist_item') return;
    const previous = h.window.__taskmate_checklist.progress(h.card, h.child, h.chore);
    const items = previous.items.map(item => ({ ...item, checked: item.id === data.item_id ? data.checked : item.checked }));
    const completed = items.every(item => item.checked) && !previous.completion_id;
    const progress = { ...previous, items, approved: false, completion_id: items.every(item => item.checked) ? 'done' : '',
      child_undo_pending: completed || undefined };
    return { response: { progress, completed, completion_id: completed ? 'done' : '', approved: false, points_awarded: 2 } };
  };
  return h;
}

for (const design of ['classic', 'playroom', 'console', 'cleanpro', 'accessible']) {
  for (const picture of [false, true]) {
    test(`${design}, picture ${picture}: checklist submits only the final item and works inside a routine`, async () => {
      const h = setup('child', { card_design: design, pre_reader: picture });
      h.child.routines = [{ id: 'morning', name: 'Morning', members: [{ chore_id: 'ready', required: true }],
        required_count: 1, completed_count: 0, bonus_points: 2 }];
      let view = h.view();
      assert.match(view.markup, /data-routine-id="morning"/);
      assert.equal((view.markup.match(/data-step-id="clothes"/g) || []).length, 1);
      assert.ok(view.step('clothes'));
      assert.ok(view.step('pajamas'));
      assert.equal(view.buttons.filter(button => !button.step).length, 0);
      // Order is optional: the second item can be checked first.
      await view.step('pajamas').click();
      assert.equal(h.calls[0].service, 'set_checklist_item');
      assert.equal(h.calls[0].data.item_id, 'pajamas');
      assert.equal(h.calls[0].data.occurrence_id, 'today:0');
      assert.equal(h.calls[0].responseRequested, true);
      assert.equal(h.card._celebrating, null);
      assert.equal(h.sounds.length, 0);
      view = h.view();
      assert.match(view.step('pajamas').attrs, /aria-checked="true"/);
      await view.step('clothes').click();
      assert.equal(h.calls.length, 2);
      assert.equal(h.card._celebrating, 'ready');
      assert.deepEqual(h.sounds, ['coin']);
      // The award belongs to the whole chore, never to its item rows.
      assert.doesNotMatch(h.view().step('clothes').content, /\+2/);
    });
  }
}

test('a new server snapshot wins over response cache and progress never leaks between children', async () => {
  const h = setup();
  await h.view().step('clothes').click();
  assert.equal(h.child.checklist_progress.ready.items[0].checked, false);
  assert.equal(h.chore.checklist_items[0].checked, undefined);
  const sibling = { id: 'sibling', checklist_progress: { ready: { ...h.child.checklist_progress.ready } } };
  assert.equal(h.window.__taskmate_checklist.progress(h.card, sibling, h.chore).items[0].checked, false);
  h.child.checklist_progress.ready = { occurrence_id: 'tomorrow:0', completion_id: '', items: h.chore.checklist_items.map(item => ({ ...item, checked: false })) };
  h.touch();
  assert.match(h.view().step('clothes').attrs, /aria-checked="false"/);
  await h.view().step('clothes').click();
  assert.equal(h.calls[1].data.occurrence_id, 'tomorrow:0');
});

for (const kind of ['child', 'routine']) {
  test(`${kind}: completed items can be unchecked only while server undo eligibility remains`, async () => {
    const h = setup(kind);
    h.child.checklist_progress.ready = { ...h.child.checklist_progress.ready,
      items: h.child.checklist_progress.ready.items.map(item => ({ ...item, checked: true })),
      completion_id: 'done', child_undo_until: '2026-09-22T12:00:10Z' };
    await h.view().step('pajamas').click();
    assert.equal(h.calls[0].data.checked, false);
    assert.match(h.view().step('clothes').attrs, /aria-checked="true"/);
    assert.match(h.view().step('pajamas').attrs, /aria-checked="false"/);
    h.child.checklist_progress.ready = { ...h.child.checklist_progress.ready };
    h.setNow('2026-09-22T12:00:10Z');
    assert.equal(h.view().step('pajamas').disabled, true);
    await h.view().step('pajamas').click();
    assert.equal(h.calls.length, 1);
  });

  test(`${kind}: service failures preserve ticks and show an error without celebrating`, async () => {
    const h = setup(kind);
    h.card.hass.callService = async () => { throw new Error('No longer available'); };
    await h.view().step('clothes').click();
    assert.match(h.view().step('clothes').attrs, /aria-checked="false"/);
    assert.match(h.card.events[0].detail.message, /No longer available/);
    assert.equal(!!h.card._celebrating || !!h.card._checklistCelebration, false);
  });

  test(`${kind}: server duplicate acknowledgement never produces an award or celebration`, async () => {
    const h = setup(kind);
    h.child.checklist_progress.ready.items[0].checked = true;
    h.card.hass.callService = async () => ({ response: { completed: false } });
    await h.view().step('pajamas').click();
    assert.equal(!!h.card._celebrating || !!h.card._checklistCelebration, false);
    assert.equal(h.card._runCompleted?.size || 0, 0);
  });
}

test('guided routine checks individual items, advances once on final submission, and never renders a Done claim button', async () => {
  const h = setup('routine');
  let view = h.view();
  assert.equal(view.buttons.some(button => /rt-done/.test(button.attrs)), false);
  await view.step('clothes').click();
  assert.equal(h.card._finished, false);
  assert.equal(h.card._runCompleted.size, 0);
  view = h.view();
  await view.step('pajamas').click();
  assert.equal(h.card._finished, true);
  assert.equal(h.card._runCompleted.get('ready').points, 2);
  assert.equal(h.card._runCompleted.get('ready').pending, true);
  assert.match(h.view().markup, /checklist-celebration/);
});

test('photo requirement opens capture only for the last item and retains its occurrence', async () => {
  const h = setup('child', {}, { require_photo: true });
  let capture;
  h.card._openPhotoCapture = (...args) => { capture = args; };
  await h.view().step('clothes').click();
  assert.equal(capture, undefined);
  await h.view().step('pajamas').click();
  assert.equal(h.calls.length, 1);
  assert.equal(capture[3].item.id, 'pajamas');
  assert.equal(capture[3].occurrenceId, 'today:0');
  await h.card._handleChecklistItem(h.chore, h.child, capture[3].item, true, '/api/taskmate/photo/example.jpg', capture[3].occurrenceId);
  assert.equal(h.calls[1].data.photo_url, '/api/taskmate/photo/example.jpg');
  assert.equal(h.card._celebrating, 'ready');
});

test('photo selected for an old occurrence cannot check an item on a new occurrence', async () => {
  const h = setup('child', {}, { require_photo: true });
  await h.card._handleChecklistItem(h.chore, h.child, h.chore.checklist_items[0], true, '/api/taskmate/photo/example.jpg', 'yesterday:0');
  assert.equal(h.calls.length, 0);
});

test('unavailable checklist previews cannot be checked but completed checklists remain undoable', () => {
  const h = setup('child', { dependency_mode: 'dim', due_days_mode: 'show' });
  h.attrs.chore_availability.ready.kid = false;
  h.touch();
  assert.equal(h.view().step('clothes').disabled, true);
  h.child.checklist_progress.ready = { ...h.child.checklist_progress.ready, completion_id: 'done', child_undo_pending: true,
    items: h.chore.checklist_items.map(item => ({ ...item, checked: true })) };
  h.touch();
  assert.equal(h.view().step('clothes').disabled, false);
});

test('while an item request is in flight, repeat taps and other items are disabled', async () => {
  const h = setup();
  let finish;
  h.card.hass.callService = () => new Promise(resolve => { finish = resolve; });
  const pending = h.view().step('clothes').click();
  assert.equal(h.view().step('pajamas').disabled, true);
  assert.equal(h.view().step('clothes').disabled, true);
  finish({ response: { completed: false } });
  await pending;
  assert.equal(h.view().step('pajamas').disabled, false);
});

test('acknowledged checklist deadline expires even before the completions sensor arrives', async () => {
  const h = setup();
  h.child.checklist_progress.ready.items[0].checked = true;
  h.card.hass.callService = async () => ({ response: { completed: true, approved: true, points_awarded: 2,
    progress: { ...h.child.checklist_progress.ready, completion_id: 'done',
      child_undo_until: '2026-09-22T12:00:10Z',
      items: h.chore.checklist_items.map(item => ({ ...item, checked: true })) } } });
  await h.view().step('pajamas').click();
  h.card.updated(new Map());
  assert.ok(h.timeouts.some(timer => timer.delay === 10001));
  h.setNow('2026-09-22T12:00:10.001Z');
  h.timeouts.find(timer => timer.delay === 10001).fn();
  assert.equal(h.view().step('pajamas').disabled, true);
});

test('guided navigation cannot skip another task while the final checkbox request is in flight', async () => {
  const h = setup('routine');
  let finish;
  h.card.hass.callService = () => new Promise(resolve => { finish = resolve; });
  const pending = h.view().step('clothes').click();
  assert.equal(h.view().buttons.find(button => button.attrs.includes('rt-skip')).disabled, true);
  h.card._skip();
  h.card._back();
  h.card._restart();
  assert.equal(h.card._finished, false);
  assert.equal(h.card._skipped.size, 0);
  finish({ response: { completed: false } });
  await pending;
});

for (const action of ['close', 'undo']) {
  test(`${action} clears checklist celebration points and timer before the next chore`, async () => {
    const h = setup();
    await h.view().step('clothes').click();
    await h.view().step('pajamas').click();
    assert.equal(h.card._celebrationPoints, 2);
    const timer = h.card._checklistCelebrationTimer;
    assert.ok(timer);
    if (action === 'undo') {
      const completion = { chore_id: 'ready', child_id: 'kid', completion_id: 'done',
        completed_at: now, child_undo_pending: true };
      await h.card._handleUndo(h.chore, h.child, [completion], true);
      assert.equal(h.calls.at(-1).service, 'undo_chore');
      assert.equal(h.card._checklistSnapshots.has('kid:ready'), false);
    } else {
      h.card._closeCelebration();
    }
    assert.equal(h.card._celebrationPoints, null);
    assert.equal(h.card._checklistCelebrationTimer, null);
    assert.ok(h.clearedTimeouts.has(timer));
    const next = { ...h.chore, id: 'next', name: 'Make bed', task_type: 'standard', points: 5 };
    h.attrs.chores.push(next);
    await h.card._handleComplete(next, h.child);
    assert.equal(h.card._celebrating, 'next');
    // Standard chore celebration now falls back to its own points.
    assert.equal(h.card._celebrationPoints, null);
  });
}

for (const kind of ['overview', 'parent-dashboard']) {
  for (const design of ['classic', 'playroom', 'console', 'cleanpro', 'accessible']) {
    test(`${kind}, ${design}: parent views direct checklist completion to its item controls`, () => {
      const h = harness(kind, { now, parent: true, config: { card_design: design }, chore: { task_type: 'checklist', bonus_subtasks: [] } });
      h.card._activeSection = 'overview';
      h.card._expanded = { kid: true };
      const view = h.view();
      assert.doesNotMatch(view.markup, /btn-complete-behalf/);
      assert.equal(view.buttons.some(button => button.content.includes(h.card._t('common.complete_on_behalf'))), false);
      if (kind === 'overview' || design === 'classic') {
        assert.match(view.markup, /Complete the checklist on the child dashboard/);
      }
    });
  }
}
