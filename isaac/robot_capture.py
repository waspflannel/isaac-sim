"""Capture actual simulator frames while the production clock is paused."""

import asyncio

import isaacsim.core.experimental.utils.app as app_utils
from omni.kit.viewport.utility import capture_viewport_to_file, get_active_viewport


def capture(app, path, camera, warmup=2):
    app_utils.pause()
    viewport = get_active_viewport()
    viewport.set_active_camera("/World/" + camera)
    for _ in range(warmup):
        app.update()
    saved = asyncio.ensure_future(capture_viewport_to_file(viewport, str(path)).wait_for_result())
    for _ in range(300):
        app.update()
        if saved.done():
            saved.result()
            break
    else:
        raise RuntimeError("Simulator frame capture did not finish")
    app_utils.play()


def capture_views(app, output):
    for camera in ("Camera", "RobotCamera", "ShippingCamera"):
        capture(app, output / f"{camera}.png", camera, warmup=12)
    get_active_viewport().set_active_camera("/World/Camera")
