# Routines

Routines sit beside Groups and Quests in the TaskMate admin panel. They group
ordinary chores into a daily routine and can award a fixed completion bonus.
They replace this fork's experimental checklist chore type.

## Set up a routine

1. Open **Routines → Add routine** and enter its name and optional bonus.
2. Set **Defaults for new chores**: children, days, time of day and approval.
   An empty child list means everyone; an empty day list means every day.
3. Create chores inside the routine, or link existing ones. The full chore
   editor is available for every item, including individual schedules, points,
   approval, photos, timers, dependencies and assignment settings.
4. Put the chores in display order. Mark each **Required for bonus** or
   **Optional**, then save the routine.

Defaults are copied when creating a chore. They do not override existing
settings or silently change linked chores. Chore edits save immediately; save
the routine separately to retain its links and ordering. A chore belongs to one
routine. Create separate morning and evening brushing chores so one completion
cannot satisfy both occasions.

The child card groups routines automatically in classic, playroom, console,
cleanpro, accessible and picture modes. Existing chore actions, approval rules,
photos, timers, sounds, animations and parent-action visibility still apply.
Card filters continue to control which chores are shown. The older guided
`taskmate-routine-card` remains a separate one-task-at-a-time presentation.

## Completion and points

Each child has independent progress for each Home Assistant local day. A
required chore counts once for the routine, even if it allows multiple daily
completions. Every approved chore earns its ordinary points. The routine bonus
is additional, fixed, and automatic; there is no final claim button.

Only required chores due and available for that child count. Dependency locks
do not remove later steps from the requirement. Optional chores never block the
bonus, and a routine with no required chores earns no bonus. “Required for
bonus” does not turn on mandatory-chore penalties or change streak rules.

Requirements and the bonus are saved when the first chore is submitted or its
timer starts. Routine edits and changing availability do not rewrite that day's
promise. Approval after midnight settles the original day. Rejection or undo
reverses the saved bonus when required work is no longer approved. Reapproval
can restore it once. An approved repeat completion can still satisfy an item.

Pausing a routine stops new daily runs; deleting one removes the group. Both
leave its ordinary chores available and preserve submitted bonus records for
approval and reversal. Deleting a chore removes its current routine link.
Backups include routine definitions and bonus records; a full data reset clears
both. History pruning retains approved sibling completions while a routine day
still has pending submissions.

## Small manual test

Create a routine with two required 2-point chores, one optional 2-point chore,
and a 4-point bonus. Require parent approval and assign it to two test children.

- Submit both required chores for the first child: progress waits for approval.
- Approve one: that child gets 2 points, with no routine bonus.
- Approve the other: the balance becomes 8. The optional chore is still available.
- Undo either approval: the balance becomes 2. Reapprove: it becomes 8 again.
- Complete the optional chore: it earns only its own points.
- Verify the second child still has independent progress.
- Try picture mode and the other card designs; grouped chores should keep their
  normal completion sounds and animation.

This fork's removed checklist format is not migrated into routines. Start from
ordinary chores or a fresh data set when switching from the experimental build.
