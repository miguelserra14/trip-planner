"""The trips between an airport and a place that the tests follow, in both directions: from the arrival airport
to the destination (tests/arrival) and from the origin to the departure airport (tests/departure).

CITIES is a city of every country of the European Union, and the routes that failed before. REMOTE is a small
town of every country, far from the airport: what a country's regional trains and buses publish decides it."""
from dataclasses import dataclass

@dataclass(frozen=True)
class Case:
    country: str
    airport: str
    city: str
    known_gap: str = ""    # why no real connection can be found. Empty: one must be found
    gap_in: str = ""       # "arrival" or "departure" when the gap is in one direction only
    remote: bool = False   # a small town far from the airport: more changes and more time are normal

    @property
    def name(self) -> str:
        return f"{self.country}-{self.airport}-{self.city.split(',')[0].replace(' ', '_')}"

CITIES = [
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

REMOTE = [
    Case("AT", "VIE", "Hallstatt, Austria", remote=True),
    Case("BE", "BRU", "Durbuy, Belgium", remote=True),
    Case("BG", "SOF", "Bansko, Bulgaria", remote=True),
    Case("HR", "ZAG", "Slunj, Croatia", remote=True, known_gap="the timetables know no stop within 5 km of Slunj: Croatia's regional buses are not published"),
    Case("CY", "LCA", "Polis Chrysochous, Cyprus", remote=True),
    Case("CZ", "PRG", "Cesky Krumlov, Czechia", remote=True),
    Case("DK", "CPH", "Skagen, Denmark", remote=True),
    Case("EE", "TLL", "Kuressaare, Estonia", remote=True),
    Case("FI", "HEL", "Savonlinna, Finland", remote=True),
    Case("FR", "LYS", "Le Puy-en-Velay, France", remote=True),
    Case("DE", "MUC", "Berchtesgaden, Germany", remote=True),
    Case("GR", "ATH", "Nafplio, Greece", remote=True, known_gap="the timetables know no stop within 5 km of Nafplio: Greece's regional coaches (KTEL) are not published"),
    Case("HU", "BUD", "Tokaj, Hungary", remote=True),
    Case("IE", "DUB", "Westport, Ireland", remote=True),
    Case("IT", "VCE", "Cortina d'Ampezzo, Italy", remote=True),
    Case("LV", "RIX", "Kuldiga, Latvia", remote=True),
    Case("LT", "VNO", "Nida, Lithuania", remote=True, known_gap="published, but one coach a day each way: it leaves Vilnius at dawn and Nida in the afternoon"),
    Case("LU", "LUX", "Vianden, Luxembourg", remote=True),
    Case("MT", "MLA", "Mellieha, Malta", remote=True, known_gap="Malta's buses are not in the published timetables"),
    Case("NL", "AMS", "Giethoorn, Netherlands", remote=True),
    Case("PL", "KRK", "Zakopane, Poland", remote=True),
    Case("PT", "OPO", "Braganca, Portugal", remote=True),
    Case("RO", "OTP", "Sighisoara, Romania", remote=True),
    Case("SK", "BTS", "Banska Stiavnica, Slovakia", remote=True, gap_in="departure",
         known_gap="published, but towards Bratislava the timetables only have the trains of the weekend"),
    Case("SI", "LJU", "Bovec, Slovenia", remote=True, known_gap="the nearest stop published is 2.5 km from Bovec, and nothing fits the day"),
    Case("ES", "SVQ", "Ronda, Spain", remote=True),
    Case("SE", "ARN", "Mora, Sweden", remote=True),
]

CASES = CITIES + REMOTE
