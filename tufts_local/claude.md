This project is a Django application that adds functionality to the Coldfront
project. Coldfront is a research computing resource allocation platform. Examples
of resources include storage allocations and compute time on high-performance clusters.

## Conventions

- **Almost no custom Django models.** `models.py` holds exactly one model, `IgnoredTask`
  (the ignore list for the django-q task report, maintained by admins through
  `admin.py`) — a deliberate, documented exception for plugin-local operational config
  that maps to no coldfront entity. Don't grow it casually: everything below still
  applies by default, and a second model needs the same kind of justification. Custom data is
  attached to coldfront core models via its EAV-style attribute system:
  `AllocationAttribute`/`AllocationAttributeType` (e.g. `sf_vol_path`, `Storage Quota (TB)`)
  and `ProjectAttribute`/`ProjectAttributeType` (e.g. `Project Key`, `Group`). Values are
  always strings; typed data (dates, floats) is parsed ad hoc where read. Status/role
  enums use coldfront's own `*StatusChoice`/`*RoleChoice` models
  (`ProjectUserStatusChoice`, `AllocationStatusChoice`, etc.), not Python enums.
  If a feature needs data that genuinely lives outside coldfront's schema (e.g. a table
  in an external/remote database), integrate it the way `starfish_utils.py` integrates
  Starfish: a thin client wrapper module, not a Django model/migration.
- **Templates**: extend `common/base.html` (vendored in coldfront core), Bootstrap 4,
  `django-crispy-forms` (bootstrap4 pack) for form rendering (`{{ form|crispy }}`), plain
  HTML tables styled with Bootstrap classes (`table table-sm` in a `table-responsive` div)
  — no django-tables2. Standard empty-state pattern:
  `<div class="alert alert-info"><i class="fa fa-info-circle"></i> There are no X to display.</div>`.
  Buttons: `btn btn-primary`/`btn-secondary`/`btn-success`/`btn-warning`, `mr-1` between
  adjacent buttons. FontAwesome (`fa`/`fas`/`far`) for icons. Bootstrap popovers
  (`data-toggle="popover"`, initialized in `{% block javascript %}`) for supplementary
  detail that shouldn't clutter a table cell.
- **htmx**: coldfront core provides htmx, and it is available to this app — use it for
  live-updating or partially-updating pages instead of hand-rolled jQuery `$.get` +
  `setInterval`. Nothing in this checkout shows it (no `hx-*` in the templates here, none
  in the pinned `coldfront` in `.venv`, and `django_htmx` isn't installed there), so its
  absence locally is not evidence against it. Pattern, worked example in
  `views/task_report.py` + `templates/tufts_local/task_report.html`: put the refreshing
  region in its own `_*.html` partial, include it inside a wrapper div carrying
  `hx-get`/`hx-trigger`/`hx-swap`, and have the view return the partial instead of the full
  page for htmx requests. Detect those with `_wants_partial()` — `request.htmx` when
  `django_htmx.middleware.HtmxMiddleware` has set it, falling back to the raw `HX-Request`
  header; don't `import django_htmx` in view code, since it isn't importable in the test
  venv. Polling supports trigger filters after the poll declaration
  (`hx-trigger="every 5s [shouldAutoRefresh()]"`) — use one to pause on `document.hidden`
  rather than polling a tab nobody is looking at. Bootstrap widgets initialized in JS
  (popovers, tooltips) don't survive a swap: re-initialize them in an `htmx:afterSwap`
  listener filtered on the target's id.
- **Views**: split across `views/*.py` by concern, re-exported through `views/__init__.py`.
  Superuser-gated admin views use `@user_passes_test(lambda u: u.is_superuser)` (FBVs) or
  `UserPassesTestMixin`/`test_func` (CBVs). Report-style pages
  (`views/reports.py` — `sf_report`, `not_updated_report`) return
  `TemplateResponse(request, 'tufts_local/<template>.html', context)`. Single-record admin
  actions (`views/update_project_owner_view.py`) use `FormView` + `reverse_lazy` +
  `messages.success`/`form.add_error` for domain errors. `urls.py` is a flat list of
  `path()` entries, kebab-case for both the path segment and `name=`.
- **Domain errors**: plain custom `Exception` subclasses defined near their usage
  (e.g. `StarfishDirectoryNotFoundError` in `starfish_utils.py`), not Django validation
  errors — except inside form `clean_*` methods, which use `forms.ValidationError`.

## Linting and formatting

This repo lints/formats with `ruff` (config in `pyproject.toml`), not pylint/flake8 —
IDE diagnostics from a different linter (e.g. default 100-char line length, docstring
requirements) don't reflect this project's actual rules and can be ignored where they
conflict. Run `ruff check .` and `ruff format --check .` (or `ruff format .` to
auto-fix) before treating new/changed code as done. Key settings: 120-char line
length, single quotes, isort-style import sorting with `coldfront` imports grouped
into their own section between third-party and first-party imports.
