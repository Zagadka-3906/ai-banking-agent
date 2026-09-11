"""Test-suite-wide configuration, applied before any test module imports.

Two settings are forced so the suite is hermetic with respect to a developer's
local ``.env``:

* ``BANK_REQUIRE_SESSION=0`` - the suite drives much of the core with
  self-asserted ``X-User-Id`` headers, which is precisely what strict session
  mode forbids.  The tests that *do* exercise strict mode turn it on themselves
  with ``monkeypatch.setenv``.  Without this pin, copying ``.env.example`` (which
  ships the secure default, ``BANK_REQUIRE_SESSION=1``) would break the suite.
* ``BANK_DEMO_MODE=1`` - the shared-key face ticket and the demo session
  endpoint are demo affordances; the tests that assert they are *refused*
  outside demo mode flip it explicitly.
* ``DEEPSEEK_API_KEY=""`` - the suite must not depend on a paid,
  nondeterministic external service.  With a real key present (a developer's
  ``.env``), ``agent.llm`` is live and one test was observed to fail
  intermittently purely because of what the model returned.  Tests that assert
  model-merge behaviour install a fake LLM via ``monkeypatch.setattr(agent,
  "llm", ...)`` and are unaffected.

``python-dotenv`` is loaded with ``override=False`` by ``security.py`` and
``agent.py``, so these values win over any ``.env`` file.
"""

import os

os.environ["BANK_REQUIRE_SESSION"] = "0"
os.environ["BANK_DEMO_MODE"] = "1"
os.environ["DEEPSEEK_API_KEY"] = ""
