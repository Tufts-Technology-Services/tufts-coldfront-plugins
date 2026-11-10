# tufts-coldfront-plugins
plugin used at Tufts for customizing coldfront

## View as, and a record of su

A superuser on a project or allocation detail page can see the site as one of that project's members
would: its PI, a manager or a user. It is django-su's "log in as" in read-only mode. The session lasts
across pages, so a flow can be followed as them, until the superuser releases them from the su
banner, which brings them back to the page they're on. Every su session, this one and django-su's own
"Login as...", is also logged.

It needs coldfront-utils 0.2.0 or later (`coldfront_utils.task_context.su`). The git dependency
can't say so, so system check `tufts_local.E004` stops `migrate` when any of the settings below is
configured with an older coldfront-utils.

### Settings

```python
# who signs in as whom: logs each start and end, then signs in (it replaces django-su's own login)
SU_CUSTOM_LOGIN_ACTION = "tufts_local.su.log_su_login"

MIDDLEWARE += [
    # coldfront-utils, if it isn't there already: the superuser stays the acting user, and history
    # rows name them
    "coldfront_utils.task_context.middleware.ActingUserMiddleware",
    # refuses changes in a View as session; after SessionMiddleware and ActingUserMiddleware
    "tufts_local.middleware.SuReadOnlyMiddleware",
]

# the control's data, as `view_as`, on the two detail pages
TEMPLATES[0]["OPTIONS"]["context_processors"] += ["tufts_local.context_processors.view_as"]

# optional: the views a read-only session may still POST to, by URL name ("app:name" where
# namespaced). It replaces the default, tufts_local.su.DEFAULT_READ_ONLY_ALLOWED_POSTS
VIEW_AS_READ_ONLY_ALLOWED_POSTS = ["project-add-users-search-results"]
```

django-su must be set up as ColdFront sets it up: `django_su.backends.SuBackend` in
`AUTHENTICATION_BACKENDS`, its URLs mounted at `su/`, and `django_su.context_processors.is_su`.

### Read-only

While a View as session is active, its session has `su_read_only`, and `SuReadOnlyMiddleware` answers
anything but GET, HEAD and OPTIONS with a 403, except signing out and the views in
`VIEW_AS_READ_ONLY_ALLOWED_POSTS`. The list is of views whose POST only shows something, such as
the add-users search results, so a flow can be followed up to the step that would change data. A
view that isn't listed is refused, so a new plugin's views are read-only until someone lists one.
A plugin with a display-only POST can be added in settings, without a code change.

### What the templates provide

The plugin gives data and URLs; the site's templates draw the control and the banner.

- `view_as`, from the context processor, only on `project-detail` and `allocation-detail`, and only
  for superusers. Before starting, `people` is at most three members who can open the page, the
  first of each kind by username, each with a `user` and a `kind` (`"pi"`, `"manager"` or `"user"`:
  the words shown are the template's), with `start_url` and `next` to post. While viewing as
  someone, `people` is empty and `viewing_as` is that user. `exit_url` releases them. After a full
  "Login as..." there is no `view_as`: that session has django-su's own way back.
- `POST view-as` with `user` (a pk) and `next` starts a read-only session and returns to `next`.
- `GET view-as-exit?next=<page>` releases the user and returns to `next`.
- `request.signed_in_by` (from `ActingUserMiddleware`) is the superuser while signed in as someone,
  and `request.session.su_read_only` says the session is read-only, for the banner.

A page off this site in `next` is replaced with the home page.

### The log

`tufts_local.su` logs a line at INFO for each start and end:

```
su start: admin=rdms_admin target=pi01 mode=read-only forwarded_for='130.64.1.2, 10.0.0.5' remote_addr='127.0.0.1'
su end: admin=rdms_admin released=pi01 back_to=rdms_admin forwarded_for='130.64.1.2, 10.0.0.5' remote_addr='127.0.0.1'
```

`admin` is always the superuser who first signed in, however many accounts they have moved through.
`forwarded_for` and `remote_addr` are given as received, since a client can set X-Forwarded-For.

## License

Copyright (C) 2026 Tufts Technology Services (TTS)

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
this program. If not, see <https://www.gnu.org/licenses/>.
