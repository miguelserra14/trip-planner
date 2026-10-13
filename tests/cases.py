"""The trips between an airport and a place that the tests follow, in both directions: from the arrival airport
to the destination (tests/arrival) and from the origin to the departure airport (tests/departure).

CITIES is a city of every country of the European Union, and the routes that failed before. REMOTE is a small
town of every country, far from the airport: what a country's regional trains and buses publish decides it.
SECOND_AIRPORTS is the airports cheap flights land at, and NEIGHBOURS the countries around the Union.

A case that cannot find a real connection says why in known_gap. Each reason was checked by asking the timetables
what they know around the place and around the airport, and what they answer when every limit is relaxed."""
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
    Case("LT", "VNO", "Nida, Lithuania", remote=True, known_gap="published, but one coach a day each way: it leaves Vilnius at dawn and Nida in the afternoon. "
                                                                   "The other way is by the Klaipeda ferry, which is not published"),
    Case("LU", "LUX", "Vianden, Luxembourg", remote=True),
    Case("MT", "MLA", "Mellieha, Malta", remote=True, known_gap="Malta's buses are not in the published timetables"),
    Case("NL", "AMS", "Giethoorn, Netherlands", remote=True),
    Case("PL", "KRK", "Zakopane, Poland", remote=True),
    Case("PT", "OPO", "Braganca, Portugal", remote=True),
    Case("RO", "OTP", "Sighisoara, Romania", remote=True),
    Case("SK", "BTS", "Banska Stiavnica, Slovakia", remote=True,
         known_gap="published, but thinly: its trains run at the weekend only and the one regional bus line on Fridays"),
    Case("SI", "LJU", "Bovec, Slovenia", remote=True),   # failed: "Bovec" was located in the middle of its municipality, 5 km from the town
    Case("ES", "SVQ", "Ronda, Spain", remote=True),
    Case("SE", "ARN", "Mora, Sweden", remote=True),
]

# The second airports of the European Union: the ones low-cost airlines fly to, often far from the city in their
# name, and the islands. This is where a cheap flight ends, so it is where the ground leg matters most.
SECOND_AIRPORTS = [
    Case("AT", "SZG", "Salzburg"),
    Case("AT", "INN", "Innsbruck"),
    Case("BE", "CRL", "Bruxelles"),
    Case("BE", "LGG", "Liege"),
    Case("BG", "VAR", "Varna"),
    Case("BG", "BOJ", "Nessebar, Bulgaria",
         known_gap="the timetables know no stop at Burgas airport or in Nessebar: the Black Sea coast south of Varna is not published"),
    Case("HR", "SPU", "Split"),
    Case("HR", "DBV", "Dubrovnik"),
    Case("HR", "ZAD", "Zadar"),
    Case("CY", "PFO", "Limassol"),
    Case("CZ", "PRG", "Plzen"),
    Case("DK", "BLL", "Aarhus"),
    Case("DK", "AAL", "Aalborg"),
    Case("EE", "TLL", "Parnu"),
    Case("FI", "HEL", "Turku"),
    Case("FI", "RVN", "Rovaniemi"),
    Case("FR", "CDG", "Reims"),
    Case("FR", "MRS", "Aix-en-Provence"),
    Case("FR", "NCE", "Cannes"),
    Case("FR", "TLS", "Albi, France"),
    Case("FR", "BOD", "Arcachon"),
    Case("FR", "NTE", "Rennes"),
    Case("DE", "BER", "Potsdam"),
    Case("DE", "FRA", "Heidelberg"),
    Case("DE", "HHN", "Mainz"),
    Case("DE", "FMM", "Munchen"),
    Case("DE", "HAM", "Lubeck"),
    Case("GR", "SKG", "Thessaloniki"),
    Case("GR", "HER", "Rethymno",
         known_gap="Crete is not published: its only stop in the timetables is the ferry port of Heraklion"),
    Case("GR", "CFU", "Corfu, Greece",
         known_gap="Corfu is not published: its only stop in the timetables is the ferry port"),
    Case("HU", "BUD", "Budapest"),
    Case("HU", "DEB", "Debrecen",
         known_gap="Debrecen's city buses (DKV), the only transport at the airport, are not published: the nearest stop with a timetable is 1.7 km away"),
    Case("IE", "ORK", "Cork"),
    Case("IE", "NOC", "Sligo"),
    Case("IE", "SNN", "Limerick"),
    Case("IT", "NAP", "Salerno"),
    Case("IT", "CTA", "Siracusa"),
    Case("IT", "BLQ", "Firenze"),
    Case("IT", "PSA", "Lucca"),
    Case("IT", "BRI", "Lecce"),
    Case("IT", "CIA", "Roma"),
    Case("IT", "TSF", "Venezia"),
    Case("IT", "CAG", "Oristano"),
    Case("LV", "RIX", "Riga"),
    Case("LV", "RIX", "Daugavpils"),
    Case("LT", "KUN", "Vilnius"),
    Case("LT", "PLQ", "Klaipeda"),
    Case("LU", "LUX", "Esch-sur-Alzette"),
    Case("NL", "AMS", "Rotterdam"),
    Case("NL", "MST", "Maastricht"),
    Case("NL", "GRQ", "Groningen"),
    Case("PL", "WMI", "Warszawa"),
    Case("PL", "GDN", "Sopot"),
    Case("PL", "KTW", "Krakow"),
    Case("PL", "POZ", "Poznan"),
    Case("PT", "LIS", "Setubal"),
    Case("PT", "FAO", "Lagos, Portugal"),
    Case("PT", "FNC", "Funchal",
         known_gap="only Funchal's city buses are published, and they do not reach the airport: the Aerobus and the island's coaches are not"),
    Case("PT", "PDL", "Ponta Delgada",
         known_gap="the Azores are not published: the timetables know no stop on the island"),
    Case("RO", "CLJ", "Cluj-Napoca"),
    Case("RO", "TSR", "Timisoara"),
    Case("RO", "IAS", "Iasi"),
    Case("SK", "KSC", "Presov",
         known_gap="Kosice's city transport, the only link between the airport and the station, is not published"),
    Case("SK", "BTS", "Bratislava"),
    Case("SI", "LJU", "Maribor"),
    Case("ES", "BCN", "Girona"),
    Case("ES", "GRO", "Barcelona"),
    Case("ES", "AGP", "Granada, Spain"),
    Case("ES", "VLC", "Castellon de la Plana"),
    Case("ES", "BIO", "San Sebastian, Spain"),
    Case("ES", "PMI", "Alcudia, Spain"),
    Case("ES", "SCQ", "A Coruna"),
    Case("ES", "TFS", "Santa Cruz de Tenerife"),
    Case("SE", "GOT", "Boras"),
    Case("SE", "NYO", "Stockholm"),
    Case("SE", "MMX", "Lund"),
]

# Outside the European Union: Switzerland, the United Kingdom, Norway, Iceland, the Balkans, Turkey and the
# small states that have no airport of their own.
NEIGHBOURS = [
    Case("CH", "ZRH", "Luzern"),
    Case("CH", "GVA", "Lausanne"),
    Case("CH", "BSL", "Bern"),
    Case("CH", "ZRH", "Zermatt", remote=True),
    Case("CH", "ZRH", "Scuol, Switzerland", remote=True),
    Case("GB", "LHR", "Oxford"),
    Case("GB", "STN", "Cambridge"),
    Case("GB", "LGW", "Brighton"),
    Case("GB", "LTN", "London"),
    Case("GB", "MAN", "Liverpool"),
    Case("GB", "EDI", "Glasgow"),
    Case("GB", "BRS", "Bath, England"),
    Case("GB", "BFS", "Derry"),
    Case("GB", "CWL", "Swansea"),
    Case("GB", "EDI", "Fort William, Scotland", remote=True),
    Case("GB", "MAN", "Keswick, England", remote=True),
    Case("GB", "NQY", "St Ives, Cornwall", remote=True),
    Case("NO", "OSL", "Lillehammer"),
    Case("NO", "TRF", "Oslo"),
    Case("NO", "BGO", "Voss"),
    Case("NO", "TOS", "Tromso"),
    Case("NO", "BGO", "Flam, Norway", remote=True),
    Case("IS", "KEF", "Reykjavik"),
    Case("IS", "KEF", "Vik, Iceland", remote=True,
         known_gap="published, but the coach to Vik (route 52) does not run every day"),
    Case("RS", "BEG", "Novi Sad"),
    Case("AL", "TIA", "Shkoder", remote=True, gap_in="departure",
         known_gap="Albania's minibuses are not published: the only coaches in the timetables go round by Kosovo, seven hours for 75 km"),
    Case("BA", "SJJ", "Mostar",
         known_gap="the coaches and trains between Sarajevo and Mostar are not published: only the city transport of both is"),
    Case("ME", "TGD", "Kotor",
         known_gap="Montenegro's coaches are not published: nothing links Podgorica airport and the coast"),
    Case("MK", "SKP", "Ohrid",
         known_gap="North Macedonia's coaches are not published, and no stop of Skopje airport is"),
    Case("XK", "PRN", "Prizren",
         known_gap="Kosovo is not published: the timetables know no stop at Pristina airport"),
    Case("MD", "RMO", "Chisinau"),
    Case("TR", "IST", "Bursa",
         known_gap="Turkey is not published: the timetables know no stop at Istanbul airport or in Bursa"),
    Case("TR", "SAW", "Istanbul"),
    Case("AD", "BCN", "Andorra la Vella"),
    Case("LI", "ZRH", "Vaduz"),
    Case("MC", "NCE", "Monte Carlo, Monaco"),
    Case("SM", "BLQ", "San Marino",
         known_gap="San Marino is not published: the coach from Rimini is in no timetable"),
]

CASES = CITIES + REMOTE + SECOND_AIRPORTS + NEIGHBOURS
