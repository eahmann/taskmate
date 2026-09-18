const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function panelHarness({ confirmation = 'RESET TASKMATE', exportError = false, downloadError = false, resetError = false, realFetch = false, reloadError = false } = {}) {
  const events = [];
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
    URL: class extends URL {
      static createObjectURL() { return 'blob:backup'; }
      static revokeObjectURL() {}
    },
    Blob,
    console: { warn() {} },
    setTimeout: (callback) => setTimeout(callback, 0),
    prompt: (message) => { events.push(['confirm', message]); return confirmation; },
    document: { createElement: () => ({
      click() {
        if (downloadError) throw new Error('Download failed');
        events.push(['download', this.download]);
      },
    }) },
  });
  const panel = Object.create(elements.get('taskmate-panel').prototype);
  Object.assign(panel, {
    _resetInProgress: false,
    _timePeriodsDraft: ['test period'],
    _vacationDraft: ['test vacation'],
    _icsUrl: 'old calendar URL',
    _bulkSel: new Set(['old chore']),
    _activeTab: 'settings',
    _state: {
      children: [{ id: 'old-child', name: 'Old profile' }],
      settings: {
        time_periods: [{ id: 'old-period', label: 'Old period', start: '07:00', end: '10:00' }],
        vacation_periods: [{ id: 'old-trip', name: 'Old trip', start: '2026-10-01', end: '2026-10-05' }],
      },
    },
    _render() {},
    _t: (key, params) => key + (params ? JSON.stringify(params) : ''),
    _showToast: (kind, message) => events.push(['toast', kind, message]),
    _fetchState: async () => events.push(['refresh']),
    _hass: { callWS: async (payload) => {
      events.push(['ws', payload.type]);
      if (payload.type === 'taskmate/config/export') {
        if (exportError) throw new Error('Export failed');
        return { taskmate_export_version: 1, data: { children: [{ name: 'Test' }] } };
      }
      if (payload.type === 'taskmate/get_state') {
        if (reloadError) throw new Error('TaskMate data was reset. Reload TaskMate from Settings > Devices & services or restart Home Assistant.');
        return { children: [], settings: {} };
      }
      if (payload.type.startsWith('taskmate/notifications/')) return {};
      assert.equal(payload.type, 'taskmate/config/reset');
      assert.equal(payload.confirmation, 'RESET TASKMATE');
      if (reloadError) throw new Error('TaskMate data was reset, but the integration could not reload.');
      if (resetError) throw new Error('Wait for active unlocks');
      return { reset: true };
    } },
  });
  if (realFetch) {
    delete panel._fetchState;
    panel._timePeriodsDraft = null;
    panel._vacationDraft = null;
    // Exercise the actual body/settings rendering and draft initialization,
    // without the outer DOM shell. This catches loading renders using old data.
    panel._render = function () { this.renderedBody = this._renderBody(); };
  }
  return { panel, events };
}

for (const confirmation of [null, '', 'reset taskmate', 'RESET', 'RESET TASKMATE ']) {
  test(`cancelled or incorrect confirmation never resets: ${JSON.stringify(confirmation)}`, async () => {
    const { panel, events } = panelHarness({ confirmation });
    await panel._doResetConfig();
    assert.deepEqual(events.filter(([kind]) => kind === 'ws'), [['ws', 'taskmate/config/export']]);
    assert.equal(panel._resetInProgress, false);
    assert.deepEqual(panel._timePeriodsDraft, ['test period']);
  });
}

for (const failure of ['exportError', 'downloadError']) {
  test(`${failure} prevents both confirmation and reset`, async () => {
    const { panel, events } = panelHarness({ [failure]: true });
    await panel._doResetConfig();
    assert.deepEqual(events.filter(([kind]) => kind === 'ws'), [['ws', 'taskmate/config/export']]);
    assert.equal(events.some(([kind]) => kind === 'confirm'), false);
    assert.equal(panel._resetInProgress, false);
  });
}

test('reset downloads a backup before confirmation and refreshes with clean local drafts', async () => {
  const { panel, events } = panelHarness();
  await panel._doResetConfig();
  assert.deepEqual(events.filter(([kind]) => kind !== 'toast').map(([kind, value]) => [
    kind, kind === 'confirm' || kind === 'download' ? 'file' : value,
  ]), [
    ['ws', 'taskmate/config/export'], ['download', 'file'], ['confirm', 'file'],
    ['ws', 'taskmate/config/reset'], ['refresh', undefined],
  ]);
  const filename = events.find(([kind]) => kind === 'download')[1];
  assert.match(filename, /^taskmate-before-reset-[\dT-Z-]+\.json$/);
  assert.ok(events.find(([kind]) => kind === 'confirm')[1].includes(filename));
  assert.equal(panel._timePeriodsDraft, null);
  assert.equal(panel._vacationDraft, null);
  assert.equal(panel._icsUrl, null);
  assert.equal(panel._bulkSel.size, 0);
  assert.equal(panel._resetInProgress, false);
});

test('backend refusal refreshes current state and reports the error', async () => {
  const { panel, events } = panelHarness({ resetError: true });
  await panel._doResetConfig();
  assert.equal(events.some(([kind]) => kind === 'refresh'), true);
  assert.ok(events.some(([kind, status, text]) => kind === 'toast' && status === 'err' && text.includes('Wait for active unlocks')));
  assert.equal(panel._timePeriodsDraft, null);
  assert.equal(panel._vacationDraft, null);
  assert.equal(panel._resetInProgress, false);
});

test('real fetch and settings render rebuild drafts from reset defaults', async () => {
  const { panel } = panelHarness({ realFetch: true });
  await panel._doResetConfig();

  assert.deepEqual([...panel._timePeriodsDraft].map((period) => period.id), ['morning', 'afternoon', 'evening', 'night']);
  assert.equal(panel._vacationDraft.length, 0);
  assert.equal(panel._state.children.length, 0);
  assert.equal(panel._error, null);
  assert.equal(panel.renderedBody.includes('Old period'), false);
  assert.equal(panel.renderedBody.includes('Old trip'), false);
});

test('partial reset with failed reload replaces old data with a persistent recovery message', async () => {
  const { panel, events } = panelHarness({ realFetch: true, reloadError: true });
  await panel._doResetConfig();

  assert.equal(panel._state, null);
  assert.equal(panel._timePeriodsDraft, null);
  assert.equal(panel._vacationDraft, null);
  assert.match(panel._error, /Reload TaskMate/);
  assert.match(panel.renderedBody, /Reload TaskMate/);
  assert.equal(panel.renderedBody.includes('Old period'), false);
  assert.equal(panel.renderedBody.includes('Old trip'), false);
  assert.equal(events.filter(([kind, type]) => kind === 'ws' && type === 'taskmate/get_state').length, 2);
  assert.equal(events.some(([kind, status, text]) => kind === 'toast' && status === 'ok' && text.includes('panel.reset_done')), false);
  assert.equal(panel._resetInProgress, false);
});

test('repeated clicks while exporting send only one reset request', async () => {
  const { panel, events } = panelHarness();
  const first = panel._doResetConfig();
  await panel._doResetConfig();
  await first;
  assert.equal(events.filter(([kind, type]) => kind === 'ws' && type === 'taskmate/config/reset').length, 1);
});
