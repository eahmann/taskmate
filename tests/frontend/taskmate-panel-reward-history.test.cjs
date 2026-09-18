const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const elements = new Map();
const source = readFileSync(path.join(
  __dirname, '../../custom_components/taskmate/www/taskmate-panel.js'
), 'utf8').replaceAll('import.meta.url', '"http://localhost/taskmate-panel.js?v=test"');
vm.runInNewContext(source, {
  HTMLElement: class {},
  customElements: {
    get: (name) => elements.get(name),
    define: (name, element) => elements.set(name, element),
  },
  URL,
});

for (const [approvedCost, expected] of [[50, '-50'], [0, '+0'], [undefined, '-20']]) {
  test(`activity uses paid reward price ${approvedCost} after current price changes`, () => {
    const panel = Object.create(elements.get('taskmate-panel').prototype);
    panel._t = (key) => key;
    panel._state = {
      children: [{ id: 'kid1', name: 'Kid 1' }],
      rewards: [{ id: 'prize', name: 'Prize', cost: 20 }],
      reward_claims: [{
        id: 'claim', reward_id: 'prize', child_id: 'kid1',
        approved: true, approved_cost: approvedCost,
        claimed_at: '2026-09-18T12:00:00Z',
      }],
    };
    const markup = panel._renderActivityTab();
    const displayed = markup.match(/class="tm-timeline-points [^"]*">([^<]+)</);
    assert.equal(displayed?.[1], expected);
  });
}

for (const [reason, key] of [
  ['Pool refund (reward funding changed): Prize', 'activity.reason_pool_refund_funding_changed'],
  ['Pool refund (reward assignment changed): Prize', 'activity.reason_pool_refund_assignment_changed'],
]) {
  test(`reward edit refund is translated and cannot be undone: ${key}`, () => {
    const panel = Object.create(elements.get('taskmate-panel').prototype);
    panel._t = (translationKey, params) => `${translationKey}:${params.name}`;
    assert.equal(panel._translateReason(reason), `${key}:Prize`);
    assert.equal(panel._txnReversible(reason), false);
  });
}
