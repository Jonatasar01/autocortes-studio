from fractions import Fraction
import copy
import sys
import threading
import pytest
from app.integrations.filmcraft.bridge import FilmCraftBridge, BridgeError, snap_ms, TICKS


@pytest.mark.parametrize('num', [24000, 30000, 60000])
def test_fractional_fps_one_hour_has_at_most_half_frame_error(num):
    fps = Fraction(num, 1001)
    ticks = snap_ms(3_600_000, fps)
    assert abs(Fraction(ticks, TICKS)-3600) <= Fraction(1, 2)/fps
    assert (Fraction(ticks, TICKS)*fps).denominator == 1


def test_exact_ticks_for_2997():
    assert snap_ms(1001, Fraction(30000, 1001)) == 254270016000


@pytest.mark.parametrize('time', [-1, 1.2, True])
def test_invalid_times_rejected(time):
    with pytest.raises(BridgeError):
        snap_ms(time, Fraction(30))


def test_missing_binary_does_not_import_or_modify_production(tmp_path):
    with pytest.raises(BridgeError, match='não instalado'):
        FilmCraftBridge(tmp_path/'missing.exe').diagnose()
    assert list(tmp_path.iterdir()) == []


def test_cancellation_stops_before_spawn():
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(BridgeError, match='cancelada'):
        FilmCraftBridge(cancel_event=cancel)._run(['-c', 'raise AssertionError()'], program=sys.executable)


def test_timeout_terminates_real_child_process():
    bridge = FilmCraftBridge(timeout=0.1)
    with pytest.raises(BridgeError, match='Tempo limite'):
        bridge._run(['-c', 'import time; time.sleep(30)'], program=sys.executable)
    assert bridge.log[-1]['interrupted']
    assert bridge.log[-1]['returncode'] is not None


def test_timeline_validation_detects_audio_offset():
    expected = [{'start': 0, 'end': 100, 'sourceIn': 200, 'sourceOut': 300}]
    sequence = {'settings': {'frame_rate': {'num': 30, 'den': 1}},
                'video': [{'items': copy.deepcopy(expected)}], 'audio': [{'items': copy.deepcopy(expected)}]}
    FilmCraftBridge._validate_timeline(sequence, expected, Fraction(30), True)
    sequence['audio'][0]['items'][0]['sourceIn'] += 1
    with pytest.raises(BridgeError, match='audio'):
        FilmCraftBridge._validate_timeline(sequence, expected, Fraction(30), True)
