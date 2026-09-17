This project is a Django application that adds functionality to the Coldfront
project. Coldfront is a research computing resource allocation platform. Examples
of resources include storage allocations and compute time on high-performance clusters.

## Conventions

- **No custom Django models.** This app has zero models of its own. Custom data is
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
