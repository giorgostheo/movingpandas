# -*- coding: utf-8 -*-

import pandas as pd
from shapely.geometry import Point, LineString, shape
from shapely.affinity import translate
from datetime import datetime, timedelta

from .time_range_utils import TemporalRange, SpatioTemporalRange


def _get_spatiotemporal_ref(row):
    """Returns the SpatioTemporalRange for the input row's spatial_intersection LineString
    by interpolating timestamps.
    """
    if type(row['spatial_intersection']) == LineString:
        pt0 = Point(row['spatial_intersection'].coords[0])
        ptn = Point(row['spatial_intersection'].coords[-1])
        t = row['prev_t']
        t_delta = row['t'] - t
        length = row['line'].length
        t0 = t + (t_delta * row['line'].project(pt0)/length)
        tn = t + (t_delta * row['line'].project(ptn)/length)
        # to avoid numerical issues with microseconds beyond six digits, we reconstruct the timestamps
        t0 = datetime(t0.year, t0.month, t0.day, t0.hour, t0.minute, t0.second, t0.microsecond)
        tn = datetime(tn.year, tn.month, tn.day, tn.hour, tn.minute, tn.second, tn.microsecond)
        # to avoid intersection issues with zero length lines
        if ptn == translate(pt0, 0.00000001, 0.00000001):
            t0 = row['prev_t']
            tn = row['t']
        # to avoid numerical issues with timestamps
        if is_equal(tn, row['t']):
            tn = row['t']
        if is_equal(t0, row['prev_t']):
            t0 = row['prev_t']
        return SpatioTemporalRange(pt0, ptn, t0, tn)
    else:
        return None


def _dissolve_ranges(ranges):
    """SpatioTemporalRanges that touch (i.e. the end of one equals the start of another) are dissolved (aka. merged)."""
    if len(ranges) == 0:
        raise ValueError("Nothing to dissolve (received empty ranges)!")
    dissolved_ranges = []
    new_range = None
    for r in ranges:
        if r is None:
            continue  # raise ValueError('Received range that is None!')
        if new_range is None:
            new_range = SpatioTemporalRange(r.pt_0, r.pt_n, r.t_0, r.t_n)
        elif new_range.t_n == r.t_0 or (r.t_0 > new_range.t_n and is_equal(r.t_0, new_range.t_n)):
            new_range.t_n = r.t_n
            new_range.pt_n = r.pt_n
        else:
            dissolved_ranges.append(new_range)
            new_range = SpatioTemporalRange(r.pt_0, r.pt_n, r.t_0, r.t_n)
    dissolved_ranges.append(new_range)
    return dissolved_ranges


def is_equal(t1, t2):
    """Similar timestamps are considered equal to avoid numerical issues."""
    if type(t2) == pd.Timestamp:
        td = abs(t1 - t2.tz_localize(t1.tzinfo).to_pydatetime())
    else:
        td = abs(t1 - t2)
    return td < timedelta(milliseconds=10)


def intersects(traj, polygon):
    try:
        line = traj.to_linestring()
    except:
        return False
    return line.intersects(polygon)


def create_entry_and_exit_points(traj, range):
    """Returns a dataframe with inserted entry and exit points according to the provided SpatioTemporalRange"""
    if type(range) != SpatioTemporalRange:
        raise TypeError("Input range has to be a SpatioTemporalRange!")
    # Create row at entry point with attributes from previous row = pad
    row0 = traj.df.iloc[traj.df.index.get_loc(range.t_0, method='pad')].copy()
    row0['geometry'] = range.pt_0
    # Create row at exit point
    rown = traj.df.iloc[traj.df.index.get_loc(range.t_n, method='pad')].copy()
    rown['geometry'] = range.pt_n
    # Insert rows
    temp_df = traj.df.copy()
    temp_df.loc[range.t_0] = row0
    temp_df.loc[range.t_n] = rown
    return temp_df.sort_index()


def _get_segments_for_ranges(traj, ranges):
    counter = 0
    segments = []  # list of trajectories
    for the_range in ranges:
        temp_traj = traj.copy()
        if type(the_range) == SpatioTemporalRange:
            temp_traj.df = create_entry_and_exit_points(traj, the_range)
        try:
            segment = temp_traj.get_segment_between(the_range.t_0, the_range.t_n)
        except ValueError as e:
            continue
        segment.id = "{}_{}".format(traj.id, counter)
        segment.parent = traj
        segments.append(segment)
        counter += 1
    return segments


def _determine_time_ranges_pointbased(traj, polygon):
    df = traj.df
    df['t'] = df.index
    df['intersects'] = df.intersects(polygon)
    df['segment'] = (df['intersects'].shift(1) != df['intersects']).astype(int).cumsum()
    df = df.groupby('segment', as_index=False).agg({'t': ['min', 'max'], 'intersects': ['min']})
    df.columns = df.columns.map('_'.join)

    ranges = []
    for index, row in df.iterrows():
        if row['intersects_min']:
            ranges.append(TemporalRange(row['t_min'], row['t_max']))
    return ranges


def _get_potentially_intersecting_lines(traj, polygon):
    """Uses a spatial index to determine which parts of the trajectory may be intersecting with the polygon"""
    line_df = traj._to_line_df()
    spatial_index = line_df.sindex
    if spatial_index:
        possible_matches_index = list(spatial_index.intersection(polygon.bounds))
        possible_matches = line_df.iloc[possible_matches_index].sort_index()
    else:
        possible_matches = line_df
    return possible_matches


def _determine_time_ranges_linebased(traj, polygon):
    """Returns list of SpatioTemporalRanges that describe trajectory intersections with the provided polygon."""
    # Note: If the trajectory contains consecutive rows without location change
    #       these will result in zero length lines that return an empty
    #       intersection.
    possible_matches = _get_potentially_intersecting_lines(traj, polygon)
    possible_matches['spatial_intersection'] = possible_matches.intersection(polygon)
    possible_matches['spatiotemporal_intersection'] = possible_matches.apply(_get_spatiotemporal_ref, axis=1)
    ranges = possible_matches['spatiotemporal_intersection']
    return _dissolve_ranges(ranges)


def clip(traj, polygon, pointbased=False):
    """Returns a list of trajectory segments clipped by the given feature."""
    if not intersects(traj, polygon):
        return []
    if pointbased:
        ranges = _determine_time_ranges_pointbased(traj, polygon)
    else:
        ranges = _determine_time_ranges_linebased(traj, polygon)
    return _get_segments_for_ranges(traj, ranges)


def _get_geometry_and_properties_from_feature(feature):
    """Provides convenience access to geometry and properties of a Shapely feature."""
    if type(feature) != dict:
        raise TypeError("Trajectories can only be intersected with a Shapely feature!")
    try:
        geometry = shape(feature['geometry'])
        properties = feature['properties']
    except:
        raise TypeError("Trajectories can only be intersected with a Shapely feature!")
    return geometry, properties


def intersection(traj, feature, pointbased=False):
    """Returns a list of trajectory segments that intersect the given feature.
    Resulting trajectories include the intersecting feature's attributes.
    """
    geometry, properties = _get_geometry_and_properties_from_feature(feature)
    clipped = clip(traj, geometry, pointbased)
    segments = []
    for clipping in clipped:
        for key, value in properties.items():
            clipping.df['intersecting_'+key] = value
        segments.append(clipping)
    return segments
