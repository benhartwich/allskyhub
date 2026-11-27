"""The parts of a camera (/hardware and the first steps on /start).

The housing is Daniel Nimmervoll's (Photography Nimmervoll e.U., liquidart-shop.at): weather
proof, made for a Raspberry Pi with PoE HAT, one network cable for data and power. The list
follows his shop's list of parts, reduced to what allskyhub uses. Prices are rough, in euros,
as of October 2026; shops change them. Links are plain, without affiliate tags.
"""

from __future__ import annotations

from dataclasses import dataclass

from allskyhub_server.i18n import english

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

# The texts above in English (/hardware and /start in English), German → English; names
# that read the same in both (shops, "Raspberry Pi 4 Model B, 4 GB") need no entry.
ENGLISH = {
    "Gehäuse": "Housing",
    "Das Gehäuse von Daniel Nimmervoll ist durchdacht und hält Wind und Wetter aus. Es kommt "
    "als Bausatz mit Kuppel, Dichtungen, Edelstahlschrauben, Druckausgleichsventil und fertig "
    "verlöteter Kuppelheizung. Wähle beim Bestellen die Kameraplatte passend zu deiner Kamera.":
    "Daniel Nimmervoll's housing is well thought out and stands up to wind and weather. It "
    "comes as a kit with dome, seals, stainless steel screws, pressure equalisation valve and "
    "a pre-soldered dome heater. When ordering, choose the camera plate that fits your camera.",
    "AllSky-Gehäuse für Raspberry Pi mit PoE HAT und Mosfet":
    "AllSky housing for Raspberry Pi with PoE HAT and MOSFET",
    "Variante „RPi HQ Kamera“ oder „ZWO ASI Kamera“ und Raspberry Pi 4 oder 5. Zur HQ-Kamera "
    "liegt ein UV/IR-Sperrfilter bei.":
    "Variant “RPi HQ Kamera” or “ZWO ASI Kamera” and Raspberry Pi 4 or 5. A UV/IR cut "
    "filter comes with the HQ camera.",
    "Streulichtschutz": "Stray-light shield",
    "Empfohlen: hält Licht von Straßenlampen und Nachbarn vom Objektiv fern.":
    "Recommended: keeps light from street lamps and neighbours off the lens.",
    "Befestigung": "Mounting",
    "Das Gehäuse sitzt am besten frei auf einem Rohr, mit möglichst freiem Blick bis zum "
    "Horizont. Die Säulenbefestigung passt auf 38-mm-Rohre, ein SAT-Wandhalter ist das "
    "passende Rohr für Hauswand oder Balkon. Für unterwegs gibt es eine Stativbefestigung.":
    "The housing is best mounted on its own on a pole, with as clear a view down to the "
    "horizon as possible. The pole mount fits 38 mm pipes; a satellite dish wall bracket is "
    "the matching pipe for a house wall or balcony. For travelling there is a tripod mount.",
    "Säulenbefestigung für 38-mm-Rohr": "Pole mount for a 38 mm pipe",
    "Mit Langlöchern zum Ausrichten (±12,5°) und Edelstahlschrauben.":
    "With slotted holes for alignment (±12.5°) and stainless steel screws.",
    "SAT-Wandhalter 30/80 cm (verzinkter Stahl)":
    "Satellite dish wall bracket 30/80 cm (galvanised steel)",
    "Das Rohr, das Daniel für die Säulenbefestigung empfiehlt.":
    "The pipe Daniel recommends for the pole mount.",
    "Stativbefestigung (statt Rohr)": "Tripod mount (instead of a pipe)",
    "Für ein Fotostativ, etwa zum Ausprobieren.": "For a photo tripod, for example to try it out.",
    "Rechner und Kamera": "Computer and camera",
    "allskyhub läuft auf einem Raspberry Pi 4 oder 5. Daniel empfiehlt den Pi 4: der Pi 5 wird "
    "im Gehäuse an heißen Tagen 2–3 °C wärmer.":
    "allskyhub runs on a Raspberry Pi 4 or 5. Daniel recommends the Pi 4: in the housing the "
    "Pi 5 runs 2–3 °C warmer on hot days.",
    "2 GB reichen auch; ein Pi 5 geht ebenfalls (dann den PoE HAT für den Pi 5).":
    "2 GB is enough too; a Pi 5 works as well (then take the PoE HAT for the Pi 5).",
    "Kamera, Variante A: ZWO ASI678MC": "Camera, option A: ZWO ASI678MC",
    "Mit allskyhub getestet, empfindlich, per USB am Pi. Ein kurzes USB-Kabel passt besser ins "
    "Gehäuse als das mitgelieferte.":
    "Tested with allskyhub, sensitive, connected to the Pi by USB. A short USB cable fits "
    "the housing better than the one supplied.",
    "USB-Kabel kurz": "Short USB cable",
    "USB-Kabel gewinkelt": "Angled USB cable",
    "Kamera, Variante B: Raspberry Pi HQ Kamera (M12)":
    "Camera, option B: Raspberry Pi HQ Camera (M12)",
    "Günstiger, direkt am Pi angeschlossen. Das Profil ist in allskyhub vorhanden, aber noch "
    "nicht an echter Hardware getestet.":
    "Cheaper, connected directly to the Pi. allskyhub has a profile for it, but it has not "
    "been tested on real hardware yet.",
    "Fischaugen-Objektiv M12 (nur zur HQ-Kamera)": "M12 fisheye lens (HQ camera only)",
    "Allsky-Objektiv mit 1,55–2,5 mm Brennweite.": "All-sky lens with 1.55–2.5 mm focal length.",
    "microSD-Karte, SanDisk Max Endurance": "microSD card, SanDisk Max Endurance",
    "32 GB reichen, 128 GB halten mehr Nächte auf der Kamera. Endurance-Karten halten das "
    "ständige Schreiben länger aus.":
    "32 GB is enough; 128 GB keeps more nights on the camera. Endurance cards last longer "
    "under constant writing.",
    "Strom und Netz über ein Kabel (PoE)": "Power and network over one cable (PoE)",
    "Ein einziges Netzwerkkabel bringt Strom und Internet ins Gehäuse. Der PoE HAT sitzt auf "
    "dem Pi, der Injektor im Haus zwischen Router und Kabel.":
    "A single network cable brings power and internet into the housing. The PoE HAT sits on "
    "the Pi, the injector indoors between router and cable.",
    "PoE HAT für den Raspberry Pi 4": "PoE HAT for the Raspberry Pi 4",
    "Für den Pi 5 den PoE HAT (F) nehmen.": "For the Pi 5, take the PoE HAT (F).",
    "PoE-Injektor (802.3at)": "PoE injector (802.3at)",
    "Hat dein Switch schon PoE, brauchst du keinen.":
    "If your switch already has PoE, you don't need one.",
    "Netzwerkkabel für draußen": "Outdoor network cable",
    "Lang genug bis zum Haus; auf den Durchmesser für die Kabelverschraubung achten (drei "
    "Dichtringe liegen bei).":
    "Long enough to reach the house; mind the diameter for the cable gland (three sealing "
    "rings are included).",
    "Kleinteile und Werkzeug": "Small parts and tools",
    "Damit das Gehäuse dicht bleibt und innen trocken.":
    "So that the housing stays sealed and dry inside.",
    "Silicagel-Beutel": "Silica gel bags",
    "Ein, zwei Beutel ins Gehäuse; ab und zu tauschen.":
    "One or two bags in the housing; replace them now and then.",
    "Silikonfett für die O-Ringe": "Silicone grease for the O-rings",
    "Ein Hauch auf die O-Ringe, dann schließt das Gehäuse dicht.":
    "A thin film on the O-rings, and the housing seals tight.",
    "Schraubendreher Torx TX8, Inbus 2 mm und 3 mm": "Screwdrivers: Torx TX8, hex 2 mm and 3 mm",
    "Mehr Werkzeug braucht der Zusammenbau nicht.": "The assembly needs no other tools.",
    "Für später: Kuppelheizung und Klimasensor": "For later: dome heater and climate sensor",
    "Das Gehäuse hat eine Heizung gegen Tau auf der Kuppel; ein Mosfet schaltet sie, ein "
    "BME280 misst Temperatur, Feuchte und Luftdruck. allskyhub steuert beides noch nicht. "
    "Baust du sie gleich mit ein, musst du das Gehäuse später nicht wieder öffnen.":
    "The housing has a heater against dew on the dome; a MOSFET switches it, a BME280 "
    "measures temperature, humidity and air pressure. allskyhub does not control either yet. "
    "If you fit them right away, you won't have to open the housing again later.",
    "Mosfet-Modul (IRF9540N)": "MOSFET module (IRF9540N)",
    "Schaltet die Kuppelheizung.": "Switches the dome heater.",
    "BME280-Sensor": "BME280 sensor",
    "Die Variante mit Stecker braucht kein Löten.":
    "The version with a connector needs no soldering.",
    "zum Löten": "for soldering",
    "ohne Löten": "no soldering",
    "Steckbrücken (Jumper-Kabel)": "Jumper wires",
    "Verbinden Mosfet und Sensor mit dem Pi.": "Connect the MOSFET and the sensor to the Pi.",
}  # fmt: skip


def text(german: str) -> str:
    """A text of the list in the language of the request."""
    return ENGLISH.get(german, german) if english() else german


def euro(price: str) -> str:
    """ "≈ 20–35 €" in German, "≈ €20–35" in English."""
    if not english() or not price.endswith(" €"):
        return price
    amount = price.removesuffix(" €")
    about = "≈ " if amount.startswith("≈ ") else ""
    return f"{about}€{amount.removeprefix('≈ ').replace(',', '.')}"
