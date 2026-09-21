const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const www = path.join(__dirname, '../../custom_components/taskmate/www');
const messages = JSON.parse(readFileSync(path.join(www, 'locales/en.json'), 'utf8'));

// Keep Lit's actual template structure and event bindings. This small harness
// renders that tree and exercises its button handlers without an HA instance
// or installing a browser/DOM framework. Disabled buttons do not dispatch clicks.
function template(strings, ...values) { return { strings, values }; }
function rendered(tree) {
  const handlers = [];
  function serialize(value) {
    if (value == null) return '';
    if (Array.isArray(value)) return value.map(serialize).join('');
    if (typeof value === 'function') return `event_${handlers.push(value) - 1}`;
    if (value.strings) return value.strings.reduce((out, str, i) => out + str + serialize(value.values[i]), '');
    return String(value);
  }
  const markup = serialize(tree);
  const buttons = Array.from(markup.matchAll(/<button\b([^>]*?)>([\s\S]*?)<\/button>/g), match => {
    const attrs = match[1];
    const disabled = /\?disabled\s*=\s*["']?true\b/.test(attrs) || /(?:^|\s)disabled(?:\s|$)/.test(attrs);
    const event = attrs.match(/@click\s*=\s*["']?event_(\d+)/);
    const step = attrs.match(/data-step-id="([^"]+)"/);
    return {
      attrs, content: match[2], disabled, step: step?.[1],
      async click() { if (!disabled && event) await handlers[Number(event[1])]({ stopPropagation() {} }); },
    };
  });
  return { markup, buttons, step: id => buttons.find(button => button.step === id) };
}

function harness(kind, options = {}) {
  let now = options.now ? new Date(options.now).getTime() : Date.now();
  class ClockDate extends Date {
    constructor(...args) { super(...(args.length ? args : [now])); }
    static now() { return now; }
  }
  class LitElement {
    constructor() {
      this.style = { setProperty() {}, removeProperty() {} };
      this.events = [];
    }
    requestUpdate() { this.updates = (this.updates || 0) + 1; }
    updated() {}
    disconnectedCallback() {}
    dispatchEvent(event) { this.events.push(event); }
  }
  LitElement.prototype.html = template;
  LitElement.prototype.css = template;
  class View extends LitElement {}
  const elements = new Map([['hui-view', View]]);
  const window = {
    __taskmate_localize: (_hass, key, params = {}) => (messages[key] || key).replace(/\{(\w+)\}/g, (_, name) => params[name] ?? ''),
    __taskmate_chore_visual: chore => chore.icon ? { kind: 'icon', icon: chore.icon } : { kind: 'none' },
    __taskmate_design: { apply: (_card, _hass, config) => config.card_design || 'classic' },
    __taskmate_is_parent: () => options.parent === true,
  };
  const timeouts = [];
  const context = {
    window, console: { info() {}, error() {} }, queueMicrotask() {},
    customElements: { get: name => elements.get(name), define: (name, element) => elements.set(name, element) },
    document: { querySelectorAll: () => [] },
    CustomEvent: class { constructor(type, init) { this.type = type; Object.assign(this, init); } },
    URLSearchParams, Date: ClockDate, Intl, setTimeout: (fn, delay) => timeouts.push({ fn, delay }), clearTimeout() {},
  };
  vm.runInNewContext(readFileSync(path.join(www, 'taskmate-attr-resolver.js'), 'utf8'), context);
  if (kind === 'bonuses' || kind === 'penalties') {
    const base = readFileSync(path.join(www, 'taskmate-incentive-card.js'), 'utf8').replace('export function', 'function');
    const wrapper = readFileSync(path.join(www, `taskmate-${kind}-card.js`), 'utf8').replace(/^import .*;$/m, '');
    vm.runInNewContext(base + '\n' + wrapper, context);
  } else {
    vm.runInNewContext(readFileSync(path.join(www, `taskmate-${kind}-card.js`), 'utf8'), context);
  }
  const card = new (elements.get(`taskmate-${kind}-card`))();
  card.setConfig({
    entity: 'sensor.taskmate_overview', child_id: 'kid', time_category: 'all',
    show_countdown: false, show_swaps: false, ...options.config,
  });
  const child = { id: 'kid', name: 'Ari', points: 5 };
  const chore = {
    id: 'ready', name: 'Get ready', task_type: 'standard', points: 4,
    icon: 'mdi:weather-sunny', assigned_to: ['kid'], time_category: 'anytime',
    enabled: true, requires_approval: true,
    bonus_subtasks: [
      { id: 'teeth', name: 'Brush teeth', icon: 'mdi:toothbrush', points: 2 },
      { id: 'dress', name: 'Get dressed', icon: 'mdi:tshirt-crew', points: 3 },
    ],
    ...options.chore,
  };
  const attrs = { parent_user_ids: options.parent ? ['family-display'] : [], children: [child], chores: [chore], chore_availability: { ready: { kid: true } }, todays_completions: [] };
  const calls = [];
  card.hass = {
    user: { id: 'family-display', is_admin: options.admin === true },
    config: { time_zone: options.timeZone || 'UTC' }, states: { 'sensor.taskmate_overview': { attributes: attrs } },
    async callService(domain, service, data) { calls.push({ domain, service, data }); },
  };
  return { card, attrs, child, chore, calls, timeouts, window,
    setNow: value => { now = new Date(value).getTime(); }, touch: () => { card.hass = { ...card.hass, states: { ...card.hass.states } }; }, view: () => rendered(card.render()) };
}

module.exports = { harness, rendered };
