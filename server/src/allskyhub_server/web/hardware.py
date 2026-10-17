"""The parts of a camera (/hardware and the first steps on /start).

The housing is Daniel Nimmervoll's (Photography Nimmervoll e.U., liquidart-shop.at): weather
proof, made for a Raspberry Pi with PoE HAT, one network cable for data and power. The list
follows his shop's list of parts, reduced to what allskyhub uses. Prices are rough, in euros,
as of October 2026; shops change them. Links are plain, without affiliate tags.
"""

from __future__ import annotations

from dataclasses import dataclass

SHOP = "https://www.liquidart-shop.at/de/allsky-kamera/"
HOUSING = "https://www.liquidart-shop.at/de/Allsky-Kamera-Body.html"
VIDEOS = "https://youtube.com/playlist?list=PLnMB8sPGVeKcyAOnHKgyQ65RpCEHa47WS"
# Daniel's voucher, as his shop states it (Benjamin, 2026-10-15: name him and the code).
VOUCHER = "allsky"
VOUCHER_FROM = "240 €"
VOUCHER_OFF = "10 %"


@dataclass(frozen=True)
class Link:
    label: str
    url: str


@dataclass(frozen=True)
class Part:
    name: str
    note: str
    price: str  # rough, e.g. "≈ 60 €"
    links: tuple[Link, ...]


@dataclass(frozen=True)
class Group:
    key: str
    title: str
    intro: str
    parts: tuple[Part, ...]


def _amazon(asin: str) -> str:
    return f"https://www.amazon.de/dp/{asin}"


GROUPS: tuple[Group, ...] = (
    Group(
        "gehaeuse",
        "Gehäuse",
        "Das Gehäuse von Daniel Nimmervoll ist durchdacht und hält Wind und Wetter aus. "
        "Es kommt als Bausatz mit Kuppel, Dichtungen, Edelstahlschrauben, Druckausgleichs"
        "ventil und fertig verlöteter Kuppelheizung. Wähle beim Bestellen die Kameraplatte "
        "passend zu deiner Kamera.",
        (
            Part(
                "AllSky-Gehäuse für Raspberry Pi mit PoE HAT und Mosfet",
                "Variante „RPi HQ Kamera“ oder „ZWO ASI Kamera“ und Raspberry Pi 4 oder 5. "
                "Zur HQ-Kamera liegt ein UV/IR-Sperrfilter bei.",
                "189–194 €",
                (Link("liquidart-shop.at", HOUSING),),
            ),
            Part(
                "Streulichtschutz",
                "Empfohlen: hält Licht von Straßenlampen und Nachbarn vom Objektiv fern.",
                "29 €",
                (
                    Link(
                        "liquidart-shop.at",
                        "https://www.liquidart-shop.at/de/Streulichtschutz-fuer-AllSky-Kamera.html",
                    ),
                ),
            ),
        ),
    ),
    Group(
        "montage",
        "Befestigung",
        "Das Gehäuse sitzt am besten frei auf einem Rohr, mit möglichst freiem Blick bis zum "
        "Horizont. Die Säulenbefestigung passt auf 38-mm-Rohre, ein SAT-Wandhalter ist das "
        "passende Rohr für Hauswand oder Balkon. Für unterwegs gibt es eine Stativbefestigung.",
        (
            Part(
                "Säulenbefestigung für 38-mm-Rohr",
                "Mit Langlöchern zum Ausrichten (±12,5°) und Edelstahlschrauben.",
                "29 €",
                (
                    Link(
                        "liquidart-shop.at",
                        "https://www.liquidart-shop.at/de/halterung-fuer-allsky-gehaeuse.html",
                    ),
                ),
            ),
            Part(
                "SAT-Wandhalter 30/80 cm (verzinkter Stahl)",
                "Das Rohr, das Daniel für die Säulenbefestigung empfiehlt.",
                "≈ 25 €",
                (Link("Amazon", _amazon("B08CVN1RFQ")),),
            ),
            Part(
                "Stativbefestigung (statt Rohr)",
                "Für ein Fotostativ, etwa zum Ausprobieren.",
                "25 €",
                (
                    Link(
                        "liquidart-shop.at",
                        "https://www.liquidart-shop.at/de/stativ-befestigung-fuer-allsky-gehaeuse.html",
                    ),
                ),
            ),
        ),
    ),
    Group(
        "technik",
        "Rechner und Kamera",
        "allskyhub läuft auf einem Raspberry Pi 4 oder 5. Daniel empfiehlt den Pi 4: der "
        "Pi 5 wird im Gehäuse an heißen Tagen 2–3 °C wärmer.",
        (
            Part(
                "Raspberry Pi 4 Model B, 4 GB",
                "2 GB reichen auch; ein Pi 5 geht ebenfalls (dann den PoE HAT für den Pi 5).",
                "≈ 60 €",
                (
                    Link(
                        "BerryBase",
                        "https://www.berrybase.at/raspberry-pi-4-computer-modell-b-4gb-ram",
                    ),
                ),
            ),
            Part(
                "Kamera, Variante A: ZWO ASI678MC",
                "Mit allskyhub getestet, empfindlich, per USB am Pi. Ein kurzes USB-Kabel "
                "passt besser ins Gehäuse als das mitgelieferte.",
                "≈ 300 €",
                (
                    Link("USB-Kabel kurz", _amazon("B0C7VYQBCJ")),
                    Link("USB-Kabel gewinkelt", _amazon("B076SVB8VW")),
                ),
            ),
            Part(
                "Kamera, Variante B: Raspberry Pi HQ Kamera (M12)",
                "Günstiger, direkt am Pi angeschlossen. Das Profil ist in allskyhub "
                "vorhanden, aber noch nicht an echter Hardware getestet.",
                "≈ 55 €",
                (
                    Link(
                        "BerryBase",
                        "https://www.berrybase.at/raspberry-pi-high-quality-kamera-m12-mount",
                    ),
                ),
            ),
            Part(
                "Fischaugen-Objektiv M12 (nur zur HQ-Kamera)",
                "Allsky-Objektiv mit 1,55–2,5 mm Brennweite.",
                "≈ 15 €",
                (Link("AliExpress", "https://de.aliexpress.com/item/1005005041863786.html"),),
            ),
            Part(
                "microSD-Karte, SanDisk Max Endurance",
                "32 GB reichen, 128 GB halten mehr Nächte auf der Kamera. Endurance-Karten "
                "halten das ständige Schreiben länger aus.",
                "≈ 20–35 €",
                (Link("Amazon", _amazon("B084CJ9T2R")),),
            ),
        ),
    ),
    Group(
        "strom",
        "Strom und Netz über ein Kabel (PoE)",
        "Ein einziges Netzwerkkabel bringt Strom und Internet ins Gehäuse. Der PoE HAT sitzt "
        "auf dem Pi, der Injektor im Haus zwischen Router und Kabel.",
        (
            Part(
                "PoE HAT für den Raspberry Pi 4",
                "Für den Pi 5 den PoE HAT (F) nehmen.",
                "≈ 25 €",
                (
                    Link(
                        "BerryBase (Pi 4)",
                        "https://www.berrybase.at/power-over-ethernet-hat-c-fuer-raspberry-pi-3b-4b-802.3af-at",
                    ),
                    Link(
                        "BerryBase (Pi 5)",
                        "https://www.berrybase.at/power-over-ethernet-poe-hat-f-fuer-raspberry-pi-5-smps-onboard-luefter-kuehlkoerper",
                    ),
                ),
            ),
            Part(
                "PoE-Injektor (802.3at)",
                "Hat dein Switch schon PoE, brauchst du keinen.",
                "≈ 20–35 €",
                (
                    Link("TP-Link TL-POE170S", _amazon("B09SGKK5XD")),
                    Link(
                        "Ubiquiti U-POE-at",
                        "https://www.berrybase.at/ubiquiti-u-poe-at-eu-poe-injector-802.3at-30w",
                    ),
                ),
            ),
            Part(
                "Netzwerkkabel für draußen",
                "Lang genug bis zum Haus; auf den Durchmesser für die Kabelverschraubung "
                "achten (drei Dichtringe liegen bei).",
                "≈ 15–30 €",
                (Link("Amazon", _amazon("B07WFTCPBF")),),
            ),
        ),
    ),
    Group(
        "kleinteile",
        "Kleinteile und Werkzeug",
        "Damit das Gehäuse dicht bleibt und innen trocken.",
        (
            Part(
                "Silicagel-Beutel",
                "Ein, zwei Beutel ins Gehäuse; ab und zu tauschen.",
                "≈ 10 €",
                (Link("Amazon", "https://www.amazon.de/s?k=silicagel+beutel"),),
            ),
            Part(
                "Silikonfett für die O-Ringe",
                "Ein Hauch auf die O-Ringe, dann schließt das Gehäuse dicht.",
                "≈ 10 €",
                (Link("Amazon", _amazon("B0DMSSFML2")),),
            ),
            Part(
                "Schraubendreher Torx TX8, Inbus 2 mm und 3 mm",
                "Mehr Werkzeug braucht der Zusammenbau nicht.",
                "",
                (),
            ),
        ),
    ),
    Group(
        "spaeter",
        "Für später: Kuppelheizung und Klimasensor",
        "Das Gehäuse hat eine Heizung gegen Tau auf der Kuppel; ein Mosfet schaltet sie, ein "
        "BME280 misst Temperatur, Feuchte und Luftdruck. allskyhub steuert beides noch nicht. "
        "Baust du sie gleich mit ein, musst du das Gehäuse später nicht wieder öffnen.",
        (
            Part(
                "Mosfet-Modul (IRF9540N)",
                "Schaltet die Kuppelheizung.",
                "≈ 5 €",
                (
                    Link(
                        "reichelt",
                        "https://www.reichelt.at/at/de/shop/produkt/entwicklerboards_-_mosfet_irf9540n-316199",
                    ),
                ),
            ),
            Part(
                "BME280-Sensor",
                "Die Variante mit Stecker braucht kein Löten.",
                "≈ 10 €",
                (
                    Link("zum Löten", _amazon("B07D8T4HP6")),
                    Link("ohne Löten", _amazon("B0BNJ9CY92")),
                ),
            ),
            Part(
                "Steckbrücken (Jumper-Kabel)",
                "Verbinden Mosfet und Sensor mit dem Pi.",
                "≈ 7 €",
                (Link("Amazon", _amazon("B01EV70C78")),),
            ),
        ),
    ),
)

# Rough totals for the page, with the stray-light shield, the pole mount and the wall bracket.
TOTAL_HQ = "≈ 500 €"
TOTAL_ZWO = "≈ 800 €"
