# Responsive child pages

`custom:taskmate-child-page-card` provides an unboxed child header, spendable
Stars, and linked **Chores / My rewards** views. Routine items use two columns
when space allows; rewards use up to three. Instructions remain visible in each row.
Existing completion, approval, celebration, undo, and reward actions are reused.
Standalone TaskMate cards keep their current appearance.

Use one panel view per page so Home Assistant does not constrain the page to a
single card column. Point both cards at the same child and the two view paths:

```yaml
views:
  - title: Child chores
    path: child
    type: panel
    cards:
      - type: custom:taskmate-child-page-card
        entity: sensor.taskmate_overview
        child_id: YOUR_CHILD_ID
        view: chores
        chores_path: /YOUR_DASHBOARD/child
        rewards_path: /YOUR_DASHBOARD/child-rewards
  - title: Child rewards
    path: child-rewards
    type: panel
    cards:
      - type: custom:taskmate-child-page-card
        entity: sensor.taskmate_overview
        child_id: YOUR_CHILD_ID
        view: rewards
        chores_path: /YOUR_DASHBOARD/child
        rewards_path: /YOUR_DASHBOARD/child-rewards
```

Optional `chore_options` and `reward_options` accept the corresponding card's
options. The page fixes the entity, selected child, and page layout, and hides
parent management actions in the chore list. It does not provide access control;
keep existing backend permissions and PIN-protected parent routes in place.

A fixed navigation card can share the panel through a `vertical-stack`.
Configure that navigation card's bottom spacing so it does not cover the list.
The page's ordinary links support browser back and opening in another tab.
The header shows spendable Stars; any Stars committed to pending reward claims
appear beneath it.

To turn the name/avatar into a child chooser, add the same `child_pages` list to
each child's Chores and My rewards page. Selecting a child keeps the current tab;
the destination is explicit, so page names need not follow a naming convention.
Names and avatars come from TaskMate. Only children present in the entity are shown.

```yaml
child_pages:
  - child_id: FIRST_CHILD_ID
    chores_path: /YOUR_DASHBOARD/first-child
    rewards_path: /YOUR_DASHBOARD/first-child-rewards
  - child_id: SECOND_CHILD_ID
    chores_path: /YOUR_DASHBOARD/second-child
    rewards_path: /YOUR_DASHBOARD/second-child-rewards
```

The chooser closes after selection, on an outside tap, or with Escape. Keyboard
users can open it with Enter/Space or an arrow key and move through its links with
Tab or arrow keys. Omitting the list preserves a static child header.

## Family overview and child colors

Set **TaskMate → Children → Edit → Child color** once. Headers, child avatars,
page navigation and routine accents inherit it. Reset returns to theme/card defaults.
Existing explicit `header_color` values and page `accent_color` overrides take
precedence. Remove old per-card colors to use the child's setting. Completion,
approval and warning colors still communicate status.

Add the family overview to a panel view. Link the navbar's Chores item directly
to this view; it no longer needs a child submenu:

```yaml
type: custom:taskmate-family-page-card
entity: sensor.taskmate_overview
parents_path: /YOUR_DASHBOARD/parents
child_pages:
  - child_id: FIRST_CHILD_ID
    chores_path: /YOUR_DASHBOARD/first-child
    rewards_path: /YOUR_DASHBOARD/first-child-rewards
  - child_id: SECOND_CHILD_ID
    chores_path: /YOUR_DASHBOARD/second-child
    rewards_path: /YOUR_DASHBOARD/second-child-rewards
```

The overview shows every currently applicable routine separately, with approved
progress, pending status, and the next available step. Its Today total counts
required routine chores only. Other available chores appear separately. Routine
links open the child's page and focus that section; all sections remain expanded.
The Parents link navigates to your existing protected route; it grants no privileges.

**Chores → Edit → Display category** groups ordinary chores under headings such
as Laundry. Categories are independent of assignment Groups and time periods.
Routine membership takes precedence, so a chore never appears twice. Child pages
show All, Routines and category filters. Routine panels and categories sit side by
side on wide screens and stack on phones. Both new fields survive backup/restore;
older data needs no migration and inherits the existing defaults.
