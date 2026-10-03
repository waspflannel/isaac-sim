# Controller composition adapted from NVIDIA's pick_place.py example.
# Copyright (c) 2021-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# License copy: licenses/NVIDIA-example-Apache-2.0.txt
# Modified to bind authored cells, queue production work, and verify handoffs.

"""A production handoff completes only after the robot places the real rigid body.

Uses Isaac 6.1's ManipulationScenario and composable pick/place controllers.
The controller composition follows NVIDIA's Apache-2.0 pick_place example:
standalone_examples/api/isaacsim.robot_motion.examples/manipulation/pick_place.py
"""

from collections import deque

import isaacsim.robot_motion.experimental.motion_generation as mg
import numpy as np
import warp as wp
from isaacsim.core.experimental.prims import RigidPrim
from isaacsim.robot.surface_gripper import _surface_gripper
from isaacsim.robot_motion.cumotion import RmpFlowController, load_cumotion_supported_robot
from isaacsim.robot_motion.examples.manipulation import (
    GripperCommand,
    ManipulationScenario,
    PickPlaceController,
    PickPlacePhase,
    SurfaceGripperController,
)


class RobotCell:
    def __init__(self, env, path, *, exclude=()):
        self.env = env
        self.path = path
        self.scenario = ManipulationScenario("ur10", robot_prim_path=path + "/Robot")
        robot = self.scenario.bind_existing_robot()
        config = self.scenario.robot_config
        defaults = dict(config.default_joint_positions)
        robot.set_default_state(
            dof_positions=[defaults[name] for name in robot.dof_names],
            dof_velocities=np.zeros(robot.num_dofs),
        )
        self.scenario.initialize_world_binding(exclude_prim_paths=exclude)
        self.gripper_path = path + "/Robot/" + config.gripper.relative_path
        arm = RmpFlowController(
            cumotion_robot=load_cumotion_supported_robot("ur10"),
            cumotion_world_interface=self.scenario.world_interface,
            robot_joint_space=self.scenario.joint_space,
            robot_site_space=self.scenario.site_space,
            tool_frame=config.tool.controller_frame,
        )
        self.controller = PickPlaceController(
            arm_controller=arm,
            gripper_open_controller=SurfaceGripperController(
                gripper_path=self.gripper_path, command=GripperCommand.OPEN
            ),
            gripper_close_controller=SurfaceGripperController(
                gripper_path=self.gripper_path, command=GripperCommand.CLOSE
            ),
            robot_site_space=self.scenario.site_space,
            tool_frame=config.tool.controller_frame,
            controller_to_grasp_position=config.tool.controller_to_grasp_position,
            controller_to_grasp_orientation=config.tool.controller_to_grasp_orientation,
            grasp_orientation=config.grasp_orientation,
            approach_height=0.30,
            position_tolerance=0.025,
            # Match the SDK's surface-gripper task tolerance under contact load.
            grasp_position_tolerance=0.025,
            phase_timeouts={PickPlacePhase.GRASP: 3.0},
        )
        self.waiting = deque()
        self.job = None
        self.completed = 0
        self.attachments = 0
        self.history = deque(maxlen=128)
        self.gripper = _surface_gripper.acquire_surface_gripper_interface()

    def move(self, item_path, destination, half_height):
        """Queue a physical pick/place and return its SimPy completion event."""
        done = self.env.event()
        self.waiting.append((item_path, destination, half_height, done))
        return done

    def start_next(self):
        path, self.destination, half_height, self.job = self.waiting.popleft()
        self.item_path = path
        self.body = RigidPrim(path)
        pick = self.body.get_world_poses()[0].numpy()[0]
        self.start_height = float(pick[2])
        self.max_height = self.start_height
        targets = np.array([pick, self.destination], dtype=np.float32)
        targets[:, 2] += half_height
        names = [PickPlaceController.PICK_SITE, PickPlaceController.PLACE_SITE]
        self.goal = mg.RobotState(
            sites=mg.SpatialState.from_name(
                spatial_space=names,
                positions=(names, wp.array(targets, dtype=wp.float32, device="cpu")),
            )
        )
        self.elapsed = 0.0
        self.stable_time = 0.0
        self.attached = False
        self.scenario.sync_world()
        if not self.controller.reset(self.scenario.read_robot_state(), self.goal, t=0):
            raise RuntimeError(f"{self.path}: {self.controller.failure_reason}")

    def step(self, dt):
        if self.job is None:
            if not self.waiting:
                return
            self.start_next()
        self.elapsed += dt
        position = self.body.get_world_poses()[0].numpy()[0]
        self.max_height = max(self.max_height, float(position[2]))
        if self.elapsed > 60:
            raise TimeoutError(f"{self.path}: handling {self.item_path} timed out")
        if not self.controller.is_done:
            self.scenario.sync_world()
            phase = self.controller.phase
            desired = self.controller.forward(
                self.scenario.read_robot_state(), self.goal, self.elapsed
            )
            if desired is None or self.controller.failed:
                raise RuntimeError(
                    f"{self.path}: {self.controller.failure_reason}; "
                    f"item={position}; destination={self.destination}"
                )
            self.scenario.apply_robot_state(desired)
            if phase is not self.controller.phase and self.controller.phase is PickPlacePhase.LIFT:
                self.attached = self.item_path in self.gripper.get_gripped_objects(
                    self.gripper_path
                )
                if not self.attached:
                    raise RuntimeError(f"{self.path}: gripper missed {self.item_path}")
                self.attachments += 1
        else:
            linear, angular = self.body.get_velocities()
            settled = (
                np.linalg.norm(position[:2] - self.destination[:2]) < 0.05
                and abs(position[2] - self.destination[2]) < 0.03
                and np.linalg.norm(linear.numpy()[0]) < 0.05
                and np.linalg.norm(angular.numpy()[0]) < 0.5
            )
            self.stable_time = self.stable_time + dt if settled else 0.0
            if self.stable_time >= 0.5:
                if not self.attached or self.max_height < self.start_height + 0.05:
                    raise RuntimeError(f"{self.path}: object did not pass physical lift gate")
                if self.gripper.get_gripped_objects(self.gripper_path):
                    raise RuntimeError(f"{self.path}: gripper did not release the object")
                self.history.append(
                    {
                        "item": self.item_path,
                        "seconds": round(self.elapsed, 3),
                        "lift_meters": round(self.max_height - self.start_height, 3),
                        "placement_error_meters": round(
                            float(np.linalg.norm(position - self.destination)), 4
                        ),
                    }
                )
                self.completed += 1
                self.job.succeed()
                self.job = None

    def snapshot(self):
        phase = "IDLE"
        if self.job:
            phase = "SETTLING" if self.controller.is_done else self.controller.phase.name
        return {
            "completed": self.completed,
            "attachments": self.attachments,
            "queued": len(self.waiting),
            "phase": phase,
        }
