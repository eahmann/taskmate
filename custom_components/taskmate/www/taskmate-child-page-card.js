/** A responsive child workspace. Existing cards still own all service actions. */
const LitElement = customElements.get("hui-masonry-view")
  ? Object.getPrototypeOf(customElements.get("hui-masonry-view"))
  : Object.getPrototypeOf(customElements.get("hui-view"));
const html = LitElement.prototype.html;
const css = LitElement.prototype.css;

class TaskMateChildPageCard extends LitElement {
  static get properties() {
    return { hass: { type: Object }, config: { type: Object }, _content: { state: true }, _pickerOpen: { state: true } };
  }

  constructor() {
    super();
    this._pickerOpen = false;
    this._outsidePicker = event => {
      if (!event.composedPath().includes(this.renderRoot.querySelector('.child-switcher'))) this._closePicker();
    };
    this._escapePicker = event => {
      if (event.key === 'Escape') { event.preventDefault(); this._closePicker(true); }
    };
  }

  static get styles() {
    return css`
      :host { display: block; min-width: 0; container-type: inline-size; }
      * { box-sizing: border-box; }
      .page {
        --tm-page-accent: #b885e3;
        --tm-page-good: #40c9a4;
        padding: clamp(12px, 2.4cqw, 48px);
        color: var(--primary-text-color);
        font-family: var(--paper-font-body1_-_font-family, system-ui, sans-serif);
      }
      .identity { display: flex; align-items: center; gap: 12px; min-height: 60px; }
      .avatar { display: grid; place-items: center; width: 44px; height: 44px; flex: none;
        color: var(--tm-page-accent); font-size: 28px; }
      .avatar ha-icon { --mdc-icon-size: 38px; }
      .avatar img { width: 100%; height: 100%; border-radius: 50%; object-fit: cover; }
      h1 { margin: 0; min-width: 0; overflow-wrap: anywhere; font-size: clamp(23px, 2.4cqw, 38px); line-height: 1.2; }
      .child-switcher { position: relative; min-width: 0; }
      .child-trigger { display: flex; align-items: center; gap: 12px; min-height: 48px;
        max-width: 100%; border: 0; padding: 4px 0; background: none; color: inherit;
        font: inherit; font-weight: inherit; text-align: left; cursor: pointer; border-radius: 10px; }
      .child-trigger .name { min-width: 0; overflow-wrap: anywhere; }
      .chevron { color: var(--secondary-text-color); --mdc-icon-size: 20px; flex: none; }
      .child-trigger:hover .name { color: var(--tm-page-accent); }
      .child-picker { position: absolute; z-index: 30; top: calc(100% + 8px); left: 0;
        width: min(272px, calc(100vw - 48px)); max-height: 55vh; overflow-y: auto;
        padding: 10px; border: 1px solid var(--divider-color, #8884); border-radius: 16px;
        background: var(--card-background-color, #222); box-shadow: 0 12px 32px #0006; }
      .picker-label { padding: 4px 8px 8px; color: var(--secondary-text-color); font-size: 14px; }
      .child-picker a { display: flex; align-items: center; gap: 12px; padding: 8px;
        min-height: 56px; border-radius: 10px; color: var(--primary-text-color);
        text-decoration: none; font-size: 18px; font-weight: 700; }
      .child-picker a[aria-current="page"], .child-picker a:hover { background: var(--secondary-background-color); }
      .child-picker .avatar { width: 32px; height: 32px; }
      .child-picker .avatar ha-icon { --mdc-icon-size: 30px; }
      .child-picker .name { flex: 1; min-width: 0; overflow-wrap: anywhere; }
      .child-picker .check { --mdc-icon-size: 22px; color: var(--tm-page-accent); }
      .child-trigger:focus-visible, .child-picker a:focus-visible { outline: 3px solid var(--primary-color); outline-offset: 2px; }
      .balance { margin-left: auto; flex: none; display: flex; align-items: center; gap: 6px;
        font-size: clamp(17px, 1.8cqw, 26px); font-weight: 750; font-variant-numeric: tabular-nums; }
      .balance ha-icon { color: #edc556; --mdc-icon-size: 24px; }
      .balance-text { display: flex; align-items: baseline; gap: 5px; }
      .points-name { font-size: .72em; font-weight: 600; }
      .reserved { margin: 4px 0 0; text-align: right; color: var(--secondary-text-color); font-size: 14px; }
      nav { display: flex; width: min(100%, 520px); margin: 20px 0 28px;
        padding: 4px; border-radius: 14px; background: var(--secondary-background-color, #282828); }
      nav a { flex: 1; min-width: 0; min-height: 48px; display: flex; justify-content: center;
        align-items: center; gap: 8px; padding: 8px 12px; border-radius: 10px; text-decoration: none;
        font-size: clamp(15px, 1.4cqw, 20px); font-weight: 700; color: var(--secondary-text-color); }
      nav a[aria-current="page"] { color: var(--tm-page-on-accent, #17131e); background: var(--tm-page-accent); }
      nav a:focus-visible { outline: 3px solid var(--primary-color); outline-offset: 3px; }
      nav ha-icon { --mdc-icon-size: 22px; }
      .workspace { min-width: 0; }
      .empty { padding: 24px 0; color: var(--secondary-text-color); }
      @container (min-width: 1000px) {
        .identity { min-height: 76px; }
        .avatar { width: 60px; height: 60px; }
        .avatar ha-icon { --mdc-icon-size: 50px; }
        nav { margin-bottom: 36px; }
        nav a { min-height: 58px; }
      }
      @media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto; } }
    `;
  }

  setConfig(config) {
    if (!config.entity || !config.child_id) throw new Error("Define entity and child_id for the child page.");
    if (config.child_pages !== undefined && !Array.isArray(config.child_pages)) throw new Error("child_pages must be a list of child routes.");
    const childIds = new Set();
    for (const page of config.child_pages || []) {
      if (!page || typeof page.child_id !== "string" || !page.child_id || childIds.has(page.child_id)) {
        throw new Error("Each child_pages entry needs a unique child_id.");
      }
      childIds.add(page.child_id);
    }
    for (const page of [config, ...(config.child_pages || [])]) for (const key of ["chores_path", "rewards_path"]) {
      // Keep these ordinary same-origin dashboard links, including browser back.
      if (typeof page[key] !== "string" || !/^\/(?!\/)/.test(page[key]) || /[\\\s]/.test(page[key])) {
        throw new Error(`Define ${key} as a local dashboard path.`);
      }
    }
    if (config.view && !["chores", "rewards"].includes(config.view)) throw new Error("view must be chores or rewards.");
    this._closePicker();
    this.config = { view: "chores", ...config };
    this._ensureContent();
  }

  getCardSize() { return 8; }
  getGridOptions() { return { columns: "full", rows: "auto", min_columns: 6 }; }

  _t(key) { return window.__taskmate_localize?.(this.hass, key) || key; }

  disconnectedCallback() {
    this._closePicker();
    super.disconnectedCallback();
  }

  _closePicker(restoreFocus = false) {
    this._pickerOpen = false;
    document.removeEventListener('pointerdown', this._outsidePicker, true);
    document.removeEventListener('keydown', this._escapePicker);
    if (restoreFocus) this.renderRoot?.querySelector('.child-trigger')?.focus();
  }

  _openPicker(focusLast = null) {
    this._pickerOpen = true;
    document.addEventListener('pointerdown', this._outsidePicker, true);
    document.addEventListener('keydown', this._escapePicker);
    if (focusLast !== null) this.updateComplete.then(() => {
      if (!this._pickerOpen) return;
      const links = this.renderRoot.querySelectorAll('.child-picker a');
      links[focusLast ? links.length - 1 : 0]?.focus();
    });
  }

  _pickerKeys(event) {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const links = [...this.renderRoot.querySelectorAll('.child-picker a')];
    const index = links.indexOf(this.renderRoot.activeElement);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? links.length - 1
      : (index + (event.key === 'ArrowDown' ? 1 : -1) + links.length) % links.length;
    links[next]?.focus();
  }

  _childPages(children) {
    const pages = (this.config.child_pages || []).map(page => page.child_id === this.config.child_id ? this.config : page);
    if (!pages.some(page => page.child_id === this.config.child_id)) pages.unshift(this.config);
    return pages.map(page => ({ ...page, child: children.find(child => child.id === page.child_id),
      path: this.config.view === 'rewards' ? page.rewards_path : page.chores_path })).filter(page => page.child);
  }

  _avatar(child) {
    const avatar = child.avatar || 'mdi:account';
    return html`<span class="avatar" aria-hidden="true">${avatar.startsWith('mdi:')
      ? html`<ha-icon icon=${avatar}></ha-icon>` : html`<img src=${avatar} alt="">`}</span>`;
  }

  _contentConfig() {
    const shared = { entity: this.config.entity, child_id: this.config.child_id,
      card_design: "playroom", app_layout: true,
      ...(this.config.accent_color ? { accent_color: this.config.accent_color } : {}) };
    return this.config.view === "rewards"
      ? { ...this.config.reward_options, ...shared, show_child_badges: false, expand_to_fit: true }
      : { time_category: "all", show_description: true, show_countdown: false,
          show_badges: false, show_next_badge: false, show_swaps: false,
          recurrence_done_mode: "show", ...this.config.chore_options, ...shared,
          show_parent_actions: false };
  }

  _ensureContent() {
    const tag = this.config.view === "rewards" ? "taskmate-rewards-card" : "taskmate-child-card";
    if (!customElements.get(tag)) {
      if (this._waitingFor !== tag) {
        this._waitingFor = tag;
        customElements.whenDefined(tag).then(() => {
          if (this._waitingFor !== tag) return;
          this._waitingFor = null;
          this._ensureContent();
        });
      }
      return;
    }
    this._waitingFor = null;
    if (this._content?.localName !== tag) this._content = document.createElement(tag);
    this._content.setConfig(this._contentConfig());
    if (this.hass) this._content.hass = this.hass;
  }

  updated(changed) {
    if (changed.has("hass") && this._content) this._content.hass = this.hass;
    const routineId = new URLSearchParams(window.location.search || "").get("routine");
    if (this.config.view !== "chores" || !routineId || this._focusedRoutine === routineId || !this._content) return;
    this._content.updateComplete?.then(() => {
      const section = [...this._content.renderRoot.querySelectorAll('[data-routine-id]')].find(el => el.dataset.routineId === routineId);
      if (!section) return;
      this._focusedRoutine = routineId;
      section.scrollIntoView({ block: "start" });
      section.focus({ preventScroll: true });
    });
  }

  _navigate(event, path) {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    this._closePicker(true);
    if (window.location.pathname === path) return;
    window.history.pushState(null, "", path);
    window.dispatchEvent(new CustomEvent("location-changed"));
  }

  render() {
    if (!this.hass || !this.config) return html``;
    const entity = this.hass.states[this.config.entity];
    const attrs = window.__taskmate_attrs?.(this.hass, this.config.entity) || entity?.attributes || {};
    const child = (attrs.children || []).find(c => c.id === this.config.child_id);
    if (!entity || !child) return html`<div class="empty" role="status">${this._t("child_page.unavailable")}</div>`;
    const accent = window.__taskmate_design?.childColor?.(child, this.config.accent_color) || "#b885e3";
    const pages = this._childPages(attrs.children || []);
    const points = child.spendable_balance ?? child.points ?? 0;
    const reserved = child.committed_points || 0;
    return html`<section class="page" style="--tm-page-accent:${accent};--tm-page-on-accent:${window.__taskmate_design?.onColor?.(accent) || '#17131e'}" aria-label=${child.name}>
      <header class="identity">
        ${pages.length > 1 ? html`<div class="child-switcher">
          <h1><button class="child-trigger" aria-expanded=${this._pickerOpen ? 'true' : 'false'} aria-controls="child-picker"
            aria-label=${`${child.name}: ${this._t('child_page.select_child')}`}
            @click=${() => this._pickerOpen ? this._closePicker() : this._openPicker()}
            @keydown=${event => { if (['ArrowDown', 'ArrowUp'].includes(event.key)) { event.preventDefault(); this._openPicker(event.key === 'ArrowUp'); } }}>
            ${this._avatar(child)}<span class="name">${child.name}</span><ha-icon class="chevron" icon="mdi:chevron-down"></ha-icon>
          </button></h1>
          ${this._pickerOpen ? html`<div class="child-picker" id="child-picker" role="group" aria-label=${this._t('child_page.select_child')} @keydown=${event => this._pickerKeys(event)}>
            <div class="picker-label">${this._t('child_page.select_child')}</div>
            ${pages.map(page => html`<a href=${page.path} aria-current=${page.child_id === child.id ? 'page' : 'false'}
              style=${`--tm-page-accent:${window.__taskmate_design?.childColor?.(page.child, page.accent_color) || accent}`}
              @click=${event => this._navigate(event, page.path)}>
              ${this._avatar(page.child)}<span class="name">${page.child.name}</span>
              ${page.child_id === child.id ? html`<ha-icon class="check" icon="mdi:check"></ha-icon>` : ''}
            </a>`)}
          </div>` : ''}
        </div>` : html`${this._avatar(child)}<h1>${child.name}</h1>`}
        <div class="balance" aria-label=${`${points} ${attrs.points_name || this._t("common.stars")}`}>
          <ha-icon icon=${attrs.points_icon || "mdi:star"}></ha-icon>
          <span class="balance-text"><span>${Number(points).toLocaleString()}</span>
          <span class="points-name">${attrs.points_name || this._t("common.stars")}</span></span>
        </div>
      </header>
      ${reserved > 0 ? html`<p class="reserved">${reserved} ${attrs.points_name || this._t("common.stars")} · ${this._t("rewards.awaiting_approval")}</p>` : ""}
      <nav aria-label=${this._t("child_page.navigation")}>
        <a href=${this.config.chores_path} aria-current=${this.config.view === "chores" ? "page" : "false"}
          @click=${e => this._navigate(e, this.config.chores_path)}>
          <ha-icon icon="mdi:format-list-checks"></ha-icon>${this._t("common.chores")}</a>
        <a href=${this.config.rewards_path} aria-current=${this.config.view === "rewards" ? "page" : "false"}
          @click=${e => this._navigate(e, this.config.rewards_path)}>
          <ha-icon icon="mdi:gift-outline"></ha-icon>${this._t("child_page.my_rewards")}</a>
      </nav>
      <div class="workspace">${this._content || html`<div class="empty" role="status">${this._t("panel.loading")}</div>`}</div>
    </section>`;
  }
}

customElements.define("taskmate-child-page-card", TaskMateChildPageCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: "taskmate-child-page-card", name: "TaskMate Child Page",
  description: "A responsive child workspace with chores, rewards and a compact Stars header." });
