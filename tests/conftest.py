"""Test-suite-wide configuration, applied before any test module imports.

Settings are forced so the suite is hermetic with respect to a developer's local
``.env``:

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
* ``BANK_DEVICE_STORE_PATH`` / ``BANK_AUDIT_LOG_PATH`` emptied - both point at
  *durable stores of real state*, and the suite enrolls devices and writes audit
  events.  Inheriting a live path from a developer's ``.env`` let a test run
  overwrite the real device registry: the file ended up holding a fixture device
  (``label="push-test"``, token ``fcm-token-1``) while the enrolled phone had
  silently vanished from it.  Emptying them here removes that class of accident;
  ``tests/test_device_store.py`` opts back in per test with ``monkeypatch.setenv``
  and a ``tmp_path``.
* ``BANK_LEDGER_STORE_PATH`` emptied and ``BANK_APPROVAL_STORE_PATH=memory`` for
  the same reason: the ledger snapshot and the approval registry are durable
  stores of state the suite mutates heavily (transfers, locks, password
  changes, approvals).  Without the pin, a developer's ``.env`` pointing at a
  live deployment's file would let a test run rewrite real balances.
  ``tests/test_ledger_persistence.py`` and ``tests/test_agent_gateway.py`` opt
  back in per test with ``monkeypatch.setenv`` and a ``tmp_path``.

``python-dotenv`` is loaded with ``override=False`` by ``security.py`` and
``agent.py``, so these values win over any ``.env`` file.
"""

import os

os.environ["BANK_REQUIRE_SESSION"] = "0"
os.environ["BANK_DEMO_MODE"] = "1"
os.environ["DEEPSEEK_API_KEY"] = ""
os.environ["BANK_DEVICE_STORE_PATH"] = ""
os.environ["BANK_AUDIT_LOG_PATH"] = ""
os.environ["BANK_LEDGER_STORE_PATH"] = ""
os.environ["BANK_APPROVAL_STORE_PATH"] = "memory"

