"""Short explanations of sky objects for the app's sky view (our own texts).

Planets, the moon and the best-known Messier objects have their own text; every other
Messier object and every constellation gets one from its kind and brightness. Facts are
rounded and well established (distances in light years from the usual references).
Photos and their credits: ``photos.json`` (tools/sky_photos.py).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

PHOTOS = Path(__file__).parent / "data" / "photos.json"

# Messier kinds in messier.json → (German, English).
KINDS = {
    "gc": ("Kugelsternhaufen", "Globular cluster"),
    "oc": ("Offener Sternhaufen", "Open cluster"),
    "s": ("Spiralgalaxie", "Spiral galaxy"),
    "e": ("Elliptische Galaxie", "Elliptical galaxy"),
    "i": ("Irreguläre Galaxie", "Irregular galaxy"),
    "sfr": ("Sternentstehungsgebiet", "Star-forming nebula"),
    "pn": ("Planetarischer Nebel", "Planetary nebula"),
    "snr": ("Supernova-Überrest", "Supernova remnant"),
    "rn": ("Reflexionsnebel", "Reflection nebula"),
    "pos": ("Sterngruppe", "Star group"),
    "planet": ("Planet", "Planet"),
    "moon": ("Mond", "Moon"),
    "constellation": ("Sternbild", "Constellation"),
}

_KIND_TEXT = {
    "gc": (
        "Hunderttausende alte Sterne, dicht zu einer Kugel gedrängt. Kugelsternhaufen "
        "umkreisen das Zentrum der Milchstraße und gehören zu ihren ältesten Bewohnern.",
        "Hundreds of thousands of old stars packed into a ball. Globular clusters orbit the "
        "centre of the Milky Way and are among its oldest members.",
    ),
    "oc": (
        "Eine Gruppe junger Sterne, die zusammen aus einer Gaswolke entstanden sind und "
        "langsam auseinanderdriften.",
        "A group of young stars born together from one gas cloud, slowly drifting apart.",
    ),
    "s": (
        "Eine Galaxie wie unsere Milchstraße, mit Spiralarmen aus Sternen, Gas und Staub, "
        "Millionen Lichtjahre entfernt.",
        "A galaxy like our Milky Way, with spiral arms of stars, gas and dust, millions of "
        "light years away.",
    ),
    "e": (
        "Eine runde bis ovale Galaxie aus meist alten Sternen, ohne Spiralarme.",
        "A round to oval galaxy of mostly old stars, without spiral arms.",
    ),
    "i": (
        "Eine Galaxie ohne klare Form, oft durch die Nähe einer anderen Galaxie verformt.",
        "A galaxy without a clear shape, often bent by a neighbouring galaxy.",
    ),
    "sfr": (
        "Eine leuchtende Gaswolke, in der gerade neue Sterne entstehen; junge, heiße Sterne "
        "bringen das Gas zum Leuchten.",
        "A glowing gas cloud where new stars are being born; young hot stars make the gas shine.",
    ),
    "pn": (
        "Die abgestoßene Gashülle eines sterbenden Sterns; so wird auch unsere Sonne einmal "
        "enden. Mit Planeten hat der Name nichts zu tun: In alten Fernrohren sah er aus wie "
        "ein Planetenscheibchen.",
        "The shed shell of a dying star, like our sun far in the future. It has nothing to "
        "do with planets: in old telescopes it looked like a planet's disc.",
    ),
    "snr": (
        "Die Trümmer einer Sternexplosion, die sich bis heute ausdehnen.",
        "The debris of an exploded star, still expanding today.",
    ),
    "rn": (
        "Staub, der das Licht naher Sterne zurückwirft und deshalb bläulich schimmert.",
        "Dust reflecting the light of nearby stars, which makes it shimmer bluish.",
    ),
    "pos": (
        "Eine auffällige Gruppe von Sternen, die Messier in seine Liste aufnahm.",
        "A striking group of stars that Messier put on his list.",
    ),
}

# Our own texts for the objects people ask about most.
_OWN: dict[str, tuple[str, str]] = {
    "moon": (
        "Unser Begleiter, rund 384 000 km entfernt. Er zeigt uns immer dieselbe Seite; hell "
        "ist er nur, weil er das Sonnenlicht zurückwirft. Im Fernglas sieht man Krater und "
        "dunkle Ebenen, die „Meere“.",
        "Our companion, about 384,000 km away. It always shows us the same side and only "
        "shines with reflected sunlight. Binoculars show craters and dark plains, the "
        "“seas”.",
    ),
    "mercury": (
        "Der sonnennächste Planet. Er steht nie weit von der Sonne und ist nur kurz in der "
        "Dämmerung tief am Horizont zu sehen.",
        "The planet closest to the sun. It never strays far from it and shows only briefly, "
        "low in twilight.",
    ),
    "venus": (
        "Nach Sonne und Mond das hellste Objekt am Himmel, als Abend- oder Morgenstern. "
        "Dichte Wolken aus Schwefelsäure verhüllen ihre glühend heiße Oberfläche.",
        "After the sun and moon the brightest object in the sky, as evening or morning "
        "star. Thick clouds of sulphuric acid hide its scorching surface.",
    ),
    "mars": (
        "Der rote Planet: Eisenoxid, also Rost, färbt seinen Staub. Alle zwei Jahre kommt er "
        "der Erde nahe und leuchtet dann besonders hell.",
        "The red planet: iron oxide, rust, colours its dust. Every two years it comes close "
        "to Earth and shines especially bright.",
    ),
    "jupiter": (
        "Der größte Planet, ein Riese aus Gas. Schon im Fernglas sieht man seine vier großen "
        "Monde als Lichtpunkte neben ihm, die jede Nacht anders stehen.",
        "The largest planet, a giant of gas. Even binoculars show its four big moons as "
        "points of light beside it, placed differently every night.",
    ),
    "saturn": (
        "Der Planet mit den Ringen aus Eis und Gestein. Seine Ringe sieht man schon in einem "
        "kleinen Fernrohr.",
        "The planet with rings of ice and rock. A small telescope already shows the rings.",
    ),
    "uranus": (
        "Ein blaugrüner Eisriese, gerade an der Grenze dessen, was das Auge unter sehr "
        "dunklem Himmel sieht. Er rollt fast auf der Seite um die Sonne.",
        "A blue-green ice giant, just at the limit of the naked eye under a very dark sky. "
        "It rolls around the sun almost on its side.",
    ),
    "neptune": (
        "Der äußerste Planet, nur im Fernglas oder Fernrohr als blauer Punkt zu finden. Er "
        "wurde 1846 zuerst berechnet und dann entdeckt.",
        "The outermost planet, found only with binoculars or a telescope as a blue dot. It "
        "was first predicted by calculation and then found in 1846.",
    ),
    "M1": (
        "Die Überreste einer Sternexplosion, die chinesische Astronomen im Jahr 1054 am "
        "Taghimmel sahen. In der Mitte dreht sich ein Neutronenstern 30-mal pro Sekunde.",
        "The remains of a star that exploded, seen in daylight by Chinese astronomers in "
        "1054. At its centre a neutron star spins 30 times a second.",
    ),
    "M13": (
        "Der schönste Kugelsternhaufen des Nordhimmels, im Herkules: rund 300 000 Sterne, "
        "etwa 22 000 Lichtjahre entfernt. Im Fernglas ein runder Nebelfleck.",
        "The finest globular cluster of the northern sky, in Hercules: some 300,000 stars, "
        "about 22,000 light years away. A round smudge in binoculars.",
    ),
    "M31": (
        "Unsere große Nachbargalaxie, rund 2,5 Millionen Lichtjahre entfernt: das Fernste, "
        "was du mit bloßem Auge sehen kannst. Unter dunklem Himmel ein länglicher "
        "Nebelfleck, im Fernglas deutlich.",
        "Our big neighbouring galaxy, about 2.5 million light years away: the farthest thing "
        "you can see with the naked eye. An elongated smudge under a dark sky, clear in "
        "binoculars.",
    ),
    "M33": (
        "Die Dreiecksgalaxie, nach Andromeda die nächste große Galaxie, knapp 3 Millionen "
        "Lichtjahre entfernt. Sehr flächig: nur unter wirklich dunklem Himmel zu sehen.",
        "The Triangulum Galaxy, the next big galaxy after Andromeda, nearly 3 million light "
        "years away. Spread out and faint: visible only under a truly dark sky.",
    ),
    "M42": (
        "Der Orionnebel im Schwert des Orion, eine Kinderstube der Sterne, rund 1 300 "
        "Lichtjahre entfernt. Mit bloßem Auge ein Nebelfleck, im Fernglas eindrucksvoll.",
        "The Orion Nebula in Orion's sword, a nursery of stars about 1,300 light years away. "
        "A smudge to the eye, impressive in binoculars.",
    ),
    "M44": (
        "Die Krippe im Krebs, ein großer Sternhaufen, rund 600 Lichtjahre entfernt. Mit "
        "bloßem Auge ein Nebelfleck, im Fernglas löst er sich in Dutzende Sterne auf.",
        "The Beehive in Cancer, a large cluster about 600 light years away. A smudge to the "
        "eye; binoculars split it into dozens of stars.",
    ),
    "M45": (
        "Die Plejaden oder das Siebengestirn, junge blaue Sterne, rund 440 Lichtjahre "
        "entfernt. Wie viele Sterne siehst du? Sechs bis sieben unter gutem Himmel.",
        "The Pleiades or Seven Sisters, young blue stars about 440 light years away. How many "
        "can you see? Six or seven under a good sky.",
    ),
    "M57": (
        "Der Ringnebel in der Leier: die Gashülle eines sterbenden Sterns, im Fernrohr ein "
        "kleiner Rauchring.",
        "The Ring Nebula in Lyra: the gas shell of a dying star, a small smoke ring in a "
        "telescope.",
    ),
    "M27": (
        "Der Hantelnebel im Füchschen, ein planetarischer Nebel, der schon im Fernglas als "
        "kleiner Fleck erscheint.",
        "The Dumbbell Nebula in Vulpecula, a planetary nebula that shows as a small patch "
        "even in binoculars.",
    ),
    "M51": (
        "Die Whirlpoolgalaxie: eine Spiralgalaxie, die gerade eine kleinere Galaxie streift, "
        "gut 20 Millionen Lichtjahre entfernt.",
        "The Whirlpool Galaxy: a spiral galaxy brushing past a smaller one, some 20-odd "
        "million light years away.",
    ),
    "M81": (
        "Bodes Galaxie im Großen Bären, eine helle Spiralgalaxie, rund 12 Millionen "
        "Lichtjahre entfernt; im Fernglas zusammen mit M82 zu sehen.",
        "Bode's Galaxy in Ursa Major, a bright spiral about 12 million light years away; in "
        "binoculars together with M82.",
    ),
    "M82": (
        "Die Zigarrengalaxie: Die Nähe zu M81 hat in ihr eine Welle neuer Sterne ausgelöst.",
        "The Cigar Galaxy: its near pass by M81 set off a burst of new stars.",
    ),
    "M104": (
        "Die Sombrerogalaxie, wegen ihres dunklen Staubbands, rund 30 Millionen Lichtjahre "
        "entfernt.",
        "The Sombrero Galaxy, named for its dark dust lane, about 30 million light years away.",
    ),
    "M8": (
        "Der Lagunennebel im Schützen, eine große leuchtende Gaswolke, unter dunklem Himmel "
        "schon mit bloßem Auge zu sehen.",
        "The Lagoon Nebula in Sagittarius, a large glowing gas cloud, visible to the naked "
        "eye under a dark sky.",
    ),
    "M16": (
        "Der Adlernebel mit den „Säulen der Schöpfung“, berühmt durch das Hubble-Bild.",
        "The Eagle Nebula with the “Pillars of Creation”, made famous by Hubble.",
    ),
    "M11": (
        "Der Wildentenhaufen im Schild, einer der dichtesten offenen Sternhaufen.",
        "The Wild Duck Cluster in Scutum, one of the densest open clusters.",
    ),
    "M7": (
        "Der Ptolemäus-Haufen im Skorpion, schon in der Antike beschrieben; tief im Süden.",
        "Ptolemy's Cluster in Scorpius, described in antiquity; low in the south.",
    ),
    "M24": (
        "Ein Stück Milchstraße im Schützen, durch eine Lücke im Staub zu sehen: unzählige "
        "Sterne auf einem Fleck.",
        "A piece of the Milky Way in Sagittarius, seen through a gap in the dust: countless "
        "stars in one patch.",
    ),
}


def _seen(mag: float | None, english: bool) -> str:
    if mag is None:
        return ""
    if mag <= 4.5:
        return (
            "Unter dunklem Himmel mit bloßem Auge zu sehen."
            if not english
            else "Visible to the naked eye under a dark sky."
        )
    if mag <= 7.5:
        return "Im Fernglas zu sehen." if not english else "Visible in binoculars."
    return "Ein Ziel für das Fernrohr." if not english else "A target for a telescope."


def kind_label(kind: str, english: bool) -> str:
    de, en = KINDS.get(kind, (kind, kind))
    return en if english else de


def text(key: str, kind: str, mag: float | None, english: bool) -> str:
    """Our explanation of an object."""
    own = _OWN.get(key)
    if own is not None:
        return own[1] if english else own[0]
    if kind == "constellation":
        return (
            "One of the 88 constellations into which astronomers divide the sky. The lines "
            "join its brightest stars."
            if english
            else "Eines der 88 Sternbilder, in die die Astronomie den Himmel einteilt. Die "
            "Linien verbinden seine hellsten Sterne."
        )
    base = _KIND_TEXT.get(kind)
    parts = [base[1] if english else base[0]] if base else []
    parts.append(_seen(mag, english))
    return " ".join(p for p in parts if p)


def photos() -> dict[str, dict[str, str]]:
    """Object key → {file, page, url, author, licence, licence_url}."""
    data: Any = json.loads(PHOTOS.read_text())
    return cast(dict[str, dict[str, str]], data) if isinstance(data, dict) else {}
