const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

// Exercise the actual card renderers without a Home Assistant browser session.
// Lit's template tag is replaced with a serializer; event handlers aren't run.
function template(strings, ...values) {
  const stringify = (value) => Array.isArray(value)
    ? value.map(stringify).join('')
    : typeof value === 'function' || value == null ? '' : String(value);
  return strings.reduce((out, part, i) => out + part + stringify(values[i]), '');
}

class LitElement {}
LitElement.prototype.html = template;
LitElement.prototype.css = template;
class HuiView extends LitElement {}
const elements = new Map([['hui-masonry-view', HuiView]]);
const source = readFileSync(path.join(
  __dirname, '../../custom_components/taskmate/www/taskmate-rewards-card.js'
), 'utf8');
vm.runInNewContext(source, {
  customElements: {
    get: (name) => elements.get(name),
    define: (name, element) => elements.set(name, element),
  },
  window: {},
  document: { querySelectorAll: () => [] },
  URLSearchParams,
  console: { info() {} },
});
const RewardsCard = elements.get('taskmate-rewards-card');

function renderReward(reward, children, selectedChild, pendingClaims = []) {
  const card = new RewardsCard();
  card.config = { entity: 'sensor.taskmate' };
  card.hass = { states: {
    'sensor.taskmate': { attributes: { pending_reward_claims: pendingClaims } },
  } };
  card._selectedChildId = selectedChild;
  return {
    classic: card._renderRewardRow(reward, 'mdi:star', 'Stars', {}, children),
    designed: card._designRewardRow(reward, children, 'mdi:star', 'Stars', 'playroom'),
  };
}

function renderJackpot(cost, allocations, selectedChild = null, pendingClaims = []) {
  const children = allocations.map((_, index) => ({
    id: `kid${index + 1}`, name: `Kid ${index + 1}`, points: 999,
  }));
  const reward = {
    id: 'jackpot', name: 'Jackpot', is_jackpot: true, cost,
    pool_allocations: Object.fromEntries(children.map((child, i) => [child.id, allocations[i]])),
    jackpot_pool_total: allocations.reduce((sum, points) => sum + points, 0),
  };
  return renderReward(reward, children, selectedChild, pendingClaims);
}

function classicSegments(markup) {
  return Array.from(markup.matchAll(
    /class="jackpot-segment color-(\d+)"\s+style="width:\s*([\d.]+)%"/g
  ), (match) => ({ color: Number(match[1]), width: Number(match[2]) }));
}

for (const { name, cost, allocations, widths } of [
  { name: 'unequal contributions fill the shared goal', cost: 50, allocations: [40, 10], widths: [80, 20] },
  { name: 'partial funding leaves only the unfunded portion empty', cost: 50, allocations: [30, 5], widths: [60, 10] },
  { name: 'one child can fund the entire shared goal', cost: 50, allocations: [50, 0], widths: [100, 0] },
  { name: 'three contributors have no rounded equal-share shortfall', cost: 50, allocations: [17, 17, 16], widths: [34, 34, 32] },
  { name: 'an empty pool has empty segments', cost: 50, allocations: [0, 0], widths: [0, 0] },
  { name: 'a zero-cost reward never divides by zero', cost: 0, allocations: [0, 0], widths: [0, 0] },
]) {
  test(name, () => {
    const { classic, designed } = renderJackpot(cost, allocations);
    const segments = classicSegments(classic);
    assert.deepEqual(segments.map((segment) => segment.width), widths);
    assert.deepEqual(segments.map((segment) => segment.color), allocations.map((_, i) => i));

    const percentages = Array.from(classic.matchAll(
      /class="jackpot-pct">\((\d+)%\)/g
    ), (match) => Number(match[1]));
    assert.deepEqual(percentages, cost > 0 ? widths : []);

    const designedWidths = Array.from(designed.matchAll(
      /<i style="width:([\d.]+)%;background:/g
    ), (match) => Number(match[1]));
    assert.deepEqual(designedWidths, widths, 'all card designs use the same contribution widths');
    assert.doesNotMatch(classic + designed, /NaN|Infinity/);
  });
}

test('switching the selected child preserves both contributions and colors', () => {
  const first = renderJackpot(50, [40, 10], 'kid1').classic;
  const second = renderJackpot(50, [40, 10], 'kid2').classic;
  assert.deepEqual(classicSegments(first), [{ color: 0, width: 80 }, { color: 1, width: 20 }]);
  assert.deepEqual(classicSegments(second), classicSegments(first));
  assert.match(first, /50\/50/);
  assert.match(second, /50\/50/);
});

for (const selectedChild of ['kid1', 'kid2']) {
  test(`a shared jackpot pending claim blocks redemption for ${selectedChild} in every design`, () => {
    const rows = renderJackpot(50, [40, 10], selectedChild, [
      { reward_id: 'jackpot', child_id: 'kid1' },
    ]);
    for (const [design, markup] of Object.entries(rows)) {
      assert.match(markup, /rewards\.awaiting_approval/, `${design} shows the shared pending claim`);
      assert.doesNotMatch(markup, /rewards\.redeem/, `${design} hides redemption while pending`);
    }
  });

  test(`an unrelated pending claim leaves the jackpot redeemable for ${selectedChild}`, () => {
    const rows = renderJackpot(50, [40, 10], selectedChild, [
      { reward_id: 'another-reward', child_id: selectedChild },
    ]);
    for (const [design, markup] of Object.entries(rows)) {
      assert.match(markup, /rewards\.redeem/, `${design} allows redemption of the funded jackpot`);
      assert.doesNotMatch(markup, /rewards\.awaiting_approval/, `${design} ignores unrelated claims`);
    }
  });
}

test('ordinary savings jars keep pending claims independent for each child in every design', () => {
  const children = [
    { id: 'kid1', name: 'Kid 1', points: 100 },
    { id: 'kid2', name: 'Kid 2', points: 100 },
  ];
  const reward = {
    id: 'savings', name: 'Savings', cost: 50, pool_enabled: true,
    pool_allocations: { kid1: 50, kid2: 50 },
  };
  const pendingClaims = [{ reward_id: 'savings', child_id: 'kid1' }];
  const claimantRows = renderReward(reward, children, 'kid1', pendingClaims);
  const otherChildRows = renderReward(reward, children, 'kid2', pendingClaims);
  for (const design of Object.keys(claimantRows)) {
    assert.match(claimantRows[design], /rewards\.awaiting_approval/, `${design} shows the claimant's pending status`);
    assert.doesNotMatch(claimantRows[design], /rewards\.redeem/, `${design} hides the claimant's redeem button`);
    assert.match(otherChildRows[design], /rewards\.redeem/, `${design} allows the other child to redeem their own savings`);
    assert.doesNotMatch(otherChildRows[design], /rewards\.awaiting_approval/, `${design} does not share ordinary pending claims`);
  }
});
