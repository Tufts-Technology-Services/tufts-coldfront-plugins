# SPDX-FileCopyrightText: (C) 2026 Tufts Technology Services (TTS)
#
# SPDX-License-Identifier: GPL-3.0-or-later

from coldfront.config.settings import ENV

SF_OWNER_TAG_PERSIST = ENV.bool('SF_OWNER_TAG_PERSIST', default=True)
SF_INDEX_TIMEOUT = ENV.int('SF_INDEX_TIMEOUT', default=600)  # Timeout in seconds for indexing tasks
