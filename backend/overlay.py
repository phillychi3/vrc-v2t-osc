from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from collections.abc import Callable
from copy import deepcopy
from typing import Any, Protocol


logger = logging.getLogger(__name__)

OVERLAY_KEY = "com.phillychi3.vrc2t.captions"
OVERLAY_NAME = "VRC2T Captions"
# Base panel widths in meters; `scale` multiplies these.
HEAD_WIDTH_METERS = 1.0
HAND_WIDTH_METERS = 0.3
# Watch mode: the panel fades in once it faces the eyes (FACING) and sits near
# the center of view (GAZE). Values are cosines of the angle; each fades over
# the following FADE band so the panel never pops.
WATCH_FACING = 0.55
WATCH_GAZE = 0.75
WATCH_FADE = 0.15
# Fraction of the remaining alpha distance covered per poll (~30 Hz).
WATCH_SMOOTHING = 0.35
# Calibration: the wearer raises the arm like checking a watch, looks at the
# spot on the forearm where the watch should be and holds still. The watch goes
# where the view direction meets the arm, whatever the controller's shape.
CALIBRATION_HOLD_SECONDS = 1.5
# Hand drift allowed while holding, measured in the room (not head space, where
# every small head turn would look like the hand moving).
CALIBRATION_STILL_METERS = 0.04
# Drift allowed for the looked-at spot, measured relative to the hand. Looser,
# since it moves with the head; the captured spot is the average anyway.
CALIBRATION_GAZE_STILL_METERS = 0.08
CALIBRATION_TIMEOUT_SECONDS = 30.0
CALIBRATION_NEAR_METERS = 0.15
CALIBRATION_FAR_METERS = 0.8
# The looked-at spot must be this close to the controller: on that forearm.
CALIBRATION_ARM_REACH_METERS = 0.35
# Progress hints are re-sent at most this often while the text keeps changing.
CALIBRATION_HINT_INTERVAL = 0.5
# Head-locked prompt shown while calibrating: position and width in meters.
CALIBRATION_PROMPT_OFFSET = (0.0, -0.1, -0.8)
CALIBRATION_PROMPT_WIDTH = 0.5

Matrix34 = list[list[float]]
IDENTITY_BASIS = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0]

StatusHandler = Callable[[str, str | None], None]
CalibrationHandler = Callable[[str, list[float] | None, str | None], None]


class OverlayUnavailable(RuntimeError):
    """OpenVR cannot be used on this machine; retrying will not help."""


class OverlayRuntime(Protocol):
    @property
    def calibrating(self) -> bool: ...

    def connect(self) -> bool: ...
    def disconnect(self) -> None: ...
    def apply_layout(self, settings: dict[str, Any]) -> None: ...
    def set_image(self, path: str | None) -> None: ...
    def poll(self) -> bool: ...
    def start_calibration(self, prompt_image: str) -> None: ...
    def update_calibration_prompt(self, prompt_image: str) -> None: ...
    def take_calibration_result(
        self,
    ) -> tuple[str, list[float] | None, str | None] | None: ...


RuntimeFactory = Callable[[], OverlayRuntime]


def _multiply(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _transpose(a: list[list[float]]) -> list[list[float]]:
    return [[a[j][i] for j in range(3)] for i in range(3)]


def _apply(rotation: list[list[float]], vector: list[float]) -> list[float]:
    return [sum(rotation[row][k] * vector[k] for k in range(3)) for row in range(3)]


def _normalize(vector: list[float]) -> list[float] | None:
    length = math.sqrt(sum(value * value for value in vector))
    return None if length < 1e-6 else [value / length for value in vector]


def _cross(a: list[float], b: list[float]) -> list[float]:
    return [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]


def _column(matrix: Matrix34, index: int) -> list[float]:
    return [matrix[row][index] for row in range(3)]


def _compose(parent: Matrix34, child: Matrix34) -> Matrix34:
    rotation = _multiply([row[:3] for row in parent], [row[:3] for row in child])
    return [
        rotation[row]
        + [sum(parent[row][k] * child[k][3] for k in range(3)) + parent[row][3]]
        for row in range(3)
    ]


def _basis_rows(basis: list[float]) -> Matrix34:
    return [[float(value) for value in basis[row * 4 : row * 4 + 4]] for row in range(3)]


def watch_mode(settings: dict[str, Any]) -> bool:
    return bool(settings.get("watchMode")) and settings["anchor"] != "head"


def _ramp(value: float, start: float) -> float:
    return min(1.0, max(0.0, (value - start) / WATCH_FADE))


def look_visibility(hmd: Matrix34, device: Matrix34, overlay: Matrix34) -> float:
    """How much a device-attached panel is being looked at, from 0 to 1.

    `hmd` and `device` are tracking-space poses; `overlay` is the panel's
    transform relative to the device.
    """
    panel = _compose(device, overlay)
    to_eyes = _normalize([hmd[row][3] - panel[row][3] for row in range(3)])
    if to_eyes is None:
        return 0.0
    # The panel shows its front along its local +Z; the HMD looks along -Z.
    facing = sum(panel[row][2] * to_eyes[row] for row in range(3))
    gaze = sum(-hmd[row][2] * -to_eyes[row] for row in range(3))
    return _ramp(facing, WATCH_FACING) * _ramp(gaze, WATCH_GAZE)


def _rotation(pitch: float, yaw: float, roll: float) -> list[list[float]]:
    """Rotation about the panel's own axes: yaw (Y), then pitch (X), then roll (Z)."""
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    cr, sr = math.cos(roll), math.sin(roll)
    rx = [[1.0, 0.0, 0.0], [0.0, cp, -sp], [0.0, sp, cp]]
    ry = [[cy, 0.0, sy], [0.0, 1.0, 0.0], [-sy, 0.0, cy]]
    rz = [[cr, -sr, 0.0], [sr, cr, 0.0], [0.0, 0.0, 1.0]]
    return _multiply(ry, _multiply(rx, rz))


def overlay_transform(settings: dict[str, Any]) -> tuple[Matrix34, float]:
    """3x4 device-relative transform and panel width for the configured anchor.

    `basis` is the panel's resting pose on the anchor (identity on the head, or
    the pose captured by calibration on a hand). Offsets and rotations adjust
    the panel along its *own* axes: +X right and +Y up as the wearer sees it,
    +Z toward the eyes. That keeps every slider moving in a straight line.
    """
    basis = _basis_rows(settings["basis"])
    basis_rotation = [row[:3] for row in basis]
    adjust = _rotation(
        *(math.radians(float(settings[axis])) for axis in ("pitch", "yaw", "roll"))
    )
    rotation = _multiply(basis_rotation, adjust)
    offset = _apply(
        basis_rotation, [float(settings[axis]) for axis in ("offsetX", "offsetY", "offsetZ")]
    )
    matrix = [rotation[row] + [basis[row][3] + offset[row]] for row in range(3)]
    base = HEAD_WIDTH_METERS if settings["anchor"] == "head" else HAND_WIDTH_METERS
    return matrix, base * float(settings["scale"])


def gaze_point(hmd: Matrix34, device: Matrix34) -> list[float]:
    """Where the wearer is looking, at the controller's distance from the eyes.

    A forearm spot next to the hand is about as far away as the hand itself,
    so this lands on the arm without knowing the controller's shape.
    """
    eyes = _column(hmd, 3)
    distance = math.dist(eyes, _column(device, 3))
    return [eyes[i] - hmd[i][2] * distance for i in range(3)]


def calibrate_basis(
    hmd: Matrix34, device: Matrix34, position: list[float]
) -> list[float] | None:
    """Device-relative resting pose for a panel at `position` (room space).

    The panel faces the eyes and is level with the headset's horizon, so its
    text reads straight in the pose the wearer holds now.
    """
    device_position = _column(device, 3)
    eyes = _column(hmd, 3)
    normal = _normalize([eyes[i] - position[i] for i in range(3)])
    head_up = _column(hmd, 1)
    along = sum(head_up[i] * normal[i] for i in range(3)) if normal else 0.0
    up = normal and _normalize([head_up[i] - along * normal[i] for i in range(3)])
    if normal is None or up is None:
        return None
    right = _cross(up, normal)
    world = [[right[row], up[row], normal[row]] for row in range(3)]
    device_rotation = [row[:3] for row in device]
    inverse = _transpose(device_rotation)
    rotation = _multiply(inverse, world)
    local = _apply(inverse, [position[i] - device_position[i] for i in range(3)])
    return [value for row in range(3) for value in rotation[row] + [local[row]]]


class CalibrationTracker:
    """Waits until the wearer holds a watch-checking pose, then captures a basis.

    Every sample leaves a human-readable `hint` saying what is still missing,
    so the wearer (and a timeout message) can tell why nothing happens yet.
    """

    def __init__(self, now: float) -> None:
        self._deadline = now + CALIBRATION_TIMEOUT_SECONDS
        # (time, controller position in the room, looked-at spot in controller
        # space) while the pose holds.
        self._samples: deque[tuple[float, list[float], list[float]]] = deque()
        self.hint = "等待控制器位置…"
        self._problems: dict[str, int] = {}
        self._problem_hints: dict[str, str] = {}

    def expired(self, now: float) -> bool:
        return now >= self._deadline

    def note(self, problem: str, hint: str) -> None:
        """Records why this sample cannot be used."""
        self.hint = hint
        self._problems[problem] = self._problems.get(problem, 0) + 1
        self._problem_hints[problem] = hint

    def failure_reason(self) -> str:
        """The most frequent blocker during the attempt, for a timeout message."""
        if not self._problems:
            return "沒有收到控制器位置"
        problem = max(self._problems, key=self._problems.__getitem__)
        return self._problem_hints[problem]

    def feed(self, now: float, hmd: Matrix34, device: Matrix34) -> list[float] | None:
        position = _column(device, 3)
        distance = math.dist(_column(hmd, 3), position)
        if distance > CALIBRATION_FAR_METERS:
            self._samples.clear()
            self.note("far", f"手臂離眼睛太遠（{distance:.2f} m），請再抬近一點")
            return None
        if distance < CALIBRATION_NEAR_METERS:
            self._samples.clear()
            self.note("near", f"手臂離眼睛太近（{distance:.2f} m），請放遠一點")
            return None
        target = gaze_point(hmd, device)
        reach = math.dist(target, position)
        if reach > CALIBRATION_ARM_REACH_METERS:
            self._samples.clear()
            self.note(
                "arm",
                f"請看著手臂上要戴手錶的位置（目前視線離手 {reach * 100:.0f} cm，"
                f"需在 {CALIBRATION_ARM_REACH_METERS * 100:.0f} cm 內）",
            )
            return None
        # The looked-at spot relative to the hand, so the arm can carry it.
        rotation = [row[:3] for row in device]
        spot = _apply(_transpose(rotation), [target[i] - position[i] for i in range(3)])
        hand_drift = max(
            (math.dist(sample, position) for _, sample, _ in self._samples), default=0.0
        )
        gaze_drift = max((math.dist(sample, spot) for _, _, sample in self._samples), default=0.0)
        if hand_drift > CALIBRATION_STILL_METERS:
            # Start the hold over from here.
            self._samples.clear()
            self.note(
                "moving",
                f"請保持手臂不動（晃動 {hand_drift * 100:.1f} cm，"
                f"需小於 {CALIBRATION_STILL_METERS * 100:.0f} cm）",
            )
        elif gaze_drift > CALIBRATION_GAZE_STILL_METERS:
            self._samples.clear()
            self.note(
                "gaze",
                f"請讓視線停在手錶的位置（移動 {gaze_drift * 100:.1f} cm，"
                f"需小於 {CALIBRATION_GAZE_STILL_METERS * 100:.0f} cm）",
            )
        self._samples.append((now, position, spot))
        while now - self._samples[0][0] > CALIBRATION_HOLD_SECONDS:
            self._samples.popleft()
        held = now - self._samples[0][0]
        if held < CALIBRATION_HOLD_SECONDS * 0.95:
            if self._samples[0][0] != now:
                self.hint = f"很好，保持住… 再 {CALIBRATION_HOLD_SECONDS - held:.1f} 秒"
            return None
        # Average the looked-at spot over the hold to smooth out head jitter.
        count = len(self._samples)
        average = [sum(sample[i] for _, _, sample in self._samples) / count for i in range(3)]
        world = [position[i] + _apply(rotation, average)[i] for i in range(3)]
        return calibrate_basis(hmd, device, world)


class OpenVrRuntime:
    """Thin pyopenvr wrapper. Every method must run on the overlay thread."""

    def __init__(self) -> None:
        self._openvr: Any = None
        self._system: Any = None
        self._overlay: Any = None
        self._handle: Any = None
        self._settings: dict[str, Any] | None = None
        self._device: int | None = None
        self._image: str | None = None
        self._visible = False
        self._next_anchor_check = 0.0
        self._alpha = 1.0
        self._poses: Any = None
        self._panel: Matrix34 | None = None
        self._calibration: CalibrationTracker | None = None
        # Latest unreported calibration update: ("running", None, hint) while
        # in progress, then ("done", basis, None) or ("failed", None, reason).
        self._calibration_result: tuple[str, list[float] | None, str | None] | None = None
        self._reported_hint: str | None = None
        self._reported_hint_at = 0.0

    @property
    def calibrating(self) -> bool:
        return self._calibration is not None

    def connect(self) -> bool:
        try:
            import openvr
        except Exception as exc:
            raise OverlayUnavailable("無法載入 OpenVR 執行階段") from exc
        self._openvr = openvr
        if not openvr.isRuntimeInstalled():
            raise OverlayUnavailable("未安裝 SteamVR")
        # A background probe never launches SteamVR; only attach as an overlay
        # application once the user already has SteamVR running.
        try:
            openvr.init(openvr.VRApplication_Background)
        except openvr.OpenVRError:
            return False
        openvr.shutdown()
        try:
            self._system = openvr.init(openvr.VRApplication_Overlay)
        except openvr.OpenVRError:
            return False
        try:
            self._overlay = openvr.VROverlay()
            self._handle = self._overlay.createOverlay(OVERLAY_KEY, OVERLAY_NAME)
        except Exception:
            self.disconnect()
            raise
        self._device = None
        self._visible = False
        return True

    def disconnect(self) -> None:
        openvr = self._openvr
        if self._calibration is not None:
            self._finish_calibration("failed", None, "SteamVR 已中斷連線")
        if openvr is None or self._system is None:
            return
        try:
            if self._overlay is not None and self._handle is not None:
                self._overlay.destroyOverlay(self._handle)
        except Exception:
            logger.exception("Failed to destroy OpenVR overlay")
        finally:
            self._system = None
            self._overlay = None
            self._handle = None
            self._visible = False
            openvr.shutdown()

    def apply_layout(self, settings: dict[str, Any]) -> None:
        self._settings = deepcopy(settings)
        matrix, width = overlay_transform(settings)
        self._overlay.setOverlayWidthInMeters(self._handle, width)
        # Watch mode starts hidden and fades in once the wearer looks at it.
        self._set_alpha(0.0 if watch_mode(settings) else float(settings["opacity"]))
        self._device = None
        self._next_anchor_check = 0.0
        self._update_anchor(matrix)

    def set_image(self, path: str | None) -> None:
        self._image = path
        if path is not None:
            self._overlay.setOverlayFromFile(self._handle, path)
        self._update_visibility()

    def start_calibration(self, prompt_image: str) -> None:
        """Shows a head-locked prompt until the controller is held in view."""
        openvr = self._openvr
        self._calibration = CalibrationTracker(time.monotonic())
        self._calibration_result = None
        self._reported_hint = None
        x, y, z = CALIBRATION_PROMPT_OFFSET
        transform = openvr.HmdMatrix34_t()
        for row, values in enumerate(
            ([1.0, 0.0, 0.0, x], [0.0, 1.0, 0.0, y], [0.0, 0.0, 1.0, z])
        ):
            for column, value in enumerate(values):
                transform.m[row][column] = value
        self._overlay.setOverlayWidthInMeters(self._handle, CALIBRATION_PROMPT_WIDTH)
        self._overlay.setOverlayTransformTrackedDeviceRelative(
            self._handle, openvr.k_unTrackedDeviceIndex_Hmd, transform
        )
        self._set_alpha(1.0)
        self._overlay.setOverlayFromFile(self._handle, prompt_image)
        self._overlay.showOverlay(self._handle)
        self._visible = True

    def update_calibration_prompt(self, prompt_image: str) -> None:
        """Swaps the prompt image, e.g. to show the latest hint."""
        if self._calibration is not None:
            self._overlay.setOverlayFromFile(self._handle, prompt_image)

    def take_calibration_result(self) -> tuple[str, list[float] | None, str | None] | None:
        result, self._calibration_result = self._calibration_result, None
        return result

    def poll(self) -> bool:
        """Returns False once SteamVR asks this application to exit."""
        openvr = self._openvr
        event = openvr.VREvent_t()
        while self._system.pollNextEvent(event):
            if event.eventType == openvr.VREvent_Quit:
                self._system.acknowledgeQuit_Exiting()
                return False
        if self._calibration is not None:
            self._update_calibration()
            return True
        if self._settings is not None and time.monotonic() >= self._next_anchor_check:
            # Controller indices change when controllers reconnect.
            self._update_anchor(overlay_transform(self._settings)[0])
        if self._settings is not None and watch_mode(self._settings):
            self._update_watch_alpha()
        return True

    def _read_poses(self) -> Any:
        openvr = self._openvr
        if self._poses is None:
            self._poses = (openvr.TrackedDevicePose_t * openvr.k_unMaxTrackedDeviceCount)()
        self._system.getDeviceToAbsoluteTrackingPose(
            openvr.TrackingUniverseStanding, 0, self._poses
        )
        return self._poses

    def _update_calibration(self) -> None:
        tracker = self._calibration
        now = time.monotonic()
        if tracker.expired(now):
            self._finish_calibration(
                "failed", None, f"校準逾時：{tracker.failure_reason()}"
            )
            return
        device = self._controller_index()
        hand = "右" if self._settings and self._settings["anchor"] == "rightHand" else "左"
        if device is None:
            tracker.note("controller", f"找不到{hand}手控制器，請確認控制器已開啟")
        else:
            poses = self._read_poses()
            hmd = poses[self._openvr.k_unTrackedDeviceIndex_Hmd]
            controller = poses[device]
            if not hmd.bPoseIsValid:
                tracker.note("tracking", "頭盔追蹤中斷，請確認頭盔在追蹤範圍內")
            elif not controller.bPoseIsValid:
                tracker.note("tracking", f"{hand}手控制器追蹤中斷，請確認在追蹤範圍內")
            else:
                basis = tracker.feed(
                    now,
                    _matrix_rows(hmd.mDeviceToAbsoluteTracking),
                    _matrix_rows(controller.mDeviceToAbsoluteTracking),
                )
                if basis is not None:
                    self._finish_calibration("done", basis, None)
                    return
        if tracker.hint != self._reported_hint and (
            now - self._reported_hint_at >= CALIBRATION_HINT_INTERVAL
        ):
            self._reported_hint = tracker.hint
            self._reported_hint_at = now
            self._calibration_result = ("running", None, tracker.hint)

    def _finish_calibration(
        self, status: str, basis: list[float] | None, message: str | None
    ) -> None:
        self._calibration = None
        self._calibration_result = (status, basis, message)

    def _update_watch_alpha(self) -> None:
        target = 0.0
        if self._device is not None and self._panel is not None:
            poses = self._read_poses()
            hmd = poses[self._openvr.k_unTrackedDeviceIndex_Hmd]
            device = poses[self._device]
            if hmd.bPoseIsValid and device.bPoseIsValid:
                target = float(self._settings["opacity"]) * look_visibility(
                    _matrix_rows(hmd.mDeviceToAbsoluteTracking),
                    _matrix_rows(device.mDeviceToAbsoluteTracking),
                    self._panel,
                )
        alpha = self._alpha + (target - self._alpha) * WATCH_SMOOTHING
        if abs(target - alpha) < 0.01:
            alpha = target
        if alpha != self._alpha:
            self._set_alpha(alpha)

    def _set_alpha(self, alpha: float) -> None:
        self._alpha = alpha
        self._overlay.setOverlayAlpha(self._handle, alpha)

    def _controller_index(self) -> int | None:
        openvr = self._openvr
        anchor = self._settings["anchor"] if self._settings else "head"
        role = (
            openvr.TrackedControllerRole_RightHand
            if anchor == "rightHand"
            else openvr.TrackedControllerRole_LeftHand
        )
        device = self._system.getTrackedDeviceIndexForControllerRole(role)
        return None if device == openvr.k_unTrackedDeviceIndexInvalid else device

    def _update_anchor(self, matrix: Matrix34) -> None:
        openvr = self._openvr
        self._next_anchor_check = time.monotonic() + 1.0
        anchor = self._settings["anchor"] if self._settings else "head"
        device = (
            openvr.k_unTrackedDeviceIndex_Hmd if anchor == "head" else self._controller_index()
        )
        if device != self._device:
            self._device = device
            if device is not None:
                self._panel = matrix
                transform = openvr.HmdMatrix34_t()
                for row in range(3):
                    for column in range(4):
                        transform.m[row][column] = matrix[row][column]
                self._overlay.setOverlayTransformTrackedDeviceRelative(
                    self._handle, device, transform
                )
        self._update_visibility()

    def _update_visibility(self) -> None:
        visible = self._image is not None and self._device is not None
        if visible == self._visible:
            return
        if visible:
            self._overlay.showOverlay(self._handle)
        else:
            self._overlay.hideOverlay(self._handle)
        self._visible = visible


def _matrix_rows(matrix: Any) -> Matrix34:
    return [[float(matrix.m[row][column]) for column in range(4)] for row in range(3)]


class OverlayService:
    """Keeps one SteamVR caption overlay alive on a dedicated thread.

    The service never launches SteamVR. While enabled it waits for SteamVR,
    attaches when it appears, and detaches cleanly when SteamVR quits.
    """

    def __init__(
        self,
        *,
        settings: dict[str, Any],
        on_status: StatusHandler,
        on_calibration: CalibrationHandler | None = None,
        runtime_factory: RuntimeFactory = OpenVrRuntime,
        retry_interval: float = 5.0,
        # Watch mode fades with head and hand movement, so poll at ~30 Hz.
        poll_interval: float = 1 / 30,
    ) -> None:
        self._on_status = on_status
        self._on_calibration = on_calibration
        self._runtime_factory = runtime_factory
        self._retry_interval = retry_interval
        self._poll_interval = poll_interval
        self._condition = threading.Condition()
        self._settings = deepcopy(settings)
        self._image: str | None = None
        self._layout_dirty = True
        self._image_dirty = True
        # (prompt image, update only) waiting for the overlay thread.
        self._calibration_prompt: tuple[str, bool] | None = None
        self._closed = False
        self._status: tuple[str, str | None] = ("disabled", None)
        self._thread = threading.Thread(
            target=self._run, name="openvr-overlay", daemon=True
        )
        self._thread.start()

    @property
    def status(self) -> tuple[str, str | None]:
        with self._condition:
            return self._status

    def configure(self, settings: dict[str, Any]) -> None:
        with self._condition:
            self._settings = deepcopy(settings)
            self._layout_dirty = True
            self._condition.notify_all()

    def show_image(self, path: str) -> None:
        with self._condition:
            self._image = path
            self._image_dirty = True
            self._condition.notify_all()

    def hide(self) -> None:
        with self._condition:
            self._image = None
            self._image_dirty = True
            self._condition.notify_all()

    def calibrate(self, prompt_image: str, update: bool = False) -> bool:
        """Starts hand calibration, or with `update` only swaps its prompt.

        Returns False unless SteamVR is connected and the panel is on a hand.
        """
        with self._condition:
            if self._status[0] != "connected" or self._settings["anchor"] == "head":
                return False
            self._calibration_prompt = (prompt_image, update)
            self._condition.notify_all()
            return True

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=3.0)

    def _set_status(self, status: str, message: str | None = None) -> None:
        with self._condition:
            if self._status == (status, message):
                return
            self._status = (status, message)
        try:
            self._on_status(status, message)
        except Exception:
            logger.exception("Overlay status callback failed")

    def _report_calibration(
        self, result: tuple[str, list[float] | None, str | None] | None
    ) -> None:
        if result is None:
            return
        if result[0] != "running":
            with self._condition:
                # Restore the regular layout and captions after the prompt.
                self._layout_dirty = True
                self._image_dirty = True
        if self._on_calibration is None:
            return
        try:
            self._on_calibration(*result)
        except Exception:
            logger.exception("Overlay calibration callback failed")

    def _wait(self, timeout: float | None) -> None:
        with self._condition:
            if not self._closed:
                self._condition.wait(timeout)

    def _disconnect(self, runtime: OverlayRuntime) -> None:
        runtime.disconnect()
        self._report_calibration(runtime.take_calibration_result())

    def _run(self) -> None:
        runtime: OverlayRuntime | None = None
        connected = False
        try:
            while True:
                with self._condition:
                    if self._closed:
                        return
                    enabled = bool(self._settings["enabled"])
                if not enabled:
                    if connected and runtime is not None:
                        self._disconnect(runtime)
                        connected = False
                    self._set_status("disabled")
                    with self._condition:
                        while not self._closed and not self._settings["enabled"]:
                            self._condition.wait()
                    continue
                if not connected:
                    if runtime is None:
                        runtime = self._runtime_factory()
                    try:
                        connected = runtime.connect()
                    except OverlayUnavailable as exc:
                        self._set_status("unavailable", str(exc))
                        self._wait_for_settings_change()
                        continue
                    except Exception as exc:
                        logger.exception("OpenVR connection failed")
                        self._set_status("error", str(exc) or "OpenVR 連線失敗")
                        self._wait(self._retry_interval)
                        continue
                    if not connected:
                        self._set_status("waiting")
                        self._wait(self._retry_interval)
                        continue
                    with self._condition:
                        self._layout_dirty = True
                        self._image_dirty = True
                    self._set_status("connected")
                try:
                    self._sync(runtime)
                    alive = runtime.poll()
                    self._report_calibration(runtime.take_calibration_result())
                except Exception as exc:
                    logger.exception("OpenVR overlay update failed")
                    self._disconnect(runtime)
                    connected = False
                    self._set_status("error", str(exc) or "OpenVR overlay 更新失敗")
                    self._wait(self._retry_interval)
                    continue
                if not alive:
                    self._disconnect(runtime)
                    connected = False
                    self._set_status("waiting")
                    self._wait(self._retry_interval)
                    continue
                with self._condition:
                    if not (
                        self._closed
                        or self._layout_dirty
                        or self._image_dirty
                        or self._calibration_prompt
                    ):
                        self._condition.wait(self._poll_interval)
        finally:
            if connected and runtime is not None:
                try:
                    runtime.disconnect()
                except Exception:
                    logger.exception("OpenVR shutdown failed")

    def _wait_for_settings_change(self) -> None:
        with self._condition:
            self._layout_dirty = False
            while not self._closed and self._settings["enabled"] and not self._layout_dirty:
                self._condition.wait()

    def _sync(self, runtime: OverlayRuntime) -> None:
        with self._condition:
            request, self._calibration_prompt = self._calibration_prompt, None
            prompt, update = request if request else (None, False)
            if update:
                # A refreshed hint; stale once calibration has already ended.
                if runtime.calibrating:
                    runtime.update_calibration_prompt(prompt)
                return
            if prompt is None and runtime.calibrating:
                # Keep the prompt up; layout and captions resume afterwards.
                return
            layout = deepcopy(self._settings) if self._layout_dirty else None
            image_dirty = self._image_dirty
            image = self._image
            self._layout_dirty = False
            self._image_dirty = False
        if layout is not None:
            runtime.apply_layout(layout)
        if prompt is not None:
            runtime.start_calibration(prompt)
            return
        if image_dirty:
            runtime.set_image(image)
