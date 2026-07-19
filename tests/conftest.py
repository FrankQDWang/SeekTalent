from __future__ import annotations

import os


# Test bodies may opt in explicitly; only credentials inherited from the host process are removed.
for credential_name in ("SEEKTALENT_TEXT_LLM_API_KEY", "SEEKTALENT_DOMI_JWT"):
    os.environ.pop(credential_name, None)
