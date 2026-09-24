/** A responsive child workspace. Existing cards still own all service actions. */
const LitElement = customElements.get("hui-masonry-view")
  ? Object.getPrototypeOf(customElements.get("hui-masonry-view"))
  : Object.getPrototypeOf(customElements.get("hui-view"));
const html = LitElement.prototype.html;
const css = LitElement.prototype.css;

class TaskMateChildPageCard extends LitElement {
  static get properties() {
    return { hass: { type: Object }, config: { type: Object }, _content: { state: true } };
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
      nav a[aria-current="page"] { color: #17131e; background: var(--tm-page-accent); }
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
    for (const key of ["chores_path", "rewards_path"]) {
      // Keep these ordinary same-origin dashboard links, including browser back.
      if (typeof config[key] !== "string" || !/^\/(?!\/)/.test(config[key]) || /[\\\s]/.test(config[key])) {
        throw new Error(`Define ${key} as a local dashboard path.`);
      }
    }
    if (config.view && !["chores", "rewards"].includes(config.view)) throw new Error("view must be chores or rewards.");
    this.config = { view: "chores", ...config };
    this._ensureContent();
  }

  getCardSize() { return 8; }
  getGridOptions() { return { columns: "full", rows: "auto", min_columns: 6 }; }

  _t(key) { return window.__taskmate_localize?.(this.hass, key) || key; }

  _contentConfig() {
    const shared = { entity: this.config.entity, child_id: this.config.child_id,
      card_design: "playroom", app_layout: true };
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
  }

  _navigate(event, path) {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
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
    const accent = /^#[0-9a-fA-F]{6}$/.test(this.config.accent_color || "") ? this.config.accent_color : "#b885e3";
    const avatar = child.avatar || "mdi:account";
    const points = child.spendable_balance ?? child.points ?? 0;
    const reserved = child.committed_points || 0;
    return html`<section class="page" style="--tm-page-accent:${accent}" aria-label=${child.name}>
      <header class="identity">
        <span class="avatar" aria-hidden="true">${avatar.startsWith("mdi:")
          ? html`<ha-icon icon=${avatar}></ha-icon>`
          : html`<img src=${avatar} alt="">`}</span>
        <h1>${child.name}</h1>
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
      <div class="workspace">${this._content || html`<div class="empty" role="status">${this._t("common.loading")}</div>`}</div>
    </section>`;
  }
}

customElements.define("taskmate-child-page-card", TaskMateChildPageCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: "taskmate-child-page-card", name: "TaskMate Child Page",
  description: "A responsive child workspace with chores, rewards and a compact Stars header." });
