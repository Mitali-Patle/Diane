"""Eye-vector clamping and lerp math with synthetic cursor positions (§19)."""
import math

from jarvis.avatar.eyes import EyeState
from jarvis.avatar.packs import EyeSocket

SOCKET = EyeSocket(center=(48, 72), radius=4)


def test_pupil_clamped_to_socket_radius():
    eyes = EyeState(sockets=(SOCKET,))
    for _ in range(200):  # converge fully
        (px, py), = eyes.update((1000.0, 72.0))
    assert math.hypot(px, py) <= SOCKET.radius + 1e-6
    assert px > 0 and abs(py) < 1e-6  # pointing at the cursor


def test_cursor_inside_socket_not_clamped():
    eyes = EyeState(sockets=(SOCKET,))
    for _ in range(200):
        (px, py), = eyes.update((50.0, 72.0))  # 2px right of center, within radius
    assert abs(px - 2.0) < 0.01
    assert abs(py) < 1e-6


def test_lerp_moves_fraction_per_tick():
    eyes = EyeState(sockets=(SOCKET,))
    (px1, _), = eyes.update((48.0 + 4.0, 72.0))  # target dx=4 (at radius)
    assert abs(px1 - 4.0 * 0.25) < 1e-6  # one tick = 25% of the way
    (px2, _), = eyes.update((48.0 + 4.0, 72.0))
    assert px2 > px1  # keeps easing toward the target


def test_two_eyes_track_independently():
    eyes = EyeState(sockets=(EyeSocket((48, 72), 4), EyeSocket((80, 72), 4)))
    for _ in range(200):
        offsets = eyes.update((64.0, 72.0))  # cursor between the eyes
    assert offsets[0][0] > 0 and offsets[1][0] < 0  # eyes look inward
