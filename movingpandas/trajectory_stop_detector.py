# -*- coding: utf-8 -*-

from .trajectory import Trajectory
from .trajectory_collection import TrajectoryCollection
from .geometry_utils import mrr_diagonal
from .trajectory_utils import convert_time_ranges_to_segments
from .time_range_utils import TemporalRangeWithTrajId


class TrajectoryStopDetector:
    """
    Detects stops in a trajectory.
    A stop is detected if the movement stays within an area of specified size for at least the specified duration.
    """
    def __init__(self, traj):
        """
        Create StopDetector

        Parameters
        ----------
        traj : Trajectory
        """
        self.traj = traj

    def get_stop_time_ranges(self, max_diameter, min_duration):
        """
        Returns detected stop start and end times

        Parameters
        ----------
        max_diameter : float
            Maximum diameter for stop detection
        min_duration : datetime.timedelta
            Minimum stop duration

        Returns
        -------
        list
            TemporalRanges of detected stops
        """
        if isinstance(self.traj, Trajectory):
            return self._process_traj(self.traj, max_diameter, min_duration)
        elif isinstance(self.traj, TrajectoryCollection):
            return self._process_traj_collection(max_diameter, min_duration)
        else:
            raise TypeError

    def _process_traj_collection(self, max_diameter, min_duration):
        result = []
        for traj in self.traj:
            for time_range in self._process_traj(traj, max_diameter, min_duration):
                result.append(time_range)
        return result

    def _process_traj(self, traj, max_diameter, min_duration):
        detected_stops = []
        segment_geoms = []
        segment_times = []
        is_stopped = False
        previously_stopped = False
        geom_column_name = traj.get_geom_column_name()

        for index, row in traj.df.iterrows():
            segment_geoms.append(row[geom_column_name])
            segment_times.append(index)

            if not is_stopped:  # remove points to the specified min_duration
                while len(segment_geoms) > 2 and segment_times[-1] - segment_times[0] >= min_duration:
                    segment_geoms.pop(0)
                    segment_times.pop(0)

            if len(segment_geoms) > 1 and mrr_diagonal(segment_geoms, traj.is_latlon) < max_diameter:
                is_stopped = True
            else:
                is_stopped = False

            if len(segment_geoms) > 1:
                segment_end = segment_times[-2]
                segment_begin = segment_times[0]
                if not is_stopped and previously_stopped:
                    if segment_end - segment_begin >= min_duration:  # detected end of a stop
                        detected_stops.append(TemporalRangeWithTrajId(segment_begin, segment_end, traj.id))
                        segment_geoms = []
                        segment_times = []

            previously_stopped = is_stopped

        if is_stopped and segment_times[-1] - segment_times[0] >= min_duration:
            detected_stops.append(TemporalRangeWithTrajId(segment_times[0], segment_times[-1], traj.id))

        return detected_stops

    def get_stop_segments(self, max_diameter, min_duration):
        """
        Returns detected stop trajectory segments

        Parameters
        ----------
        max_diameter : float
            Maximum diameter for stop detection
        min_duration : datetime.timedelta
            Minimum stop duration

        Returns
        -------
        list
            Trajectory segments
        """
        stop_time_ranges = self.get_stop_time_ranges(max_diameter, min_duration)
        return TrajectoryCollection(convert_time_ranges_to_segments(self.traj, stop_time_ranges))
