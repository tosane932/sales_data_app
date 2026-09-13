import datetime
from types import SimpleNamespace

import pytest

import app as app_module


@pytest.mark.parametrize(
    ("utc_now", "expected_business_date"),
    [
        (
            datetime.datetime(
                2026,
                9,
                11,
                15,
                30,
                tzinfo=datetime.timezone.utc,
            ),
            datetime.date(2026, 9, 12),
        ),
        (
            datetime.datetime(
                2026,
                9,
                30,
                15,
                30,
                tzinfo=datetime.timezone.utc,
            ),
            datetime.date(2026, 10, 1),
        ),
        (
            datetime.datetime(
                2026,
                12,
                31,
                15,
                30,
                tzinfo=datetime.timezone.utc,
            ),
            datetime.date(2027, 1, 1),
        ),
    ],
)
def test_business_today_uses_jst_at_utc_date_boundary(
    monkeypatch,
    utc_now,
    expected_business_date,
):
    real_datetime = datetime.datetime

    class FrozenDateTime(real_datetime):
        @classmethod
        def now(cls, timezone=None):
            assert timezone == app_module.BUSINESS_TIMEZONE
            return utc_now.astimezone(timezone)

    monkeypatch.setattr(
        app_module,
        "datetime",
        SimpleNamespace(datetime=FrozenDateTime),
    )

    assert app_module.business_today() == expected_business_date
