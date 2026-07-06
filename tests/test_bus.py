"""Bus contract tests: fan-out, latest-wins mailbox, payload immutability (§10)."""
import dataclasses

import pytest

from jarvis.bus import (
    AvatarActivate,
    Bus,
    Cancel,
    State,
    StateChanged,
    Token,
)


async def test_fanout_delivers_to_all_subscribers():
    bus = Bus()
    q1, q2 = bus.subscribe(), bus.subscribe()
    bus.publish(StateChanged(State.LISTENING))
    assert (await q1.get()).new_state is State.LISTENING
    assert (await q2.get()).new_state is State.LISTENING


async def test_latest_wins_mailbox_drops_stale_events():
    bus = Bus()
    avatar_q = bus.subscribe(latest_wins=True)
    bus.publish(StateChanged(State.LISTENING))
    bus.publish(StateChanged(State.THINKING))
    bus.publish(StateChanged(State.SPEAKING))
    # Only the newest survives; showing the current state late beats an old one on time.
    assert (await avatar_q.get()).new_state is State.SPEAKING
    assert avatar_q.empty()


async def test_regular_subscriber_keeps_full_history():
    bus = Bus()
    q = bus.subscribe()
    bus.publish(Cancel(turn_id=1))
    bus.publish(AvatarActivate())
    assert isinstance(await q.get(), Cancel)
    assert isinstance(await q.get(), AvatarActivate)


async def test_unsubscribe_stops_delivery():
    bus = Bus()
    q = bus.subscribe()
    bus.unsubscribe(q)
    bus.publish(Cancel(turn_id=1))
    assert q.empty()


def test_payloads_are_frozen():
    t = Token(text="hi", turn_id=1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        t.text = "bye"  # type: ignore[misc]
