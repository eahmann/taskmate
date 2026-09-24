<p align="center">
  <img src="https://raw.githubusercontent.com/tempus2016/taskmate/main/logo.svg" alt="TaskMate" width="180">
</p>

<h1 align="center">TaskMate</h1>

This fork adds [daily Routines built from ordinary chores](ROUTINES.md).

An optional [responsive child page](CHILD-PAGES.md) combines a compact Stars
header, Chores / My rewards navigation, and flat lists that expand for tablets.

**Checklist chores** break one chore into required, unscored items. Choose
**Task type → Checklist** when adding or editing a chore, set the chore's points,
then add its items. For example, **Get dressed — 2 stars** could contain
**Put daytime clothes on** and **Put pajamas away**. Items can be checked in any
order; checking the final item automatically submits the whole chore. Parent
approval and photo proof still apply when enabled. There is no separate Claim
button and partial progress earns no points.

Each child has their own saved progress, shared across their dashboards and
devices. A new occurrence starts with unchecked items. Children can uncheck
items before submission; afterward, the global chore undo rules below apply,
preserving the other checked items. Checklist chores can also be members of
Routines. Open-ended chores cannot use a checklist because a checklist has a
fixed whole-chore award. Each checklist supports 1–30 items.

It also adds a global **Chore undo window (seconds)** in **TaskMate → Settings**.
The default is 10 seconds; choose 0–3600 seconds for all children and dashboards.
Automatically approved chores can be undone during that window. Pending child
submissions can be withdrawn until parent approval, even with the window set to
0. A parent's review locks child undo immediately. Parents retain their existing
correction controls. This applies to new submissions; older completion records
remain parent-only.

The child card (including routine groups, all designs, and picture mode) and
guided routine card show **Undo [chore]** near the top. It remains available when
`show_parent_actions: false`; this does not expose parent controls. Backend checks
enforce the deadline and linked-child permissions, and reversals include the chore
award, affected bonus subtasks, and routine completion bonus. Automations can use
`taskmate.undo_chore` with a `completion_id` under the same restrictions.

<p align="center">
  <strong>Turn chores into a game your kids actually want to play.</strong><br>
  A Home Assistant integration for family chore management, rewards, and streak tracking.
</p>

<p align="center">
  <a href="https://github.com/tempus2016/taskmate/releases"><img src="https://img.shields.io/github/v/release/tempus2016/taskmate" alt="Latest Release"></a>
  <a href="https://github.com/hacs/default"><img src="https://img.shields.io/badge/HACS-Default-41BDF5.svg" alt="HACS Default"></a>
  <a href="https://github.com/tempus2016/taskmate/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="License"></a>
  <img src="https://img.shields.io/badge/Home%20Assistant-2024.7+-blue" alt="HA Version">
  <a href="https://github.com/tempus2016/taskmate/releases"><img src="https://img.shields.io/github/downloads/tempus2016/taskmate/total" alt="Downloads"></a>
  <a href="https://community.home-assistant.io/t/taskmate-family-chore-rewards-integration-now-in-hacs-default/1023281"><img src="https://img.shields.io/badge/community-forum-41BDF5?logo=home-assistant&logoColor=white" alt="Community Forum"></a>
</p>

<p align="center">
  <a href="https://github.com/tempus2016/taskmate/actions/workflows/validate.yml"><img src="https://github.com/tempus2016/taskmate/actions/workflows/validate.yml/badge.svg" alt="HACS Validation"></a>
  <a href="https://github.com/tempus2016/taskmate/actions/workflows/hassfest.yaml"><img src="https://github.com/tempus2016/taskmate/actions/workflows/hassfest.yaml/badge.svg" alt="hassfest"></a>
  <a href="https://github.com/tempus2016/taskmate/actions/workflows/tests.yml"><img src="https://github.com/tempus2016/taskmate/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
  <a href="https://github.com/tempus2016/taskmate/actions/workflows/lint.yml"><img src="https://github.com/tempus2016/taskmate/actions/workflows/lint.yml/badge.svg" alt="Lint"></a>
  <a href="https://github.com/tempus2016/taskmate/actions/workflows/data-checks.yml"><img src="https://github.com/tempus2016/taskmate/actions/workflows/data-checks.yml/badge.svg" alt="Data checks"></a>
  <a href="https://results.pre-commit.ci/latest/github/tempus2016/taskmate/main"><img src="https://results.pre-commit.ci/badge/github/tempus2016/taskmate/main.svg" alt="pre-commit.ci"></a>
</p>

<p align="center">
  <strong>📖 Full documentation lives in the <a href="https://github.com/tempus2016/taskmate/wiki">Wiki</a>.</strong>
</p>

---

## How it works

### Shared family screens (fork feature)

Cards with parent controls now have a **Show parent actions** switch in their visual editor. Set `show_parent_actions: false` on open kids/family pages when the shared Home Assistant account has the TaskMate parent role. The default remains `true`.

This hides and blocks the card's undo, complete-on-behalf, approval, points-adjustment and incentive actions across Classic, Playroom, Console, Clean Pro and Accessible designs. Child chore completion and reward claims continue to work. Parent-only dashboard, approvals and points cards render no controls when the option is off; the overview, activity and incentive cards retain their informational content.

The switch controls presentation, not backend authorization. Pair it with Dashboard Lock on parent views for a shared-screen PIN; the account retains its normal TaskMate and Home Assistant permissions.

### Daily flow

1. **Create chores** — assign them to children, set point values and schedules
2. **Kids complete chores** — tap the child card to tick them off, earn points, build streaks
3. **Parents approve** — chores marked "requires approval" land in a pending queue
4. **Kids claim rewards** — points are only deducted once a parent approves the claim

Everything is managed from the **TaskMate panel** in the Home Assistant sidebar, and surfaced on your dashboards through 21 Lovelace cards.

All data stays inside your Home Assistant instance. Nothing is sent to any external service.

<p align="center">
  <img src="https://raw.githubusercontent.com/tempus2016/taskmate/main/images/adminPanel.png" alt="TaskMate admin panel" width="700">
</p>

---

## Install

TaskMate is a **default HACS integration** — no custom repository needed.

1. Open **HACS** → search **"TaskMate"** → **Download**
2. **Restart Home Assistant**
3. **Settings → Devices & Services → Add Integration → TaskMate**
4. Pick your points name (Stars, Coins, Bucks…) and icon — that's the whole config flow

[![Open TaskMate in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=tempus2016&repository=taskmate&category=integration)
&nbsp;
[![Add TaskMate integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=taskmate)

Requires **Home Assistant 2024.7+** and a current browser (Chrome, Firefox, Edge, or Safari).

For manual installation and the first-run walkthrough, see [Installation](https://github.com/tempus2016/taskmate/wiki/Installation) and [Getting Started](https://github.com/tempus2016/taskmate/wiki/Getting-Started).

### Clear a test setup before going live

As a Home Assistant administrator, open **TaskMate → Settings → Reset all TaskMate data → Back up and reset…**. Check that the timestamped JSON backup downloaded, then type **RESET TASKMATE** to confirm. Cancelling leaves your setup unchanged.

Reset clears profiles, chores, rewards, balances, allocations, activity, custom configuration, and settings. It restores the built-in defaults and reloads the existing integration. Home Assistant users, dashboards, and automations remain; cards or automations targeting deleted children may need reconfiguring. Active timed reward unlocks must finish before resetting.

The JSON backup contains data and media references, not uploaded files. Chore images and sounds stay on disk, while unreferenced evidence photos may be removed by routine cleanup. Keep a full Home Assistant backup if you need to preserve uploaded media as well. To undo a reset, use **Restore backup** and reload the integration as prompted.

---

## What you get

**Chores** — flexible [scheduling](https://github.com/tempus2016/taskmate/wiki/Chore-Scheduling) (specific days, recurring, rotation, first-come), [difficulty tiers](https://github.com/tempus2016/taskmate/wiki/Difficulty-Tiers), [dependencies](https://github.com/tempus2016/taskmate/wiki/Chore-Dependencies), [timed tasks](https://github.com/tempus2016/taskmate/wiki/Timed-Tasks), [photo proof](https://github.com/tempus2016/taskmate/wiki/Photo-Proof), [swaps](https://github.com/tempus2016/taskmate/wiki/Chore-Swaps), [vacation mode](https://github.com/tempus2016/taskmate/wiki/Vacation-Mode), and chores that appear or hide based on [HA entity state](https://github.com/tempus2016/taskmate/wiki/Dynamic-Chore-Visibility) or [the weather](https://github.com/tempus2016/taskmate/wiki/Weather-Aware-Chores).

**Points & rewards** — [fixed-cost rewards](https://github.com/tempus2016/taskmate/wiki/Rewards) with a parent approval flow, [savings jars](https://github.com/tempus2016/taskmate/wiki/Pool-Mode-\(Savings-Jars\)), [bonuses](https://github.com/tempus2016/taskmate/wiki/Bonuses) and [penalties](https://github.com/tempus2016/taskmate/wiki/Penalties), [weekend multipliers and streak milestones](https://github.com/tempus2016/taskmate/wiki/Bonus-Points), [real-money allowance](https://github.com/tempus2016/taskmate/wiki/Allowance), [shared family goals](https://github.com/tempus2016/taskmate/wiki/Family-Goals), [timed device unlocks](https://github.com/tempus2016/taskmate/wiki/Timed-Unlock-Rewards), and [time-locked rewards](https://github.com/tempus2016/taskmate/wiki/Time-Locked-Rewards) that are only claimable on chosen days or between set hours.

**Gamification** — [levels & XP](https://github.com/tempus2016/taskmate/wiki/Levels-and-XP), [achievement badges](https://github.com/tempus2016/taskmate/wiki/Achievement-Badges), [quests](https://github.com/tempus2016/taskmate/wiki/Quests), [challenges](https://github.com/tempus2016/taskmate/wiki/Challenges), [unlockable avatars](https://github.com/tempus2016/taskmate/wiki/Avatars), [celebrations](https://github.com/tempus2016/taskmate/wiki/Celebrations), and [leaderboard seasons](https://github.com/tempus2016/taskmate/wiki/Leaderboard-Seasons).

**Parent tooling** — [notifications](https://github.com/tempus2016/taskmate/wiki/Notifications) with [quiet hours](https://github.com/tempus2016/taskmate/wiki/Quiet-Hours) and [reminder escalation](https://github.com/tempus2016/taskmate/wiki/Reminder-Escalation), [multi-parent approval routing](https://github.com/tempus2016/taskmate/wiki/Multi-Parent-Approval-Routing), a [weekly digest](https://github.com/tempus2016/taskmate/wiki/Weekly-Digest), [insight reports](https://github.com/tempus2016/taskmate/wiki/Insights) on fairness and friction, an [audit log](https://github.com/tempus2016/taskmate/wiki/Admin-Audit-Log), and [backup & restore](https://github.com/tempus2016/taskmate/wiki/Backup-and-Restore).

**Made for kids** — [Routine Mode](https://github.com/tempus2016/taskmate/wiki/Routine-Mode) for guided morning and bedtime flows, [Pre-Reader Mode](https://github.com/tempus2016/taskmate/wiki/Pre-Reader-Mode) for children who can't read yet, [Read Aloud](https://github.com/tempus2016/taskmate/wiki/Read-Aloud) to a media player, a [printable fridge chart](https://github.com/tempus2016/taskmate/wiki/Printable-Weekly-Chart), [custom completion sounds](https://github.com/tempus2016/taskmate/wiki/Completion-Sounds) you can upload and pick per chore, and five [card design styles](https://github.com/tempus2016/taskmate/wiki/Card-Design-Styles) including a high-contrast accessible theme.

---

## Dashboard cards

Lovelace resources register automatically on startup. Edit your dashboard → **Add Card** → search "taskmate".

| For kids | For parents |
|----------|-------------|
| Child · Rewards · Reward Progress · Points Display · Routine · Streak · Badges | Approvals · Parent Dashboard · Overview · Activity · Points · Reorder · Penalties · Bonuses · Weekly · Graph · Leaderboard · Calendar · Family Goal · Photo Gallery |

Every card takes a `header_color` and a `card_design` option. See [Dashboard Cards](https://github.com/tempus2016/taskmate/wiki/Dashboard-Cards) for each card's full configuration.

<p align="center">
  <img src="https://raw.githubusercontent.com/tempus2016/taskmate/main/images/childCard.png" alt="TaskMate child card" width="380">
  &nbsp;
  <img src="https://raw.githubusercontent.com/tempus2016/taskmate/main/images/overview.png" alt="TaskMate overview card" width="380">
</p>

---

## Documentation

| | |
|---|---|
| [Getting Started](https://github.com/tempus2016/taskmate/wiki/Getting-Started) | First-time setup walkthrough |
| [Admin Panel](https://github.com/tempus2016/taskmate/wiki/Admin-Panel) | The management hub in the HA sidebar |
| [Settings](https://github.com/tempus2016/taskmate/wiki/Settings) | Every configurable setting in one place |
| [Dashboard Cards](https://github.com/tempus2016/taskmate/wiki/Dashboard-Cards) | All 21 Lovelace cards and their options |
| [Services](https://github.com/tempus2016/taskmate/wiki/Services) | Every callable service, with examples |
| [Sensor Reference](https://github.com/tempus2016/taskmate/wiki/Sensor-Reference) | All entities and their attributes |
| [Automations](https://github.com/tempus2016/taskmate/wiki/Automations) | Bus events, blueprints, and examples |
| [Voice Assistants](https://github.com/tempus2016/taskmate/wiki/Voice-Assistants) | Ask Assist about chores and points |
| [Localization](https://github.com/tempus2016/taskmate/wiki/Localization) | Supported languages and translating |
| [Troubleshooting](https://github.com/tempus2016/taskmate/wiki/Troubleshooting) | Common issues and fixes |
| [FAQ](https://github.com/tempus2016/taskmate/wiki/FAQ) | Frequently asked questions |

**[→ Browse the full wiki](https://github.com/tempus2016/taskmate/wiki)** — 70+ pages, one per feature.

---

## Changelog

Release notes for every version live on the [Releases page](https://github.com/tempus2016/taskmate/releases).

## Contributing

Bug reports and feature requests are welcome in [Issues](https://github.com/tempus2016/taskmate/issues); questions and ideas belong in [Discussions](https://github.com/tempus2016/taskmate/discussions). Pull requests are welcome — see [CONTRIBUTING.md](.github/CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).

> Originally created by [vinnybad/choremander](https://github.com/vinnybad/choremander). This fork adds 21 Lovelace cards, an admin panel, a bonus points system, streak tracking, reward approval flow, a penalty system, and much more.
