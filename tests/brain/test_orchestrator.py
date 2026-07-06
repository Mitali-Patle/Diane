"""Orchestrator state-machine tests with a fake LLM (§7, §19 — fully headless)."""
import asyncio

from jarvis.bus import (
    AvatarActivate,
    Bus,
    Cancel,
    FinalTranscript,
    State,
    StateChanged,
)
from jarvis.brain.orchestrator import Orchestrator


class FakeLlm:
    def __init__(self, deltas, delay=0.0):
        self.deltas = deltas
        self.delay = delay
        self.calls = []

    async def chat(self, messages):
        self.calls.append(messages)
        for d in self.deltas:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield d


def states_from(queue) -> list[State]:
    out = []
    while not queue.empty():
        ev = queue.get_nowait()
        if isinstance(ev, StateChanged):
            out.append(ev.new_state)
    return out


async def test_happy_path_state_sequence():
    bus = Bus()
    q = bus.subscribe()
    orch = Orchestrator(bus, FakeLlm(["Hi", " there", "."]))

    orch.on_wake()  # IDLE -> LISTENING
    tokens = [t async for t in orch.run_turn(FinalTranscript("hello", turn_id=1))]

    assert "".join(t.text for t in tokens) == "Hi there."
    assert states_from(q) == [State.LISTENING, State.THINKING, State.SPEAKING, State.IDLE]
    assert orch.state is State.IDLE


async def test_rolling_context_accumulates_and_trims():
    bus = Bus()
    orch = Orchestrator(bus, FakeLlm(["ok"]))
    n_turns = 15  # config context_turns=10 -> trimmed to 21 messages (sys + 10*2)
    for i in range(n_turns):
        async for _ in orch.run_turn(FinalTranscript(f"msg {i}", turn_id=i)):
            pass
    msgs = orch._messages
    assert msgs[0]["role"] == "system"
    assert len(msgs) <= 1 + 10 * 2
    assert msgs[-1] == {"role": "assistant", "content": "ok"}


async def test_barge_in_cancel_stops_generation_mid_stream():
    bus = Bus()
    orch = Orchestrator(bus, FakeLlm(["a"] * 50, delay=0.01))
    loop_task = asyncio.create_task(orch.event_loop())

    tokens = []
    async for t in orch.run_turn(FinalTranscript("talk a lot", turn_id=2)):
        tokens.append(t)
        if len(tokens) == 3:
            bus.publish(Cancel(turn_id=-1))
            await asyncio.sleep(0.05)  # let event_loop process the Cancel

    assert len(tokens) < 50
    assert orch.state is State.LISTENING  # barge-in: SPEAKING -> LISTENING, not IDLE
    loop_task.cancel()


async def test_avatar_activate_only_in_idle():
    bus = Bus()
    activated = []
    orch = Orchestrator(bus, FakeLlm(["x"]))
    orch.add_activate_hook(lambda: activated.append(True))
    loop_task = asyncio.create_task(orch.event_loop())
    await asyncio.sleep(0)

    bus.publish(AvatarActivate())      # IDLE: accepted
    await asyncio.sleep(0.02)
    assert activated == [True]
    assert orch.state is State.LISTENING

    bus.publish(AvatarActivate())      # LISTENING: ignored (DL-10)
    await asyncio.sleep(0.02)
    assert activated == [True]
    loop_task.cancel()


async def test_llm_failure_sets_error_then_idle():
    class BrokenLlm:
        async def chat(self, messages):
            raise RuntimeError("ollama down")
            yield  # pragma: no cover

    bus = Bus()
    q = bus.subscribe()
    orch = Orchestrator(bus, BrokenLlm())
    tokens = [t async for t in orch.run_turn(FinalTranscript("hi", turn_id=1))]
    assert tokens == []
    seq = states_from(q)
    assert State.ERROR in seq
    assert orch.state is State.IDLE


async def test_barge_in_via_on_wake_race_still_cancels():
    """The real barge-in ordering: wake_vad publishes Cancel then synchronously
    calls on_wake() BEFORE the orchestrator's event_loop dequeues the Cancel.
    Generation must still stop and state must end LISTENING, not IDLE."""
    bus = Bus()
    orch = Orchestrator(bus, FakeLlm(["a"] * 50, delay=0.01))
    loop_task = asyncio.create_task(orch.event_loop())

    tokens = []
    async for t in orch.run_turn(FinalTranscript("talk", turn_id=3)):
        tokens.append(t)
        if len(tokens) == 3:
            bus.publish(Cancel(turn_id=-1))  # queued, not yet processed
            orch.on_wake()                   # synchronous preemption (the race)

    assert len(tokens) < 50
    assert orch.state is State.LISTENING
    loop_task.cancel()
