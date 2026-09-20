"""The interim notifier and the real alert engine must never coexist.

`ops/notify/` pushes published signals to Telegram so the developer does not
check by hand at a rate of roughly one signal a fortnight. It implements §10.1's
grade and tier caps and a daily ceiling, and it implements none of §10.1's
priority split, §10.2's storm breaker or §10.3's cooldowns. That is an honest
trade while the alert engine does not exist. The moment it does, the same file
becomes a second delivery path that bypasses the discipline layer -- and it
would bypass it silently, because nothing about a working notifier looks wrong.

Three documents record the intention to delete it: the roadmap's S18 entry, the
notifier's own module docstring, and the session tracker. None of them is
checked by anything. This is, and it runs on every pull request.

The trigger is `infrastructure/channels/`, which is where S18 builds the
delivery adapters and which today holds one docstring and no code. When that
stops being true, this test fails and names what to do about it.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

CHANNELS = REPO / "backend" / "src" / "scanner" / "infrastructure" / "channels"

NOTIFIER = REPO / "ops" / "notify"

RETIREMENT = """
S18 has begun: {reason}

The interim notifier must go with it. To retire it:

  1. delete ops/notify/ and its crontab entry on the VM
  2. delete backend/tests/integration/test_notify_role_pg.py,
     backend/tests/unit/test_interim_notifier_behaviour.py,
     backend/tests/support/notify_role.py and the notify_engine_ fixture
  3. DROP ROLE scanner_notify;  (and delete ops/db/notify-role.sql)
  4. delete this test
  5. remove the deletion line from DEVELOPMENT_ROADMAP.md's Sprint S18

Leaving it in place gives the alert engine a sibling that skips §10.1's
priority split, §10.2's storm breaker and §10.3's cooldowns.
"""


def _channels_are_still_a_stub() -> tuple[bool, str]:
    """Whether `infrastructure/channels/` is the empty placeholder it is today.

    A module holding nothing but its docstring parses to a body of one
    `Expr`. Counting lines or bytes instead would trip on somebody rewrapping
    a sentence, and a test that cries wolf gets deleted rather than obeyed.
    """
    if not CHANNELS.is_dir():
        return True, "channels/ does not exist"

    modules = sorted(path.name for path in CHANNELS.glob("*.py"))

    if modules != ["__init__.py"]:
        extra = ", ".join(name for name in modules if name != "__init__.py")

        return False, f"channels/ gained {extra or 'modules'}"

    tree = ast.parse((CHANNELS / "__init__.py").read_text(encoding="utf-8"))

    body = [node for node in tree.body if not isinstance(node, ast.Expr)]

    if body:
        return False, "channels/__init__.py gained code beyond its docstring"

    return True, ""


def test_the_interim_notifier_is_deleted_once_the_alert_engine_exists() -> None:
    stub, reason = _channels_are_still_a_stub()

    if stub:
        return

    assert not NOTIFIER.exists(), RETIREMENT.format(reason=reason)


def test_the_guard_can_actually_fail() -> None:
    """A guard nobody has seen fail is a guard nobody knows works.

    `_channels_are_still_a_stub` is the whole mechanism, and its two failure
    modes are checked here against fixtures rather than against the repository
    -- which, today and by design, cannot exercise either of them.
    """
    assert _channels_are_still_a_stub()[0], (
        "channels/ is no longer a stub, so the retirement check above is now "
        "the live one -- this self-test has served its purpose and goes with "
        "the rest of the notifier"
    )

    # The same predicate, pointed at directories that are not the repository's.
    def is_stub(sources: dict[str, str], tmp: Path) -> bool:
        for name, text in sources.items():
            (tmp / name).write_text(text, encoding="utf-8")

        modules = sorted(path.name for path in tmp.glob("*.py"))

        if modules != ["__init__.py"]:
            return False

        tree = ast.parse((tmp / "__init__.py").read_text(encoding="utf-8"))

        return not [node for node in tree.body if not isinstance(node, ast.Expr)]

    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)

        assert is_stub({"__init__.py": '"""Just a docstring."""\n'}, tmp)

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)

        assert not is_stub(
            {"__init__.py": '"""Doc."""\n\n\nclass TelegramChannel:\n    pass\n'},
            tmp,
        ), "code in __init__.py must be detected"

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)

        assert not is_stub(
            {"__init__.py": '"""Doc."""\n', "telegram.py": "x = 1\n"},
            tmp,
        ), "a new module beside __init__.py must be detected"
