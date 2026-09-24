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
        accent_color: '#b885e3'
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
        accent_color: '#b885e3'
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
