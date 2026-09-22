/**
 * TaskMate attribute resolver.
 *
 * The overview sensor used to expose every data slice (chores, rewards,
 * recent activity, penalties, bonuses) as attributes of
 * sensor.taskmate_overview. That payload routinely exceeded Home Assistant's
 * 16 KB recorder limit, so the data is now split across companion sensors
 * (sensor.taskmate_chores, _rewards, _activity, _incentives).
 *
 * To keep existing Lovelace dashboards working unchanged, every card reads
 * attributes through window.__taskmate_attrs(hass, primaryEntityId), which
 * returns a merged attribute object: primary attributes plus each companion
 * sensor's attributes overlaid on top. If a companion sensor is missing
 * (e.g. older backend), its keys fall back to the primary sensor's
 * attributes. Cards therefore do not need to know which sensor owns which
 * attribute — lookup is transparent.
 */

(function () {
  "use strict";

  // Fixed companion entity ids. TaskMate is a single-instance integration
  // (config_flow enforces unique_id == DOMAIN), so these ids are stable.
  const COMPANIONS = [
    "sensor.taskmate_chores",
    "sensor.taskmate_chore_availability",
    "sensor.taskmate_rewards",
    "sensor.taskmate_activity",
    "sensor.taskmate_incentives",
    // Full pending-approvals lists (chore_completions / reward_claims). These
    // are NOT filtered to today, so approval cards pointed at the overview
    // entity can still surface a completion left pending from a previous day.
    // Named "Pending Approvals", but it carries no _attr_has_entity_name, so
    // the TaskMate device name still prefixes its entity id. This entry was
    // missing that prefix and so matched nothing at all, silently dropping
    // chore_completions and mandatory_misses from every merge (#798).
    "sensor.taskmate_pending_approvals",
  ];

  // Attributes a companion must NOT contribute to the merge, because another
  // sensor already owns that name for different data (#834).
  //
  // sensor.taskmate_pending_approvals publishes both the full lists and a
  // scalar count of each. Its `pending_reward_claims` count collides with
  // sensor.taskmate_rewards' `pending_reward_claims` LIST, and the approvals
  // sensor merges last, so the number won — every card that did
  // `claims.filter(...)` / `claims.some(...)` threw as soon as a claim was
  // pending, blanking the card. Zero pending claims hid it: the cards' own
  // `|| []` fallback catches a count of 0 because it is falsy, so the crash
  // only appeared once a child actually claimed something.
  //
  // Nothing reads these counts through the resolver — the admin panel takes
  // its badge counts from the taskmate/get_state WebSocket call and the cards
  // derive theirs from the lists' length. They stay readable directly off
  // sensor.taskmate_pending_approvals for templates and automations.
  const COMPANION_SKIP_KEYS = {
    "sensor.taskmate_pending_approvals": [
      "pending_chore_completions",
      "pending_reward_claims",
      "pending_mandatory_misses",
    ],
  };

  function mergedAttributes(hass, primaryEntityId) {
    if (!hass || !hass.states) return {};
    const merged = {};
    const primary = hass.states[primaryEntityId];
    if (primary && primary.attributes) {
      Object.assign(merged, primary.attributes);
    }
    for (const id of COMPANIONS) {
      const s = hass.states[id];
      if (!s || !s.attributes) continue;
      const skip = COMPANION_SKIP_KEYS[id];
      if (!skip) {
        Object.assign(merged, s.attributes);
        continue;
      }
      for (const [key, value] of Object.entries(s.attributes)) {
        if (!skip.includes(key)) merged[key] = value;
      }
    }
    return merged;
  }

  // Cache merged attributes per hass/primary tuple so repeated card renders
  // during a single Home Assistant state update don't rebuild the object.
  let _cache = { stateRef: null, byPrimary: new Map() };

  function resolveAttrs(hass, primaryEntityId) {
    if (!hass) return {};
    if (_cache.stateRef !== hass.states) {
      _cache = { stateRef: hass.states, byPrimary: new Map() };
    }
    if (!_cache.byPrimary.has(primaryEntityId)) {
      _cache.byPrimary.set(primaryEntityId, mergedAttributes(hass, primaryEntityId));
    }
    return _cache.byPrimary.get(primaryEntityId);
  }

  window.__taskmate_attrs = resolveAttrs;

  /**
   * Non-admin parent role (#661). True for Home Assistant admins and for users
   * the admin has designated as TaskMate parents (published as the
   * parent_user_ids attribute on sensor.taskmate_overview). Parents get the
   * day-to-day controls (approve/reject, complete-on-behalf, undo) without HA
   * admin rights. Admin remains the only tier that can open the admin panel or
   * change configuration.
   */
  function isTaskmateParent(hass) {
    if (!hass || !hass.user) return false;
    if (hass.user.is_admin) return true;
    let ids;
    const ov = hass.states && hass.states["sensor.taskmate_overview"];
    if (ov && ov.attributes && Array.isArray(ov.attributes.parent_user_ids)) {
      ids = ov.attributes.parent_user_ids;
    } else if (hass.states) {
      // Overview sensor renamed: find whichever taskmate sensor carries the list.
      for (const st of Object.values(hass.states)) {
        if (st && st.attributes && Array.isArray(st.attributes.parent_user_ids)) {
          ids = st.attributes.parent_user_ids;
          break;
        }
      }
    }
    return Array.isArray(ids) && ids.includes(hass.user.id);
  }

  window.__taskmate_is_parent = isTaskmateParent;

  // Server-issued eligibility is shared by every child-facing card. Never
  // derive approval type from a chore's current (editable) approval setting.
  function canUndoChore(completion, now = Date.now()) {
    return !!(completion && (completion.completion_id || completion.id)
      && (completion.child_undo_pending === true
        || Date.parse(completion.child_undo_until) > now));
  }

  function undoCandidates(attrs, childId) {
    const byId = new Map();
    // Today's records win over older pending snapshots during a state update.
    for (const c of [...(attrs.chore_completions || []), ...(attrs.todays_completions || [])]) {
      if (c.child_id === childId) byId.set(c.completion_id || c.id, c);
    }
    return [...byId.values()].filter(c => canUndoChore(c))
      .sort((a, b) => Date.parse(b.completed_at) - Date.parse(a.completed_at));
  }

  function scheduleUndoExpiry(card, attrs, childId) {
    clearTimeout(card._undoExpiryTimer);
    card._undoExpiryTimer = null;
    const checklist = Object.values((attrs.children || []).find(c => c.id === childId)?.checklist_progress || {});
    const acknowledged = [...(card._checklistSnapshots || new Map())]
      .filter(([key]) => key.startsWith(`${childId}:`)).map(([, entry]) => entry.progress);
    const deadlines = [...undoCandidates(attrs, childId), ...checklist, ...acknowledged]
      .filter(c => canUndoChore(c))
      .map(c => Date.parse(c.child_undo_until)).filter(t => Number.isFinite(t));
    if (deadlines.length) {
      card._undoExpiryTimer = setTimeout(() => card.requestUpdate(),
        Math.max(1, Math.min(...deadlines) - Date.now() + 1));
    }
  }

  function renderChoreUndo(html, card, attrs, childId, onUndo) {
    const candidates = undoCandidates(attrs, childId);
    if (!candidates.length) return html``;
    return html`
      <style>
        .tm-child-undo { display:flex; flex-wrap:wrap; gap:8px; padding:12px 16px;
          background:var(--tmd-surface, var(--card-background-color, #fff)); }
        .tm-child-undo button { display:flex; align-items:center; gap:8px;
          min-height:44px; padding:8px 12px; border:1px solid var(--divider-color, #888);
          border-radius:12px; background:var(--card-background-color, #fff);
          color:var(--primary-text-color, #222); font:inherit; cursor:pointer; }
        .tm-child-undo button:disabled { opacity:.5; cursor:default; }
        .tm-child-undo button:focus-visible { outline:3px solid var(--primary-color, #03a9f4); }
      </style>
      <div class="tm-child-undo" aria-label=${card._t('child.undo_recent')}>
        ${candidates.map(c => html`<button type="button"
          ?disabled=${!!card._busy || !!card._loading?.[c.chore_id]}
          @click=${() => onUndo(c)}>
          <ha-icon icon="mdi:undo-variant"></ha-icon>
          ${card._t('child.undo_named', { name: c.chore_name || (attrs.chores || []).find(chore => chore.id === c.chore_id)?.name || '' })}
        </button>`)}
      </div>`;
  }

  window.__taskmate_chore_undo = {
    canUndo: canUndoChore, candidates: undoCandidates,
    scheduleExpiry: scheduleUndoExpiry, render: renderChoreUndo,
  };

  // Checklist state belongs to an individual child and occurrence. Never put
  // checked flags on the shared chore definition (siblings use that same object).
  function checklistProgress(card, child, chore) {
    const source = child?.checklist_progress?.[chore.id];
    const key = `${child?.id}:${chore.id}`;
    const cached = card._checklistSnapshots?.get(key);
    if (cached && source === cached.source) return cached.progress;
    if (cached) card._checklistSnapshots.delete(key);
    return source || { items: (chore.checklist_items || []).map(item => ({ ...item, checked: false })), occurrence_id: '', completion_id: '' };
  }

  function rememberChecklist(card, child, chore, source, progress) {
    if (!progress) return;
    card._checklistSnapshots ||= new Map();
    card._checklistSnapshots.set(`${child.id}:${chore.id}`, { source, progress });
  }

  function renderChecklistItems(html, card, progress, disabled, onToggle) {
    const items = progress.items || [];
    return html`
      <style>
        .tm-checklist-items { width:100%; display:flex; flex-direction:column; gap:8px; text-align:left; }
        .tm-checklist-progress { font-size:.88em; color:var(--tmd-dim,var(--secondary-text-color)); }
        .tm-checklist-item { display:flex; align-items:center; gap:12px; width:100%; min-height:48px;
          text-align:left; padding:10px 12px; font:inherit; font-weight:600; cursor:pointer;
          border:1px solid var(--tmd-border,var(--divider-color,#8886)); border-radius:var(--tmd-radius-sm,12px);
          color:var(--tmd-text,var(--primary-text-color)); background:var(--tmd-surface,var(--card-background-color,#fff)); }
        .tm-checklist-item[aria-checked="true"] { background:color-mix(in srgb,var(--tmd-good,#16a34a) 12%,var(--card-background-color,#fff)); }
        .tm-checklist-item ha-icon { flex:0 0 24px; --mdc-icon-size:24px; }
        .tm-checklist-item span { overflow-wrap:anywhere; }
        .tm-checklist-item:disabled { cursor:default; opacity:.65; }
        .tm-checklist-item:focus-visible { outline:3px solid var(--primary-color,#03a9f4); outline-offset:2px; }
      </style>
      <div class="tm-checklist-items" role="group" aria-label=${card._t('child.checklist_hint')}>
        <div class="tm-checklist-progress" aria-live="polite">${card._t('child.checklist_progress', { done: items.filter(i => i.checked).length, total: items.length })}</div>
        ${items.map(item => html`<button class="tm-checklist-item" type="button" role="checkbox"
          data-step-id="${item.id}" aria-checked="${!!item.checked}"
          ?disabled=${disabled || !progress.occurrence_id || (!!progress.completion_id && !canUndoChore(progress))}
          @click=${event => { event.stopPropagation(); return onToggle(item, !item.checked); }}>
          <ha-icon icon="${item.checked ? 'mdi:checkbox-marked' : 'mdi:checkbox-blank-outline'}"></ha-icon>
          <span>${item.name}</span>
        </button>`)}
      </div>`;
  }

  window.__taskmate_checklist = { progress: checklistProgress, remember: rememberChecklist, render: renderChecklistItems };

  /**
   * Stable id of a badge entry from the badges sensor.
   *
   * ChildBadgesSensor publishes earned/available entries keyed `badge_id`, but
   * several card templates were written against `b.id` — undefined on those
   * entries, so every "is this the badge that was just earned?" test compared
   * against the string "undefined" and never matched (#752). Read the id
   * through this helper so both spellings work.
   */
  function badgeId(badge) {
    if (!badge) return "";
    const id = badge.badge_id != null ? badge.badge_id : badge.id;
    return id != null ? String(id) : "";
  }

  window.__taskmate_badge_id = badgeId;

  /**
   * Event-driven update guard for LitElement cards.
   *
   * HA sets a new `hass` object on every card whenever ANY entity changes.
   * Cards that don't filter will re-render for every light toggle, thermostat
   * update, etc. This helper compares only TaskMate-relevant entities so
   * cards skip renders that can't possibly change their output.
   *
   * Usage inside a card class:
   *   shouldUpdate(changedProps) {
   *     if (changedProps.has("hass")) {
   *       return window.__taskmate_hasChanged(changedProps.get("hass"), this.hass, this.config?.entity);
   *     }
   *     return true;
   *   }
   */
  const TASKMATE_BUTTON_PREFIX = "button.taskmate_";

  function hasRelevantChange(oldHass, newHass, primaryEntityId) {
    if (!oldHass || !oldHass.states || !newHass || !newHass.states) return true;
    if (oldHass.states === newHass.states) return false;

    if (primaryEntityId) {
      if (oldHass.states[primaryEntityId] !== newHass.states[primaryEntityId]) return true;
    }

    for (const id of COMPANIONS) {
      if (oldHass.states[id] !== newHass.states[id]) return true;
    }

    for (const id in newHass.states) {
      if (id.startsWith(TASKMATE_BUTTON_PREFIX)) {
        if (oldHass.states[id] !== newHass.states[id]) return true;
      }
    }

    return false;
  }

  window.__taskmate_hasChanged = hasRelevantChange;

  /**
   * Find the entity id of a TaskMate button by (child_id, action, target_id).
   *
   * TaskMate creates per-child/per-chore and per-child/per-reward button
   * entities. Lovelace cards used to call the backend services directly
   * (e.g. taskmate.complete_chore), which bypassed the button entity and
   * left its last_pressed state unchanged — so HA state-trigger automations
   * on button.taskmate_<child>_complete_<chore> never fired. Cards now
   * resolve the matching button entity and call button.press, making the
   * card and the HA entity the same code path.
   *
   * action: "complete" (chore) | "claim" (reward)
   * target_id: chore_id or reward_id
   */
  function findButtonEntity(hass, childId, action, targetId) {
    if (!hass || !hass.states || !childId || !targetId) return null;
    const targetKey = action === "claim" ? "reward_id" : "chore_id";
    for (const [entityId, state] of Object.entries(hass.states)) {
      if (!entityId.startsWith("button.taskmate_")) continue;
      const attrs = state && state.attributes;
      if (!attrs) continue;
      if (attrs.child_id !== childId) continue;
      if (attrs[targetKey] !== targetId) continue;
      // Disambiguate chore complete vs reward claim — both carry child_id,
      // but only one of chore_id / reward_id.
      if (action === "claim" && !("reward_id" in attrs)) continue;
      if (action === "complete" && !("chore_id" in attrs)) continue;
      return entityId;
    }
    return null;
  }

  window.__taskmate_find_button = findButtonEntity;

  // Cold-cache fix: if any TaskMate cards already mounted before this module
  // finished loading, they fell back to entity.attributes (overview-only) and
  // rendered empty chore lists. Walk the DOM (crossing shadow roots, since HA
  // nests Lovelace inside several shadow trees) and force a re-render now
  // that window.__taskmate_attrs is defined.
  function _refreshTaskMateCards() {
    const stack = [document];
    while (stack.length) {
      const node = stack.pop();
      if (!node) continue;
      if (node.tagName && node.tagName.startsWith("TASKMATE-")) {
        if (typeof node.requestUpdate === "function") {
          try { node.requestUpdate(); } catch (_e) { /* ignore */ }
        }
      }
      if (node.shadowRoot) stack.push(node.shadowRoot);
      const children = node.children;
      if (children) {
        for (let i = 0; i < children.length; i++) stack.push(children[i]);
      }
    }
  }
  queueMicrotask(_refreshTaskMateCards);
})();
