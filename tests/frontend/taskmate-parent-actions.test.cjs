const assert = require('node:assert/strict');
const { test } = require('node:test');
const { harness, rendered } = require('./card-harness.cjs');

for (const design of ['classic', 'playroom', 'console', 'cleanpro', 'accessible']) {
  for (const visible of [undefined, false]) {
    test(`${design}: completed picture tile allows undo only when parent actions are shown (${visible})`, async () => {
      const { card, chore, child } = harness('child', {
        parent: true, config: { card_design: design, pre_reader: true, show_parent_actions: visible },
        chore: { task_type: 'standard', bonus_subtasks: [] },
      });
      let undos = 0;
      card._handleUndo = async () => { undos++; };
      const tile = rendered(card._renderPreReaderTile(chore, child, 'mdi:star', [completion('')]));
      assert.equal(tile.buttons[0].disabled, visible === false);
      await tile.buttons[0].click();
      assert.equal(undos, visible === false ? 0 : 1);
    });
  }

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
