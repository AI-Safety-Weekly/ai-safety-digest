"""The approved late window applies to exactly one edition, with normal fences."""
import copy
from datetime import datetime, timezone

import pytest

from safety_digest import dot_edition as edition, dot_handoff as dot


NOW = datetime(2026, 10, 25, 23, tzinfo=timezone.utc)
ANCHOR = "2026-10-25T10:17:00+00:00"
DEADLINE = "2026-10-27T18:00:00+00:00"


def late_claim(**changes):
    values = dict(edition_id="recovery-20261025", owner_id="recovery-worker",
                  anchor=ANCHOR, deadline_at=DEADLINE, lease_until=DEADLINE,
                  basis_sha256="a" * 64, now=NOW)
    values.update(changes)
    return edition.claim(None, **values)


def test_approved_oct25_window_can_collect_and_expires():
    state = late_claim()
    pending, request = edition.submit(state, state["owner_id"], "dot-requests/oct25.json",
        dict(schema_version=1, run_id="oct25", mode="export", base_commit="b" * 40,
             until=ANCHOR, days=7), now=NOW)
    assert pending["pending"]["sha256"] == dot.digest(request)
    with pytest.raises(dot.Invalid, match="expired"):
        edition.owned(state, state["owner_id"], now=dot.utc(DEADLINE))


@pytest.mark.parametrize("changes", [
    {"edition_id": "another-edition"},
    {"anchor": "2026-10-25T10:18:00+00:00"},
    {"anchor": "2026-11-01T10:17:00+00:00"},
    {"deadline_at": "2026-10-27T18:00:01+00:00"},
    {"deadline_at": "2026-10-26T18:00:00+00:00"},
    {"lease_until": "2026-10-27T18:00:01+00:00"},
])
def test_exception_cannot_expand_to_other_editions_or_windows(changes):
    with pytest.raises(dot.Invalid):
        late_claim(**changes)


def test_late_exception_preserves_owner_pending_and_restart_fences():
    state = late_claim()
    with pytest.raises(dot.Invalid, match="another worker"):
        edition.claim(state, edition_id=state["edition_id"], owner_id="other",
                      anchor=ANCHOR, deadline_at=DEADLINE, lease_until=DEADLINE,
                      basis_sha256=state["basis_sha256"], now=NOW)
    terminal = copy.deepcopy(state)
    terminal["status"] = "complete"
    with pytest.raises(dot.Invalid, match="restarted"):
        edition.claim(terminal, edition_id=state["edition_id"], owner_id=state["owner_id"],
                      anchor=ANCHOR, deadline_at=DEADLINE, lease_until=DEADLINE,
                      basis_sha256=state["basis_sha256"], now=NOW)
