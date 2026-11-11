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

    async def chat(self, messages, tools=None):
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


async def test_external_playback_keeps_speaking_until_finish_turn():
    bus = Bus()
    q = bus.subscribe()
    orch = Orchestrator(bus, FakeLlm(["Hi", "."]), external_playback=True)
    async for _ in orch.run_turn(FinalTranscript("hello", turn_id=1)):
        pass
    assert orch.state is State.SPEAKING  # playback still owed
    orch.finish_turn()
    assert orch.state is State.IDLE
    assert states_from(q) == [State.THINKING, State.SPEAKING, State.IDLE]


async def test_finish_turn_noop_after_barge_in():
    bus = Bus()
    orch = Orchestrator(bus, FakeLlm(["a"] * 10, delay=0.01), external_playback=True)
    tokens = []
    async for t in orch.run_turn(FinalTranscript("x", turn_id=1)):
        tokens.append(t)
        if len(tokens) == 2:
            orch.on_wake()  # barge-in
    assert orch.state is State.LISTENING
    orch.finish_turn()
    assert orch.state is State.LISTENING  # not clobbered to IDLE


# --------------------------------------------------------------------------- #
# 2026-11 audit regressions
# --------------------------------------------------------------------------- #

async def test_end_listening_returns_to_idle_after_false_wake():
    """A wake with no speech used to leave the state stuck at LISTENING forever,
    which also disabled click-to-activate (IDLE-only)."""
    bus = Bus()
    orch = Orchestrator(bus, FakeLlm(["x"]))
    orch.on_wake()
    assert orch.state is State.LISTENING
    orch.end_listening()
    assert orch.state is State.IDLE


async def test_end_listening_is_noop_outside_listening():
    orch = Orchestrator(Bus(), FakeLlm(["x"]), external_playback=True)
    async for _ in orch.run_turn(FinalTranscript("hi", turn_id=1)):
        pass
    assert orch.state is State.SPEAKING
    orch.end_listening()
    assert orch.state is State.SPEAKING


async def test_wake_during_thinking_cancels_and_listens():
    orch = Orchestrator(Bus(), FakeLlm(["a"] * 20, delay=0.01), external_playback=True)
    seen = []
    async for t in orch.run_turn(FinalTranscript("x", turn_id=1)):
        seen.append(t)
        if len(seen) == 1:
            orch._set_state(State.THINKING)  # simulate wake landing mid-think
            orch.on_wake()
    assert orch.state is State.LISTENING
    assert len(seen) < 20


async def test_unreachable_llm_speaks_apology_in_error_state():
    from jarvis.brain.orchestrator import APOLOGY_UNREACHABLE

    class ConnectError(Exception):  # same name httpx uses
        pass

    class DownLlm:
        async def chat(self, messages, tools=None):
            raise ConnectError("All connection attempts failed")
            yield  # pragma: no cover

    bus = Bus()
    orch = Orchestrator(bus, DownLlm(), external_playback=True)
    tokens = [t async for t in orch.run_turn(FinalTranscript("hi", turn_id=1))]
    assert [t.text for t in tokens] == [APOLOGY_UNREACHABLE]
    assert orch.state is State.ERROR and orch.is_voicing
    assert orch._messages[-1]["role"] == "system"  # failed user msg not left dangling
    orch.finish_turn()
    assert orch.state is State.IDLE


class ToolLlm:
    """First round proposes a tool, second round answers."""

    def __init__(self):
        self.calls = []

    async def chat(self, messages, tools=None):
        from jarvis.bus import ToolCall

        self.calls.append(list(messages))
        if len(self.calls) == 1:
            yield ToolCall(name="check_disk", args={})
        else:
            yield "Plenty of space."


class FakeTools:
    def __init__(self, delay=0.0):
        self.delay = delay

    def specs(self):
        return []

    async def run(self, call, turn_id):
        await asyncio.sleep(self.delay)
        return "50G free"


async def test_tool_call_message_precedes_tool_result():
    llm = ToolLlm()
    orch = Orchestrator(Bus(), llm, tools=FakeTools())
    async for _ in orch.run_turn(FinalTranscript("disk?", turn_id=1)):
        pass
    second = llm.calls[1]
    roles = [m["role"] for m in second]
    assert roles[-2:] == ["assistant", "tool"]
    assert second[-2]["tool_calls"][0]["function"]["name"] == "check_disk"


async def test_barge_in_aborts_slow_tool():
    orch = Orchestrator(Bus(), ToolLlm(), tools=FakeTools(delay=5.0))

    async def barge():
        await asyncio.sleep(0.05)
        orch.on_wake()

    t0 = asyncio.get_running_loop().time()
    barger = asyncio.create_task(barge())
    async for _ in orch.run_turn(FinalTranscript("disk?", turn_id=1)):
        pass
    await barger
    assert asyncio.get_running_loop().time() - t0 < 1.0  # didn't wait the 5 s tool
    assert orch.state is State.LISTENING


async def test_trim_keeps_tool_messages_with_their_turn():
    orch = Orchestrator(Bus(), ToolLlm(), tools=FakeTools())
    orch._context_turns = 2
    for i in range(5):
        orch._llm = ToolLlm()
        async for _ in orch.run_turn(FinalTranscript(f"q{i}", turn_id=i)):
            pass
    msgs = orch._messages
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"  # never starts on an orphaned tool message
    assert sum(m["role"] == "user" for m in msgs) == 2


# --------------------------------------------------------------------------- #
# handbook-audit regressions
# --------------------------------------------------------------------------- #

async def test_outer_cancellation_kills_inflight_tool():
    """P5: cancelling the turn's task (shutdown) must cancel the tool too, not
    leave it running (e.g. a subprocess outliving Diane)."""
    tool_cancelled = asyncio.Event()

    class SlowTools(FakeTools):
        async def run(self, call, turn_id):
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                tool_cancelled.set()
                raise

    orch = Orchestrator(Bus(), ToolLlm(), tools=SlowTools())

    async def drive():
        async for _ in orch.run_turn(FinalTranscript("disk?", turn_id=1)):
            pass

    task = asyncio.create_task(drive())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert task.cancelled()          # the outer cancellation was not swallowed
    assert tool_cancelled.is_set()   # and the tool died with it


async def test_barge_in_mid_tool_rolls_back_dangling_tool_calls():
    orch = Orchestrator(Bus(), ToolLlm(), tools=FakeTools(delay=5.0))

    async def barge():
        await asyncio.sleep(0.05)
        orch.on_wake()

    barger = asyncio.create_task(barge())
    async for _ in orch.run_turn(FinalTranscript("disk?", turn_id=1)):
        pass
    await barger
    assert not any("tool_calls" in m for m in orch._messages)
    assert not any(m["role"] == "tool" for m in orch._messages)


async def test_consumer_closing_early_is_not_an_llm_failure():
    """Stopping reading a turn early (aclose) must not log/speak an LLM error."""
    bus = Bus()
    q = bus.subscribe()
    orch = Orchestrator(bus, FakeLlm(["a", "b", "c"]), external_playback=True)
    gen = orch.run_turn(FinalTranscript("x", turn_id=1))
    await gen.__anext__()
    await gen.aclose()
    assert State.ERROR not in states_from(q)


async def test_late_finalized_turn_does_not_clobber_newer_turn():
    orch = Orchestrator(Bus(), FakeLlm(["a"] * 5), external_playback=True)
    old = orch.run_turn(FinalTranscript("one", turn_id=1))
    await old.__anext__()                       # turn 1 abandoned mid-stream
    new = orch.run_turn(FinalTranscript("two", turn_id=2))
    await new.__anext__()                       # turn 2 in flight
    await old.aclose()                          # turn 1 finalized late
    assert orch._turn_active                    # turn 2 still active
    await new.aclose()
