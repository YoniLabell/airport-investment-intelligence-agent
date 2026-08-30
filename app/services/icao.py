"""IATA to ICAO station-code resolution.

AviationWeather.gov keys everything on ICAO identifiers, while this application
(and BTS) speaks IATA. The mapping is deterministic and lives in code, not in a
model's memory:

* Airports in the contiguous US take a ``K`` prefix on the IATA code
  (SFO -> KSFO, BOS -> KBOS). This rule is reliable for the airports with
  scheduled service that this project covers.
* Alaska (``PA``), Hawaii (``PH``), Guam (``PG``) and Puerto Rico / the US
  Virgin Islands (``TJ``/``TI``) do not follow that rule, so every such station
  is enumerated below.
* A four-letter code that is already ICAO is passed through unchanged, which
  makes the tool usable with either identifier.
"""

from __future__ import annotations

import re

#: Stations whose ICAO identifier is not simply ``K`` + IATA.
ICAO_OVERRIDES: dict[str, str] = {
    # --- Alaska ---------------------------------------------------------
    "ANC": "PANC",  # Ted Stevens Anchorage International
    "FAI": "PAFA",  # Fairbanks International
    "JNU": "PAJN",  # Juneau International
    "KTN": "PAKT",  # Ketchikan International
    "SIT": "PASI",  # Sitka Rocky Gutierrez
    "ADQ": "PADQ",  # Kodiak
    "CDV": "PACV",  # Cordova
    "DLG": "PADL",  # Dillingham
    "BET": "PABE",  # Bethel
    "OME": "PAOM",  # Nome
    "OTZ": "PAOT",  # Kotzebue
    "BRW": "PABR",  # Utqiagvik / Wiley Post-Will Rogers Memorial
    "SCC": "PASC",  # Deadhorse
    # --- Hawaii ---------------------------------------------------------
    "HNL": "PHNL",  # Daniel K. Inouye International
    "OGG": "PHOG",  # Kahului
    "KOA": "PHKO",  # Ellison Onizuka Kona International
    "LIH": "PHLI",  # Lihue
    "ITO": "PHTO",  # Hilo International
    # --- Territories ----------------------------------------------------
    "GUM": "PGUM",  # Antonio B. Won Pat International, Guam
    "SJU": "TJSJ",  # Luis Munoz Marin International, Puerto Rico
    "STT": "TIST",  # Cyril E. King, US Virgin Islands
}

_IATA_RE = re.compile(r"^[A-Z]{3}$")
_ICAO_RE = re.compile(r"^[A-Z]{4}[0-9]?$")


def resolve_icao(code: str) -> str | None:
    """Return the ICAO station id for ``code``, or ``None`` if it cannot be mapped.

    Accepts an IATA code (``SFO``), an ICAO code (``KSFO``), and any casing or
    surrounding whitespace.
    """
    token = str(code or "").strip().upper()
    if not token:
        return None
    if _ICAO_RE.match(token):
        return token
    if not _IATA_RE.match(token):
        return None
    return ICAO_OVERRIDES.get(token, f"K{token}")
