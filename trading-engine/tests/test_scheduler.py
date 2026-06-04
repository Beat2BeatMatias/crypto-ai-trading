"""Verifica que el order_tracker se registra con el intervalo correcto."""
from __future__ import annotations
from unittest.mock import MagicMock, patch
import pytest
from scheduler import EngineScheduler


def test_order_tracker_registered_at_10s():
    sched = EngineScheduler()

    with patch.object(sched._scheduler, "add_job") as mock_add_job:
        sched.add_order_tracker(lambda: None, seconds=10)

        assert mock_add_job.called, "add_job should have been called"
        call_args = mock_add_job.call_args

        # El segundo argumento posicional es el IntervalTrigger
        trigger = call_args.args[1]
        assert trigger.interval.total_seconds() == 10, (
            f"Expected interval of 10s, got {trigger.interval.total_seconds()}s"
        )

        # El id del job debe ser "order_tracker"
        assert call_args.kwargs.get("id") == "order_tracker", (
            f"Expected job id='order_tracker', got: {call_args.kwargs}"
        )


def test_update_decisor_interval_reschedules_job():
    sched = EngineScheduler()
    fn = lambda: None

    with patch.object(sched._scheduler, "add_job") as mock_add_job, \
         patch.object(sched._scheduler, "reschedule_job") as mock_reschedule:
        sched.add_decisor(fn, interval_min=5)
        assert sched._decisor_interval_min == 5

        changed = sched.update_decisor_interval(15)

        assert changed is True
        assert sched._decisor_interval_min == 15
        mock_reschedule.assert_called_once()
        assert mock_reschedule.call_args.args[0] == "decisor"
        trigger = mock_reschedule.call_args.kwargs["trigger"]
        assert trigger.interval.total_seconds() == 15 * 60


def test_update_decisor_interval_noop_when_unchanged():
    sched = EngineScheduler()

    with patch.object(sched._scheduler, "add_job"), \
         patch.object(sched._scheduler, "reschedule_job") as mock_reschedule:
        sched.add_decisor(lambda: None, interval_min=15)

        changed = sched.update_decisor_interval(15)

        assert changed is False
        mock_reschedule.assert_not_called()


def test_main_uses_10s_for_order_tracker():
    import pathlib
    main_text = (pathlib.Path(__file__).parent.parent / "main.py").read_text()
    # Verificar que no queda la versión vieja de 30s para order_tracker
    assert "add_order_tracker(order_tracker_tick, seconds=30)" not in main_text, (
        "main.py still uses seconds=30 for order_tracker — should be 10"
    )
    assert "add_order_tracker(order_tracker_tick, seconds=10)" in main_text, (
        "main.py does not have add_order_tracker(order_tracker_tick, seconds=10)"
    )
