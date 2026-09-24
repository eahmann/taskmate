/** Family doorway: live routine summaries, child routes and a parent-route link. */
const LitElement = Object.getPrototypeOf(customElements.get("hui-masonry-view") || customElements.get("hui-view"));
const html = LitElement.prototype.html;
const css = LitElement.prototype.css;

class TaskMateFamilyPageCard extends LitElement {
  static get properties() { return { hass: { type: Object }, config: { type: Object } }; }

  static get styles() {
    return css`
      :host { display: block; min-width: 0; container-type: inline-size; }
      * { box-sizing: border-box; }
      .page { padding: clamp(12px, 2.4cqw, 48px); color: var(--primary-text-color);
        font-family: var(--paper-font-body1_-_font-family, system-ui, sans-serif); }
      .page-header, .identity, .routine-title, .links { display: flex; align-items: center; gap: 12px; }
      .page-header { justify-content: space-between; margin-bottom: 32px; flex-wrap: wrap; }
      .today { margin-bottom: 32px; --accent: var(--primary-color); }
      .today h2 { font-size: 20px; margin-bottom: 8px; }
      h1 { font-size: clamp(28px, 3cqw, 46px); margin: 0; }
      h2 { font-size: clamp(23px, 2.4cqw, 36px); margin: 0; overflow-wrap: anywhere; }
      .children { display: grid; gap: 36px; grid-template-columns: minmax(0, 1fr); align-items: start; }
      .child { min-width: 0; }
      a { color: inherit; text-decoration: none; border-radius: 12px; }
      a:focus-visible { outline: 3px solid var(--primary-color); outline-offset: 3px; }
      .identity { min-height: 64px; margin-bottom: 20px; }
      .identity > a { display: flex; align-items: center; gap: 12px; min-width: 0; min-height: 48px; }
      .avatar { color: var(--accent); width: 44px; height: 44px; flex: none; --mdc-icon-size: 40px; }
      .avatar img { width: 100%; height: 100%; border-radius: 50%; object-fit: cover; }
      .balance { margin-left: auto; flex: none; font-weight: 750; font-size: clamp(17px, 1.7cqw, 24px); }
      .balance ha-icon, .bonus { color: #edc556; }
      .balance small { font-size: .72em; }
      .routine { display: block; margin-bottom: 14px; padding: clamp(16px, 2cqw, 24px); border-radius: 20px;
        background: color-mix(in srgb, var(--accent) 13%, var(--primary-background-color));
        border: 1px solid color-mix(in srgb, var(--accent) 25%, transparent); }
      .routine:hover { border-color: var(--accent); }
      .routine-title { align-items: start; }
      .routine-title > ha-icon { color: var(--accent); --mdc-icon-size: 28px; }
      .routine-title strong { flex: 1; min-width: 0; overflow-wrap: anywhere; font-size: clamp(18px, 1.8cqw, 26px); }
      .bonus { white-space: nowrap; font-weight: 700; }
      .bonus ha-icon { --mdc-icon-size: 18px; }
      .progress-text, .next, .muted { color: var(--secondary-text-color); font-size: 15px; line-height: 1.5; }
      .next { margin: 12px 0 0; color: var(--primary-text-color); }
      progress { display: block; appearance: none; width: 100%; height: 6px; border: 0; border-radius: 3px;
        overflow: hidden; margin: 14px 0 8px; background: var(--divider-color, #8884); }
      progress::-webkit-progress-bar { background: var(--divider-color, #8884); }
      progress::-webkit-progress-value { background: var(--accent); border-radius: 3px; }
      progress::-moz-progress-bar { background: var(--accent); }
      .links { flex-wrap: wrap; justify-content: space-between; margin-top: 18px; }
      .link, .parents { display: inline-flex; align-items: center; gap: 8px; min-height: 48px; padding: 8px 12px; }
      .link { color: var(--accent); font-weight: 700; }
      .parents { border: 1px solid var(--divider-color, #8884); }
      .link:hover, .parents:hover { background: var(--secondary-background-color); }
      @container (min-width: 900px) { .children { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 48px; } }
    `;
  }

  setConfig(config) {
    if (!config.entity || !Array.isArray(config.child_pages) || !config.child_pages.length) {
      throw new Error("Define entity and child_pages for the family overview.");
    }
    const ids = new Set();
    const local = path => typeof path === "string" && /^\/(?!\/)/.test(path) && !/[\\\s]/.test(path);
    for (const page of config.child_pages) {
      if (!page?.child_id || ids.has(page.child_id) || !local(page.chores_path) || !local(page.rewards_path)) {
        throw new Error("Each child needs a unique child_id and local chores_path / rewards_path.");
      }
      ids.add(page.child_id);
    }
    if (config.parents_path && !local(config.parents_path)) throw new Error("parents_path must be a local dashboard path.");
    this.config = config;
    this._readers = new Map();
    customElements.whenDefined("taskmate-child-card").then(() => this.requestUpdate());
  }

  getCardSize() { return 8; }
  getGridOptions() { return { columns: "full", rows: "auto" }; }
  _t(key, params) { return window.__taskmate_localize?.(this.hass, key, params) || key; }

  _summary(child) {
    if (!customElements.get("taskmate-child-card")) return null;
    // Detached, read-only readers reuse the real card's scheduling/completion logic.
    // They never connect, start timers, render actions, or call services.
    if (!this._readers.has(child.id)) {
      const reader = document.createElement("taskmate-child-card");
      reader.setConfig({ ...this.config.chore_options, entity: this.config.entity, child_id: child.id,
        time_category: "all", dependency_mode: "show", recurrence_done_mode: "show", show_countdown: false });
      this._readers.set(child.id, reader);
    }
    const reader = this._readers.get(child.id);
    reader.hass = this.hass;
    return reader.overviewData(child);
  }

  _navigate(event, path) {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    window.history.pushState(null, "", path);
    window.dispatchEvent(new CustomEvent("location-changed"));
  }

  _routinePath(path, id) {
    const url = new URL(path, window.location.origin);
    url.searchParams.set("routine", id);
    return url.pathname + url.search + url.hash;
  }

  render() {
    if (!this.hass || !this.config) return html``;
    // The shell follows HA; child accents come from the same shared design resolver.
    window.__taskmate_design?.apply(this, this.hass, this.config, this.config.entity);
    const entity = this.hass.states[this.config.entity];
    const attrs = window.__taskmate_attrs?.(this.hass, this.config.entity) || entity?.attributes || {};
    if (!entity) return html`<p role="status">${this._t("child_page.unavailable")}</p>`;
    const pages = this.config.child_pages.map(page => ({ ...page, child: (attrs.children || []).find(c => c.id === page.child_id) }))
      .filter(p => p.child).map(page => ({ ...page, summary: this._summary(page.child) }));
    const routines = pages.flatMap(page => page.summary?.routines || []);
    const required = routines.reduce((n, r) => n + (r.required_count || 0), 0);
    const approved = routines.reduce((n, r) => n + Math.min(r.completed_count || 0, r.required_count || 0), 0);
    const pending = routines.reduce((n, r) => n + (r.pending_count || 0), 0);
    return html`<section class="page">
      <header class="page-header"><h1>${this.config.title || this._t("common.chores")}</h1>
        ${this.config.parents_path ? html`<a class="parents" href=${this.config.parents_path} @click=${e => this._navigate(e, this.config.parents_path)}>
          <ha-icon icon="mdi:lock-outline"></ha-icon>${this._t("family.parents")}</a>` : ""}</header>
      ${required ? html`<section class="today"><h2>${this._t("family.today")}</h2>
        <div class="muted">${this._t("family.routine_progress", { done: approved, total: required })}
          ${pending ? html` · ${this._t("routine.pending", { count: pending })}` : ""}</div>
        <progress aria-label=${this._t("family.today")} max=${required} value=${approved}></progress></section>` : ""}
      <div class="children">${pages.map(page => {
        const { child, summary } = page;
        const accent = window.__taskmate_design?.childColor?.(child, page.accent_color || this.config.accent_color) || "#b885e3";
        const avatar = child.avatar || "mdi:account-circle";
        return html`<section class="child" style="--accent:${accent}" aria-label=${child.name}>
          <header class="identity"><a href=${page.chores_path} @click=${e => this._navigate(e, page.chores_path)}>
            <span class="avatar">${avatar.startsWith("mdi:") ? html`<ha-icon icon=${avatar}></ha-icon>` : html`<img src=${avatar} alt="">`}</span><h2>${child.name}</h2></a>
            <span class="balance"><ha-icon icon=${attrs.points_icon || "mdi:star"}></ha-icon>
              ${Number(child.spendable_balance ?? child.points ?? 0).toLocaleString()} <small>${attrs.points_name || this._t("common.stars")}</small></span></header>
          ${summary ? html`${summary.routines.map(routine => {
            const path = this._routinePath(page.chores_path, routine.id);
            return html`<a class="routine" href=${path} @click=${e => this._navigate(e, path)}>
              <div class="routine-title"><ha-icon icon=${routine.done ? "mdi:check-circle" : routine.icon || "mdi:format-list-checks"}></ha-icon><strong>${routine.name}</strong>
                ${routine.bonus_points ? html`<span class="bonus">+${routine.bonus_points} <ha-icon icon=${attrs.points_icon || "mdi:star"}></ha-icon></span>` : ""}</div>
              <progress aria-label=${routine.name} max=${Math.max(1, routine.required_count || 0)} value=${Math.min(routine.completed_count || 0, routine.required_count || 0)}></progress>
              <div class="progress-text">${this._t("routine.progress", { done: routine.completed_count, total: routine.required_count })}</div>
              <p class="next">${routine.done ? this._t("routine.complete_status") : routine.next ? this._t("family.next", { name: routine.next.name })
                : routine.pending_count ? this._t("routine.pending", { count: routine.pending_count }) : this._t("family.none_ready")}</p>
            </a>`;
          })}${!summary.routines.length ? html`<p class="muted">${this._t("family.no_routines")}</p>` : ""}`
            : html`<p role="status">${this._t("panel.loading")}</p>`}
          <div class="links"><a class="link" href=${page.chores_path} @click=${e => this._navigate(e, page.chores_path)}>
            <ha-icon icon="mdi:format-list-checks"></ha-icon>${summary?.other.length ? this._t("family.other_available", { count: summary.other.length }) : this._t("family.open_chores")}</a>
            <a class="link" href=${page.rewards_path} @click=${e => this._navigate(e, page.rewards_path)}><ha-icon icon="mdi:gift-outline"></ha-icon>${this._t("child_page.my_rewards")}</a></div>
        </section>`;
      })}</div>
    </section>`;
  }
}

customElements.define("taskmate-family-page-card", TaskMateFamilyPageCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: "taskmate-family-page-card", name: "TaskMate Family Page", description: "Family overview with live routines and child navigation." });
