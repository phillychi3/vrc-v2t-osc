import math
import threading
import unittest

from backend.overlay import (
    CALIBRATION_HOLD_SECONDS,
    CalibrationTracker,
    OverlayService,
    OverlayUnavailable,
    calibrate_basis,
    gaze_point,
    look_visibility,
    overlay_transform,
    watch_mode,
)
from backend.settings import default_settings


class FakeRuntime:
    def __init__(self, connect_results: list[object]) -> None:
        self.connect_results = connect_results
        self.calls: list[tuple[str, object]] = []
        self.alive = True
        self.changed = threading.Event()
        self.calibrating = False
        self.calibration_result: tuple[str, list[float] | None, str | None] | None = None

    def _record(self, name: str, value: object = None) -> None:
        self.calls.append((name, value))
        self.changed.set()

    def connect(self) -> bool:
        result = self.connect_results.pop(0) if self.connect_results else True
        self._record("connect", result)
        if isinstance(result, Exception):
            raise result
        return bool(result)

    def disconnect(self) -> None:
        self._record("disconnect")

    def apply_layout(self, settings: dict[str, object]) -> None:
        self._record("layout", settings["anchor"])

    def set_image(self, path: str | None) -> None:
        self._record("image", path)

    def poll(self) -> bool:
        return self.alive

    def start_calibration(self, prompt_image: str) -> None:
        self.calibrating = True
        self._record("calibrate", prompt_image)

    def update_calibration_prompt(self, prompt_image: str) -> None:
        self._record("prompt", prompt_image)

    def take_calibration_result(self):
        result, self.calibration_result = self.calibration_result, None
        if result is not None and result[0] != "running":
            self.calibrating = False
        return result


class OverlayServiceTests(unittest.TestCase):
    def make_service(
        self, runtime: FakeRuntime, *, enabled: bool = True, anchor: str = "head"
    ) -> tuple[OverlayService, list[tuple[str, str | None]]]:
        settings = default_settings()["overlay"]
        settings["enabled"] = enabled
        settings["anchor"] = anchor
        statuses: list[tuple[str, str | None]] = []
        self.calibrations: list[tuple[str, list[float] | None, str | None]] = []
        service = OverlayService(
            settings=settings,
            on_status=lambda status, message: statuses.append((status, message)),
            on_calibration=lambda *result: self.calibrations.append(result),
            runtime_factory=lambda: runtime,
            retry_interval=0.01,
            poll_interval=0.01,
        )
        self.addCleanup(service.close)
        return service, statuses

    def wait_for(self, predicate, timeout: float = 2.0) -> None:
        deadline = threading.Event()
        timer = threading.Timer(timeout, deadline.set)
        timer.start()
        try:
            while not predicate():
                if deadline.is_set():
                    self.fail("condition was not reached")
                deadline.wait(0.005)
        finally:
            timer.cancel()

    def test_disabled_service_never_touches_openvr(self) -> None:
        runtime = FakeRuntime([])
        service, statuses = self.make_service(runtime, enabled=False)
        self.assertFalse(runtime.changed.wait(0.1))
        service.close()
        self.assertEqual(runtime.calls, [])
        self.assertEqual(statuses, [])

    def test_waits_for_steamvr_then_applies_layout_and_image(self) -> None:
        runtime = FakeRuntime([False, True])
        service, statuses = self.make_service(runtime)
        self.wait_for(lambda: ("connected", None) in statuses)
        self.assertEqual(statuses[:2], [("waiting", None), ("connected", None)])
        service.show_image("C:/data/overlay/caption-0.png")
        self.wait_for(lambda: ("image", "C:/data/overlay/caption-0.png") in runtime.calls)
        service.hide()
        self.wait_for(lambda: runtime.calls[-1] == ("image", None))
        self.assertIn(("layout", "head"), runtime.calls)

    def test_reconnects_after_steamvr_quits(self) -> None:
        runtime = FakeRuntime([True, True])
        _service, statuses = self.make_service(runtime)
        self.wait_for(lambda: ("connected", None) in statuses)
        runtime.alive = False
        self.wait_for(lambda: ("disconnect", None) in runtime.calls)
        runtime.alive = True
        self.wait_for(lambda: statuses.count(("connected", None)) >= 2)
        self.assertIn(("waiting", None), statuses)

    def test_disabling_disconnects_and_reports_disabled(self) -> None:
        runtime = FakeRuntime([True])
        service, statuses = self.make_service(runtime)
        self.wait_for(lambda: ("connected", None) in statuses)
        settings = default_settings()["overlay"]
        service.configure(settings)
        self.wait_for(lambda: statuses[-1] == ("disabled", None))
        self.assertEqual(runtime.calls[-1], ("disconnect", None))

    def test_missing_runtime_is_reported_without_retry_loop(self) -> None:
        runtime = FakeRuntime([OverlayUnavailable("未安裝 SteamVR")])
        service, statuses = self.make_service(runtime)
        self.wait_for(lambda: ("unavailable", "未安裝 SteamVR") in statuses)
        runtime.changed.clear()
        self.assertFalse(runtime.changed.wait(0.1))
        settings = default_settings()["overlay"]
        settings["enabled"] = True
        settings["anchor"] = "leftHand"
        service.configure(settings)
        self.wait_for(lambda: ("layout", "leftHand") in runtime.calls)

    def test_close_disconnects_active_runtime(self) -> None:
        runtime = FakeRuntime([True])
        service, statuses = self.make_service(runtime)
        self.wait_for(lambda: ("connected", None) in statuses)
        service.close()
        self.assertEqual(runtime.calls[-1], ("disconnect", None))

    def test_calibration_holds_captions_until_the_result_arrives(self) -> None:
        runtime = FakeRuntime([True])
        service, statuses = self.make_service(runtime, anchor="leftHand")
        self.wait_for(lambda: ("connected", None) in statuses)
        self.assertTrue(service.calibrate("C:/data/overlay/calibration.png"))
        self.wait_for(lambda: ("calibrate", "C:/data/overlay/calibration.png") in runtime.calls)
        # Captions arriving mid-calibration must not replace the prompt.
        service.show_image("C:/data/overlay/caption-0.png")
        self.assertFalse(runtime.changed.wait(0.05) and runtime.calls[-1][0] == "image")
        # Progress hints reach the callback and can refresh the prompt image.
        runtime.calibration_result = ("running", None, "請保持手臂不動")
        self.wait_for(lambda: ("running", None, "請保持手臂不動") in self.calibrations)
        self.assertTrue(service.calibrate("C:/data/overlay/hint.png", update=True))
        self.wait_for(lambda: ("prompt", "C:/data/overlay/hint.png") in runtime.calls)
        runtime.calibration_result = ("done", [1.0] * 12, None)
        self.wait_for(lambda: self.calibrations[-1] == ("done", [1.0] * 12, None))
        self.wait_for(lambda: ("image", "C:/data/overlay/caption-0.png") in runtime.calls)
        # A late hint refresh after completion must not start a new calibration.
        calls = len([call for call in runtime.calls if call[0] == "calibrate"])
        service.calibrate("C:/data/overlay/hint.png", update=True)
        threading.Event().wait(0.05)  # give the overlay thread a chance to act
        self.assertEqual(len([call for call in runtime.calls if call[0] == "calibrate"]), calls)

    def test_calibration_requires_a_connected_hand_anchor(self) -> None:
        runtime = FakeRuntime([False] * 50)
        service, _statuses = self.make_service(runtime, anchor="leftHand")
        self.assertFalse(service.calibrate("C:/data/overlay/calibration.png"))
        head_runtime = FakeRuntime([True])
        head_service, head_statuses = self.make_service(head_runtime)
        self.wait_for(lambda: ("connected", None) in head_statuses)
        self.assertFalse(head_service.calibrate("C:/data/overlay/calibration.png"))


def pose(x: float, y: float, z: float, rotation=None) -> list[list[float]]:
    rotation = rotation or [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    return [list(map(float, rotation[row])) + [(x, y, z)[row]] for row in range(3)]


def compose(parent: list[list[float]], child: list[list[float]]) -> list[list[float]]:
    return [
        [sum(parent[r][k] * child[k][c] for k in range(3)) for c in range(3)]
        + [sum(parent[r][k] * child[k][3] for k in range(3)) + parent[r][3]]
        for r in range(3)
    ]


def rounded(matrix: list[list[float]]) -> list[list[float]]:
    return [[round(value, 6) + 0.0 for value in row] for row in matrix]


# A controller rolled 90 degrees about its own forward axis, like a wrist
# turned toward the face; its axes are deliberately not aligned with the view.
ROLLED = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]


class OverlayTransformTests(unittest.TestCase):
    def test_head_anchor_uses_offsets_and_scale(self) -> None:
        settings = default_settings()["overlay"]
        settings.update({"scale": 1.5, "offsetZ": -2.0, "offsetY": -0.2})
        matrix, width = overlay_transform(settings)
        self.assertEqual(width, 1.5)
        self.assertEqual([row[3] for row in matrix], [0.0, -0.2, -2.0])
        self.assertEqual([row[:3] for row in matrix], [[1, 0, 0], [0, 1, 0], [0, 0, 1]])

    def test_offsets_move_along_the_panel_axes_not_the_controller_axes(self) -> None:
        settings = default_settings()["overlay"]
        basis = [0.0, -1.0, 0.0, 0.1, 1.0, 0.0, 0.0, 0.2, 0.0, 0.0, 1.0, 0.3]
        settings.update(anchor="leftHand", basis=basis, scale=0.6)
        rest, width = overlay_transform(settings)
        self.assertAlmostEqual(width, 0.18)
        settings["offsetX"] = 0.05  # "right" as the wearer sees the panel
        moved, _ = overlay_transform(settings)
        step = [moved[row][3] - rest[row][3] for row in range(3)]
        panel_right = [rest[row][0] * 0.05 for row in range(3)]
        self.assertEqual([round(v, 6) for v in step], [round(v, 6) for v in panel_right])

    def test_rotations_turn_the_panel_in_place(self) -> None:
        settings = default_settings()["overlay"]
        settings.update(anchor="leftHand", offsetX=0.02, pitch=30, yaw=-20, roll=10)
        tilted, _ = overlay_transform(settings)
        settings.update(pitch=0, yaw=0, roll=0)
        level, _ = overlay_transform(settings)
        self.assertEqual([row[3] for row in tilted], [row[3] for row in level])


class CalibrationTests(unittest.TestCase):
    def test_captured_panel_sits_where_the_wearer_looks_and_faces_them(self) -> None:
        hmd = pose(0, 1.6, 0)  # looking forward (-Z)
        controller = pose(0, 1.4, -0.4, ROLLED)
        spot = gaze_point(hmd, controller)
        # Straight ahead, as far away as the hand: on the arm next to it.
        self.assertAlmostEqual(spot[2], -math.dist([0, 1.6, 0], [0, 1.4, -0.4]))
        basis = calibrate_basis(hmd, controller, spot)
        rows = [basis[r * 4 : r * 4 + 4] for r in range(3)]
        panel = compose(controller, rows)
        self.assertEqual(rounded([[panel[r][3] for r in range(3)]]), rounded([spot]))
        to_eyes = [hmd[r][3] - panel[r][3] for r in range(3)]
        length = math.sqrt(sum(v * v for v in to_eyes))
        normal = [panel[r][2] for r in range(3)]
        self.assertAlmostEqual(sum(normal[r] * to_eyes[r] / length for r in range(3)), 1.0)
        # Text right stays horizontal (no vertical component).
        self.assertAlmostEqual(panel[1][0], 0.0)
        self.assertEqual(look_visibility(hmd, controller, rows), 1.0)

    def test_tracker_waits_for_a_steady_raised_hand(self) -> None:
        tracker = CalibrationTracker(0.0)
        hmd = pose(0, 1.6, 0)
        lowered = pose(0.2, 0.9, 0.1, ROLLED)
        raised = pose(0, 1.4, -0.4, ROLLED)
        self.assertIsNone(tracker.feed(0.0, hmd, lowered))
        now = 0.0
        result = None
        while result is None and now < 5:
            now += 1 / 30
            result = tracker.feed(now, hmd, raised)
        self.assertIsNotNone(result)
        self.assertGreaterEqual(now, CALIBRATION_HOLD_SECONDS * 0.95)
        self.assertLess(now, CALIBRATION_HOLD_SECONDS + 0.2)

    def test_tracker_restarts_when_the_hand_moves(self) -> None:
        tracker = CalibrationTracker(0.0)
        hmd = pose(0, 1.6, 0)
        now = 0.0
        for step in range(40):
            now += 1 / 30
            drift = 0.01 * step  # 30 cm/s: never still for long enough
            self.assertIsNone(tracker.feed(now, hmd, pose(drift, 1.4, -0.4, ROLLED)))

    def test_small_head_turns_do_not_restart_the_hold(self) -> None:
        tracker = CalibrationTracker(0.0)
        raised = pose(0, 1.4, -0.4, ROLLED)
        now = 0.0
        result = None
        step = 0
        while result is None and now < 5:
            now += 1 / 30
            step += 1
            # The head sways +-2 degrees while the hand stays put in the room.
            angle = math.radians(2 if step % 2 else -2)
            turn = [
                [math.cos(angle), 0, math.sin(angle)],
                [0, 1, 0],
                [-math.sin(angle), 0, math.cos(angle)],
            ]
            result = tracker.feed(now, pose(0, 1.6, 0, turn), raised)
        self.assertIsNotNone(result)
        self.assertLess(now, CALIBRATION_HOLD_SECONDS + 0.2)

    def test_hints_name_the_current_blocker(self) -> None:
        tracker = CalibrationTracker(0.0)
        hmd = pose(0, 1.6, 0)
        tracker.feed(0.0, hmd, pose(0, 0.5, -0.4))
        self.assertIn("太遠", tracker.hint)
        tracker.feed(0.1, hmd, pose(0.4, 1.6, -0.1))
        self.assertIn("手臂上要戴手錶的位置", tracker.hint)
        tracker.feed(0.2, hmd, pose(0, 1.4, -0.4))
        tracker.feed(0.25, hmd, pose(0, 1.4, -0.4))
        self.assertIn("保持住", tracker.hint)
        tracker.feed(0.3, hmd, pose(0.1, 1.4, -0.4))
        self.assertIn("保持手臂不動", tracker.hint)
        tracker.feed(0.35, hmd, pose(0.1, 1.4, -0.4))
        # Glancing down toward the hand: still on the arm, but the spot moved.
        down = -0.3
        tilted = [
            [1, 0, 0],
            [0, math.cos(down), -math.sin(down)],
            [0, math.sin(down), math.cos(down)],
        ]
        tracker.feed(0.4, pose(0, 1.6, 0, tilted), pose(0.1, 1.4, -0.4))
        self.assertIn("視線停在手錶的位置", tracker.hint)

    def test_captured_spot_is_the_average_of_the_hold(self) -> None:
        tracker = CalibrationTracker(0.0)
        controller = pose(0, 1.4, -0.4)
        now = 0.0
        result = None
        step = 0
        while result is None:
            now += 1 / 30
            step += 1
            angle = math.radians(1 if step % 2 else -1)
            turn = [
                [math.cos(angle), 0, math.sin(angle)],
                [0, 1, 0],
                [-math.sin(angle), 0, math.cos(angle)],
            ]
            result = tracker.feed(now, pose(0, 1.6, 0, turn), controller)
        # Head sway cancels out: the watch lands straight ahead (x close to 0).
        self.assertAlmostEqual(result[3], 0.0, places=3)

    def test_timeout_reason_is_the_most_frequent_blocker(self) -> None:
        tracker = CalibrationTracker(0.0)
        self.assertEqual(tracker.failure_reason(), "沒有收到控制器位置")
        hmd = pose(0, 1.6, 0)
        for step in range(5):
            tracker.feed(step / 30, hmd, pose(0.4, 1.6, -0.1))  # off to the side
        tracker.note("controller", "找不到左手控制器，請確認控制器已開啟")
        self.assertIn("手臂上要戴手錶的位置", tracker.failure_reason())

    def test_tracker_times_out(self) -> None:
        tracker = CalibrationTracker(0.0)
        self.assertFalse(tracker.expired(29.9))
        self.assertTrue(tracker.expired(30.0))


class WatchVisibilityTests(unittest.TestCase):
    # Panel lying flat on the wrist, facing up.
    flat = [[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0]]
    # HMD looking straight down.
    looking_down = [[1, 0, 0], [0, 0, 1], [0, -1, 0]]

    def test_raised_wrist_under_the_eyes_is_fully_visible(self) -> None:
        hmd = pose(0, 1.6, 0, self.looking_down)
        wrist = pose(0, 1.2, 0)
        self.assertEqual(look_visibility(hmd, wrist, self.flat), 1.0)

    def test_lowered_arm_outside_the_view_is_hidden(self) -> None:
        hmd = pose(0, 1.6, 0)  # looking forward
        wrist = pose(0.2, 0.9, 0.1)
        self.assertEqual(look_visibility(hmd, wrist, self.flat), 0.0)

    def test_panel_facing_away_is_hidden_even_in_view(self) -> None:
        hmd = pose(0, 1.6, 0, self.looking_down)
        upside_down = [[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0]]
        wrist = pose(0, 1.2, 0)
        self.assertEqual(look_visibility(hmd, wrist, upside_down), 0.0)

    def test_watch_mode_requires_a_hand_anchor(self) -> None:
        settings = default_settings()["overlay"]
        settings["watchMode"] = True
        self.assertFalse(watch_mode(settings))
        settings["anchor"] = "leftHand"
        self.assertTrue(watch_mode(settings))


if __name__ == "__main__":
    unittest.main()
