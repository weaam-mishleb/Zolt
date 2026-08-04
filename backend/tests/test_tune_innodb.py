"""Tests for raising the InnoDB buffer pool before a load.

WHY THIS EXISTS
---------------
The managed instance ships with a 1 GB buffer pool on a 24 GB container, against
a 1,619 MB working set. That single number is what took upserts down to
~300 rows/s and got the big chains cancelled at the job timeout.

The variable is dynamic, but two details make a naive `SET GLOBAL` misleading:
InnoDB only accepts whole multiples of (chunk_size x instances) and silently
rounds anything else, and the resize runs in the BACKGROUND — so a load can
start into a pool that is still growing.
"""
from __future__ import annotations

from scripts.tune_innodb import DEFAULT_TARGET_GIB, GIB, valid_target

MB = 1024**2


def test_a_valid_target_is_returned_unchanged():
    # 8 GiB against 128 MB chunks x 4 instances (a 512 MB granule) divides evenly
    assert valid_target(8 * GIB, 128 * MB, 4, 1024 * MB) == 8 * GIB


def test_a_target_is_rounded_DOWN_to_a_whole_granule():
    """MySQL rounds a bad value silently. Computing it here is what keeps the
    log honest instead of merely optimistic."""
    granule = 128 * MB * 4                       # 512 MB
    assert valid_target(5 * GIB + 100 * MB, 128 * MB, 4, 1024 * MB) % granule == 0
    assert valid_target(5 * GIB + 100 * MB, 128 * MB, 4, 1024 * MB) == 5 * GIB


def test_an_already_larger_pool_is_never_shrunk():
    """Shrinking would evict exactly the pages this exists to keep resident."""
    assert valid_target(8 * GIB, 128 * MB, 4, 16 * GIB) is None


def test_an_equal_pool_is_left_alone():
    assert valid_target(8 * GIB, 128 * MB, 4, 8 * GIB) is None


def test_a_target_below_one_granule_is_declined_rather_than_rounded_to_zero():
    """Rounding down must never produce a SET GLOBAL of 0."""
    assert valid_target(100 * MB, 128 * MB, 4, 1024 * MB) is None


def test_a_single_instance_host_uses_the_chunk_size_as_the_granule():
    assert valid_target(1 * GIB, 128 * MB, 1, 512 * MB) == 1 * GIB


def test_zero_instances_does_not_divide_by_zero():
    """Defensive: a host reporting 0 instances must not crash the whole plan job."""
    assert valid_target(1 * GIB, 128 * MB, 0, 512 * MB) == 1 * GIB


def test_the_default_target_fits_the_observed_container():
    """24 GB of RAM, and the pool is only part of mysqld's footprint. 8 GiB
    covers the 1,619 MB working set many times over with room to grow."""
    assert 2 <= DEFAULT_TARGET_GIB <= 16
