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

for (const [submitted, expected] of [[6, 6], [0, 0], [null, 18], [undefined, 18]]) {
  test(`pending timer displays saved award ${submitted} after rate edit`, () => {
    const panel = Object.create(elements.get('taskmate-panel').prototype);
    panel._t = (key) => key;
    panel._state = {
      children: [{ id: 'kid1', name: 'Kid 1' }],
      chores: [{
        id: 'timer', name: 'Reading', points: 0,
        task_type: 'timed', timed_rate_minutes: 1, timed_rate_points: 9,
      }],
      pending_completions: [{
        id: 'completion', chore_id: 'timer', child_id: 'kid1',
        completed_at: '2026-09-18T12:00:00Z',
        timed_duration_seconds: 120, submitted_points: submitted,
      }],
    };
    const markup = panel._renderActivityTab();
    assert.match(markup, new RegExp(`· ${expected} panel\\.activity_points`));
  });
}
