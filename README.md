# tufts-coldfront-plugins
plugin used at Tufts for customizing coldfront

## A record of su

Every django-su session is logged: who signed in as whom, and when they released them. Set:

```python
# logs each start and end, then signs in (it replaces django-su's own login)
SU_CUSTOM_LOGIN_ACTION = "tufts_local.su.log_su_login"
```

It needs coldfront-utils 0.2.0 or later (`coldfront_utils.task_context.su`), which finds the
superuser who first signed in.

`tufts_local.su` logs a line at INFO for each start and end:

```
su start: admin=rdms_admin target=pi01 mode=full forwarded_for='130.64.1.2, 10.0.0.5' remote_addr='127.0.0.1'
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
