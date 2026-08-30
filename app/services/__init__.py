"""Live operational context.

Everything in this package is **supplementary**: it describes conditions at an
airport *right now*. None of it feeds the analytics in :mod:`app.analytics` or
the Airport Expansion Score, which are built exclusively on historical US DOT /
BTS data. Keeping the two in separate packages is the point — a weather outage
must never be able to move an investment score.
"""

from app.services.aviation_weather import (
    AviationWeatherProvider,
    ConditionsStatus,
    get_weather_provider,
)

__all__ = ["AviationWeatherProvider", "ConditionsStatus", "get_weather_provider"]
