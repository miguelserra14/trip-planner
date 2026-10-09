"""The trips from an arrival airport to a destination that the tests follow: every country of the European Union,
and the routes that failed before."""
from dataclasses import dataclass

@dataclass(frozen=True)
class Case:
    country: str
    airport: str
    city: str
    known_gap: str = ""   # why no real connection can be found. Empty: one must be found

    @property
    def name(self) -> str:
        return f"{self.country}-{self.airport}-{self.city.split(',')[0].replace(' ', '_')}"

CASES = [
    Case("AT", "VIE", "Linz"),
    Case("BE", "BRU", "Antwerpen"),       # failed: the planner walks 35 minutes to the station under the terminal
    Case("BE", "CRL", "Gent"),
    Case("BG", "SOF", "Plovdiv"),
    Case("HR", "ZAG", "Rijeka"),
    Case("CY", "LCA", "Nicosia"),
    Case("CZ", "PRG", "Brno"),
    Case("DK", "CPH", "Odense"),
    Case("EE", "TLL", "Tartu"),
    Case("FI", "HEL", "Tampere"),
    Case("FR", "BVA", "Paris"),
    Case("FR", "LYS", "Grenoble"),
    Case("DE", "CGN", "Bonn"),            # failed: without the terminal, the middle of the airfield is 2 km from the station
    Case("DE", "NRN", "Duisburg"),        # failed: the same
    Case("GR", "ATH", "Athens, Greece"),  # failed: Athens' metro is not in the timetables, the station is 40 minutes on foot
    Case("HU", "BUD", "Szeged"),
    Case("IE", "DUB", "Galway"),
    Case("IT", "BGY", "Pavia"),           # failed: "Pavia" was located in the middle of its province
    Case("IT", "FCO", "Roma"),
    Case("LV", "RIX", "Jelgava"),
    Case("LT", "VNO", "Kaunas"),
    Case("LU", "LUX", "Luxembourg"),      # "Luxembourg" was located in the middle of the country
    Case("MT", "MLA", "Valletta", known_gap="Malta's buses are not in the published timetables"),
    Case("NL", "EIN", "Utrecht"),
    Case("PL", "WAW", "Lodz"),
    Case("PT", "OPO", "Coimbra"),
    Case("RO", "OTP", "Brasov"),
    Case("SK", "BTS", "Trnava"),
    Case("SI", "LJU", "Ljubljana"),
    Case("ES", "MAD", "Toledo, Spain"),
    Case("SE", "ARN", "Uppsala"),
]
