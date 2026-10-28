# Plan: alert audiences

Status: proposed, 2026-10-28. This builds on the admin alerts feature described in
[alert-queue-info.md](alert-queue-info.md).

## Goal

Today every alert goes to the same people: staff and superusers. This plan lets each alert
reach only the people it concerns, whether that's a site-wide role (superusers, staff,
reviewers) or a role on a particular project (PI, Approvers, project members). All of them
see their alerts in the same navbar bell and on the same alerts page.

## Decisions

- **Email opt-outs don't affect in-app alerts.** `ProjectUser.enable_notifications` only
  controls email. Someone who has opted out of emails still sees "your allocation expires
  Friday" in the bell.
- **Alerts can't be dismissed.** They are live checks: an alert stays until its cause is
  fixed. Alerts that can be dismissed may come later; see
  [Later: dismissable alerts](#later-dismissable-alerts).
- **Project alerts share the navbar bell with site-wide alerts.** A project member sees
  their project alerts in the same bell and on the same `/alerts/` page that staff use.

## Who's who

| Audience | Who | Scope |
| --- | --- | --- |
| Superuser | `user.is_superuser` | site-wide |
| Staff | `user.is_staff` or superuser | site-wide |
| Reviewer | holds `allocation.can_review_allocation_requests` or `project.can_review_pending_project_reviews` | site-wide |
| PI | `project.pi` | one project |
| Approver | an active ProjectUser with the Manager role who **is not the PI** | one project |
| Project member | any active ProjectUser, including the PI and Approvers | one project |

**Exclude the PI when finding Approvers.** ColdFront's `ProjectCreateView` adds the PI as an
active ProjectUser with the Manager role, so filtering on the Manager role alone includes
the PI. Approver queries must also exclude `user_id=project.pi_id`.

**Don't use `Project.user_permissions()` to find PIs, Approvers or members.** It costs a
query per project. It also only treats a PI as PI if they have a ProjectUser row, and a typo
upstream (`MANAGER in permissions or MANAGER in permissions`) means a PI never gets UPDATE.
Filter on `pi_id` and `projectuser__role__name` directly instead.

## What ColdFront already provides

ColdFront has no in-app notifications. Everything it calls a notification is email:

- the `enable_notifications` opt-out on each ProjectUser;
- the `EXPIRE NOTIFICATION` and `CLOUD_USAGE_NOTIFICATION` attributes on allocations;
- the `EMAIL_*` settings.

What's worth reusing:

- **Who's who.** The permissions and project roles in the table above.
- **The email settings' thresholds.** For example, alert on an expiring allocation using the
  same days as the expiry emails, `EMAIL_ALLOCATION_EXPIRING_NOTIFICATION_DAYS`.
- **The messages admins already write.** `ProjectUserMessage` is what our branding shows on a
  project page as "Notifications". It's only written by superusers, and it's public when
  `is_private` is false. It makes a good alert source; see phase 3. `AllocationUserNote`
  works the same way, but it's also used for invoice notes, so leave it out for now.
  `ProjectAdminComment` and `AllocationAdminNote` are internal notes for admins, not alerts.

## Design: two registries, one bell

Site-wide and project alerts differ in what they cost and who they're for. Each kind keeps
its own registry, but they share the `Alert` type, the templates, the navbar bell and the
alerts page.

```text
                         navbar bell, /alerts/
                                  │
                         alerts_for(user)
                ┌─────────────────┴──────────────────┐
  site-wide (alerts.py)                  project (project_alerts.py)
  register_alert_source(                 register_project_alert_source(name)
      name, audience=staff)                source(user) -> [Alert]
  source() -> [Alert]                    the source finds the user's projects
  one run shared by all, cached 15s      itself, through projects_for()
  in each process, then filtered by      cached per user in Django's cache,
  each source's audience                 about 2 minutes
```

|  | Site-wide alerts | Project alerts |
| --- | --- | --- |
| Example | the task queue is down | an allocation expires in 7 days |
| Same for everyone who sees it? | yes | no: it depends on the user's projects |
| Cost | one run for everybody | database queries for each user |
| Cache | in memory, per process (Django's cache is the same Redis the queue check watches) | Django's cache, keyed by user |
| When a source fails | the same audience sees a "Could not check X" warning | the user sees nothing, and the failure is logged with its traceback |

**Don't show project members source failures.** A warning like "Could not check X" can
include details such as `ConnectionError: redis-host:6379`, which aren't for ordinary
users. Because a failing project source is only logged, its tests are the real safeguard,
so test each source directly.

`alerts_for(user)` merges the two kinds, worst first. The views call it and nothing else.

## Phase 1: audiences for site-wide alerts

1. Add an audience to each source: `register_alert_source(name, audience=staff)`. An
   audience is a function that takes a user and returns true or false. Provide
   `superusers`, `staff` and `with_permission(codename)`. The default is `staff`, so the
   current alerts behave as they do now.
2. `get_current_alerts(user)` still runs each source once for all users and caches the
   result. It then filters by each source's audience. A source's "Could not check X"
   warning goes to that source's audience.
3. Decide whether the task queue alert stays with all staff or goes to superusers only.
4. Add sources for reviewers:
   - allocation requests that have waited more than N days, for
     `can_review_allocation_requests`;
   - pending project reviews, for `can_review_pending_project_reviews`.

## Phase 2: project alerts in the same bell

1. **The new module.** `project_alerts.py` has its own registry and
   `get_project_alerts(user)`. Its sources take the user and return that user's alerts:
   - The cache is Django's, keyed `tufts_local:project-alerts:<user pk>`, for about 2
     minutes.
   - It isolates failing sources the way the site-wide registry does, except that failures
     are logged rather than shown (see above).
   - It shares the code that runs the sources with `collect_alerts()`.
2. **Finding a user's projects.** `projects_for(user, roles)` returns a single queryset of
   active projects where the user has any of the given roles: PI, Approver or member. The
   Approver role excludes the PI, as above.
3. **A project alert names its project.** For example, the title reads "Allocation expires
   in 7 days: <project title>", and the link goes to the allocation or the project.
4. **One way in.** `alerts_for(user)` merges site-wide and project alerts. The bell and
   `/alerts/` use it, and so does the check on who may open the page.
5. **Access to the views.** Any logged-in user may open them, and each user sees only their
   own alerts. The `_may_view` staff check goes.
6. **The navbar (coldfront-branding).** The bell moves out of the staff-only block, so every
   logged-in user gets it. The plugin-presence check stays. How often it polls depends on
   who's looking:
   - staff: on load, then every 30s, as now;
   - everyone else: on load, then every 5 minutes. Their alerts change slowly, and this
     keeps hundreds of open tabs from polling every 30 seconds.
7. **The first project sources,** all built from existing data:

   | Source | Who sees it | Fires when |
   | --- | --- | --- |
   | Project review due | PI and Approvers | `project.needs_review` |
   | Allocation expiring | PI and Approvers | an active allocation's end date is within the longest of the expiry email days |
   | EULA waiting | the project member it's for | their AllocationUser status is `PendingEULA`, when `ALLOCATION_EULA_ENABLE` is on |

## Phase 3: admin messages as alerts

A source for public `ProjectUserMessage`s, keyed on `created` (not `modified`, so editing an
old message doesn't bring it back), for the past N days. This tells members about a
message they'd otherwise only see by opening the project page.

- **Visibility:** it must match ColdFront's project page exactly, so an alert never shows
  someone a message they couldn't open. That means public messages only, shown to active
  project members.
- **No new models,** and no changes to ColdFront.
- **Admins must untick `is_private`.** It defaults to true, so a message stays private, and
  raises no alert, until they do.

## Later: dismissable alerts

Not part of this plan. If some alerts become dismissable later:

- they'll need a stable identity, such as `'allocation-expiring:<allocation pk>:<end date>'`,
  so that dismissing one hides that alert but not the next one;
- storing who dismissed what needs a model, which this plugin's conventions discourage. It
  needs the same justification as `IgnoredTask` and `TaskAcknowledgement`: operational
  state that doesn't belong to any ColdFront object;
- only some sources would opt in. A source still decides whether its alerts can be
  dismissed. "The task queue is down" should never be.

Nothing in phases 1–3 has to change for this. The only thing that would ease it later is
sources building alert titles from stable facts rather than counts that change.

## Testing

- Test each source directly, as `task_queue_alerts()` is tested now, for each audience: the
  PI, an Approver, a member who is neither, a former member, and staff who aren't on the
  project.
- Test that a PI is never picked up as their own project's Approver.
- Test that someone who has turned off email notifications still sees their alerts.
- Test that a failing project source shows the user nothing and logs the failure.
- Test the views: each user sees only their own alerts, and the per-user cache is cleared
  between tests (as `no_cached_alerts` does for site-wide alerts).

## Open questions

1. **Who sees admin messages?** Everyone who can open the project, as the project page does,
   or only the PI and Approvers?
2. **The `EXPIRE NOTIFICATION` attribute.** It's an admin's per-allocation switch for the
   expiry email. This plan treats it as an email setting that the expiry alert ignores,
   following the decision on opt-outs. Should a "No" also turn off the alert?
3. **Polling for non-staff.** Is every 5 minutes right, or should the bell update only when
   a page loads?
4. **The task queue alert.** Should it go to all staff or to superusers only (phase 1,
   step 3)?
