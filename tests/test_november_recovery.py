"""The November 1 repair permits one bounded, historically anchored edition."""
import copy
from datetime import datetime, timezone

import pytest

from safety_digest import dot_edition as edition, dot_handoff as dot


NOW = datetime(2026, 11, 3, 21, tzinfo=timezone.utc)
ANCHOR = '2026-11-01T10:17:00+00:00'
DEADLINE = '2026-11-04T18:00:00+00:00'


def recovery():
    return edition.claim_idle(
        None, edition_id='recovery-20261101', owner_id='november-repair',
        anchor=ANCHOR, deadline_at=DEADLINE, basis_sha256='a' * 64, now=NOW)


def test_recovery_keeps_historical_window_and_bounded_lease():
    state = recovery()
    assert state['schema_version'] == 2
    assert state['days'] == 7
    assert state['lease_until'] == '2026-11-03T22:50:00+00:00'
    pending, request = edition.submit(
        state, state['owner_id'], 'dot-requests/november-recovery.json',
        {'schema_version': 1, 'run_id': 'november-recovery', 'mode': 'export',
         'base_commit': 'b' * 40, 'until': ANCHOR, 'days': 7}, now=NOW)
    assert request['until'] == ANCHOR
    assert pending['pending']['sha256'] == dot.digest(request)
    # Pending lifetime still ends at the exact recovery cutoff.
    cutoff = dot.utc(DEADLINE)
    with pytest.raises(dot.Invalid, match='expired'):
        edition.owned(pending, state['owner_id'], now=cutoff)
    with pytest.raises(dot.Invalid, match='future'):
        edition.claim_idle(
            state, edition_id=state['edition_id'], owner_id=state['owner_id'],
            anchor=ANCHOR, deadline_at=DEADLINE, basis_sha256='a' * 64,
            now=cutoff)


@pytest.mark.parametrize('field,value', [
    ('edition_id', '2026-W44'),
    ('edition_id', 'recovery-20261108'),
    ('anchor', '2026-11-01T10:18:00+00:00'),
    ('anchor', '2026-11-02T10:17:00+00:00'),
    ('deadline_at', '2026-11-04T18:00:01+00:00'),
    ('deadline_at', '2026-11-05T18:00:00+00:00'),
    ('days', 6),
])
def test_recovery_does_not_allow_other_windows_or_deadlines(field, value):
    state = recovery()
    state[field] = value
    with pytest.raises(dot.Invalid):
        edition.validate(state)


def test_recovery_cannot_restart_terminal_edition_or_replace_pending():
    state = recovery()
    terminal = {**state, 'status': 'complete'}
    with pytest.raises(dot.Invalid, match='restarted'):
        edition.claim_idle(
            terminal, edition_id=state['edition_id'], owner_id=state['owner_id'],
            anchor=ANCHOR, deadline_at=DEADLINE, basis_sha256='a' * 64, now=NOW)
    pending = copy.deepcopy(state)
    pending['pending'] = {'path': 'dot-requests/old.json', 'sha256': 'c' * 64}
    with pytest.raises(dot.Invalid, match='pending'):
        edition.claim_idle(
            pending, edition_id=state['edition_id'], owner_id='another-worker',
            anchor=ANCHOR, deadline_at=DEADLINE, basis_sha256='a' * 64, now=NOW)


def test_historical_october_recovery_remains_valid():
    state = recovery()
    state.update(edition_id='recovery-20261025',
                 anchor='2026-10-25T10:17:00+00:00',
                 deadline_at='2026-10-27T18:00:00+00:00',
                 lease_until='2026-10-27T18:00:00+00:00', status='complete')
    assert edition.validate(state) == state
