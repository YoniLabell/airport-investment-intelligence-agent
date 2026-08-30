"""Generate the bundled demo dataset for the Airport Investment Intelligence Agent.

This script is the *provenance record* for everything in ``app/data/seed/``.
Re-running it regenerates the CSVs byte-for-byte (there is no randomness).

IMPORTANT / HONESTY NOTE
------------------------
The bundled dataset is a **DEMO** dataset. Airport-level totals are rounded
approximations of publicly reported FAA/BTS enplanement and operations figures;
the route table is a *synthesized* stand-in for the structure of a BTS T-100
segment extract. It exists so the app is demoable without network access.
It is never presented to the user as live data (see ``DataStatus``).

Run with:  python scripts/generate_seed_data.py
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

SEED_DIR = Path(__file__).resolve().parents[1] / "app" / "data" / "seed"
YEARS = [2022, 2023, 2024]
BASE_YEAR = YEARS[-1]

# --------------------------------------------------------------------------
# Coordinates for every route endpoint (US airports + international).
# --------------------------------------------------------------------------
COORDS: dict[str, tuple[float, float, str, str]] = {
    # iata: (lat, lon, name, country)
    "BOS": (42.3656, -71.0096, "Boston Logan International", "US"),
    "PVD": (41.7240, -71.4283, "Rhode Island T.F. Green International", "US"),
    "BDL": (41.9389, -72.6832, "Bradley International", "US"),
    "MHT": (42.9326, -71.4357, "Manchester-Boston Regional", "US"),
    "PWM": (43.6462, -70.3093, "Portland International Jetport", "US"),
    "BTV": (44.4720, -73.1533, "Burlington International", "US"),
    "JFK": (40.6413, -73.7781, "John F. Kennedy International", "US"),
    "EWR": (40.6895, -74.1745, "Newark Liberty International", "US"),
    "LGA": (40.7769, -73.8740, "LaGuardia", "US"),
    "PHL": (39.8729, -75.2437, "Philadelphia International", "US"),
    "BWI": (39.1754, -76.6683, "Baltimore/Washington International", "US"),
    "DCA": (38.8512, -77.0402, "Ronald Reagan Washington National", "US"),
    "IAD": (38.9531, -77.4565, "Washington Dulles International", "US"),
    "ALB": (42.7483, -73.8017, "Albany International", "US"),
    "BUF": (42.9405, -78.7322, "Buffalo Niagara International", "US"),
    "PIT": (40.4915, -80.2329, "Pittsburgh International", "US"),
    "ATL": (33.6407, -84.4277, "Hartsfield-Jackson Atlanta International", "US"),
    "CLT": (35.2144, -80.9473, "Charlotte Douglas International", "US"),
    "MCO": (28.4312, -81.3081, "Orlando International", "US"),
    "MIA": (25.7959, -80.2871, "Miami International", "US"),
    "FLL": (26.0742, -80.1506, "Fort Lauderdale-Hollywood International", "US"),
    "TPA": (27.9755, -82.5332, "Tampa International", "US"),
    "RDU": (35.8801, -78.7880, "Raleigh-Durham International", "US"),
    "BNA": (36.1263, -86.6774, "Nashville International", "US"),
    "RSW": (26.5362, -81.7552, "Southwest Florida International", "US"),
    "JAX": (30.4941, -81.6879, "Jacksonville International", "US"),
    "SAV": (32.1276, -81.2021, "Savannah/Hilton Head International", "US"),
    "ORD": (41.9742, -87.9073, "Chicago O'Hare International", "US"),
    "MDW": (41.7868, -87.7522, "Chicago Midway International", "US"),
    "DTW": (42.2162, -83.3554, "Detroit Metropolitan Wayne County", "US"),
    "MSP": (44.8848, -93.2223, "Minneapolis-St Paul International", "US"),
    "STL": (38.7487, -90.3700, "St. Louis Lambert International", "US"),
    "MCI": (39.2976, -94.7139, "Kansas City International", "US"),
    "CLE": (41.4117, -81.8498, "Cleveland Hopkins International", "US"),
    "CVG": (39.0489, -84.6678, "Cincinnati/Northern Kentucky International", "US"),
    "IND": (39.7169, -86.2956, "Indianapolis International", "US"),
    "DFW": (32.8998, -97.0403, "Dallas/Fort Worth International", "US"),
    "DAL": (32.8471, -96.8518, "Dallas Love Field", "US"),
    "IAH": (29.9902, -95.3368, "George Bush Intercontinental", "US"),
    "HOU": (29.6454, -95.2789, "William P. Hobby", "US"),
    "AUS": (30.1975, -97.6664, "Austin-Bergstrom International", "US"),
    "MSY": (29.9934, -90.2580, "Louis Armstrong New Orleans International", "US"),
    "SAT": (29.5337, -98.4698, "San Antonio International", "US"),
    "DEN": (39.8561, -104.6737, "Denver International", "US"),
    "PHX": (33.4342, -112.0116, "Phoenix Sky Harbor International", "US"),
    "LAS": (36.0840, -115.1537, "Harry Reid International", "US"),
    "SLC": (40.7899, -111.9791, "Salt Lake City International", "US"),
    "ABQ": (35.0402, -106.6091, "Albuquerque International Sunport", "US"),
    "BOI": (43.5644, -116.2228, "Boise Air Terminal", "US"),
    "RNO": (39.4991, -119.7681, "Reno-Tahoe International", "US"),
    "LAX": (33.9416, -118.4085, "Los Angeles International", "US"),
    "SFO": (37.6213, -122.3790, "San Francisco International", "US"),
    "SAN": (32.7338, -117.1933, "San Diego International", "US"),
    "SNA": (33.6757, -117.8682, "John Wayne Airport, Orange County", "US"),
    "SJC": (37.3639, -121.9289, "Norman Y. Mineta San Jose International", "US"),
    "OAK": (37.7126, -122.2197, "Oakland International", "US"),
    "SMF": (38.6954, -121.5908, "Sacramento International", "US"),
    "ONT": (34.0560, -117.6012, "Ontario International", "US"),
    "BUR": (34.2007, -118.3587, "Hollywood Burbank", "US"),
    "PDX": (45.5898, -122.5951, "Portland International", "US"),
    "SEA": (47.4502, -122.3088, "Seattle-Tacoma International", "US"),
    "PSP": (33.8297, -116.5067, "Palm Springs International", "US"),
    "ANC": (61.1743, -149.9962, "Ted Stevens Anchorage International", "US"),
    "FAI": (64.8151, -147.8560, "Fairbanks International", "US"),
    "HNL": (21.3187, -157.9224, "Daniel K. Inouye International", "US"),
    "OGG": (20.8986, -156.4305, "Kahului Airport", "US"),
    # Additional US endpoints (route destinations only)
    "SJU": (18.4394, -66.0018, "Luis Munoz Marin International", "US"),
    "LIH": (21.9760, -159.3390, "Lihue Airport", "US"),
    "KOA": (19.7388, -156.0456, "Ellison Onizuka Kona International", "US"),
    "ITO": (19.7203, -155.0485, "Hilo International", "US"),
    "GUM": (13.4839, 144.7960, "Antonio B. Won Pat International", "US"),
    "BET": (60.7798, -161.8380, "Bethel Airport", "US"),
    "SCC": (70.1947, -148.4652, "Deadhorse Airport", "US"),
    "BRW": (71.2854, -156.7660, "Wiley Post-Will Rogers Memorial", "US"),
    "OTZ": (66.8847, -162.5985, "Ralph Wien Memorial", "US"),
    "OME": (64.5122, -165.4451, "Nome Airport", "US"),
    "JNU": (58.3549, -134.5763, "Juneau International", "US"),
    "KTN": (55.3556, -131.7137, "Ketchikan International", "US"),
    "SIT": (57.0471, -135.3616, "Sitka Rocky Gutierrez", "US"),
    "ADQ": (57.7500, -152.4939, "Kodiak Airport", "US"),
    "CDV": (60.4917, -145.4776, "Merle K. Mudhole Smith", "US"),
    "DLG": (58.9967, -158.5033, "Dillingham Airport", "US"),
    "MVY": (41.3931, -70.6143, "Martha's Vineyard Airport", "US"),
    "ACK": (41.2531, -70.0603, "Nantucket Memorial", "US"),
    # International endpoints
    "LHR": (51.4700, -0.4543, "London Heathrow", "GB"),
    "CDG": (49.0097, 2.5479, "Paris Charles de Gaulle", "FR"),
    "FRA": (50.0379, 8.5622, "Frankfurt am Main", "DE"),
    "AMS": (52.3105, 4.7683, "Amsterdam Schiphol", "NL"),
    "MAD": (40.4719, -3.5626, "Madrid Barajas", "ES"),
    "DUB": (53.4213, -6.2701, "Dublin Airport", "IE"),
    "LIS": (38.7742, -9.1342, "Lisbon Portela", "PT"),
    "FCO": (41.8003, 12.2389, "Rome Fiumicino", "IT"),
    "MUC": (48.3538, 11.7861, "Munich Airport", "DE"),
    "ZRH": (47.4647, 8.5492, "Zurich Airport", "CH"),
    "BCN": (41.2974, 2.0833, "Barcelona El Prat", "ES"),
    "ATH": (37.9364, 23.9445, "Athens International", "GR"),
    "KEF": (63.9850, -22.6056, "Keflavik International", "IS"),
    "TLV": (32.0114, 34.8867, "Ben Gurion International", "IL"),
    "DXB": (25.2532, 55.3657, "Dubai International", "AE"),
    "DOH": (25.2731, 51.6141, "Hamad International", "QA"),
    "DEL": (28.5562, 77.1000, "Indira Gandhi International", "IN"),
    "BOM": (19.0896, 72.8656, "Chhatrapati Shivaji Maharaj", "IN"),
    "NRT": (35.7720, 140.3929, "Narita International", "JP"),
    "HND": (35.5494, 139.7798, "Tokyo Haneda", "JP"),
    "ICN": (37.4602, 126.4407, "Incheon International", "KR"),
    "PVG": (31.1443, 121.8083, "Shanghai Pudong International", "CN"),
    "HKG": (22.3080, 113.9185, "Hong Kong International", "HK"),
    "TPE": (25.0777, 121.2328, "Taiwan Taoyuan International", "TW"),
    "SIN": (1.3644, 103.9915, "Singapore Changi", "SG"),
    "SYD": (-33.9399, 151.1753, "Sydney Kingsford Smith", "AU"),
    "AKL": (-37.0082, 174.7850, "Auckland Airport", "NZ"),
    "YYZ": (43.6777, -79.6248, "Toronto Pearson International", "CA"),
    "YVR": (49.1967, -123.1815, "Vancouver International", "CA"),
    "YUL": (45.4706, -73.7408, "Montreal-Trudeau International", "CA"),
    "MEX": (19.4363, -99.0721, "Mexico City International", "MX"),
    "CUN": (21.0365, -86.8771, "Cancun International", "MX"),
    "SJD": (23.1518, -109.7215, "Los Cabos International", "MX"),
    "PVR": (20.6801, -105.2544, "Puerto Vallarta International", "MX"),
    "GDL": (20.5218, -103.3111, "Guadalajara International", "MX"),
    "GRU": (-23.4356, -46.4731, "Sao Paulo Guarulhos", "BR"),
    "EZE": (-34.8222, -58.5358, "Buenos Aires Ezeiza", "AR"),
    "BOG": (4.7016, -74.1469, "El Dorado International", "CO"),
    "LIM": (-12.0219, -77.1143, "Jorge Chavez International", "PE"),
    "PTY": (9.0714, -79.3835, "Tocumen International", "PA"),
    "SDQ": (18.4297, -69.6689, "Las Americas International", "DO"),
    "PUJ": (18.5674, -68.3634, "Punta Cana International", "DO"),
    "NAS": (25.0390, -77.4661, "Lynden Pindling International", "BS"),
    "MBJ": (18.5037, -77.9134, "Sangster International", "JM"),
    "KIN": (17.9357, -76.7875, "Norman Manley International", "JM"),
    "POS": (10.5954, -61.3372, "Piarco International", "TT"),
    "SXM": (18.0410, -63.1089, "Princess Juliana International", "SX"),
}

# --------------------------------------------------------------------------
# Airport master table.
# (iata, city, state, region, runways, gates, slot_controlled,
#  enplanements_2024, load_factor, avg_seats_per_departure,
#  pax_cagr, seat_cagr)
# --------------------------------------------------------------------------
AIRPORTS: list[tuple] = [
    # --- New England -------------------------------------------------------
    ("BOS", "Boston", "MA", "New England", 6, 102, True, 21_900_000, 0.845, 138, 0.041, 0.031),
    ("PVD", "Providence", "RI", "New England", 2, 22, False, 2_180_000, 0.862, 128, 0.068, 0.043),
    ("BDL", "Windsor Locks", "CT", "New England", 2, 24, False, 3_180_000, 0.851, 131, 0.052, 0.038),
    ("MHT", "Manchester", "NH", "New England", 2, 12, False, 900_000, 0.838, 126, 0.047, 0.040),
    ("PWM", "Portland", "ME", "New England", 2, 11, False, 1_120_000, 0.849, 121, 0.056, 0.041),
    ("BTV", "Burlington", "VT", "New England", 2, 8, False, 780_000, 0.833, 108, 0.049, 0.044),
    # --- Mid-Atlantic ------------------------------------------------------
    ("JFK", "New York", "NY", "Mid-Atlantic", 4, 128, True, 32_400_000, 0.836, 168, 0.037, 0.030),
    ("EWR", "Newark", "NJ", "Mid-Atlantic", 3, 118, True, 24_600_000, 0.852, 152, 0.033, 0.021),
    ("LGA", "New York", "NY", "Mid-Atlantic", 2, 82, True, 16_800_000, 0.847, 129, 0.029, 0.024),
    ("PHL", "Philadelphia", "PA", "Mid-Atlantic", 4, 126, False, 15_100_000, 0.828, 124, 0.026, 0.028),
    ("BWI", "Baltimore", "MD", "Mid-Atlantic", 4, 77, False, 13_400_000, 0.841, 138, 0.031, 0.027),
    ("DCA", "Arlington", "VA", "Mid-Atlantic", 3, 60, True, 12_600_000, 0.856, 122, 0.034, 0.018),
    ("IAD", "Dulles", "VA", "Mid-Atlantic", 4, 123, False, 12_900_000, 0.821, 158, 0.058, 0.049),
    ("ALB", "Albany", "NY", "Mid-Atlantic", 3, 16, False, 1_320_000, 0.827, 112, 0.024, 0.026),
    ("BUF", "Buffalo", "NY", "Mid-Atlantic", 2, 24, False, 2_310_000, 0.831, 118, 0.021, 0.023),
    ("PIT", "Pittsburgh", "PA", "Mid-Atlantic", 4, 51, False, 4_620_000, 0.822, 121, 0.036, 0.038),
    # --- Southeast ---------------------------------------------------------
    ("ATL", "Atlanta", "GA", "Southeast", 5, 192, False, 52_800_000, 0.851, 146, 0.028, 0.024),
    ("CLT", "Charlotte", "NC", "Southeast", 4, 115, False, 25_400_000, 0.843, 122, 0.032, 0.029),
    ("MCO", "Orlando", "FL", "Southeast", 4, 129, False, 28_100_000, 0.856, 152, 0.048, 0.036),
    ("MIA", "Miami", "FL", "Southeast", 4, 131, False, 26_300_000, 0.849, 155, 0.055, 0.041),
    ("FLL", "Fort Lauderdale", "FL", "Southeast", 2, 66, False, 17_200_000, 0.858, 149, 0.043, 0.030),
    ("TPA", "Tampa", "FL", "Southeast", 3, 62, False, 12_400_000, 0.845, 141, 0.041, 0.037),
    ("RDU", "Raleigh", "NC", "Southeast", 3, 43, False, 7_100_000, 0.839, 126, 0.062, 0.048),
    ("BNA", "Nashville", "TN", "Southeast", 4, 54, False, 11_300_000, 0.851, 133, 0.071, 0.052),
    ("RSW", "Fort Myers", "FL", "Southeast", 1, 28, False, 5_400_000, 0.862, 144, 0.045, 0.031),
    ("JAX", "Jacksonville", "FL", "Southeast", 2, 20, False, 3_620_000, 0.838, 130, 0.039, 0.035),
    ("SAV", "Savannah", "GA", "Southeast", 2, 14, False, 1_780_000, 0.844, 122, 0.066, 0.044),
    # --- Midwest -----------------------------------------------------------
    ("ORD", "Chicago", "IL", "Midwest", 8, 191, True, 38_900_000, 0.838, 141, 0.030, 0.026),
    ("MDW", "Chicago", "IL", "Midwest", 5, 43, False, 10_400_000, 0.849, 148, 0.027, 0.022),
    ("DTW", "Detroit", "MI", "Midwest", 6, 129, False, 14_600_000, 0.836, 132, 0.022, 0.024),
    ("MSP", "Minneapolis", "MN", "Midwest", 4, 131, False, 17_400_000, 0.834, 129, 0.024, 0.025),
    ("STL", "St. Louis", "MO", "Midwest", 4, 62, False, 6_800_000, 0.829, 133, 0.025, 0.028),
    ("MCI", "Kansas City", "MO", "Midwest", 3, 39, False, 5_700_000, 0.833, 130, 0.034, 0.041),
    ("CLE", "Cleveland", "OH", "Midwest", 3, 39, False, 4_900_000, 0.827, 126, 0.028, 0.030),
    ("CVG", "Hebron", "KY", "Midwest", 4, 51, False, 4_400_000, 0.818, 128, 0.031, 0.034),
    ("IND", "Indianapolis", "IN", "Midwest", 3, 39, False, 4_800_000, 0.831, 124, 0.036, 0.035),
    # --- South Central -----------------------------------------------------
    ("DFW", "Dallas", "TX", "South Central", 7, 174, False, 42_100_000, 0.842, 138, 0.047, 0.040),
    ("DAL", "Dallas", "TX", "South Central", 3, 20, False, 8_600_000, 0.861, 165, 0.030, 0.014),
    ("IAH", "Houston", "TX", "South Central", 5, 130, False, 22_500_000, 0.836, 141, 0.041, 0.036),
    ("HOU", "Houston", "TX", "South Central", 4, 30, False, 7_500_000, 0.854, 152, 0.035, 0.024),
    ("AUS", "Austin", "TX", "South Central", 2, 34, False, 11_100_000, 0.858, 139, 0.074, 0.049),
    ("MSY", "New Orleans", "LA", "South Central", 3, 35, False, 5_900_000, 0.836, 132, 0.033, 0.032),
    ("SAT", "San Antonio", "TX", "South Central", 3, 26, False, 5_300_000, 0.841, 126, 0.048, 0.042),
    # --- Mountain West -----------------------------------------------------
    ("DEN", "Denver", "CO", "Mountain West", 6, 174, False, 41_300_000, 0.847, 141, 0.052, 0.045),
    ("PHX", "Phoenix", "AZ", "Mountain West", 3, 122, False, 25_100_000, 0.845, 143, 0.038, 0.034),
    ("LAS", "Las Vegas", "NV", "Mountain West", 4, 110, False, 28_600_000, 0.859, 158, 0.036, 0.028),
    ("SLC", "Salt Lake City", "UT", "Mountain West", 4, 94, False, 14_700_000, 0.841, 132, 0.043, 0.041),
    ("ABQ", "Albuquerque", "NM", "Mountain West", 3, 23, False, 2_770_000, 0.828, 128, 0.029, 0.031),
    ("BOI", "Boise", "ID", "Mountain West", 3, 12, False, 2_260_000, 0.849, 131, 0.058, 0.038),
    ("RNO", "Reno", "NV", "Mountain West", 3, 23, False, 2_180_000, 0.836, 133, 0.031, 0.033),
    # --- Pacific West ------------------------------------------------------
    ("LAX", "Los Angeles", "CA", "Pacific West", 4, 146, False, 38_600_000, 0.847, 160, 0.031, 0.027),
    ("SFO", "San Francisco", "CA", "Pacific West", 4, 90, False, 24_100_000, 0.861, 154, 0.049, 0.029),
    ("SAN", "San Diego", "CA", "Pacific West", 1, 51, False, 12_400_000, 0.858, 143, 0.042, 0.030),
    ("SNA", "Santa Ana", "CA", "Pacific West", 1, 20, False, 5_650_000, 0.872, 148, 0.023, 0.008),
    ("SJC", "San Jose", "CA", "Pacific West", 2, 30, False, 5_600_000, 0.843, 141, 0.034, 0.033),
    ("OAK", "Oakland", "CA", "Pacific West", 2, 30, False, 5_300_000, 0.851, 148, 0.026, 0.024),
    ("SMF", "Sacramento", "CA", "Pacific West", 2, 32, False, 6_300_000, 0.845, 138, 0.040, 0.036),
    ("ONT", "Ontario", "CA", "Pacific West", 2, 26, False, 3_300_000, 0.839, 141, 0.055, 0.047),
    ("BUR", "Burbank", "CA", "Pacific West", 2, 14, False, 3_100_000, 0.856, 137, 0.037, 0.021),
    ("PDX", "Portland", "OR", "Pacific West", 3, 55, False, 9_600_000, 0.840, 133, 0.030, 0.031),
    ("SEA", "Seattle", "WA", "Pacific West", 3, 91, False, 25_500_000, 0.855, 148, 0.044, 0.030),
    ("PSP", "Palm Springs", "CA", "Pacific West", 2, 17, False, 1_620_000, 0.851, 134, 0.052, 0.040),
    # --- Non-Contiguous ----------------------------------------------------
    ("ANC", "Anchorage", "AK", "Non-Contiguous", 3, 20, False, 2_650_000, 0.771, 129, 0.019, 0.026),
    ("FAI", "Fairbanks", "AK", "Non-Contiguous", 3, 10, False, 610_000, 0.762, 114, 0.023, 0.028),
    ("HNL", "Honolulu", "HI", "Non-Contiguous", 4, 57, False, 10_300_000, 0.828, 168, 0.016, 0.022),
    ("OGG", "Kahului", "HI", "Non-Contiguous", 2, 16, False, 3_700_000, 0.833, 158, 0.011, 0.019),
]

# --------------------------------------------------------------------------
# Destination lists, in rough order of departure volume.
# --------------------------------------------------------------------------
DESTINATIONS: dict[str, list[str]] = {
    "BOS": ["LGA", "DCA", "ORD", "BWI", "PHL", "ATL", "MCO", "FLL", "DEN", "LAX",
            "SFO", "SEA", "LHR", "DUB", "CDG", "AMS", "FRA", "MAD", "LIS", "PUJ"],
    "PVD": ["MCO", "FLL", "BWI", "ATL", "CLT", "PHL", "DTW", "TPA", "RSW", "DEN", "DUB"],
    "BDL": ["MCO", "ATL", "BWI", "CLT", "ORD", "DTW", "PHL", "FLL", "DEN", "LAX", "DUB", "CUN"],
    "MHT": ["MCO", "BWI", "ATL", "CLT", "ORD", "PHL", "FLL", "DEN"],
    "PWM": ["MCO", "BWI", "ATL", "CLT", "PHL", "LGA", "DTW", "ORD", "DEN"],
    "BTV": ["ORD", "CLT", "PHL", "DCA", "ATL", "LGA", "DTW", "MCO"],
    "JFK": ["LAX", "SFO", "MCO", "FLL", "MIA", "BOS", "ATL", "ORD", "LAS", "SEA",
            "LHR", "CDG", "AMS", "FRA", "MAD", "FCO", "DXB", "NRT", "ICN", "TLV", "GRU", "DEL"],
    "EWR": ["ORD", "MCO", "FLL", "ATL", "LAX", "SFO", "BOS", "DEN", "IAH", "SEA",
            "LHR", "CDG", "FRA", "MUC", "AMS", "DUB", "NRT", "HKG", "DEL", "GRU", "TLV"],
    "LGA": ["ORD", "ATL", "MIA", "CLT", "DTW", "MCO", "BOS", "DFW", "DEN", "MSP", "YYZ", "YUL"],
    "PHL": ["ATL", "ORD", "BOS", "MCO", "CLT", "DFW", "LAX", "DEN", "FLL", "LHR", "CDG", "FRA", "DUB", "CUN"],
    "BWI": ["ATL", "MCO", "BOS", "FLL", "ORD", "DEN", "LAS", "TPA", "DFW", "LAX", "CUN", "LHR"],
    "DCA": ["ATL", "ORD", "BOS", "MCO", "DFW", "CLT", "DEN", "LAX", "MIA", "MSP", "YYZ"],
    "IAD": ["ORD", "ATL", "DFW", "LAX", "SFO", "DEN", "BOS", "MCO", "LHR", "CDG", "FRA",
            "MUC", "AMS", "DXB", "DOH", "NRT", "ICN", "PVG", "DEL", "GRU", "ADQ"],
    "ALB": ["ORD", "ATL", "CLT", "PHL", "DCA", "MCO", "FLL", "DTW"],
    "BUF": ["ATL", "ORD", "CLT", "MCO", "FLL", "PHL", "DTW", "LAS", "DEN"],
    "PIT": ["ORD", "ATL", "CLT", "PHL", "MCO", "DFW", "DEN", "LAX", "FLL", "LHR"],
    "ATL": ["MCO", "DFW", "LGA", "FLL", "ORD", "DEN", "LAX", "MIA", "TPA", "BOS",
            "SEA", "SFO", "LHR", "CDG", "AMS", "FRA", "MEX", "GRU", "NRT", "ICN", "DXB", "SJU"],
    "CLT": ["ATL", "ORD", "MCO", "LGA", "DFW", "DEN", "LAX", "BOS", "PHL", "FLL",
            "SFO", "SEA", "LHR", "CDG", "FRA", "MUC", "CUN", "NAS"],
    "MCO": ["ATL", "JFK", "EWR", "BOS", "ORD", "DFW", "PHL", "BWI", "DEN", "LAX",
            "SFO", "SEA", "LHR", "FRA", "AMS", "GRU", "EZE", "BOG", "SJU", "SXM"],
    "MIA": ["ATL", "JFK", "LGA", "ORD", "DFW", "LAX", "BOS", "MCO", "DEN", "SFO",
            "LHR", "MAD", "FRA", "CDG", "GRU", "EZE", "BOG", "LIM", "PTY", "SDQ", "KIN", "POS", "DOH"],
    "FLL": ["ATL", "EWR", "JFK", "BOS", "BWI", "ORD", "MCO", "DFW", "LAX", "DEN",
            "SJU", "NAS", "MBJ", "PUJ", "BOG", "LIM", "GRU", "LHR"],
    "TPA": ["ATL", "MCO", "ORD", "JFK", "BOS", "DFW", "DEN", "LAX", "PHL", "BWI", "CUN", "LHR", "KEF"],
    "RDU": ["ATL", "ORD", "LGA", "DFW", "CLT", "MCO", "DEN", "LAX", "BOS", "SFO", "LHR", "CDG"],
    "BNA": ["ATL", "ORD", "DFW", "LGA", "MCO", "DEN", "LAX", "BOS", "PHX", "SFO", "LHR", "CUN"],
    "RSW": ["ATL", "EWR", "BOS", "ORD", "PHL", "DTW", "MSP", "BWI", "DFW", "YYZ"],
    "JAX": ["ATL", "CLT", "ORD", "DFW", "MCO", "PHL", "LGA", "DEN"],
    "SAV": ["ATL", "CLT", "ORD", "LGA", "DFW", "DCA", "PHL", "DEN", "BOS"],
    "ORD": ["LGA", "ATL", "DFW", "DEN", "LAX", "SFO", "BOS", "MCO", "SEA", "PHX",
            "LHR", "CDG", "FRA", "MUC", "AMS", "DUB", "NRT", "HND", "ICN", "PVG", "HKG", "DEL", "DOH"],
    "MDW": ["ATL", "DEN", "LAS", "MCO", "PHX", "DAL", "BWI", "LAX", "BOS", "CUN"],
    "DTW": ["ATL", "ORD", "LGA", "MCO", "DFW", "DEN", "LAX", "BOS", "SEA", "AMS", "CDG", "ICN", "NRT"],
    "MSP": ["ORD", "ATL", "LGA", "DEN", "DFW", "LAX", "SEA", "MCO", "BOS", "AMS", "CDG", "ICN", "HND"],
    "STL": ["ATL", "ORD", "DFW", "DEN", "LAS", "MCO", "LGA", "LAX", "PHX", "CUN"],
    "MCI": ["ATL", "ORD", "DFW", "DEN", "LAS", "MCO", "LGA", "PHX", "LAX"],
    "CLE": ["ATL", "ORD", "CLT", "DFW", "DEN", "MCO", "LGA", "PHL", "LAS"],
    "CVG": ["ATL", "ORD", "CLT", "DFW", "DEN", "MCO", "LGA", "LAX", "CDG"],
    "IND": ["ATL", "ORD", "CLT", "DFW", "DEN", "MCO", "LGA", "PHX", "LAS", "CDG"],
    "DFW": ["ATL", "ORD", "LAX", "DEN", "LGA", "MCO", "SFO", "LAS", "PHX", "SEA",
            "BOS", "MIA", "LHR", "CDG", "FRA", "MAD", "NRT", "ICN", "HKG", "SYD", "DOH", "MEX", "GRU"],
    "DAL": ["HOU", "ATL", "DEN", "LAS", "MCO", "PHX", "MDW", "LAX", "BWI", "SAT"],
    "IAH": ["ATL", "ORD", "DFW", "LAX", "DEN", "LGA", "MCO", "SFO", "LAS", "SEA",
            "LHR", "CDG", "FRA", "AMS", "MEX", "GRU", "EZE", "BOG", "LIM", "NRT", "ICN", "SYD", "DOH"],
    "HOU": ["DAL", "ATL", "DEN", "LAS", "MCO", "PHX", "MDW", "LAX", "CUN", "MEX"],
    "AUS": ["DFW", "ATL", "DEN", "LAX", "ORD", "LGA", "PHX", "SFO", "SEA", "LAS", "LHR", "CDG", "CUN"],
    "MSY": ["ATL", "DFW", "ORD", "DEN", "LGA", "MCO", "LAX", "PHX", "CUN"],
    "SAT": ["DFW", "ATL", "DEN", "ORD", "LAX", "PHX", "LAS", "LGA", "MEX"],
    "DEN": ["ORD", "ATL", "DFW", "LAX", "PHX", "LAS", "SFO", "SEA", "MCO", "LGA",
            "BOS", "MSP", "LHR", "CDG", "FRA", "MUC", "AMS", "NRT", "ICN", "MEX", "CUN"],
    "PHX": ["LAX", "DEN", "ORD", "DFW", "ATL", "LAS", "SFO", "SEA", "MCO", "LGA", "LHR", "CUN", "SJD"],
    "LAS": ["LAX", "DEN", "PHX", "ORD", "DFW", "ATL", "SFO", "SEA", "MCO", "LGA",
            "BOS", "LHR", "ICN", "NRT", "CUN", "YVR"],
    "SLC": ["DEN", "LAX", "SFO", "SEA", "PHX", "ORD", "ATL", "DFW", "LGA", "BOS",
            "CDG", "AMS", "LHR", "ICN", "MEX"],
    "ABQ": ["DEN", "PHX", "DFW", "LAX", "ORD", "ATL", "LAS", "SFO"],
    "BOI": ["SEA", "DEN", "SFO", "LAX", "PHX", "SLC", "ORD", "DFW", "LAS"],
    "RNO": ["LAX", "SFO", "DEN", "PHX", "SEA", "LAS", "DFW", "ORD"],
    "LAX": ["SFO", "LAS", "JFK", "DEN", "SEA", "PHX", "ORD", "DFW", "ATL", "BOS",
            "HNL", "OGG", "KOA", "LIH", "NRT", "HND", "ICN", "PVG", "HKG", "TPE", "SIN", "SYD",
            "AKL", "LHR", "CDG", "FRA", "MEX", "GRU", "DXB", "DOH"],
    "SFO": ["LAX", "JFK", "SEA", "LAS", "DEN", "SAN", "ORD", "DFW", "BOS", "ATL",
            "HNL", "OGG", "NRT", "HND", "ICN", "PVG", "HKG", "TPE", "SIN", "SYD",
            "LHR", "CDG", "FRA", "AMS", "MUC", "ZRH", "DEL", "DXB"],
    "SAN": ["SFO", "LAX", "LAS", "DEN", "PHX", "SEA", "ORD", "DFW", "JFK", "ATL",
            "BOS", "HNL", "OGG", "LHR", "CUN", "SJD", "NRT"],
    "SNA": ["SFO", "SJC", "LAS", "PHX", "DEN", "SEA", "DFW", "ORD", "JFK", "ATL", "SLC", "SJD", "PVR"],
    "SJC": ["LAX", "SNA", "SEA", "LAS", "DEN", "PHX", "ORD", "DFW", "JFK", "HNL", "OGG", "GDL", "NRT"],
    "OAK": ["LAX", "SNA", "LAS", "SEA", "PHX", "DEN", "HNL", "OGG", "JFK", "GDL", "SJD"],
    "SMF": ["LAX", "SFO", "LAS", "SEA", "PHX", "DEN", "SAN", "ORD", "DFW", "HNL", "GDL"],
    "ONT": ["SFO", "SJC", "LAS", "PHX", "DEN", "SEA", "DFW", "ORD", "GDL", "HNL"],
    "BUR": ["SFO", "SJC", "LAS", "PHX", "SEA", "DEN", "SLC", "DFW", "JFK"],
    "PDX": ["SEA", "SFO", "LAX", "LAS", "DEN", "PHX", "ORD", "DFW", "JFK", "HNL", "OGG", "AMS", "YVR"],
    "SEA": ["LAX", "SFO", "PDX", "LAS", "DEN", "PHX", "ORD", "JFK", "DFW", "ATL", "ANC",
            "HNL", "OGG", "KOA", "LHR", "CDG", "AMS", "FRA", "ICN", "NRT", "HND", "TPE", "DXB", "YVR"],
    "PSP": ["SFO", "LAX", "SEA", "DEN", "PHX", "DFW", "ORD", "JFK", "YVR"],
    "ANC": ["SEA", "PDX", "SFO", "LAX", "DEN", "MSP", "ORD", "PHX", "LAS", "HNL",
            "FAI", "BET", "OME", "OTZ", "BRW", "SCC", "JNU", "KTN", "ADQ", "CDV", "DLG", "SIT"],
    "FAI": ["ANC", "SEA", "MSP", "ORD", "DEN", "PDX", "BRW", "OTZ"],
    "HNL": ["OGG", "KOA", "LIH", "ITO", "LAX", "SFO", "SEA", "PDX", "SAN", "SJC",
            "OAK", "PHX", "LAS", "DEN", "ORD", "DFW", "JFK", "NRT", "HND", "ICN", "SYD", "GUM"],
    "OGG": ["HNL", "LAX", "SFO", "SEA", "PDX", "SAN", "SJC", "OAK", "PHX", "DEN", "DFW", "ORD", "YVR"],
}


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in statute miles."""
    radius = 3958.7613
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def write_airports() -> None:
    rows = []
    for (iata, city, state, region, runways, gates, slot, pax, lf, spd, pg, sg) in AIRPORTS:
        lat, lon, name, _ = COORDS[iata]
        rows.append({
            "iata": iata,
            "name": name,
            "city": city,
            "state": state,
            "region": region,
            "latitude": f"{lat:.4f}",
            "longitude": f"{lon:.4f}",
            "runways": runways,
            "gates": gates,
            "slot_controlled": str(bool(slot)).lower(),
        })
    _write(SEED_DIR / "airports.csv", rows)


def write_endpoints() -> None:
    rows = [
        {"iata": k, "name": v[2], "country": v[3],
         "latitude": f"{v[0]:.4f}", "longitude": f"{v[1]:.4f}"}
        for k, v in sorted(COORDS.items())
    ]
    _write(SEED_DIR / "endpoints.csv", rows)


def write_annual() -> None:
    """Back-cast the yearly series from the 2024 anchor and the stated CAGRs.

    passengers[y] = pax_2024 / (1 + pax_cagr) ** (2024 - y)
    seats[y]      = seats_2024 / (1 + seat_cagr) ** (2024 - y)
    flights[y]    = seats[y] / avg_seats_per_departure
    """
    rows = []
    for (iata, city, state, region, runways, gates, slot, pax, lf, spd, pg, sg) in AIRPORTS:
        seats_2024 = pax / lf
        for year in YEARS:
            back = BASE_YEAR - year
            passengers = pax / ((1 + pg) ** back)
            seats = seats_2024 / ((1 + sg) ** back)
            flights = seats / spd
            rows.append({
                "iata": iata,
                "year": year,
                "passengers": int(round(passengers)),
                "seats": int(round(seats)),
                "flights": int(round(flights)),
            })
    _write(SEED_DIR / "airport_annual.csv", rows)


def write_routes() -> None:
    """Synthesize a T-100-shaped segment table.

    Departure counts follow a Zipf-like decay over the curated destination list
    and are rescaled so each origin's route departures sum to its 2024 total.
    """
    annual_flights = {}
    for (iata, city, state, region, runways, gates, slot, pax, lf, spd, pg, sg) in AIRPORTS:
        annual_flights[iata] = (pax / lf) / spd

    seats_per_dep = {a[0]: a[9] for a in AIRPORTS}
    load_factor = {a[0]: a[8] for a in AIRPORTS}

    rows = []
    for (iata, *_r) in AIRPORTS:
        dests = DESTINATIONS[iata]
        # Zipf-like weights, with a gentle long-haul uplift for wide-body markets.
        weights = []
        o_lat, o_lon = COORDS[iata][0], COORDS[iata][1]
        for rank, dest in enumerate(dests, start=1):
            dist = haversine_miles(o_lat, o_lon, COORDS[dest][0], COORDS[dest][1])
            w = 1.0 / (rank ** 0.85)
            if dist >= 2500:
                w *= 0.55  # long-haul routes operate at lower frequency
            weights.append(w)
        total_w = sum(weights)
        total_dep = annual_flights[iata]
        for dest, w in zip(dests, weights):
            dist = haversine_miles(o_lat, o_lon, COORDS[dest][0], COORDS[dest][1])
            deps = total_dep * (w / total_w)
            # Long-haul segments use larger equipment.
            gauge = seats_per_dep[iata] * (1.55 if dist >= 2500 else 0.92)
            seats = deps * gauge
            rows.append({
                "origin": iata,
                "destination": dest,
                "distance_miles": round(dist, 1),
                "departures_performed": int(round(deps)),
                "seats": int(round(seats)),
                "passengers": int(round(seats * load_factor[iata])),
            })
    _write(SEED_DIR / "routes.csv", rows)


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {path.relative_to(path.parents[4])} ({len(rows)} rows)")


def main() -> None:
    missing = {d for dests in DESTINATIONS.values() for d in dests} - set(COORDS)
    if missing:
        raise SystemExit(f"Missing coordinates for: {sorted(missing)}")
    if set(DESTINATIONS) != {a[0] for a in AIRPORTS}:
        raise SystemExit("DESTINATIONS keys must match AIRPORTS")
    write_airports()
    write_endpoints()
    write_annual()
    write_routes()


if __name__ == "__main__":
    main()
