"""Proposal queue concurrency + replay-corruption tests.

The queue is a single-process append-only JSONL log. Two concerns:
- Concurrent accept/dismiss on the same id must produce exactly one winner
  (the queue is the source of truth for whether the user has acted on a
  Pebble proposal — double-firing creates duplicate vault writes).
- Replay must skip corrupt JSON lines without raising, so a torn write or
  manually edited file doesn't poison the whole queue.
"""

from __future__ import annotations

import threading


def test_accept_is_idempotent(pebble_home):
    from storage.proposal_queue import ProposalQueue
    q = ProposalQueue()
    pid = q.add({'kind': 'alias', 'note_id': '07 - People/Amber Li.md',
                  'note': 'consider alias "Dr Li"'})
    assert q.accept(pid) is True
    assert q.accept(pid) is False        # same target state — no-op


def test_concurrent_accept_only_one_wins(pebble_home):
    """Two threads calling accept(same_id) — exactly one returns True."""
    from storage.proposal_queue import ProposalQueue
    q = ProposalQueue()
    pid = q.add({'kind': 'alias', 'note_id': 'x.md'})

    results: list[bool] = []
    barrier = threading.Barrier(2)

    def race():
        barrier.wait()
        results.append(q.accept(pid))

    t1 = threading.Thread(target=race)
    t2 = threading.Thread(target=race)
    t1.start(); t2.start()
    t1.join();  t2.join()

    assert sorted(results) == [False, True]
    assert q.get(pid).status == 'accepted'


def test_replay_skips_corrupt_lines(pebble_home):
    """A torn write or hand-edit leaves invalid JSON — replay skips it."""
    from storage.proposal_queue import ProposalQueue
    q = ProposalQueue()
    good_pid = q.add({'kind': 'alias', 'note_id': 'a.md'})
    # Append a corrupt line manually (simulating a torn write)
    with q.path().open('a', encoding='utf-8') as f:
        f.write('{not valid json\n')
        f.write('\n')
    # Add another good row — should not be lost
    good_pid_2 = q.add({'kind': 'alias', 'note_id': 'b.md'})

    pending = {p.id for p in q.list_pending()}
    assert good_pid     in pending
    assert good_pid_2   in pending


def test_get_returns_none_for_unknown_id(pebble_home):
    from storage.proposal_queue import ProposalQueue
    q = ProposalQueue()
    assert q.get('nonexistent') is None


def test_postpone_then_accept(pebble_home):
    """User postpones, then accepts later — both should succeed."""
    from storage.proposal_queue import ProposalQueue
    q = ProposalQueue()
    pid = q.add({'kind': 'alias', 'note_id': 'x.md'})
    assert q.postpone(pid) is True
    assert q.get(pid).status == 'postponed'
    assert q.accept(pid)   is True
    assert q.get(pid).status == 'accepted'
