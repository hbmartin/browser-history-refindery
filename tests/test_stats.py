"""Derived counters on RunStats."""

from browser_history_refindery.stats import ProfileStats, RunStats


def test_processed_and_skipped_locally():
    stats = RunStats(
        submitted=7,
        errors=3,
        skipped=2,
        already_submitted=4,
        previously_rejected=1,
    )
    assert stats.processed == 10
    assert stats.skipped_locally == 7


def test_throughput_zero_before_any_processing():
    assert RunStats().throughput == 0.0


def test_elapsed_is_nonnegative():
    assert RunStats().elapsed_seconds >= 0.0


def test_add_event_bounded_to_deque_maxlen():
    stats = RunStats()
    for index in range(50):
        stats.add_event(f"event {index}")
    assert len(stats.events) == 15  # deque maxlen
    assert "event 49" in stats.events[-1]


def test_profile_stats_defaults():
    profile = ProfileStats(label="Chrome — Default")
    assert profile.urls_read == 0
    assert profile.done is False
