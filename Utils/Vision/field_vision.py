import math
import time
import ntcore
import config
from vision_estimator import VisionEstimator
from wpimath.geometry import Pose2d, Pose3d, Rotation2d, Translation2d, Translation3d
from Units.units import seconds
from wpilib import Timer
from SubsystemTemplates import drivetrain
from wpilib import RobotState, TimedRobot

#TODO fix class
def weighted_pose_average(
        robot_pose: Pose2d, vision_pose: Pose3d, robot_weight: float, vision_weight: float
) -> Pose2d:
    """
    Returns a weighted average of two poses.
    :param robot_pose: Pose of the robot.
    :type robot_pose: Pose2d
    :param vision_pose: Pose of the limelight.
    :type vision_pose: Pose3d
    :param robot_weight: Weight of the robot pose.
    :type robot_weight: float
    :param vision_weight: Weight of the limelight pose.
    :type vision_weight: float
    :return: Weighted average of the two poses.
    :rtype: Pose2d
    """

    vision_pose = vision_pose.toPose2d()

    return Pose2d(
        Translation2d(
            (
                    robot_pose.translation().X() * robot_weight
                    + vision_pose.translation().X() * vision_weight
            ),
            (
                    robot_pose.translation().Y() * robot_weight
                    + vision_pose.translation().Y() * vision_weight
            ),
        ),
        Rotation2d(
            robot_pose.rotation().radians() * robot_weight
            + vision_pose.rotation().radians() * vision_weight
        ),
    )


class FieldOdometry:
    """
    Keeps track of robot position relative to field using a vision estimator (e.g. limelight, photon-vision)
    """

    def __init__(
            self, drivetrain: drivetrain, vision_estimator: VisionEstimator | None, field_width: float,
            field_length: float
    ):
        self.drivetrain: drivetrain = drivetrain
        self.table = ntcore.NetworkTableInstance.getDefault().getTable("Odometry")
        self.last_update_time: seconds | None = None
        self.min_update_wait_time: seconds = (
            0.05  # time to wait before checking for pose update
        )

        self.vision_estimator: VisionEstimator = vision_estimator
        self.vision_estimator_pose_weight: float = 0.4
        self.robot_pose_weight: float = 1 - self.vision_estimator_pose_weight
        self.degree_thres = 10
        self.std_dev: tuple[float, float, float] = (0.5, 0.5, 0.5)
        self.field_width = field_width
        self.field_length = field_length
        self.last_pose: Pose2d = Pose2d(0, 0, 0)

        self.shooting: bool = False

        self.dist_thres = 1.0

        self.vision_on = True

        self.std_formula = lambda x: abs(x ** 2) / 2.5

        self.use_speaker_tags: bool = False

        self.vision_poses: list[Pose3d] = []

    def enable(self):
        self.vision_on = True

    def disable(self):
        self.vision_on = False

    def enable_speaker_tags(self):
        self.use_speaker_tags = True

    def disable_speaker_tags(self):
        self.use_speaker_tags = False

    def enable_shooting(self):
        self.shooting = True

    def disable_shooting(self):
        self.shooting = False

    def update(self) -> Pose2d:
        """
        Updates the robot's pose relative to the field. This should be called periodically.
        """

        self.update_from_internal()
        # self.vision_estimator.set_orientations()

        if not self.pose_within_field(self.getPose()):
            self.keep_pose_in_field()

        #if self.potential_crash():
           # self.hold_pose()

        if not self.vision_on:
            return self.getPose()

        vision_robot_pose_list = self.get_vision_poses()

        if vision_robot_pose_list is None:
            return self.getPose()

        self.vision_poses = []

        for vision_pose in vision_robot_pose_list:
            if vision_pose is None:
                continue

            vision_time: float
            vision_robot_pose: Pose3d

            vision_robot_pose, vision_time, tag_count, distance_to_target, tag_area, tag_id, megatag2 = vision_pose

            self.vision_poses += [vision_robot_pose]
            # vision_robot_pose, vision_time = pose_data
            # distance_to_target = target_pose.translation()

            #self.add_vision_measure(vision_robot_pose, vision_time, distance_to_target, int(tag_count), tag_area, tag_id,
                                #    megatag2)

        # self.update_tables()

        self.last_pose = self.getPose()

        return self.getPose()

    def pose_within_field(self, pose: Pose2d):
        x_within = 0 <= pose.translation().X() <= self.field_length
        y_within = 0 <= pose.translation().Y() <= self.field_width
        return x_within and y_within

    # noinspection PyTypeChecker
    def keep_pose_in_field(self):
        pose = self.getPose()

        new_x = max(0, min(pose.translation().X(), self.field_length))
        new_y = max(0, min(pose.translation().Y(), self.field_width))
        new_pose = Pose2d(new_x, new_y, pose.rotation())
        self.resetOdometry(new_pose)
    #TODO fix pls
    """
    
    def potential_crash(self):
        
        if not config.odometry_crash_detection_enabled:
            return False
        accel_x = self.drivetrain.gyro.get_y_accel()
        accel_y = self.drivetrain.gyro.get_x_accel()

        rvx = self.drivetrain.chassis_speeds.vx
        rvy = self.drivetrain.chassis_speeds.vy
        if (
                abs(accel_x) > config.odometry_crash_accel_threshold
                and
                (abs(rvx - accel_x) > abs(rvx))  # if the robot is slowing down and the drivetrain is still moving
                or
                abs(accel_y) > config.odometry_crash_accel_threshold
                and
                (abs(rvy - accel_y) > abs(rvy))  # if the robot is slowing down and the drivetrain is still moving
        ):
            return True
        return False"""

    def hold_pose(self):
        self.resetOdometry(self.getPose())

    def set_std_auto(self):
        #TODO fix
        #self.std_formula = config.odometry_std_auto_formula
        pass

    def set_std_tele(self):
        #TODO fix
        #self.std_formula = config.odometry_std_tele_formula
        pass

    def within_est_pos(self, vision: Pose3d):
        est_pose = self.drivetrain.odometry_estimator.getEstimatedPosition()
        dist = est_pose.translation().distance(vision.toPose2d().translation())
        if dist <= self.dist_thres:
            return True
        return False

    def within_est_rotation(self, vision: Pose3d):
        angle_diff = (
                math.degrees(
                    vision.toPose2d().rotation().radians()
                    - self.drivetrain.gyro.get_robot_heading()
                )
                % 360
        )

        if abs(angle_diff) < self.degree_thres:
            return True
        return False

    def update_from_internal(self):

        self.drivetrain.odometry_estimator.updateWithTime(
            Timer.getFPGATimestamp(),
            self.drivetrain.get_heading(),
            self.drivetrain.node_positions,
        )

        # self.drivetrain.odometry.update(
        #     self.drivetrain.get_heading(), self.drivetrain.node_positions
        # )
    """
    def add_vision_measure(self, vision_pose: Pose3d, vision_time: float, distance_to_target: float, tag_count: int,
                           tag_area: float, tag_id: float, megatag2: bool = False):
        if not self.pose_within_field(vision_pose.toPose2d()):
            return
        distance_deviation = self.getPose().translation().distance(vision_pose.toPose2d().translation())

        gyro_rate = abs(self.drivetrain.gyro.get_robot_heading_rate()) / math.radians(720)

        # std_dev_omega = abs(math.radians(120) + gyro_rate * 4000)
        std_dev_omega = abs(math.radians(44))

        std_dev = 0.7  # + gyro_rate

        # print(gyro_rate)
        if tag_count == 0:
            return
        # std_dev_omega = 9999999
        if tag_count < 2:
            if distance_to_target > config.odometry_tag_distance_threshold:
                return
            if tag_area < config.odometry_tag_area_threshold:
                return
            if distance_deviation > config.odometry_distance_deviation_threshold:
                return
            # std_dev = 1.4
            # std_dev_omega = abs(math.radians(14))
        if tag_count == 2:
            std_dev = 0.5
            if distance_to_target > config.odometry_two_tag_distance_threshold:
                return

       def compensate_speaker(deviation):

            return deviation + distance_to_target / (config.odometry_two_tag_distance_threshold * 2)

        using_speaker_tags = False
        if self.use_speaker_tags:
            if config.active_team == config.Team.RED:
                if tag_id == 3 or tag_id == 4:
                    std_dev = compensate_speaker(0.2) if tag_count > 1 else compensate_speaker(0.5)
                    using_speaker_tags = True
            elif config.active_team == config.Team.BLUE:
                if tag_id == 7 or tag_id == 8:
                    std_dev = compensate_speaker(0.2) if tag_count > 1 else compensate_speaker(0.5)
                    using_speaker_tags = True
        if self.shooting:
            std_dev_omega = math.radians(9)
            if tag_count < 2:
                return
            if using_speaker_tags == False and tag_count < 3:
                std_dev = 1.5

        dist_calculations = (std_dev, std_dev, std_dev_omega)
        self.std_dev = dist_calculations

        final_pose = Pose2d(vision_pose.toPose2d().translation(), self.drivetrain.get_heading())

        self.drivetrain.odometry_estimator.addVisionMeasurement(
            final_pose, vision_time, self.std_dev
        )
        """#TODO dont need this function but could be useful

    def get_vision_poses(self):
        vision_robot_pose_list: list[tuple[Pose3d, float, float, float, float, float, bool]] | None
        try:

            vision_robot_pose_list = (
                self.vision_estimator.get_estimated_robot_pose()
                if self.vision_estimator
                else None
            )
        except Exception as e:
            vision_robot_pose_list = None
            # print(e)
        return vision_robot_pose_list

    def getPose(self) -> Pose2d:
        """
        Returns the robot's pose relative to the field.
        :return: Robot pose.
        :rtype: Pose2d
        """
        # return self.drivetrain.odometry.getPose()
        est_pose = self.drivetrain.odometry_estimator.getEstimatedPosition()
        if not self.vision_on or TimedRobot.isSimulation():
            est_pose = self.drivetrain.odometry.getPose()
        else:
            self.drivetrain.odometry.resetPosition(
                self.drivetrain.get_heading(),
                self.drivetrain.node_positions,
                est_pose
            )

        return est_pose

    def send_vision_poses(self):

        vision_array = []

        for pose in self.vision_poses:
            vision_array += [
                pose.X(),
                pose.Y(),
                pose.rotation().toRotation2d().radians()
            ]

        self.table.putNumberArray(
            'Vision Poses', vision_array
        )

    def update_tables(self):

        est_pose = self.getPose()

        self.table.putNumberArray('Estimated Pose', [
            est_pose.translation().X(),
            est_pose.translation().Y(),
            est_pose.rotation().radians()
        ])

        self.table.putNumber(
            'Estimated Rotation',
            est_pose.rotation().degrees()
        )

        n_states = self.drivetrain.node_states

        self.table.putNumberArray('Node States', [
            n_states[0].angle.radians(), n_states[0].speed,
            n_states[1].angle.radians(), n_states[1].speed,
            n_states[2].angle.radians(), n_states[2].speed,
            n_states[3].angle.radians(), n_states[3].speed
        ])

        speeds = self.drivetrain.chassis_speeds

        self.table.putNumberArray('Velocity Robot', [
            speeds.vx,
            speeds.vy,
            speeds.omega
        ])

        self.send_vision_poses()

        # speeds_field = speeds.fromRobotRelativeSpeeds(speeds, self.drivetrain.get_heading())

        # self.table.putNumberArray('Velocity Field', [
        #     speeds_field.vx,
        #     speeds_field.vy,
        #     speeds_field.omega
        # ])

        self.table.putNumber('Robot Heading Degrees', self.drivetrain.get_heading().degrees())

        self.table.putNumberArray('Abs value',
                                  self.drivetrain.get_abs())

        self.table.putNumberArray('standard deviation', [
            *self.std_dev
        ])

        self.table.putBoolean('drivetrain ready to shoot',
                              self.drivetrain.ready_to_shoot
                              )

        self.table.putBoolean('ready to shoot', self.drivetrain.ready_to_shoot)

        def bound_angle(degrees: float):
            degrees = degrees % 360
            if degrees > 180:
                degrees -= 360
            if degrees < -180:
                degrees += 360
            return degrees

        self.table.putNumber(
            'estimated rotation',
            math.degrees(bound_angle(self.drivetrain.odometry_estimator.getEstimatedPosition().rotation().degrees()))
        )

        self.table.putNumber(
            'accel x',
            self.drivetrain.gyro.get_y_accel()
        )

        self.table.putNumber(
            'velocity x',
            speeds.vx
        )

    def resetOdometry(self, pose: Pose2d):
        """
        Resets the robot's odometry.

        :param pose: Pose of the robot to reset to.
        :type pose: Pose2d
        """
        self.drivetrain.reset_odometry(pose)