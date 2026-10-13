// The camera's local UI (SPEC §7, roadmap L2–L7): one page, the menu switches views by
// the URL fragment. Everything comes from the camera itself, so it works without the hub.
"use strict";

// --- texts (SPEC §7 Language and units) ---------------------------------------------
// Every text of the page, German and English, under the same keys. {name} is filled by t().
// Some values hold a little HTML (a link, <code>): they are only used where HTML goes.
const T = {
  de: {
    // general
    "app.title": "allskyhub-Kamera",
    "loading": "Lade …",
    "loadFailed": "Konnte nicht geladen werden ({e}).",
    "save": "Speichern",
    "saved": "Gespeichert.",
    "notSaved": "Nicht gespeichert.",
    "notApplied": "Nicht übernommen.",
    "apply": "Übernehmen",
    "failed": "Hat nicht geklappt.",
    "download": "Herunterladen",
    "image": "Bild",
    "night": "Nacht",
    "day": "Tag",
    "time": "Uhrzeit",
    "clock": "Uhrzeit",
    "exposure": "Belichtung",
    "gain": "Gain",
    "sensor": "Sensor",
    "sensorTemp": "Sensortemperatur",
    "capture": "Aufnahme",
    "power": "Stromversorgung",
    "storage": "Speicher",
    "orientation": "Ausrichtung",
    "hub": "Hub",
    "yes": "ja",
    "no": "nein",
    "on": "an",
    "off": "aus",
    "ok": "in Ordnung",
    "connected": "verbunden",
    "unreachable": "nicht erreichbar",
    // menu, pairing, lightbox (index.html)
    "nav.menu": "Hauptmenü",
    "nav.live": "Live",
    "nav.archive": "Archiv",
    "nav.events": "Ereignisse",
    "nav.sky": "Himmel",
    "nav.settings": "Einstellungen",
    "nav.system": "System",
    "nav.red": "Rotlicht für die Nacht",
    "nav.logout": "Abmelden",
    "pair.code": "Kopplungscode",
    "pair.help": "Gib den Code in der App oder auf allskyhub.org ein. Er erneuert sich alle paar Minuten.",
    "lb.prev": "Vorheriges Bild",
    "lb.next": "Nächstes Bild",
    "lb.zoom": "Vergrößern",
    "lb.zoomTitle": "Zoom (Doppelklick, Mausrad)",
    "lb.close": "Schließen",
    // detections (SPEC §6.4)
    "kind.meteor": "Meteor",
    "kind.lightning": "Blitz",
    "kind.aurora": "Polarlicht",
    "kind.nlc": "Leuchtende Nachtwolken",
    "kind.satellite": "Satellit",
    "kind.clouds": "Wolken",
    "kind.sky_quality": "Himmelsqualität",
    "reason.aircraft": "Flugzeug",
    "reason.satellite": "Satellit",
    "reason.artifact": "Artefakt",
    "reason.other": "Sonstiges",
    // products: short on the badges, longer on the night page
    "prod.keogram": "Keogramm",
    "prod.startrails": "Sternspuren",
    "prod.timelapse": "Zeitraffer",
    "prod.day_timelapse": "Tageszeitraffer",
    "prod.day_keogram": "Tageskeogramm",
    "prodLong.day_timelapse": "Zeitraffer vom Tag",
    "prodLong.day_keogram": "Keogramm vom Tag",
    // overlay fields and values
    "ovf.time": "Uhrzeit",
    "ovf.date": "Datum",
    "ovf.exposure": "Belichtung",
    "ovf.gain": "Gain",
    "ovf.sensor_temp": "Sensortemperatur",
    "ovf.sun": "Sonne",
    "ovf.moon": "Mond",
    "ovf.cloud_cover": "Bewölkung",
    "ovf.sqm": "Himmelshelligkeit (SQM)",
    "ovf.stars": "Sterne",
    "ovf.mode": "Tag/Nacht",
    "ovf.text1": "Eigener Text 1",
    "ovf.text2": "Eigener Text 2",
    "ovv.gain": "Gain {v}",
    "ovv.sun": "Sonne {v}°",
    "ovv.moon": "Mond {p} % · {a}°",
    "ovv.clouds": "Bewölkung {v} %",
    "ovv.sqm": "SQM {v}",
    "ovv.stars": "{n} Sterne",
    // live
    "live.current": "Aktuelles Bild",
    "live.saveOverlay": "Mit Overlay speichern",
    "live.designOverlay": "Overlay gestalten",
    "live.waiting": "Warte auf das erste Bild …",
    "live.paused": 'Aufnahme pausiert. <a href="#system">Unter System fortsetzen</a>.',
    "live.lastHour": "Die letzte Stunde",
    "live.loop": "Loop der letzten Stunde",
    "live.play": "Abspielen",
    "live.pause": "Anhalten",
    "live.position": "Position",
    "live.mini": "Mini-Zeitraffer der letzten Stunde",
    "live.focus": "Fokus",
    "live.focusStart": "Fokusmodus starten",
    "live.focusStop": "Fokusmodus beenden",
    "live.resetBest": "Bestwert zurücksetzen",
    "live.best": "Bestwert",
    "live.focusHelp": "Den Fokusring in kleinen Schritten drehen: höher ist schärfer. Sinkt die Zahl, bist du zu weit, also zurück. Dann die Feststellschraube anziehen und die Zahl noch einmal prüfen.",
    "live.crop": "Bildmitte in voller Auflösung",
    "live.taken": "Aufgenommen",
    "live.mode": "Modus",
    "live.brightness": "Helligkeit",
    "live.sun": "Sonne",
    // archive
    "archive.none": "Noch keine Nächte gespeichert.",
    "archive.frames": "{n} Bilder",
    "archive.events": "{n} Ereignisse",
    "keo.title": "Keolapse",
    "keo.help": "Die Keogramme mehrerer Nächte nebeneinander, die älteste links.",
    "keo.from": "Von",
    "keo.to": "Bis",
    "keo.show": "Zeigen",
    "night.back": "‹ Alle Nächte",
    "night.title": "Nacht vom {d}",
    "night.images": "Bilder",
    "night.jumpHour": "Zur Stunde springen",
    "night.jump": "Springen",
    "night.earlier": "Frühere laden",
    "night.later": "Spätere laden",
    "night.delete": "Nacht löschen",
    "night.deleteHelp": "Löscht alle Bilder, Produkte und Ereignisse dieser Nacht, hier und auf dem Hub. Das lässt sich nicht rückgängig machen.",
    "night.deleteAsk": "Die Nacht vom {d} wirklich löschen? Alle Bilder, Produkte und Ereignisse gehen verloren, auch auf dem Hub.",
    "night.current": "Die laufende Nacht kann nicht gelöscht werden.",
    "night.imageAt": "Bild von {t}",
    "night.rejected": "verworfen ({why})",
    "night.hideBad": "Verworfene ausblenden",
    "night.showBad": "{n} verworfene zeigen",
    "bad.dark": "zu dunkel",
    "bad.bright": "zu hell",
    // events
    "ev.direction": "Richtung {az}°, Höhe {alt}°",
    "ev.flashes": "{n} Blitze in 30 min",
    "ev.index": "Index {v}",
    "ev.confidence": "Sicherheit {p} %",
    "ev.confirmed": "bestätigt",
    "ev.not": "kein {kind}",
    "ev.auto": "automatisch",
    "ev.classify": "Einordnen",
    "ev.reset": "zurücksetzen",
    "ev.none": "Noch nichts erkannt.",
    "ev.noneNight": "In dieser Nacht nichts erkannt.",
    "ev.hideFp": "Fehlalarme ausblenden",
    "ev.showFp1": "{n} Fehlalarm zeigen",
    "ev.showFp": "{n} Fehlalarme zeigen",
    "ev.onlyFp": "Nur Fehlalarme in dieser Nacht.",
    "ev.whole": "Ganzes Bild",
    "ev.wholeCaption": "ganzes Bild",
    "ev.crop": "Ausschnitt",
    // sky charts
    "sky.none": "Noch keine Messwerte.",
    "sky.noneNight": "Keine Messwerte in dieser Nacht.",
    "sky.clouds": "Bewölkung",
    "sky.sqm": "Himmelshelligkeit",
    "sky.sqmHelp": "mag/″², höher ist dunkler. Nur bei astronomischer Nacht gemessen.",
    "sky.stars": "Sterne",
    "sky.exposureGain": "Belichtung und Gain",
    "sky.exposureGainHelp": "Blau: Belichtung in Sekunden, grün: Gain.",
    // settings
    "set.empty": "leer",
    "set.isSet": "gesetzt",
    "set.notSet": "nicht gesetzt",
    "set.latitude": "Breite",
    "set.longitude": "Länge",
    "set.here": "Standort dieses Geräts",
    "set.none": "Diese Kamera speichert keine Einstellungen.",
    "set.nothing": "Nichts geändert.",
    "set.saved": "Gespeichert. Die Kamera startet die Aufnahme neu …",
    "set.notSaved": "Nicht gespeichert: {what}.",
    "set.invalid": "ungültige Eingabe",
    // overlay designer
    "ov.title": "Overlay",
    "ov.help": "Was über dem Bild steht: im Livebild, beim Speichern mit Overlay. Es wird nie in die gespeicherten Bilder geschrieben.",
    "ov.preview": "Vorschau",
    "ov.show": "Overlay anzeigen",
    "ov.up": "{f} nach oben",
    "ov.textPlaceholder": "z. B. Sternwarte am Berg",
    "ov.size": "Schriftgröße",
    "ov.compass": "Himmelsrichtungen",
    "ov.stars": "Sternbilder (sobald die Ausrichtung bestimmt ist)",
    "ov.save": "Overlay speichern",
    "ov.reset": "Standard",
    "ov.saved": "Overlay gespeichert.",
    "ov.invalid": "Nicht gespeichert: ungültiges Layout.",
    "corner.off": "aus",
    "corner.tl": "oben links",
    "corner.tr": "oben rechts",
    "corner.bl": "unten links",
    "corner.br": "unten rechts",
    "size.small": "klein",
    "size.normal": "normal",
    "size.large": "groß",
    // sky mask and orientation
    "cal.title": "Himmelsmaske und Ausrichtung",
    "cal.exclude": "Bereich ausschließen",
    "cal.include": "Bereich einschließen",
    "cal.done": "Fläche fertig",
    "cal.undo": "Punkt zurück",
    "cal.grid": "Horizont und Himmelsrichtungen",
    "cal.save": "Maske speichern",
    "cal.relearn": "Maske neu lernen",
    "cal.excluded": "Ausgeschlossen {n}",
    "cal.included": "Eingeschlossen {n}",
    "cal.removeArea": "Bereich entfernen",
    "cal.remove": "Entfernen",
    "cal.learned": "Gelernt am {d}. Rot: zählt nicht als Himmel (Bäume, Dächer, Rand). Die Kamera lernt die Maske jede klare Nacht neu, gezeichnete Bereiche bleiben.",
    "cal.notLearned": "Noch keine Maske gelernt, das passiert in der ersten Nacht. Bis dahin gilt der Bildkreis des Objektivs; gezeichnete Bereiche gelten sofort.",
    "cal.north": "Norden",
    "cal.northValue": "{deg}° im Bild (im Uhrzeigersinn von oben)",
    "cal.mirrored": ", gespiegelt",
    "cal.aligned": "Ausgerichtet",
    "cal.alignedValue": "{d} mit {n} Sternen, Abweichung {rms}°",
    "cal.check": "Prüfen",
    "cal.checkHelp": "Stimmt N mit der Wirklichkeit überein und liegt die gelbe Linie auf dem Horizont? Sonst ist die Ausrichtung falsch; sie wird nach 7 Tagen neu bestimmt.",
    "cal.notAligned": "Noch nicht bestimmt. Die Kamera findet sie selbst in einer klaren, mondlosen Nacht anhand der Sterne.",
    "cal.saved": "Gespeichert. Gilt ab dem nächsten Bild für Belichtung, Erkennung und Himmelswerte.",
    "cal.relearnAsk": "Gelernte Maske verwerfen? Die Kamera lernt in der nächsten klaren Nacht eine neue. Gezeichnete Bereiche bleiben.",
    "cal.discarded": "Verworfen. Bis zur nächsten Nacht gilt der Bildkreis.",
    // public page on the hub
    "pub.title": "Öffentliche Seite",
    "pub.unreachable": "Der Hub ist gerade nicht erreichbar. Die öffentliche Seite liegt auf dem Hub und lässt sich nur ändern, wenn die Kamera verbunden ist.",
    "pub.help": "Eine Seite auf allskyhub.org, die jeder ohne Anmeldung sehen kann. Sie liegt auf dem Hub; Änderungen hier und dort sind dieselben.",
    "pub.offline": "Der Hub ist gerade nicht erreichbar: zuletzt bekannter Stand, nur lesbar.",
    "pub.on": "eingeschaltet",
    "pub.off": "ausgeschaltet",
    "pub.turnOn": "Einschalten",
    "pub.turnOff": "Ausschalten",
    "pub.offHelp": "Ausschalten macht die Adresse sofort ungültig; beim erneuten Einschalten gibt es eine neue.",
    "pub.texts": "Texte",
    "pub.coordinates": "Koordinaten zeigen (auf 0,1° gerundet, etwa 10 km)",
    "pub.notes": "Notizen",
    "pub.sections": "Abschnitte",
    "pub.save": "Seite speichern",
    "pub.offAsk": "Öffentliche Seite ausschalten? Die Adresse wird sofort ungültig.",
    "pub.turnedOn": "Eingeschaltet.",
    "pub.turnedOff": "Ausgeschaltet.",
    "pub.section.live": "Livebild",
    "pub.section.loop": "Loop der letzten Stunde",
    "pub.section.archive": "Archiv",
    "pub.section.events": "Ereignisse",
    "pub.section.sky": "Himmel (Charts)",
    "pub.section.aurora": "Polarlicht-Lage",
    "pub.section.observatory": "Sternwarte (N.I.N.A.)",
    "pub.section.aircraft": "Flugzeuge",
    "pub.text.title": "Titel",
    "pub.text.location_label": "Ort",
    "pub.text.camera": "Kamera",
    "pub.text.lens": "Objektiv",
    "pub.text.computer": "Rechner",
    "pub.text.owner": "Betrieben von",
    // system
    "sys.cameraHub": "Kamera und Hub",
    "sys.computer": "Rechner",
    "sys.restartTitle": "Neu starten",
    "sys.restartHelp": "Die Aufnahme läuft danach von selbst weiter. Gespeicherte Bilder bleiben erhalten.",
    "sys.restart": "Kamera-Dienst neu starten",
    "sys.reboot": "Rechner neu starten",
    "sys.shutdown": "Ausschalten",
    "sys.ask.restart": "Kamera-Dienst neu starten? Die Seite ist ein paar Sekunden nicht erreichbar.",
    "sys.ask.reboot": "Rechner neu starten? Die Kamera ist etwa eine Minute nicht erreichbar.",
    "sys.ask.shutdown": "Kamera ausschalten? Sie bleibt aus, bis jemand den Strom kurz trennt.",
    "sys.done.restart": "Der Dienst startet neu …",
    "sys.done.reboot": "Der Rechner startet neu …",
    "sys.done.shutdown": "Die Kamera fährt herunter. Warte 20 Sekunden, bevor du den Strom trennst.",
    "sys.device": "Gerät",
    "sys.profile": "Profil",
    "sys.paired": "Gekoppelt",
    "sys.connection": "Verbindung",
    "sys.disconnected": "getrennt",
    "sys.timezone": "Zeitzone",
    "sys.noHub": "keiner",
    "sys.agent": "Agent",
    "sys.frames": "Bilder seit Start",
    "sys.powerHelp": "Unterspannung macht Bilder und SD-Karte kaputt: ein Netzteil mit 5,1 V / 3 A nehmen.",
    "sys.updates": "Systemupdates",
    "sys.rebootPending": "Neustart für Sicherheitsupdates ausstehend",
    "sys.rebootPendingHelp": "Die Kamera startet tagsüber zwischen 10 und 16 Uhr von selbst neu, nie nachts.",
    "sys.model": "Modell",
    "sys.os": "System",
    "sys.uptime": "Läuft seit",
    "sys.load": "Last {l} ({n} Kerne)",
    "sys.memory": "Arbeitsspeicher",
    "sys.memoryOf": "{a} von {b}",
    "sys.diskFree": "{a} frei von {b}",
    "sys.portBusy": "Port belegt",
    "sys.portBusyHelp": "Port {port} oder 32227 nutzt schon ein anderes Programm (z. B. ASCOM Remote).",
    "sys.port": "Port {port}",
    "sys.safe": "sicher",
    "sys.unsafe": "unsicher",
    "sys.accurate": "genau",
    "sys.inaccurate": "ungenau",
    "power.under_voltage": "Unterspannung jetzt",
    "power.freq_capped": "Takt begrenzt",
    "power.throttled": "gedrosselt",
    "power.temp_limit": "Temperaturgrenze erreicht",
    "power.under_voltage_seen": "Unterspannung seit dem Start",
    "power.freq_capped_seen": "Takt war begrenzt",
    "power.throttled_seen": "war gedrosselt",
    "power.temp_limit_seen": "Temperaturgrenze war erreicht",
    // camera check
    "check.title": "Kamera-Check",
    "check.power": "Stromversorgung",
    "check.clock": "Uhrzeit",
    "check.disk": "Speicher",
    "check.frames": "Aufnahme",
    "check.mask": "Himmelsmaske",
    "check.orientation": "Ausrichtung",
    "check.hub": "Hub",
    "state.ok": "in Ordnung",
    "state.warn": "Hinweis",
    "state.fail": "Problem",
    // capture
    "cap.paused": "Pausiert. Es werden keine Bilder aufgenommen, auch nach einem Neustart nicht, bis du fortsetzt.",
    "cap.running": "Läuft. Zum Putzen der Kuppel kannst du die Aufnahme anhalten.",
    "cap.resume": "Aufnahme fortsetzen",
    "cap.pause": "Aufnahme anhalten",
    // log, support, backup
    "sup.title": "Protokoll und Support",
    "sup.log1": "Protokoll der Kamera (letzte {n} Zeile)",
    "sup.log": "Protokoll der Kamera (letzte {n} Zeilen)",
    "sup.refresh": "Aktualisieren",
    "sup.follow": "live mitlesen",
    "sup.help": "Das Support-Paket enthält Protokoll, Versionen, System- und Netzwerkdaten, Einstellungen und die Kalibrierung, aber keine Schlüssel, Passwörter oder Bilder. Hänge es an deine Frage an.",
    "sup.download": "Support-Paket herunterladen",
    "sup.backup": "Sicherung",
    "sup.backupHelp": "Einstellungen und gezeichnete Maskenbereiche, zum Beispiel für eine neue SD-Karte.",
    "sup.backupDownload": "Sicherung herunterladen",
    "sup.restore": "Sicherung einspielen",
    "sup.restoreAsk": "Sicherung „{f}“ einspielen? Die jetzigen Einstellungen werden ersetzt.",
    "sup.restored": "Eingespielt. Die Kamera startet die Aufnahme neu …",
    "sup.notBackup": "Das ist keine Sicherung dieser Kamera-Software.",
    "sup.restoreInvalid": "Nicht eingespielt, ungültig: {f}.",
    // updates
    "upd.title": "Updates",
    "upd.version": "Version",
    "upd.last": "Zuletzt",
    "upd.older": "Ältere Versionen",
    "upd.help": "Die Kamera sucht stündlich nach Updates und installiert sie nur am Tag. Läuft eine neue Version nicht sauber, geht sie von selbst zurück.",
    "upd.now": "Jetzt suchen und installieren",
    "upd.rollback": "Vorige Version",
    "upd.pending": "Angefragt, der Updater ist dran …",
    "upd.manual": "Auf dieser Installation werden Updates von Hand eingespielt.",
    "upd.nowAsk": "Jetzt nach einem Update suchen und es gleich installieren, auch nachts? Die Aufnahme pausiert dabei kurz.",
    "upd.rollbackAsk": "Zur vorigen Version zurückgehen? Die jetzige wird dann nicht von selbst wieder installiert.",
    "upd.state.up_to_date": "aktuell",
    "upd.state.downloading": "lädt herunter",
    "upd.state.waiting": "wartet auf den Tag",
    "upd.state.installing": "wird installiert",
    "upd.state.installed": "installiert",
    "upd.state.rolled_back": "zurückgenommen",
    "upd.state.failed": "fehlgeschlagen",
    "upd.code.unhealthy": "lief nicht sauber",
    "upd.code.asked": "auf Wunsch",
    "upd.code.no_space": "zu wenig Speicher",
    "upd.code.no_previous": "keine ältere Version vorhanden",
    "upd.code.bad_signature": "Signatur ungültig",
    "upd.code.checksum": "Prüfsumme falsch",
    "upd.code.bad_bundle": "Paket beschädigt",
    // network
    "net.title": "Netzwerk",
    "net.wifi": "WLAN",
    "net.lan": "LAN",
    "net.noAddress": "verbunden, keine Adresse",
    "net.notConnected": "nicht verbunden",
    "net.internet": "Internet",
    "net.hostname": "Name im Netz",
    "net.signal": "Signal {s} %",
    "net.other": "Anderes WLAN",
    "net.network": "Netz",
    "net.searching": "Suche …",
    "net.password": "Passwort",
    "net.passwordEmpty": "leer bei offenem Netz",
    "net.country": "Land (Funkregeln)",
    "net.fallback": "Klappt die Verbindung nicht, öffnet die Kamera nach 2 Minuten ihr eigenes WLAN <code>allskyhub-…</code>, über das du sie mit der App wieder einrichtest.",
    "net.join": "Verbinden",
    "net.unmanaged": "Das Netzwerk verwaltet auf dieser Installation das Betriebssystem, nicht die Kamera.",
    "net.secured": ", gesichert",
    "net.none": "Keine Netze gefunden",
    "net.searchFailed": "Suche fehlgeschlagen",
    "net.choose": "Bitte ein Netz wählen.",
    "net.joinAsk": "Mit „{s}“ verbinden? Die Kamera ist danach nur noch in diesem Netz erreichbar.",
    "net.switching": "Die Kamera wechselt zu „{s}“. Diese Seite ist jetzt nicht mehr erreichbar.",
    "net.bad.password": "Passwort (8 bis 63 Zeichen)",
    "net.bad.country": "Land (zwei Buchstaben)",
    "net.notApplied": "Nicht übernommen: {why}.",
    "net.invalid": "ungültig",
    // HTTPS
    "tls.kind.own": "eigenes Zertifikat",
    "tls.kind.self": "Zertifikat der Kamera (selbst signiert)",
    "tls.kind.none": "noch keines",
    "tls.status": "Status",
    "tls.on": "an, Port {port}",
    "tls.toHttps": "zur verschlüsselten Seite",
    "tls.where": "(Einstellungen → Sicherheit)",
    "tls.certificate": "Zertifikat",
    "tls.validUntil": "gültig bis {d}",
    "tls.fingerprint": "Fingerabdruck",
    "tls.for": "Für",
    "tls.selfHelp": "Beim selbst signierten Zertifikat warnt der Browser einmal. Vergleiche dann den Fingerabdruck.",
    "tls.own": "Eigenes Zertifikat verwenden",
    "tls.cert": "Zertifikat (PEM, mit Zwischenzertifikaten)",
    "tls.key": "Privater Schlüssel (PEM, ohne Passwort)",
    "tls.remove": "Zurück zum Kamera-Zertifikat",
    "tls.applied": "Übernommen. Die Kamera startet neu, die Seite ist gleich wieder da.",
    "tls.why.bad_certificate": "Das Zertifikat ist kein gültiges PEM.",
    "tls.why.bad_key": "Der Schlüssel ist kein gültiges PEM (oder hat ein Passwort).",
    "tls.why.key_mismatch": "Schlüssel und Zertifikat gehören nicht zusammen.",
    "tls.why.expired": "Das Zertifikat ist abgelaufen oder noch nicht gültig.",
    "tls.why.too_large": "Zu groß.",
  },
  en: {
    // general
    "app.title": "allskyhub camera",
    "loading": "Loading …",
    "loadFailed": "Could not be loaded ({e}).",
    "save": "Save",
    "saved": "Saved.",
    "notSaved": "Not saved.",
    "notApplied": "Not applied.",
    "apply": "Apply",
    "failed": "That did not work.",
    "download": "Download",
    "image": "Image",
    "night": "Night",
    "day": "Day",
    "time": "Time",
    "clock": "Clock",
    "exposure": "Exposure",
    "gain": "Gain",
    "sensor": "Sensor",
    "sensorTemp": "Sensor temperature",
    "capture": "Capture",
    "power": "Power supply",
    "storage": "Storage",
    "orientation": "Orientation",
    "hub": "Hub",
    "yes": "yes",
    "no": "no",
    "on": "on",
    "off": "off",
    "ok": "OK",
    "connected": "connected",
    "unreachable": "unreachable",
    // menu, pairing, lightbox (index.html)
    "nav.menu": "Main menu",
    "nav.live": "Live",
    "nav.archive": "Archive",
    "nav.events": "Events",
    "nav.sky": "Sky",
    "nav.settings": "Settings",
    "nav.system": "System",
    "nav.red": "Red light for the night",
    "nav.logout": "Log out",
    "pair.code": "Pairing code",
    "pair.help": "Enter the code in the app or on allskyhub.org. It changes every few minutes.",
    "lb.prev": "Previous image",
    "lb.next": "Next image",
    "lb.zoom": "Zoom in",
    "lb.zoomTitle": "Zoom (double click, mouse wheel)",
    "lb.close": "Close",
    // detections (SPEC §6.4)
    "kind.meteor": "Meteor",
    "kind.lightning": "Lightning",
    "kind.aurora": "Aurora",
    "kind.nlc": "Noctilucent clouds",
    "kind.satellite": "Satellite",
    "kind.clouds": "Clouds",
    "kind.sky_quality": "Sky quality",
    "reason.aircraft": "Aircraft",
    "reason.satellite": "Satellite",
    "reason.artifact": "Artefact",
    "reason.other": "Other",
    // products: short on the badges, longer on the night page
    "prod.keogram": "Keogram",
    "prod.startrails": "Star trails",
    "prod.timelapse": "Timelapse",
    "prod.day_timelapse": "Day timelapse",
    "prod.day_keogram": "Day keogram",
    "prodLong.day_timelapse": "Timelapse of the day",
    "prodLong.day_keogram": "Keogram of the day",
    // overlay fields and values
    "ovf.time": "Time",
    "ovf.date": "Date",
    "ovf.exposure": "Exposure",
    "ovf.gain": "Gain",
    "ovf.sensor_temp": "Sensor temperature",
    "ovf.sun": "Sun",
    "ovf.moon": "Moon",
    "ovf.cloud_cover": "Cloud cover",
    "ovf.sqm": "Sky brightness (SQM)",
    "ovf.stars": "Stars",
    "ovf.mode": "Day/night",
    "ovf.text1": "Own text 1",
    "ovf.text2": "Own text 2",
    "ovv.gain": "Gain {v}",
    "ovv.sun": "Sun {v}°",
    "ovv.moon": "Moon {p} % · {a}°",
    "ovv.clouds": "Clouds {v} %",
    "ovv.sqm": "SQM {v}",
    "ovv.stars": "{n} stars",
    // live
    "live.current": "Current image",
    "live.saveOverlay": "Save with overlay",
    "live.designOverlay": "Design the overlay",
    "live.waiting": "Waiting for the first image …",
    "live.paused": 'Capture is paused. <a href="#system">Resume it under System</a>.',
    "live.lastHour": "The last hour",
    "live.loop": "Loop of the last hour",
    "live.play": "Play",
    "live.pause": "Pause",
    "live.position": "Position",
    "live.mini": "Mini timelapse of the last hour",
    "live.focus": "Focus",
    "live.focusStart": "Start focus mode",
    "live.focusStop": "End focus mode",
    "live.resetBest": "Reset best value",
    "live.best": "Best value",
    "live.focusHelp": "Turn the focus ring in small steps: higher is sharper. If the number drops, you have gone too far, so turn back. Then tighten the locking screw and check the number once more.",
    "live.crop": "Image centre at full resolution",
    "live.taken": "Taken",
    "live.mode": "Mode",
    "live.brightness": "Brightness",
    "live.sun": "Sun",
    // archive
    "archive.none": "No nights saved yet.",
    "archive.frames": "{n} images",
    "archive.events": "{n} events",
    "keo.title": "Keolapse",
    "keo.help": "The keograms of several nights side by side, the oldest on the left.",
    "keo.from": "From",
    "keo.to": "To",
    "keo.show": "Show",
    "night.back": "‹ All nights",
    "night.title": "Night of {d}",
    "night.images": "Images",
    "night.jumpHour": "Jump to an hour",
    "night.jump": "Go",
    "night.earlier": "Load earlier",
    "night.later": "Load later",
    "night.delete": "Delete night",
    "night.deleteHelp": "Deletes all images, products and events of this night, here and on the hub. This cannot be undone.",
    "night.deleteAsk": "Really delete the night of {d}? All images, products and events will be lost, on the hub too.",
    "night.current": "The current night cannot be deleted.",
    "night.imageAt": "Image at {t}",
    "night.rejected": "rejected ({why})",
    "night.hideBad": "Hide rejected",
    "night.showBad": "Show {n} rejected",
    "bad.dark": "too dark",
    "bad.bright": "too bright",
    // events
    "ev.direction": "Direction {az}°, altitude {alt}°",
    "ev.flashes": "{n} flashes in 30 min",
    "ev.index": "Index {v}",
    "ev.confidence": "Confidence {p} %",
    "ev.confirmed": "confirmed",
    "ev.not": "not {kind}",
    "ev.auto": "automatic",
    "ev.classify": "Classify",
    "ev.reset": "reset",
    "ev.none": "Nothing detected yet.",
    "ev.noneNight": "Nothing detected this night.",
    "ev.hideFp": "Hide false alarms",
    "ev.showFp1": "Show {n} false alarm",
    "ev.showFp": "Show {n} false alarms",
    "ev.onlyFp": "Only false alarms this night.",
    "ev.whole": "Whole image",
    "ev.wholeCaption": "whole image",
    "ev.crop": "Detail",
    // sky charts
    "sky.none": "No readings yet.",
    "sky.noneNight": "No readings this night.",
    "sky.clouds": "Cloud cover",
    "sky.sqm": "Sky brightness",
    "sky.sqmHelp": "mag/″², higher is darker. Only measured during astronomical night.",
    "sky.stars": "Stars",
    "sky.exposureGain": "Exposure and gain",
    "sky.exposureGainHelp": "Blue: exposure in seconds, green: gain.",
    // settings
    "set.empty": "empty",
    "set.isSet": "set",
    "set.notSet": "not set",
    "set.latitude": "Latitude",
    "set.longitude": "Longitude",
    "set.here": "Location of this device",
    "set.none": "This camera does not store settings.",
    "set.nothing": "Nothing changed.",
    "set.saved": "Saved. The camera is restarting capture …",
    "set.notSaved": "Not saved: {what}.",
    "set.invalid": "invalid input",
    // overlay designer
    "ov.title": "Overlay",
    "ov.help": "What is shown over the image: in the live image and when saving with overlay. It is never written into the stored images.",
    "ov.preview": "Preview",
    "ov.show": "Show overlay",
    "ov.up": "Move {f} up",
    "ov.textPlaceholder": "e.g. Hilltop Observatory",
    "ov.size": "Font size",
    "ov.compass": "Compass points",
    "ov.stars": "Constellations (once the orientation is known)",
    "ov.save": "Save overlay",
    "ov.reset": "Default",
    "ov.saved": "Overlay saved.",
    "ov.invalid": "Not saved: invalid layout.",
    "corner.off": "off",
    "corner.tl": "top left",
    "corner.tr": "top right",
    "corner.bl": "bottom left",
    "corner.br": "bottom right",
    "size.small": "small",
    "size.normal": "normal",
    "size.large": "large",
    // sky mask and orientation
    "cal.title": "Sky mask and orientation",
    "cal.exclude": "Exclude area",
    "cal.include": "Include area",
    "cal.done": "Finish area",
    "cal.undo": "Undo point",
    "cal.grid": "Horizon and compass points",
    "cal.save": "Save mask",
    "cal.relearn": "Learn mask again",
    "cal.excluded": "Excluded {n}",
    "cal.included": "Included {n}",
    "cal.removeArea": "Remove area",
    "cal.remove": "Remove",
    "cal.learned": "Learned on {d}. Red: does not count as sky (trees, roofs, edge). The camera learns the mask again every clear night; drawn areas stay.",
    "cal.notLearned": "No mask learned yet; that happens in the first night. Until then the lens's image circle applies; drawn areas apply at once.",
    "cal.north": "North",
    "cal.northValue": "{deg}° in the image (clockwise from the top)",
    "cal.mirrored": ", mirrored",
    "cal.aligned": "Aligned",
    "cal.alignedValue": "{d} with {n} stars, deviation {rms}°",
    "cal.check": "Check",
    "cal.checkHelp": "Does N match reality, and does the yellow line lie on the horizon? If not, the orientation is wrong; it is determined again after 7 days.",
    "cal.notAligned": "Not determined yet. The camera finds it by itself from the stars on a clear, moonless night.",
    "cal.saved": "Saved. Applies from the next image to exposure, detection and sky values.",
    "cal.relearnAsk": "Discard the learned mask? The camera learns a new one in the next clear night. Drawn areas stay.",
    "cal.discarded": "Discarded. Until the next night the image circle applies.",
    // public page on the hub
    "pub.title": "Public page",
    "pub.unreachable": "The hub cannot be reached right now. The public page lives on the hub and can only be changed while the camera is connected.",
    "pub.help": "A page on allskyhub.org that anyone can see without logging in. It lives on the hub; changes here and there are the same.",
    "pub.offline": "The hub cannot be reached right now: last known state, read only.",
    "pub.on": "on",
    "pub.off": "off",
    "pub.turnOn": "Turn on",
    "pub.turnOff": "Turn off",
    "pub.offHelp": "Turning it off makes the address invalid at once; turning it on again gives a new one.",
    "pub.texts": "Texts",
    "pub.coordinates": "Show coordinates (rounded to 0.1°, about 10 km)",
    "pub.notes": "Notes",
    "pub.sections": "Sections",
    "pub.save": "Save page",
    "pub.offAsk": "Turn off the public page? The address becomes invalid at once.",
    "pub.turnedOn": "Turned on.",
    "pub.turnedOff": "Turned off.",
    "pub.section.live": "Live image",
    "pub.section.loop": "Loop of the last hour",
    "pub.section.archive": "Archive",
    "pub.section.events": "Events",
    "pub.section.sky": "Sky (charts)",
    "pub.section.aurora": "Aurora outlook",
    "pub.section.observatory": "Observatory (N.I.N.A.)",
    "pub.section.aircraft": "Aircraft",
    "pub.text.title": "Title",
    "pub.text.location_label": "Location",
    "pub.text.camera": "Camera",
    "pub.text.lens": "Lens",
    "pub.text.computer": "Computer",
    "pub.text.owner": "Run by",
    // system
    "sys.cameraHub": "Camera and hub",
    "sys.computer": "Computer",
    "sys.restartTitle": "Restart",
    "sys.restartHelp": "Capture carries on by itself afterwards. Stored images are kept.",
    "sys.restart": "Restart the camera service",
    "sys.reboot": "Restart the computer",
    "sys.shutdown": "Shut down",
    "sys.ask.restart": "Restart the camera service? The page will be unreachable for a few seconds.",
    "sys.ask.reboot": "Restart the computer? The camera will be unreachable for about a minute.",
    "sys.ask.shutdown": "Shut the camera down? It stays off until someone briefly cuts the power.",
    "sys.done.restart": "The service is restarting …",
    "sys.done.reboot": "The computer is restarting …",
    "sys.done.shutdown": "The camera is shutting down. Wait 20 seconds before you cut the power.",
    "sys.device": "Device",
    "sys.profile": "Profile",
    "sys.paired": "Paired",
    "sys.connection": "Connection",
    "sys.disconnected": "disconnected",
    "sys.timezone": "Time zone",
    "sys.noHub": "none",
    "sys.agent": "Agent",
    "sys.frames": "Images since start",
    "sys.powerHelp": "Under-voltage damages images and the SD card: use a 5.1 V / 3 A power supply.",
    "sys.updates": "System updates",
    "sys.rebootPending": "Reboot for security updates pending",
    "sys.rebootPendingHelp": "The camera reboots by itself during the day between 10:00 and 16:00, never at night.",
    "sys.model": "Model",
    "sys.os": "System",
    "sys.uptime": "Running for",
    "sys.load": "load {l} ({n} cores)",
    "sys.memory": "Memory",
    "sys.memoryOf": "{a} of {b}",
    "sys.diskFree": "{a} free of {b}",
    "sys.portBusy": "Port in use",
    "sys.portBusyHelp": "Port {port} or 32227 is already used by another program (e.g. ASCOM Remote).",
    "sys.port": "port {port}",
    "sys.safe": "safe",
    "sys.unsafe": "unsafe",
    "sys.accurate": "accurate",
    "sys.inaccurate": "inaccurate",
    "power.under_voltage": "Under-voltage now",
    "power.freq_capped": "Clock capped",
    "power.throttled": "throttled",
    "power.temp_limit": "Temperature limit reached",
    "power.under_voltage_seen": "Under-voltage since boot",
    "power.freq_capped_seen": "Clock was capped",
    "power.throttled_seen": "was throttled",
    "power.temp_limit_seen": "Temperature limit was reached",
    // camera check
    "check.title": "Camera check",
    "check.power": "Power supply",
    "check.clock": "Clock",
    "check.disk": "Storage",
    "check.frames": "Capture",
    "check.mask": "Sky mask",
    "check.orientation": "Orientation",
    "check.hub": "Hub",
    "state.ok": "OK",
    "state.warn": "Note",
    "state.fail": "Problem",
    // capture
    "cap.paused": "Paused. No images are taken, not even after a restart, until you resume.",
    "cap.running": "Running. You can pause capture to clean the dome.",
    "cap.resume": "Resume capture",
    "cap.pause": "Pause capture",
    // log, support, backup
    "sup.title": "Log and support",
    "sup.log1": "Camera log (last {n} line)",
    "sup.log": "Camera log (last {n} lines)",
    "sup.refresh": "Refresh",
    "sup.follow": "follow live",
    "sup.help": "The support bundle contains the log, versions, system and network data, settings and the calibration, but no keys, passwords or images. Attach it to your question.",
    "sup.download": "Download support bundle",
    "sup.backup": "Backup",
    "sup.backupHelp": "Settings and drawn mask areas, for example for a new SD card.",
    "sup.backupDownload": "Download backup",
    "sup.restore": "Restore backup",
    "sup.restoreAsk": "Restore the backup “{f}”? The current settings will be replaced.",
    "sup.restored": "Restored. The camera is restarting capture …",
    "sup.notBackup": "This is not a backup of this camera software.",
    "sup.restoreInvalid": "Not restored, invalid: {f}.",
    // updates
    "upd.title": "Updates",
    "upd.version": "Version",
    "upd.last": "Last",
    "upd.older": "Older versions",
    "upd.help": "The camera looks for updates every hour and installs them only in the daytime. If a new version does not run cleanly, it goes back by itself.",
    "upd.now": "Check and install now",
    "upd.rollback": "Previous version",
    "upd.pending": "Requested, the updater is on it …",
    "upd.manual": "On this installation updates are installed by hand.",
    "upd.nowAsk": "Look for an update now and install it right away, even at night? Capture pauses briefly.",
    "upd.rollbackAsk": "Go back to the previous version? The current one will then not be installed again by itself.",
    "upd.state.up_to_date": "up to date",
    "upd.state.downloading": "downloading",
    "upd.state.waiting": "waiting for daytime",
    "upd.state.installing": "installing",
    "upd.state.installed": "installed",
    "upd.state.rolled_back": "rolled back",
    "upd.state.failed": "failed",
    "upd.code.unhealthy": "did not run cleanly",
    "upd.code.asked": "on request",
    "upd.code.no_space": "not enough storage",
    "upd.code.no_previous": "no older version available",
    "upd.code.bad_signature": "invalid signature",
    "upd.code.checksum": "wrong checksum",
    "upd.code.bad_bundle": "damaged bundle",
    // network
    "net.title": "Network",
    "net.wifi": "Wi-Fi",
    "net.lan": "LAN",
    "net.noAddress": "connected, no address",
    "net.notConnected": "not connected",
    "net.internet": "Internet",
    "net.hostname": "Network name",
    "net.signal": "signal {s} %",
    "net.other": "Other Wi-Fi",
    "net.network": "Network",
    "net.searching": "Searching …",
    "net.password": "Password",
    "net.passwordEmpty": "empty for an open network",
    "net.country": "Country (radio rules)",
    "net.fallback": "If the connection fails, the camera opens its own Wi-Fi <code>allskyhub-…</code> after 2 minutes, through which you set it up again with the app.",
    "net.join": "Connect",
    "net.unmanaged": "On this installation the operating system manages the network, not the camera.",
    "net.secured": ", secured",
    "net.none": "No networks found",
    "net.searchFailed": "Search failed",
    "net.choose": "Please choose a network.",
    "net.joinAsk": "Connect to “{s}”? Afterwards the camera can only be reached in that network.",
    "net.switching": "The camera is switching to “{s}”. This page can no longer be reached.",
    "net.bad.password": "Password (8 to 63 characters)",
    "net.bad.country": "Country (two letters)",
    "net.notApplied": "Not applied: {why}.",
    "net.invalid": "invalid",
    // HTTPS
    "tls.kind.own": "own certificate",
    "tls.kind.self": "the camera's certificate (self-signed)",
    "tls.kind.none": "none yet",
    "tls.status": "Status",
    "tls.on": "on, port {port}",
    "tls.toHttps": "to the encrypted page",
    "tls.where": "(Settings → Security)",
    "tls.certificate": "Certificate",
    "tls.validUntil": "valid until {d}",
    "tls.fingerprint": "Fingerprint",
    "tls.for": "For",
    "tls.selfHelp": "With the self-signed certificate the browser warns once. Then compare the fingerprint.",
    "tls.own": "Use your own certificate",
    "tls.cert": "Certificate (PEM, with intermediate certificates)",
    "tls.key": "Private key (PEM, without password)",
    "tls.remove": "Back to the camera's certificate",
    "tls.applied": "Applied. The camera is restarting; the page will be back shortly.",
    "tls.why.bad_certificate": "The certificate is not valid PEM.",
    "tls.why.bad_key": "The key is not valid PEM (or has a password).",
    "tls.why.key_mismatch": "Key and certificate do not belong together.",
    "tls.why.expired": "The certificate has expired or is not valid yet.",
    "tls.why.too_large": "Too large.",
  },
};

const $ = (id) => document.getElementById(id);
const page = $("page");
// SPEC §7 Language and units: the setting `language` (de default, en) picks the texts;
// it is read once at start, before the first view is drawn. `<html lang>` follows it, and
// the schema texts follow it too so nothing is mixed (SPEC §6.9).
let LANG = "de";
const text = (s) => (s == null ? "" : typeof s === "string" ? s : (LANG === "en" && s.en) || s.de);
// The dictionary entry for key, or null when there is none (names that come from the camera).
const tn = (key) => T[LANG][key] ?? T.de[key] ?? null;
// The text for key with {name} filled from vars. The values go in as they are: escape them
// where the text ends up as HTML.
const t = (key, vars) => {
  const s = tn(key) ?? key;
  return vars ? s.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m)) : s;
};
const LOCALE = () => (LANG === "en" ? "en-GB" : "de-AT");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// What a detection is called (SPEC §6.4); unknown kinds show as they come.
const kindName = (k) => tn("kind." + k) || k;
// The calibration sends German compass letters (N O S W); English has E for east.
const compassLabel = (l) => (LANG === "en" && l === "O" ? "E" : l);

// The static HTML: data-i18n fills the text, data-i18n-title and data-i18n-label the
// title and aria-label attributes.
function translateStatic() {
  document.documentElement.lang = LANG;
  document.title = t("app.title");
  for (const el of document.querySelectorAll("[data-i18n]")) el.textContent = t(el.dataset.i18n);
  for (const el of document.querySelectorAll("[data-i18n-title]")) el.title = t(el.dataset.i18nTitle);
  for (const el of document.querySelectorAll("[data-i18n-label]")) el.setAttribute("aria-label", t(el.dataset.i18nLabel));
}
// Times in the camera's time zone, not the browser's (set from /api/setup at start).
let CAMERA_TZ;
const tzOpt = () => (CAMERA_TZ ? { timeZone: CAMERA_TZ } : {});
const fmtTime = (iso) => new Date(iso).toLocaleTimeString(LOCALE(), { hour: "2-digit", minute: "2-digit", ...tzOpt() });
const fmtDateTime = (iso) => new Date(iso).toLocaleString(LOCALE(),
  { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", ...tzOpt() });
// SPEC §7: temperatures as the owner chose (setting temperature_unit), always °C inside.
let TEMP_UNIT = "c";
const fmtTemp = (c) => (TEMP_UNIT === "f" ? `${(c * 9 / 5 + 32).toFixed(1)} °F` : `${c.toFixed(1)} °C`);
const camHour = (iso) => Number(new Date(iso).toLocaleString("en-GB", { hour: "2-digit", hourCycle: "h23", ...tzOpt() }));
const fmtNight = (id) => [id.slice(6, 8), id.slice(4, 6), id.slice(0, 4)].join(LANG === "en" ? "/" : ".");
const fmtExposure = (us) => (us >= 1e6 ? (us / 1e6).toFixed(1) + " s" : (us / 1000).toFixed(2) + " ms");
const fileUrl = (night, name, thumb) => `files/${night}/${encodeURIComponent(name)}${thumb ? "?thumb=1" : ""}`;

let timers = [];
const every = (fn, ms) => { fn(); timers.push(setInterval(fn, ms)); };
const clearTimers = () => { timers.forEach(clearInterval); timers = []; };

async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (r.status === 401) { location.href = "local-login"; throw new Error("login"); }
  if (!r.ok) throw new Error(String(r.status));
  return r.json();
}

// --- lightbox ----------------------------------------------------------------------
let lbItems = [];
let lbIndex = 0;
function lightbox(items, index) {
  lbItems = items;
  lbIndex = index;
  showLightbox();
  $("lightbox").showModal();
}
// Zoom: double click, wheel or the button; drag to move while zoomed.
let lbScale = 1;
let lbX = 0;
let lbY = 0;
function lbApply() {
  $("lbWrap").style.transform = `translate(${lbX}px, ${lbY}px) scale(${lbScale})`;
  $("lbStage").classList.toggle("zoomed", lbScale > 1);
}
function lbZoom(scale) {
  lbScale = Math.min(8, Math.max(1, scale));
  if (lbScale === 1) { lbX = 0; lbY = 0; }
  lbApply();
}
$("lbZoom").onclick = () => lbZoom(lbScale >= 4 ? 1 : lbScale * 2);
$("lbStage").addEventListener("dblclick", () => lbZoom(lbScale > 1 ? 1 : 3));
$("lbStage").addEventListener("wheel", (e) => { e.preventDefault(); lbZoom(lbScale * (e.deltaY < 0 ? 1.25 : 0.8)); }, { passive: false });
{
  let drag = null;
  $("lbStage").addEventListener("pointerdown", (e) => {
    if (lbScale === 1) return;
    drag = { x: e.clientX - lbX, y: e.clientY - lbY };
    $("lbStage").setPointerCapture(e.pointerId);
  });
  $("lbStage").addEventListener("pointermove", (e) => {
    if (!drag) return;
    lbX = e.clientX - drag.x;
    lbY = e.clientY - drag.y;
    lbApply();
  });
  $("lbStage").addEventListener("pointerup", () => { drag = null; });
}
function showLightbox() {
  const it = lbItems[lbIndex];
  lbZoom(1);
  $("lightboxImg").src = it.src;
  // An item may bring the overlay (SPEC §7 Overlay): a function of the picture's size.
  const ovl = $("lbOverlay");
  ovl.innerHTML = "";
  ovl.hidden = !it.overlay;
  if (it.overlay) {
    const img = $("lightboxImg");
    const draw = () => {
      if (!img.naturalWidth) return;
      ovl.setAttribute("viewBox", `0 0 1000 ${(1000 * img.naturalHeight / img.naturalWidth).toFixed(1)}`);
      ovl.innerHTML = it.overlay(img.naturalWidth, img.naturalHeight);
    };
    if (img.complete) draw(); else img.addEventListener("load", draw, { once: true });
  }
  $("lbDownload").href = it.src;
  $("lbCaption").textContent = it.caption || "";
  $("lbPrev").disabled = lbIndex === 0;
  $("lbNext").disabled = lbIndex === lbItems.length - 1;
  // Optional buttons of the item, e.g. "full frame" or "not a meteor" on an event.
  const extra = $("lbExtra");
  extra.innerHTML = "";
  extra.hidden = !it.actions;
  for (const a of it.actions || []) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "secondary";
    b.textContent = a.label;
    b.onclick = () => a.run(it);
    extra.appendChild(b);
  }
}
$("lbPrev").onclick = () => { if (lbIndex > 0) { lbIndex--; showLightbox(); } };
$("lbNext").onclick = () => { if (lbIndex < lbItems.length - 1) { lbIndex++; showLightbox(); } };
$("lbClose").onclick = () => $("lightbox").close();
// A click on the dark backdrop (outside the dialog's box) closes it too.
$("lightbox").addEventListener("click", (e) => {
  const r = $("lightbox").getBoundingClientRect();
  if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) $("lightbox").close();
});
document.addEventListener("keydown", (e) => {
  if (!$("lightbox").open) return;
  if (e.key === "ArrowLeft") $("lbPrev").click();
  if (e.key === "ArrowRight") $("lbNext").click();
});

// --- Overlay (L11) ------------------------------------------------------------------
// Drawn over the image when it is shown; the stored frames stay as the camera took them.
// On unless this browser switched it off.
const loadPref = (k) => { try { return localStorage.getItem("ash-ov-" + k) !== "0"; } catch { return true; } };
// Default closed: only true when this browser opened it before.
const localPref = (k) => { try { return localStorage.getItem("ash-ov-" + k) === "1"; } catch { return false; } };
const savePref = (k, on) => { try { localStorage.setItem("ash-ov-" + k, on ? "1" : "0"); } catch { /* private mode */ } };

// SPEC §7 Overlay: the camera's layout, four corners of fields, own text lines, compass,
// constellations. `extra` is /api/status's sky and moon.
// The fields in their default order; their names are "ovf.<key>" in the dictionary.
const OV_FIELDS = ["time", "date", "exposure", "gain", "sensor_temp", "sun", "moon", "cloud_cover",
  "sqm", "stars", "mode", "text1", "text2"];
const OV_SIZE = { small: 0.8, normal: 1, large: 1.35 };
const DEFAULT_LAYOUT = { enabled: true, corners: { tl: ["date", "time"], tr: ["exposure", "gain", "sensor_temp"],
  bl: ["sun", "moon"], br: ["cloud_cover", "sqm"] }, texts: ["", ""], size: "normal", compass: true, stars: true };
function ovValue(key, f, extra, layout) {
  const sky = extra && extra.sky;
  const moon = extra && extra.moon;
  const at = f ? new Date(f.captured_at) : null;
  switch (key) {
    case "time": return at && at.toLocaleTimeString(LOCALE(), { hour: "2-digit", minute: "2-digit", second: "2-digit", ...tzOpt() });
    case "date": return at && at.toLocaleDateString(LOCALE(), { day: "2-digit", month: "2-digit", year: "numeric", ...tzOpt() });
    case "exposure": return f && fmtExposure(f.exposure_us);
    case "gain": return f && t("ovv.gain", { v: f.gain });
    case "sensor_temp": return f && f.sensor_temp_c != null ? fmtTemp(f.sensor_temp_c) : null;
    case "sun": return f && t("ovv.sun", { v: f.sun_elevation.toFixed(1) });
    case "moon": return moon && t("ovv.moon", { p: Math.round(moon.illumination * 100), a: moon.altitude_deg.toFixed(0) });
    case "cloud_cover": return sky && sky.cloud_cover != null ? t("ovv.clouds", { v: Math.round(sky.cloud_cover * 100) }) : null;
    case "sqm": return sky && sky.sqm_mag != null ? t("ovv.sqm", { v: sky.sqm_mag.toFixed(2) }) : null;
    case "stars": return sky && sky.stars != null ? t("ovv.stars", { n: sky.stars }) : null;
    case "mode": return f && (f.mode === "night" ? t("night") : t("day"));
    case "text1": return layout.texts[0] || null;
    case "text2": return layout.texts[1] || null;
    default: return null;
  }
}
function overlaySvg(f, extra, orientation, layout, w, h, fixedColours = false, sky = null) {
  const H = w ? 1000 * h / w : 562.5;
  const fs = 22 * (OV_SIZE[layout.size] || 1);  // in viewBox units: about 2 % of the width
  const cls = fixedColours ? ' fill="#fff" stroke="rgba(0,0,0,.75)" stroke-width="4" paint-order="stroke"' : "";
  let out = "";
  if (!layout.enabled) return out;
  if (layout.stars && sky) {
    // Pixels of the solved frame, scaled to the drawing's 1000 units wide.
    const k = 1000 / sky.width;
    const line = fixedColours ? ' stroke="rgba(160,190,255,.7)"' : "";
    const label = fixedColours ? ' fill="rgba(200,215,255,.9)"' : "";
    out += `<g class="ov-sky" transform="scale(${k.toFixed(5)})">
      ${sky.paths.map((d) => `<path d="${d}" fill="none" stroke-width="${(1.6 / k).toFixed(2)}"${line}/>`).join("")}
      ${sky.names.map((n) => `<text x="${n.x}" y="${n.y}" font-size="${sky.font}" text-anchor="middle"${label}>${esc(n.text)}</text>`).join("")}
    </g>`;
  }
  const step = fs * 1.25;
  for (const [corner, keys] of Object.entries(layout.corners)) {
    const lines = keys.map((k) => ovValue(k, f, extra, layout)).filter((t) => t);
    const right = corner.endsWith("r");
    const bottom = corner.startsWith("b");
    const x = right ? 1000 - 16 : 16;
    lines.forEach((t, i) => {
      const y = bottom ? H - 16 - step * (lines.length - 1 - i) : 16 + step * (i + 1) - fs * 0.25;
      out += `<text class="ov-text" x="${x}" y="${y.toFixed(1)}" font-size="${fs.toFixed(1)}"${right ? ' text-anchor="end"' : ""}${cls}>${esc(t)}</text>`;
    });
  }
  const o = orientation && orientation.overlay;
  if (layout.compass && o) {
    out += o.points.map((p) => `<text class="ov-compass" x="${(p.x * 1000).toFixed(1)}" y="${(p.y * H).toFixed(1)}" font-size="${fs * 1.6}"
      text-anchor="middle" dominant-baseline="middle"${cls}>${compassLabel(p.label)}</text>`).join("");
  }
  return out;
}

// The shown image plus the overlay as one JPEG, made in the browser.
function saveWithOverlay(img, svgInner, f) {
  if (!img.naturalWidth) return;
  const w = img.naturalWidth, h = img.naturalHeight;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 1000 ${(1000 * h / w).toFixed(1)}"
    font-family="system-ui, sans-serif" font-weight="600">${svgInner}</svg>`;
  const layer = new Image();
  layer.onload = () => {
    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    const g = c.getContext("2d");
    g.drawImage(img, 0, 0);
    g.drawImage(layer, 0, 0, w, h);
    c.toBlob((blob) => {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      const stamp = f ? f.name.replace(/\.jpg$/, "") : "allsky";
      a.download = `${stamp}-overlay.jpg`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    }, "image/jpeg", 0.92);
  };
  layer.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
}

// --- Live (L3) ---------------------------------------------------------------------
function viewLive() {
  page.innerHTML = `
    <section class="card stack">
      <div class="live-stage">
        <img class="live-img" id="live" alt="${t("live.current")}">
        <svg id="liveOverlay" class="overlay" viewBox="0 0 1000 562.5" aria-hidden="true"></svg>
      </div>
      <div class="row">
        <button type="button" class="secondary small" id="ovShow" aria-pressed="false">${t("ov.title")}</button>
        <button type="button" class="secondary small" id="ovSave">${t("live.saveOverlay")}</button>
        <a class="small" href="#einstellungen/overlay">${t("live.designOverlay")}</a>
      </div>
      <dl class="facts" id="facts"><dt>${t("image")}</dt><dd>${t("live.waiting")}</dd></dl>
      <p class="msg warn" id="pausedMsg" hidden>${t("live.paused")}</p>
    </section>
    <section class="card stack">
      <h2>${t("live.lastHour")}</h2>
      <img class="live-img" id="loopImg" alt="${t("live.loop")}">
      <div class="loop-controls">
        <button type="button" class="secondary small" id="loopPlay">${t("live.play")}</button>
        <input type="range" id="loopPos" min="0" max="0" value="0" aria-label="${t("live.position")}">
        <span class="muted small" id="loopTime"></span>
      </div>
      <video class="live-img" id="mini" controls muted loop playsinline preload="none" hidden
        aria-label="${t("live.mini")}"></video>
    </section>
    <section class="card stack">
      <h2>${t("live.focus")}</h2>
      <div class="row">
        <button id="focusBtn">${t("live.focusStart")}</button>
        <button class="secondary" id="resetBtn" hidden>${t("live.resetBest")}</button>
      </div>
      <div id="focusPanel" hidden>
        <div class="row"><span class="score" id="score">–</span><div class="bar"><div id="bar"></div></div></div>
        <p class="muted">${t("live.best")} <b id="best">–</b>. ${t("live.focusHelp")}</p>
        <img class="live-img" id="crop" alt="${t("live.crop")}">
      </div>
    </section>`;
  let lastFrames = -1;
  let focus = false;
  let lastFrame = null;
  let orientation = null;
  getJSON("api/calibration").then((c) => { orientation = c.orientation; drawOverlay(); }).catch(() => {});
  // The layout is the camera's (SPEC §7 Overlay); showing it is this browser's choice.
  let layout = DEFAULT_LAYOUT;
  let extra = null;
  let shown = loadPref("show");
  getJSON("api/overlay").then((l) => { layout = l; drawOverlay(); }).catch(() => {});
  let sky = null;  // constellations for the shown frame (packages/sky, as on the hub)
  let skyFor = null;
  const loadSky = () => {
    if (!lastFrame || lastFrame.captured_at === skyFor) return;
    skyFor = lastFrame.captured_at;
    getJSON("api/sky-overlay?at=" + encodeURIComponent(lastFrame.captured_at)).then((s) => {
      sky = s;
      drawOverlay();
    }).catch(() => {});
  };
  const syncShow = () => { $("ovShow").classList.toggle("active", shown); $("ovShow").setAttribute("aria-pressed", String(shown)); };
  syncShow();
  $("ovShow").onclick = () => { shown = !shown; savePref("show", shown); syncShow(); drawOverlay(); };
  // The overlay is drawn on top, never into the stored image (L11): Benjamin, 2026-10-12.
  const drawOverlay = () => {
    const img = $("live"), svg = $("liveOverlay");
    if (!img || !svg) return;
    svg.innerHTML = shown ? overlaySvg(lastFrame, extra, orientation, layout, img.naturalWidth, img.naturalHeight, false, sky) : "";
    if (img.naturalWidth) svg.setAttribute("viewBox", `0 0 1000 ${(1000 * img.naturalHeight / img.naturalWidth).toFixed(1)}`);
  };
  $("live").addEventListener("load", drawOverlay);
  $("ovSave").onclick = () => saveWithOverlay($("live"), overlaySvg(lastFrame, extra, orientation,
    { ...layout, enabled: true }, $("live").naturalWidth, $("live").naturalHeight, true, sky), lastFrame);
  $("live").onclick = () => lightbox([{ src: $("live").src, caption: t("live.current"),
    overlay: shown ? (w, h) => overlaySvg(lastFrame, extra, orientation, layout, w, h, false, sky) : null }], 0);
  const render = (s) => {
    $("version").textContent = s.version;
    focus = s.focus_mode;
    $("focusBtn").textContent = focus ? t("live.focusStop") : t("live.focusStart");
    $("focusPanel").hidden = !focus;
    $("resetBtn").hidden = !focus;
    extra = { sky: s.sky, moon: s.moon };
    if (s.frame) {
      const f = s.frame;
      lastFrame = f;
      loadSky();
      $("facts").innerHTML = `
        <dt>${t("live.taken")}</dt><dd>${fmtTime(f.captured_at)}</dd>
        <dt>${t("live.mode")}</dt><dd>${f.mode === "night" ? t("night") : t("day")}</dd>
        <dt>${t("exposure")}</dt><dd>${fmtExposure(f.exposure_us)}</dd>
        <dt>${t("gain")}</dt><dd>${f.gain}</dd>
        <dt>${t("live.brightness")}</dt><dd>${f.mean.toFixed(2)}</dd>
        <dt>${t("live.sun")}</dt><dd>${f.sun_elevation.toFixed(1)}°</dd>
        ${f.sensor_temp_c != null ? `<dt>${t("sensor")}</dt><dd>${fmtTemp(f.sensor_temp_c)}</dd>` : ""}`;
    }
    if (focus && s.sharpness != null) {
      $("score").textContent = Math.round(s.sharpness);
      $("best").textContent = s.sharpness_max != null ? Math.round(s.sharpness_max) : "–";
      $("bar").style.width = (s.sharpness_max ? Math.min(100, 100 * s.sharpness / s.sharpness_max) : 0) + "%";
    }
    $("pausedMsg").hidden = !s.paused;
    if (s.frames !== lastFrames) {
      lastFrames = s.frames;
      $("live").src = "api/live.jpg?n=" + s.frames;
      if (focus) $("crop").src = "api/crop.jpg?n=" + s.frames;
    }
  };
  const post = async (action) => {
    const r = await fetch("api/focus", { method: "POST", body: action });
    if (r.ok) render(await r.json());
  };
  $("focusBtn").onclick = () => post(focus ? "off" : "on");
  $("resetBtn").onclick = () => post("reset");
  every(() => getJSON("api/status").then(render).catch(() => {}), 2000);

  // SPEC §5.2: the mini timelapse, when it is switched on and built.
  fetch("api/mini.mp4", { method: "HEAD" }).then((r) => {
    if (!r.ok) return;
    const v = $("mini");
    v.src = "api/mini.mp4?t=" + Date.now();
    v.hidden = false;
  }).catch(() => {});
  // The loop: thumbnails of the last hour, refreshed every minute.
  let frames = [];
  let playing = null;
  const show = (i) => {
    const f = frames[i];
    if (!f) return;
    $("loopImg").src = fileUrl(f.night_id, f.name, true);
    $("loopTime").textContent = fmtTime(f.captured_at);
    $("loopPos").value = i;
  };
  $("loopPos").oninput = (e) => show(Number(e.target.value));
  $("loopPlay").onclick = () => {
    if (playing) { clearInterval(playing); playing = null; $("loopPlay").textContent = t("live.play"); return; }
    $("loopPlay").textContent = t("live.pause");
    playing = setInterval(() => show((Number($("loopPos").value) + 1) % Math.max(1, frames.length)), 250);
    timers.push(playing);
  };
  every(() => getJSON("api/loop").then((l) => {
    const atEnd = Number($("loopPos").value) >= frames.length - 1;
    frames = l;
    $("loopPos").max = Math.max(0, frames.length - 1);
    if (atEnd && !playing) show(frames.length - 1);
  }).catch(() => {}), 60000);
}

// --- Archive (L4) --------------------------------------------------------------------
async function viewArchive(night) {
  if (night) return viewNight(night);
  page.innerHTML = `<h1>${t("nav.archive")}</h1><section class="card stack" id="keolapse" hidden></section>
    <div class="nights" id="nights"><p class="muted">${t("loading")}</p></div>`;
  const nights = await getJSON("api/nights");
  if (!nights.length) { $("nights").innerHTML = `<p class="empty">${t("archive.none")}</p>`; return; }
  keolapseCard(nights);
  $("nights").innerHTML = nights.map((n) => `
    <a class="night" href="#archiv/${n.night_id}">
      <div class="card stack">
        ${n.cover ? `<img class="night-cover" loading="lazy" alt="" src="${fileUrl(n.night_id, n.cover, true)}">` : ""}
        <h2>${fmtNight(n.night_id)}</h2>
        <p class="muted small">${t("archive.frames", { n: n.frames })}${n.first ? ` · ${fmtTime(n.first)}–${fmtTime(n.last)}` : ""}</p>
        <div class="badges">
          ${[...n.products, ...(n.day_products || [])].map((p) => `<span class="badge ok">${esc(tn("prod." + p.kind) || p.kind)}</span>`).join("")}
          ${n.events ? `<span class="badge">${t("archive.events", { n: n.events })}</span>` : ""}
        </div>
      </div>
    </a>`).join("");
}

// SPEC §5.4: the keograms of a range of nights side by side, built on request.
function keolapseCard(nights) {
  const withKeo = nights.filter((n) => n.products.some((p) => p.kind === "keogram"));
  if (withKeo.length < 2) return;
  const box = $("keolapse");
  const opts = (sel) => withKeo.map((n) => `<option value="${n.night_id}"${n.night_id === sel ? " selected" : ""}>${fmtNight(n.night_id)}</option>`).join("");
  const last = withKeo[0].night_id;
  const first = withKeo[Math.min(withKeo.length, 30) - 1].night_id;
  box.innerHTML = `<h2>${t("keo.title")}</h2>
    <p class="muted small">${t("keo.help")}</p>
    <div class="row"><label>${t("keo.from")} <select id="keoFrom">${opts(first)}</select></label>
      <label>${t("keo.to")} <select id="keoTo">${opts(last)}</select></label>
      <button type="button" class="secondary small" id="keoShow">${t("keo.show")}</button></div>
    <a id="keoLink" target="_blank" rel="noopener" hidden><img id="keoImg" class="keolapse" alt="${t("keo.title")}"></a>`;
  box.hidden = false;
  $("keoShow").onclick = () => {
    const src = `api/keolapse.jpg?from=${$("keoFrom").value}&to=${$("keoTo").value}`;
    $("keoImg").src = src;
    $("keoLink").href = src;
    $("keoLink").hidden = false;
  };
}

async function viewNight(night) {
  page.innerHTML = `<p><a class="back" href="#archiv">${t("night.back")}</a></p><h1>${t("night.title", { d: fmtNight(night) })}</h1>
    <div class="products" id="products"></div>
    <section class="card stack"><h2>${t("night.images")}</h2>
      <div class="row jump" id="jump" hidden>
        <div class="chips" id="hours" role="group" aria-label="${t("night.jumpHour")}"></div>
        <label class="inline">${t("time")} <input type="time" id="jumpTime"></label>
        <button type="button" class="secondary small" id="jumpGo">${t("night.jump")}</button>
      </div>
      <div class="actions"><button type="button" class="secondary" id="less" hidden>${t("night.earlier")}</button></div>
      <div class="thumbs" id="thumbs"></div>
      <div class="actions"><button type="button" class="secondary" id="more" hidden>${t("night.later")}</button>
        <button type="button" class="secondary" id="bad" hidden aria-pressed="false"></button></div></section>
    <section class="card stack"><h2>${t("night.delete")}</h2>
      <p class="muted small">${t("night.deleteHelp")}</p>
      <div class="actions"><button type="button" class="danger" id="delNight">${t("night.delete")}</button></div>
      <p class="msg" id="delMsg" hidden></p></section>`;
  $("delNight").onclick = async () => {
    if (!confirm(t("night.deleteAsk", { d: fmtNight(night) }))) return;
    const r = await fetch(`api/nights/${night}/delete`, { method: "POST" });
    if (r.status === 401) { location.href = "local-login"; return; }
    const m = $("delMsg");
    m.hidden = false;
    if (r.ok) { location.hash = "#archiv"; return; }
    m.className = "msg error";
    m.textContent = r.status === 409 ? t("night.current") : t("failed");
  };
  const nights = await getJSON("api/nights");
  const n = nights.find((x) => x.night_id === night);
  // The day products have longer names here than on the badges ("prodLong." before "prod.").
  const productName = (k) => tn("prodLong." + k) || tn("prod." + k);
  $("products").innerHTML = (n ? [...n.products, ...(n.day_products || [])] : []).map((p) => {
    const src = `files/${night}/products/${p.name}`;
    const media = p.content_type === "video/mp4"
      ? `<video controls preload="metadata" src="${src}" poster="${src}?thumb=1"></video>`
      : `<a href="${src}" target="_blank" rel="noopener"><img loading="lazy" src="${src}?thumb=1" alt=""></a>`;
    return `<section class="card stack product"><h2>${productName(p.kind) || esc(p.kind)}</h2>${media}
      <a class="button secondary small" href="${src}" download>${t("download")}</a></section>`;
  }).join("");
  // SPEC §4.9: bad frames are hidden unless asked for.
  const badCount = n ? n.bad || 0 : 0;
  let showBad = false;
  const badLabel = (why) => tn("bad." + why);
  const PAGE = 200;
  let lo = 0;      // index of the first loaded frame
  let total = 0;
  let all = [];    // the loaded frames, lo .. lo + all.length
  const url = (offset, extra = "") => `api/nights/${night}/frames?offset=${offset}&limit=${PAGE}${showBad ? "&bad=1" : ""}${extra}`;
  const render = (focus) => {
    const box = $("thumbs");
    box.innerHTML = "";
    all.forEach((f, i) => {
      const b = document.createElement("button");
      b.type = "button";
      if (f.bad) b.className = "bad";
      b.innerHTML = `<img loading="lazy" alt="" src="${fileUrl(night, f.name, true)}"><span class="t">${fmtTime(f.captured_at)}</span>`;
      b.setAttribute("aria-label", t("night.imageAt", { t: fmtTime(f.captured_at) }));
      b.onclick = () => lightbox(all.map((x) => ({ src: fileUrl(night, x.name, false),
        caption: `${fmtDateTime(x.captured_at)} · ${fmtExposure(x.exposure_us)} · ${t("ovv.gain", { v: x.gain })}`
          + (x.bad ? ` · ${t("night.rejected", { why: badLabel(x.bad) || esc(x.bad) })}` : "") })), i);
      box.appendChild(b);
    });
    $("less").hidden = lo <= 0;
    $("more").hidden = lo + all.length >= total;
    if (focus != null && box.children[focus]) box.children[focus].scrollIntoView({ block: "center" });
  };
  const loadAt = async (offset, extra = "") => {
    const data = await getJSON(url(offset, extra));
    total = data.total;
    lo = data.offset ?? offset;
    all = data.frames;
    render(extra ? 0 : null);
  };
  $("more").onclick = async () => {
    const data = await getJSON(url(lo + all.length));
    total = data.total;
    all.push(...data.frames);
    render();
  };
  $("less").onclick = async () => {
    const from = Math.max(0, lo - PAGE);
    const data = await getJSON(url(from));
    const keep = all.length;
    all = [...data.frames.slice(0, lo - from), ...all];
    lo = from;
    render(all.length - keep);
  };
  // Jump to a time: hour buttons for the hours the night has, and a free time field.
  if (n && n.first && n.last) {
    // Every camera-time hour from the first frame's to the last frame's.
    const hours = [];
    const end = Date.parse(n.last);
    for (let t = Date.parse(n.first) - 3600e3; t <= end + 3600e3; t += 900e3) {
      const h = camHour(new Date(Math.min(Math.max(t, Date.parse(n.first)), end)).toISOString());
      if (!hours.includes(h)) hours.push(h);
    }
    $("hours").innerHTML = hours.map((h) => `<button type="button" class="chip" data-h="${h}">${String(h).padStart(2, "0")}</button>`).join("");
    $("hours").onclick = (e) => {
      const h = e.target.closest("[data-h]")?.dataset.h;
      if (h != null) loadAt(0, `&at=${h}:00`);
    };
    $("jump").hidden = false;
  }
  $("jumpGo").onclick = () => { if ($("jumpTime").value) loadAt(0, `&at=${$("jumpTime").value}`); };
  $("jumpTime").onkeydown = (e) => { if (e.key === "Enter") $("jumpGo").click(); };
  const bad = $("bad");
  const label = () => { bad.textContent = showBad ? t("night.hideBad") : t("night.showBad", { n: badCount }); };
  bad.hidden = !badCount;
  label();
  bad.onclick = async () => {
    showBad = !showBad;
    bad.setAttribute("aria-pressed", String(showBad));
    label();
    await loadAt(0);
  };
  await loadAt(0);
}

// --- night picker shared by events and sky ----------------------------------------------
async function nightPicker(current, route) {
  const nights = await getJSON("api/nights");
  const night = current || (nights[0] && nights[0].night_id);
  const options = nights.map((n) => `<option value="${n.night_id}"${n.night_id === night ? " selected" : ""}>${fmtNight(n.night_id)}</option>`).join("");
  return { night, html: `<label>${t("night")} <select class="night-select" id="nightSel">${options}</select></label>`,
    bind: () => { const s = $("nightSel"); if (s) s.onchange = () => { location.hash = `${route}/${s.value}`; }; } };
}

// --- Events (L5) ---------------------------------------------------------------------
function eventFacts(e) {
  const d = e.data || {};
  const out = [];
  if (d.azimuth_deg != null) out.push(t("ev.direction", { az: Math.round(d.azimuth_deg), alt: Math.round(d.altitude_deg) }));
  if (e.kind === "meteor" && d.shower) out.push(esc(d.shower));
  if (e.kind === "meteor" && d.length_px) out.push(`${d.length_px} px`);
  if (e.kind === "lightning" && d.storm_flashes) out.push(t("ev.flashes", { n: d.storm_flashes }));
  if ((e.kind === "aurora" || e.kind === "nlc") && d.peak_index != null) out.push(t("ev.index", { v: d.peak_index }));
  out.push(t("ev.confidence", { p: Math.round(e.confidence * 100) }));
  return out.join(" · ");
}

// SPEC §6.4 Labels: the owner says what a detection really was; false positives are
// hidden (never deleted) and the hub learns the same. Their names are "reason.<key>".
const REASONS = ["aircraft", "satellite", "artifact", "other"];
const reasonName = (r) => tn("reason." + r) || r;
function labelBadge(e) {
  if (e.label === "confirmed") return `<span class="badge ok">${t("ev.confirmed")}</span>`;
  // SPEC §7.8: a matcher's label (aircraft) says so; any click below replaces it.
  const auto = e.label_auto ? t("ev.auto") + ": " : "";
  const detail = e.label_auto && e.label_detail ? " " + esc(e.label_detail) : "";
  if (e.label === "false_positive") return `<span class="badge warn">${auto}${t("ev.not", { kind: esc(kindName(e.kind)) })}${e.reason ? ": " + esc(reasonName(e.reason)) : ""}${detail}</span>`;
  return "";
}
function labelButtons(e) {
  const on = (l, r) => (e.label === l && (e.reason || null) === (r || null) ? ' aria-pressed="true"' : "");
  return `<div class="chips label-chips" role="group" aria-label="${t("ev.classify")}">
    <button type="button" class="chip" data-l="confirmed"${on("confirmed")}>✓ ${esc(kindName(e.kind))}</button>
    ${REASONS.map((r) => `<button type="button" class="chip" data-l="false_positive" data-r="${r}"${on("false_positive", r)}>✕ ${reasonName(r)}</button>`).join("")}
    ${e.label ? `<button type="button" class="chip" data-l="">${t("ev.reset")}</button>` : ""}
  </div>`;
}

async function viewEvents(night) {
  const picker = await nightPicker(night, "#ereignisse");
  page.innerHTML = `<h1>${t("nav.events")}</h1><div class="card stack">${picker.html}
    <ul class="events" id="events"><li class="muted">${t("loading")}</li></ul>
    <div class="actions"><button type="button" class="secondary small" id="showFp" hidden aria-pressed="false"></button></div></div>`;
  picker.bind();
  if (!picker.night) { $("events").innerHTML = `<li class="empty">${t("ev.none")}</li>`; return; }
  const events = await getJSON(`api/nights/${picker.night}/events`);
  if (!events.length) { $("events").innerHTML = `<li class="empty">${t("ev.noneNight")}</li>`; return; }
  let showFp = false;
  const render = () => {
    const shown = events.filter((e) => showFp || e.label !== "false_positive");
    const hidden = events.length - events.filter((e) => e.label !== "false_positive").length;
    const fpBtn = $("showFp");
    fpBtn.hidden = !hidden;
    fpBtn.textContent = showFp ? t("ev.hideFp") : t(hidden === 1 ? "ev.showFp1" : "ev.showFp", { n: hidden });
    if (!shown.length) { $("events").innerHTML = `<li class="empty">${t("ev.onlyFp")}</li>`; return; }
    const pictures = shown.filter((e) => e.has_image);
    $("events").innerHTML = shown.map((e) => `
      <li class="event${e.label === "false_positive" ? " fp" : ""}" data-id="${esc(e.id)}">
        ${e.has_image ? `<img loading="lazy" alt="" data-i="${pictures.indexOf(e)}" src="files/${e.night_id}/events/${e.id}.jpg?thumb=1">` : "<span></span>"}
        <div><b>${esc(kindName(e.kind))}</b> <span class="muted">${fmtTime(e.start)}</span> ${labelBadge(e)}
          <p class="muted small">${eventFacts(e)}</p>
          ${labelButtons(e)}</div>
      </li>`).join("");
    // The detection's picture, and with one click the whole frame it was seen on.
    const items = pictures.map((e) => {
      const crop = `files/${e.night_id}/events/${e.id}.jpg`;
      const full = e.frame ? fileUrl(e.night_id, e.frame, false) : null;
      const caption = `${kindName(e.kind)} · ${fmtDateTime(e.start)}`;
      return { src: crop, caption, actions: full ? [{ label: t("ev.whole"), run: (it) => {
        const whole = it.src !== full;
        it.src = whole ? full : crop;
        it.caption = whole ? `${caption} · ${t("ev.wholeCaption")}` : caption;
        it.actions[0].label = whole ? t("ev.crop") : t("ev.whole");
        showLightbox();
      } }] : null };
    });
    for (const img of $("events").querySelectorAll("img[data-i]")) {
      img.onclick = () => lightbox(items, Number(img.dataset.i));
    }
  };
  $("events").onclick = async (ev) => {
    const btn = ev.target.closest("button[data-l]");
    if (!btn) return;
    const li = btn.closest("li[data-id]");
    const e = events.find((x) => x.id === li.dataset.id);
    const body = { label: btn.dataset.l || null, reason: btn.dataset.r || null };
    const r = await fetch(`api/nights/${e.night_id}/events/${encodeURIComponent(e.id)}/label`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (r.status === 401) { location.href = "local-login"; return; }
    if (!r.ok) { alert(t("notSaved")); return; }
    e.label = body.label;
    e.reason = body.reason;
    e.label_auto = false;
    e.label_detail = null;
    render();
  };
  $("showFp").onclick = () => { showFp = !showFp; $("showFp").setAttribute("aria-pressed", String(showFp)); render(); };
  render();
}

// --- Sky charts (L6) -------------------------------------------------------------------
function chart(points, opts) {
  // points: [{t: ms, v: number}], one or two series as opts.series
  const W = 640, H = 200, L = 44, R = 12, T = 12, B = 26;
  const all = opts.series.flatMap((s) => s.points);
  if (!all.length) return `<p class="empty">${t("sky.noneNight")}</p>`;
  const t0 = Math.min(...all.map((p) => p.t)), t1 = Math.max(...all.map((p) => p.t)) || t0 + 1;
  const lo = opts.min ?? Math.min(...all.map((p) => p.v)), hi = opts.max ?? Math.max(...all.map((p) => p.v));
  const span = hi - lo || 1;
  const x = (t) => L + ((t - t0) / (t1 - t0 || 1)) * (W - L - R);
  const y = (v) => {
    const f = (v - lo) / span;
    return T + (opts.invert ? f : 1 - f) * (H - T - B);
  };
  const lines = opts.series.map((s, i) => {
    const d = s.points.map((p, j) => `${j ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join("");
    return `<path class="line${i ? " alt" : ""}" d="${d}"/>`;
  }).join("");
  const ticks = [lo, lo + span / 2, hi].map((v) => `<text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${opts.fmt(v)}</text>`).join("");
  const hours = [];
  for (let t = Math.ceil(t0 / 3.6e6) * 3.6e6; t <= t1; t += 3.6e6 * Math.max(1, Math.round((t1 - t0) / 3.6e6 / 8))) {
    hours.push(`<text x="${x(t)}" y="${H - 8}" text-anchor="middle">${new Date(t).getHours()}h</text>`);
  }
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opts.label)}">
    <line class="axis" x1="${L}" y1="${H - B}" x2="${W - R}" y2="${H - B}"/>
    <line class="axis" x1="${L}" y1="${T}" x2="${L}" y2="${H - B}"/>${ticks}${hours.join("")}${lines}</svg>`;
}

async function viewSky(night) {
  const picker = await nightPicker(night, "#himmel");
  page.innerHTML = `<h1>${t("nav.sky")}</h1><div class="card stack">${picker.html}</div><div class="stack" id="charts"></div>`;
  picker.bind();
  if (!picker.night) { $("charts").innerHTML = `<p class="empty">${t("sky.none")}</p>`; return; }
  const [sky, frames] = await Promise.all([
    getJSON(`api/nights/${picker.night}/sky`),
    getJSON(`api/nights/${picker.night}/frames?limit=2000&bad=1`),
  ]);
  const pts = (rows, key, scale = 1, at = "at") => rows.filter((r) => r[key] != null)
    .map((r) => ({ t: Date.parse(r[at]), v: r[key] * scale }));
  const nightFrames = frames.frames.filter((f) => f.mode === "night");
  $("charts").innerHTML = `
    <section class="card stack"><h2>${t("sky.clouds")}</h2>
      ${chart(null, { series: [{ points: pts(sky, "cloud_cover", 100) }], min: 0, max: 100, fmt: (v) => Math.round(v) + " %", label: t("sky.clouds") })}</section>
    <section class="card stack"><h2>${t("sky.sqm")}</h2>
      <p class="muted small">${t("sky.sqmHelp")}</p>
      ${chart(null, { series: [{ points: pts(sky, "sqm_mag") }], fmt: (v) => v.toFixed(2), label: t("sky.sqm") })}</section>
    <section class="card stack"><h2>${t("sky.stars")}</h2>
      ${chart(null, { series: [{ points: pts(sky, "stars") }], min: 0, fmt: (v) => Math.round(v), label: t("sky.stars") })}</section>
    <section class="card stack"><h2>${t("sky.exposureGain")}</h2>
      <p class="muted small">${t("sky.exposureGainHelp")}</p>
      ${chart(null, { series: [{ points: pts(nightFrames, "exposure_us", 1e-6, "captured_at") }], min: 0, fmt: (v) => v.toFixed(0) + " s", label: t("exposure") })}
      ${chart(null, { series: [{ points: [] }, { points: pts(nightFrames, "gain", 1, "captured_at") }], min: 0, fmt: (v) => Math.round(v), label: t("gain") })}</section>
    <section class="card stack"><h2>${t("sensorTemp")}</h2>
      ${chart(null, { series: [{ points: pts(frames.frames, "sensor_temp_c", 1, "captured_at").map((q) => ({ ...q, v: TEMP_UNIT === "f" ? q.v * 9 / 5 + 32 : q.v })) }], fmt: (v) => v.toFixed(0) + (TEMP_UNIT === "f" ? " °F" : " °C"), label: t("sensorTemp") })}</section>`;
}

// --- Settings (L7, SPEC §6.9) -------------------------------------------------------------
let schema = [];
let original = {};

function input(spec, value) {
  const el = document.createElement(spec.type === "choice" ? "select" : "input");
  el.name = spec.key;
  if (spec.type === "choice") {
    for (const c of spec.choices) {
      const o = document.createElement("option");
      o.value = c.value;
      o.textContent = text(c.label);
      el.appendChild(o);
    }
    el.value = value ?? "";
  } else if (spec.type === "bool") {
    el.type = "checkbox";
    el.checked = !!value;
  } else if (spec.type === "number" || spec.type === "int") {
    el.type = "number";
    el.inputMode = spec.type === "int" ? "numeric" : "decimal";
    for (const a of ["min", "max", "step"]) if (spec[a] != null) el[a] = spec[a];
    el.value = value ?? "";
    if (spec.nullable) el.placeholder = t("set.empty");
  } else {
    el.type = spec.write_only ? "password" : "text";
    if (spec.max_length) el.maxLength = spec.max_length;
    el.value = spec.write_only ? "" : value ?? "";
    if (spec.write_only) el.placeholder = original[spec.key + "_set"] ? t("set.isSet") : t("set.notSet");
  }
  return el;
}

function field(spec) {
  const wrap = document.createElement("div");
  wrap.className = "field";
  wrap.dataset.key = spec.key;
  const label = document.createElement("label");
  label.textContent = text(spec.label) + (spec.unit ? ` (${spec.unit})` : "");
  wrap.appendChild(label);
  if (spec.type === "location") {
    const pair = document.createElement("div");
    pair.className = "pair";
    for (const [k, ph] of [["latitude", t("set.latitude")], ["longitude", t("set.longitude")]]) {
      const el = input({ key: k, type: "number", step: 0.0001, nullable: true }, original[k]);
      el.placeholder = ph;
      el.setAttribute("aria-label", ph);
      pair.appendChild(el);
    }
    wrap.appendChild(pair);
    if (navigator.geolocation) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "secondary small";
      b.textContent = t("set.here");
      b.onclick = () => navigator.geolocation.getCurrentPosition((p) => {
        pair.children[0].value = p.coords.latitude.toFixed(4);
        pair.children[1].value = p.coords.longitude.toFixed(4);
      });
      wrap.appendChild(b);
    }
  } else if (spec.type === "bool") {
    label.className = "check";
    label.textContent = "";
    label.append(input(spec, original[spec.key]), " " + text(spec.label));
  } else {
    const el = input(spec, original[spec.key]);
    el.id = "s_" + spec.key;
    label.htmlFor = el.id;
    wrap.appendChild(el);
  }
  if (spec.help) {
    const h = document.createElement("p");
    h.className = "muted small";
    h.textContent = text(spec.help);
    wrap.appendChild(h);
  }
  return wrap;
}

function changes() {
  const form = $("settingsForm");
  const out = {};
  for (const spec of schema) {
    if (spec.type === "location") {
      const lat = form.elements.latitude.value, lon = form.elements.longitude.value;
      const a = lat === "" ? null : parseFloat(lat), b = lon === "" ? null : parseFloat(lon);
      if (a !== original.latitude || b !== original.longitude) { out.latitude = a; out.longitude = b; }
      continue;
    }
    const el = form.elements[spec.key];
    let v;
    if (spec.type === "bool") v = el.checked;
    else if (spec.type === "number" || spec.type === "int") {
      v = el.value === "" ? (spec.nullable ? null : undefined) : spec.type === "int" ? parseInt(el.value, 10) : parseFloat(el.value);
    } else if (spec.write_only) v = el.value === "" ? undefined : el.value;  // empty keeps the secret
    else v = el.value;
    if (v !== undefined && v !== original[spec.key]) out[spec.key] = v;
  }
  return out;
}

function message(msg, good) {
  const m = $("settingsMsg");
  m.textContent = msg;
  m.className = "msg " + (good ? "ok" : "error");
  m.hidden = false;
}

async function viewSettings() {
  page.innerHTML = `<h1>${t("nav.settings")}</h1><div class="card stack"><form class="form" id="settingsForm" novalidate></form>
    <div class="actions"><button type="button" id="saveBtn">${t("save")}</button></div>
    <p class="msg" id="settingsMsg" hidden></p></div>
    <section class="card stack" id="overlay" hidden></section>
    <section class="card stack" id="calibration" hidden></section>
    <section class="card stack" id="public" hidden></section>`;
  viewOverlayDesigner().then(() => {
    if (location.hash === "#einstellungen/overlay") $("overlay").scrollIntoView({ block: "start" });
  }).catch(() => {});
  viewCalibration().catch(() => {});
  viewPublic().catch(() => {});
  let data;
  try { data = await getJSON("api/settings"); } catch {
    $("settingsForm").innerHTML = `<p class="empty">${t("set.none")}</p>`;
    $("saveBtn").hidden = true;
    return;
  }
  schema = data.schema;
  original = data.values;
  const form = $("settingsForm");
  const groups = new Map();
  for (const spec of schema) {
    const g = text(spec.group);
    if (!groups.has(g)) {
      // One fold-out box per group, the first open; the open ones are remembered per browser,
      // by the German name so the choice survives a change of language.
      const box = document.createElement("details");
      box.className = "group";
      const sum = document.createElement("summary");
      sum.textContent = g;
      box.appendChild(sum);
      const pref = "grp-" + ((spec.group && spec.group.de) || g);
      box.open = groups.size === 0 ? loadPref(pref) : localPref(pref);
      box.addEventListener("toggle", () => savePref(pref, box.open));
      const fs = document.createElement("fieldset");
      box.appendChild(fs);
      groups.set(g, fs);
      form.appendChild(box);
    }
    groups.get(g).appendChild(field(spec));
  }
  $("saveBtn").onclick = async () => {
    for (const el of document.querySelectorAll(".field.bad")) el.classList.remove("bad");
    const body = changes();
    if (!Object.keys(body).length) { message(t("set.nothing"), true); return; }
    const r = await fetch("api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    // A new language needs the whole page drawn again, menu included.
    if (r.ok) { message(t("set.saved"), true); setTimeout(body.language ? () => location.reload() : viewSettings, 8000); return; }
    const err = await r.json().catch(() => ({ fields: [] }));
    const names = [];
    for (const k of err.fields || []) {
      const key = k === "latitude" || k === "longitude" ? "location" : k;
      const wrap = document.querySelector(`.field[data-key="${key}"]`);
      if (wrap) {
        wrap.classList.add("bad");
        names.push(wrap.querySelector("label").textContent);
        const box = wrap.closest("details");
        if (box) box.open = true;  // show the field with the error
      }
    }
    message(t("set.notSaved", { what: names.join(", ") || t("set.invalid") }), false);
  };
}

// --- Overlay designer (SPEC §7 Overlay) ---------------------------------------------------
// Each field goes to a corner (or nowhere); the order within a corner is the list order.
// The live image with the layout drawn over it shows the result before saving.
async function viewOverlayDesigner() {
  const box = $("overlay");
  let layout;
  try { layout = await getJSON("api/overlay"); } catch { return; }
  const [st, cal] = await Promise.all([getJSON("api/status").catch(() => ({})), getJSON("api/calibration").catch(() => ({}))]);
  const CORNER = { "": t("corner.off"), tl: t("corner.tl"), tr: t("corner.tr"), bl: t("corner.bl"), br: t("corner.br") };
  const fieldName = (k) => tn("ovf." + k) || k;
  const where = {};
  for (const [c, keys] of Object.entries(layout.corners)) for (const k of keys) where[k] = c;
  const order = [...Object.values(layout.corners).flat(), ...OV_FIELDS.filter((k) => !where[k])];
  box.hidden = false;
  box.innerHTML = `<h2>${t("ov.title")}</h2>
    <p class="muted small">${t("ov.help")}</p>
    <div class="live-stage"><img class="live-img" id="ovPrevImg" alt="${t("ov.preview")}" src="api/live.jpg?n=${Date.now()}">
      <svg id="ovPrev" class="overlay" viewBox="0 0 1000 562.5" aria-hidden="true"></svg></div>
    <form class="form stack" id="ovForm" novalidate>
      <label class="check"><input type="checkbox" name="enabled"${layout.enabled ? " checked" : ""}> ${t("ov.show")}</label>
      <div class="ov-grid">${order.map((k) => `<div class="field ov-field" data-k="${k}">
        <label for="ov-${k}">${esc(fieldName(k))}</label>
        <div class="row"><select id="ov-${k}" name="${k}">${Object.entries(CORNER).map(([v, name]) => `<option value="${v}"${(where[k] || "") === v ? " selected" : ""}>${name}</option>`).join("")}</select>
          <button type="button" class="chip" data-up="${k}" aria-label="${esc(t("ov.up", { f: fieldName(k) }))}">↑</button></div>
        ${k.startsWith("text") ? `<input name="t-${k}" maxlength="60" placeholder="${t("ov.textPlaceholder")}" value="${esc(layout.texts[k === "text1" ? 0 : 1])}">` : ""}
      </div>`).join("")}</div>
      <div class="field"><label for="ovSize">${t("ov.size")}</label><select id="ovSize" name="size">
        ${["small", "normal", "large"].map((v) => `<option value="${v}"${layout.size === v ? " selected" : ""}>${t("size." + v)}</option>`).join("")}</select></div>
      <label class="check"><input type="checkbox" name="compass"${layout.compass ? " checked" : ""}> ${t("ov.compass")}</label>
      <label class="check"><input type="checkbox" name="stars"${layout.stars ? " checked" : ""}> ${t("ov.stars")}</label>
    </form>
    <div class="actions"><button type="button" id="ovSaveLayout">${t("ov.save")}</button>
      <button type="button" class="secondary" id="ovReset">${t("ov.reset")}</button></div>
    <p class="msg" id="ovMsg" hidden></p>`;
  let sky = null;
  if (st.frame) getJSON("api/sky-overlay?at=" + encodeURIComponent(st.frame.captured_at)).then((x) => { sky = x; preview(); }).catch(() => {});
  const read = () => {
    const f = $("ovForm").elements;
    const corners = { tl: [], tr: [], bl: [], br: [] };
    const rows = [...$("ovForm").querySelectorAll(".ov-field")].map((el) => el.dataset.k);
    for (const k of rows) { const c = f[k].value; if (c) corners[c].push(k); }
    return { enabled: f.enabled.checked, corners, size: f.size.value, compass: f.compass.checked, stars: f.stars.checked,
      texts: [f["t-text1"].value.trim(), f["t-text2"].value.trim()] };
  };
  const preview = () => {
    const img = $("ovPrevImg");
    if (!img.naturalWidth) return;
    $("ovPrev").setAttribute("viewBox", `0 0 1000 ${(1000 * img.naturalHeight / img.naturalWidth).toFixed(1)}`);
    $("ovPrev").innerHTML = overlaySvg(st.frame, { sky: st.sky, moon: st.moon }, cal.orientation, read(),
      img.naturalWidth, img.naturalHeight, false, sky);
  };
  $("ovPrevImg").addEventListener("load", preview);
  $("ovForm").addEventListener("input", preview);
  $("ovForm").addEventListener("change", preview);
  $("ovForm").onclick = (e) => {
    const k = e.target.dataset && e.target.dataset.up;
    if (!k) return;
    const el = $("ovForm").querySelector(`.ov-field[data-k="${k}"]`);
    if (el.previousElementSibling) el.parentNode.insertBefore(el, el.previousElementSibling);
    preview();
  };
  const say = (msg, ok) => { const m = $("ovMsg"); m.textContent = msg; m.className = "msg " + (ok ? "ok" : "error"); m.hidden = false; };
  const save = async (body) => {
    const r = await fetch("api/overlay", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (r.status === 401) { location.href = "local-login"; return; }
    if (r.ok) { say(t("ov.saved"), true); return; }
    say(t("ov.invalid"), false);
  };
  $("ovSaveLayout").onclick = () => save(read());
  $("ovReset").onclick = async () => { await save(DEFAULT_LAYOUT); viewOverlayDesigner(); };
}

// --- Sky mask and orientation (L8) ----------------------------------------------------
// The live image with everything that is not sky shaded red. Tap points to draw an area,
// tap the first point again (or "Fläche fertig") to close it. Coordinates are fractions
// of the image, so they fit every frame size.
async function viewCalibration() {
  const box = $("calibration");
  let info = await getJSON("api/calibration");
  let areas = { exclude: info.edits.exclude.slice(), include: info.edits.include.slice() };
  let drawing = null;  // {kind, points}
  let showGrid = true;
  box.hidden = false;
  box.innerHTML = `
    <h2>${t("cal.title")}</h2>
    <p class="muted small" id="maskState"></p>
    <div class="mask-stage" id="maskStage">
      <img id="maskImg" alt="${t("live.current")}">
      <img id="maskShade" alt="" hidden>
      <svg id="maskSvg" viewBox="0 0 1000 562.5" aria-hidden="true"></svg>
    </div>
    <div class="row">
      <button type="button" class="secondary small" data-draw="exclude">${t("cal.exclude")}</button>
      <button type="button" class="secondary small" data-draw="include">${t("cal.include")}</button>
      <button type="button" class="secondary small" id="maskDone" hidden>${t("cal.done")}</button>
      <button type="button" class="secondary small" id="maskUndo" hidden>${t("cal.undo")}</button>
      <button type="button" class="secondary small active" id="maskGrid" aria-pressed="true">${t("cal.grid")}</button>
    </div>
    <ul class="areas" id="maskAreas"></ul>
    <div class="actions">
      <button type="button" id="maskSave">${t("cal.save")}</button>
      <button type="button" class="secondary" id="maskRelearn">${t("cal.relearn")}</button>
    </div>
    <p class="msg" id="maskMsg" hidden></p>
    <dl class="facts" id="orientFacts"></dl>`;
  const img = $("maskImg"), shade = $("maskShade"), svg = $("maskSvg");
  const say = (msg, good) => { const m = $("maskMsg"); m.textContent = msg; m.className = "msg " + (good ? "ok" : "error"); m.hidden = false; };
  // The drawing has the image's aspect, so letters and circles are not stretched.
  let VH = 1000 * 9 / 16;
  const X = (x) => (x * 1000).toFixed(1), Y = (y) => (y * VH).toFixed(1);
  const pts = (list) => list.map(([x, y]) => `${X(x)},${Y(y)}`).join(" ");
  const refreshShade = () => {
    if (!img.naturalWidth) return;
    VH = 1000 * img.naturalHeight / img.naturalWidth;
    svg.setAttribute("viewBox", `0 0 1000 ${VH.toFixed(1)}`);
    draw();
    shade.onerror = () => { shade.hidden = true; };
    shade.onload = () => { shade.hidden = false; };
    shade.src = `api/skymask.png?w=${img.naturalWidth}&h=${img.naturalHeight}&t=${Date.now()}`;
  };
  const draw = () => {
    const o = info.orientation && info.orientation.overlay;
    let out = "";
    if (o && showGrid) {
      out += `<polyline class="ring horizon" points="${pts(o.horizon)}"/>`;
      out += o.rings.map((r) => `<polyline class="ring" points="${pts(r)}"/>`).join("");
      out += `<circle class="zenith" cx="${X(o.zenith[0])}" cy="${Y(o.zenith[1])}" r="6"/>`;
      out += o.points.map((p) => `<text class="compass" x="${X(p.x)}" y="${Y(p.y)}">${compassLabel(p.label)}</text>`).join("");
    }
    for (const kind of ["exclude", "include"]) {
      out += areas[kind].map((a) => `<polygon class="area ${kind}" points="${pts(a)}"/>`).join("");
    }
    if (drawing) {
      out += `<polyline class="area ${drawing.kind} open" points="${pts(drawing.points)}"/>`;
      out += drawing.points.map(([x, y], i) => `<circle class="vertex${i ? "" : " first"}" cx="${X(x)}" cy="${Y(y)}" r="${i ? 7 : 11}"/>`).join("");
    }
    svg.innerHTML = out;
    $("maskDone").hidden = $("maskUndo").hidden = !drawing;
    for (const b of box.querySelectorAll("[data-draw]")) b.classList.toggle("active", !!drawing && drawing.kind === b.dataset.draw);
    $("maskAreas").innerHTML = ["exclude", "include"].flatMap((kind) => areas[kind].map((a, i) => `
      <li><span class="badge ${kind === "exclude" ? "" : "ok"}">${t(kind === "exclude" ? "cal.excluded" : "cal.included", { n: i + 1 })}</span>
        <button type="button" class="secondary small" data-remove="${kind}:${i}" aria-label="${t("cal.removeArea")}">${t("cal.remove")}</button></li>`)).join("");
    for (const b of $("maskAreas").querySelectorAll("[data-remove]")) {
      b.onclick = () => { const [k, i] = b.dataset.remove.split(":"); areas[k].splice(Number(i), 1); draw(); };
    }
  };
  const describe = () => {
    $("maskState").textContent = info.mask_learned_at
      ? t("cal.learned", { d: fmtDateTime(info.mask_learned_at) })
      : t("cal.notLearned");
    const o = info.orientation;
    $("orientFacts").innerHTML = o ? `
      <dt>${t("cal.north")}</dt><dd>${t("cal.northValue", { deg: o.north_deg })}${o.mirrored ? t("cal.mirrored") : ""}</dd>
      <dt>${t("cal.aligned")}</dt><dd>${t("cal.alignedValue", { d: fmtDateTime(o.solved_at), n: o.stars, rms: o.rms_deg.toFixed(2) })}</dd>
      <dt>${t("cal.check")}</dt><dd class="muted small">${t("cal.checkHelp")}</dd>`
      : `<dt>${t("orientation")}</dt><dd>${t("cal.notAligned")}</dd>`;
  };
  const at = (e) => {
    const r = img.getBoundingClientRect();
    return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))];
  };
  const finish = () => {
    if (drawing && drawing.points.length >= 3) areas[drawing.kind].push(drawing.points);
    drawing = null;
    draw();
  };
  $("maskStage").onclick = (e) => {
    if (!drawing) return;
    const p = at(e);
    const first = drawing.points[0];
    const r = img.getBoundingClientRect();
    if (first && drawing.points.length >= 3 && Math.hypot((p[0] - first[0]) * r.width, (p[1] - first[1]) * r.height) < 18) { finish(); return; }
    drawing.points.push(p.map((v) => Math.round(v * 1e4) / 1e4));
    draw();
  };
  for (const b of box.querySelectorAll("[data-draw]")) {
    b.onclick = () => { drawing = drawing && drawing.kind === b.dataset.draw ? null : { kind: b.dataset.draw, points: [] }; draw(); };
  }
  $("maskDone").onclick = finish;
  $("maskUndo").onclick = () => { if (drawing) { drawing.points.pop(); draw(); } };
  $("maskGrid").onclick = (e) => {
    showGrid = !showGrid;
    e.target.classList.toggle("active", showGrid);
    e.target.setAttribute("aria-pressed", String(showGrid));
    draw();
  };
  const post = async (url, body) => {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (r.status === 401) { location.href = "local-login"; return null; }
    return r.ok ? r.json() : null;
  };
  $("maskSave").onclick = async () => {
    if (drawing) finish();
    const next = await post("api/calibration/mask", areas);
    if (!next) { say(t("notSaved"), false); return; }
    info = next;
    say(t("cal.saved"), true);
    describe(); draw(); refreshShade();
  };
  $("maskRelearn").onclick = async () => {
    if (!confirm(t("cal.relearnAsk"))) return;
    const next = await post("api/calibration/relearn", {});
    if (!next) { say(t("failed"), false); return; }
    info = next;
    say(t("cal.discarded"), true);
    describe(); draw(); refreshShade();
  };
  img.onload = refreshShade;
  img.src = "api/live.jpg?t=" + Date.now();
  describe();
  draw();
}

// --- Public page on the hub (L10, SPEC §6.8) --------------------------------------------
// The sections and text fields of the page; their names are "pub.section.<key>" and
// "pub.text.<key>" in the dictionary.
const SECTIONS = ["live", "loop", "archive", "events", "sky", "aurora", "observatory", "aircraft"];
const TEXTS = [["title", 100], ["location_label", 200], ["camera", 200],
  ["lens", 200], ["computer", 200], ["owner", 200]];

async function viewPublic() {
  const box = $("public");
  const r = await fetch("api/public", { cache: "no-store" });
  if (r.status === 404) return;  // no hub configured
  box.hidden = false;
  const say = (msg, good) => { const m = $("pubMsg"); m.textContent = msg; m.className = "msg " + (good ? "ok" : "error"); m.hidden = false; };
  if (!r.ok) {
    box.innerHTML = `<h2>${t("pub.title")}</h2><p class="muted">${t("pub.unreachable")}</p>`;
    return;
  }
  const render = (st) => {
    const p = st.profile;
    const off = !st.online;
    box.innerHTML = `
      <h2>${t("pub.title")}</h2>
      <p class="muted small">${t("pub.help")}</p>
      ${off ? `<p class="msg error">${t("pub.offline")}</p>` : ""}
      <div class="row">
        ${st.enabled ? `<span class="badge ok">${t("pub.on")}</span> <a href="${esc(st.url)}" target="_blank" rel="noopener">${esc(st.url)}</a>`
          : `<span class="badge">${t("pub.off")}</span>`}
      </div>
      <div class="actions"><button type="button" class="${st.enabled ? "secondary" : ""}" id="pubToggle"${off ? " disabled" : ""}>
        ${st.enabled ? t("pub.turnOff") : t("pub.turnOn")}</button></div>
      ${st.enabled ? `<p class="muted small">${t("pub.offHelp")}</p>` : ""}
      <form class="form stack" id="pubForm" novalidate>
        <fieldset${off ? " disabled" : ""}><legend>${t("pub.texts")}</legend>
          ${TEXTS.map(([k, n]) => `<div class="field"><label for="pub_${k}">${t("pub.text." + k)}</label>
            <input id="pub_${k}" name="${k}" maxlength="${n}" value="${esc(p[k])}"></div>`).join("")}
          <label class="check"><input type="checkbox" name="show_coordinates"${p.show_coordinates ? " checked" : ""}> ${t("pub.coordinates")}</label>
          <div class="field"><label for="pub_notes">${t("pub.notes")}</label>
            <textarea id="pub_notes" name="notes" maxlength="2000" rows="4">${esc(p.notes)}</textarea></div>
        </fieldset>
        <fieldset${off ? " disabled" : ""}><legend>${t("pub.sections")}</legend>
          ${SECTIONS.map((k) => `<label class="check"><input type="checkbox" name="section_${k}"${p.sections[k] ? " checked" : ""}> ${t("pub.section." + k)}</label>`).join("")}
        </fieldset>
      </form>
      <div class="actions"><button type="button" id="pubSave"${off ? " disabled" : ""}>${t("pub.save")}</button></div>
      <p class="msg" id="pubMsg" hidden></p>`;
    const send = async (body, done) => {
      const res = await fetch("api/public", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (res.status === 401) { location.href = "local-login"; return; }
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { say(data.detail || t("notSaved"), false); return; }
      render(data);
      say(done, true);
    };
    $("pubToggle").onclick = () => {
      if (st.enabled && !confirm(t("pub.offAsk"))) return;
      send({ enabled: !st.enabled }, st.enabled ? t("pub.turnedOff") : t("pub.turnedOn"));
    };
    $("pubSave").onclick = () => {
      const f = $("pubForm").elements;
      const profile = { show_coordinates: f.show_coordinates.checked, notes: f.notes.value, sections: {} };
      for (const [k] of TEXTS) profile[k] = f[k].value;
      for (const k of SECTIONS) profile.sections[k] = f["section_" + k].checked;
      send({ profile }, t("saved"));
    };
  };
  render(await r.json());
}

// --- System (L12) ----------------------------------------------------------------------
// The power flags are "power.<flag>" in the dictionary.
const fmtBytes = (b) => (b >= 1e9 ? (b / 1e9).toFixed(1) + " GB" : Math.round(b / 1e6) + " MB");
const fmtUptime = (s) => {
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return (d ? `${d} d ` : "") + `${h} h ${m} min`;
};
const meter = (used, total) => `<div class="row"><div class="bar"><div style="width:${Math.min(100, 100 * used / total).toFixed(0)}%"></div></div>
  <span class="small muted">${Math.round(100 * used / total)} %</span></div>`;

async function viewSystem() {
  page.innerHTML = `<h1>${t("nav.system")}</h1>
    <section class="card stack" id="check" hidden></section>
    <section class="card stack" id="capture"><h2>${t("capture")}</h2>
      <p class="muted small" id="capState">…</p>
      <div class="actions"><button type="button" class="secondary" id="capToggle" hidden></button></div></section>
    <section class="card stack"><h2>${t("sys.cameraHub")}</h2><dl class="facts" id="sys"></dl></section>
    <section class="card stack" id="hw" hidden><h2>${t("sys.computer")}</h2><dl class="facts" id="hwFacts"></dl></section>
    <section class="card stack" id="net" hidden></section>
    <section class="card stack" id="tls" hidden></section>
    <section class="card stack" id="upd" hidden></section>
    <section class="card stack" id="support" hidden></section>
    <section class="card stack" id="actions" hidden><h2>${t("sys.restartTitle")}</h2>
      <p class="muted small">${t("sys.restartHelp")}</p>
      <div class="actions">
        <button type="button" class="secondary" data-act="restart">${t("sys.restart")}</button>
        <button type="button" class="secondary" data-act="reboot">${t("sys.reboot")}</button>
        <button type="button" class="danger" data-act="shutdown">${t("sys.shutdown")}</button>
      </div>
      <p class="msg" id="sysMsg" hidden></p></section>`;
  for (const b of document.querySelectorAll("[data-act]")) {
    b.onclick = async () => {
      const act = b.dataset.act;
      if (!confirm(t("sys.ask." + act))) return;
      const r = await fetch("api/system/" + act, { method: "POST" });
      if (r.status === 401) { location.href = "local-login"; return; }
      const data = await r.json().catch(() => ({}));
      const m = $("sysMsg");
      m.textContent = r.ok ? t("sys.done." + act) : data.detail || t("failed");
      m.className = "msg " + (r.ok ? "ok" : "error");
      m.hidden = false;
    };
  }
  viewCheck().catch(() => {});
  viewCapture().catch(() => {});
  viewNetwork().catch(() => {});
  viewTls().catch(() => {});
  viewUpdates().catch(() => {});
  viewSupport().catch(() => {});
  every(async () => {
    const [st, setup, hw] = await Promise.all([getJSON("api/status").catch(() => null),
      getJSON("api/setup").catch(() => null), getJSON("api/system").catch(() => null)]);
    if (!st) return;
    // Without a hub (e.g. --sim on a laptop) there is no setup API.
    const hub = setup ? `
      <dt>${t("sys.device")}</dt><dd><code>${esc(setup.device_id)}</code></dd>
      <dt>${t("sys.profile")}</dt><dd>${esc(setup.profile)}</dd>
      <dt>${t("hub")}</dt><dd>${esc(setup.hub_url)}</dd>
      <dt>${t("sys.paired")}</dt><dd>${setup.paired ? t("yes") : t("no")}</dd>
      <dt>${t("sys.connection")}</dt><dd>${setup.connected ? `<span class="badge ok">${t("connected")}</span>` : `<span class="badge">${t("sys.disconnected")}</span>`}</dd>
      <dt>${t("sys.timezone")}</dt><dd>${esc(setup.timezone || "–")}</dd>` : `<dt>${t("hub")}</dt><dd>${t("sys.noHub")}</dd>`;
    $("sys").innerHTML = `
      <dt>${t("sys.agent")}</dt><dd>${esc(st.version)}</dd>${hub}
      <dt>${t("sys.frames")}</dt><dd>${st.frames}</dd>
      ${st.frame && st.frame.sensor_temp_c != null ? `<dt>${t("sensor")}</dt><dd>${fmtTemp(st.frame.sensor_temp_c)}</dd>` : ""}`;
    if (!hw) return;
    $("hw").hidden = false;
    $("actions").hidden = false;
    const power = hw.power == null ? "" : hw.power.length
      ? `<dt>${t("power")}</dt><dd>${hw.power.map((f) => `<span class="badge">${esc(tn("power." + f) || f)}</span>`).join(" ")}
          <p class="muted small">${t("sys.powerHelp")}</p></dd>`
      : `<dt>${t("power")}</dt><dd><span class="badge ok">${t("ok")}</span></dd>`;
    $("hwFacts").innerHTML = `
      ${hw.model ? `<dt>${t("sys.model")}</dt><dd>${esc(hw.model)}</dd>` : ""}
      ${hw.os ? `<dt>${t("sys.os")}</dt><dd>${esc(hw.os)}</dd>` : ""}
      <dt>${t("sys.uptime")}</dt><dd>${fmtUptime(hw.uptime_s)}</dd>
      ${hw.cpu_temp_c != null ? `<dt>CPU</dt><dd>${fmtTemp(hw.cpu_temp_c)}${hw.load ? ` · ${t("sys.load", { l: hw.load[0].toFixed(2), n: hw.cpus || "?" })}` : ""}</dd>` : ""}
      ${hw.memory ? `<dt>${t("sys.memory")}</dt><dd>${t("sys.memoryOf", { a: fmtBytes(hw.memory.total - hw.memory.available), b: fmtBytes(hw.memory.total) })}
          ${meter(hw.memory.total - hw.memory.available, hw.memory.total)}</dd>` : ""}
      ${hw.disk ? `<dt>${t("storage")}</dt><dd>${t("sys.diskFree", { a: fmtBytes(hw.disk.free), b: fmtBytes(hw.disk.total) })}
          ${meter(hw.disk.total - hw.disk.free, hw.disk.total)}</dd>` : ""}
      ${power}
      ${hw.reboot_required ? `<dt>${t("sys.updates")}</dt><dd><span class="badge">${t("sys.rebootPending")}</span>
          <p class="muted small">${t("sys.rebootPendingHelp")}</p></dd>` : ""}
      ${hw.alpaca ? `<dt>ASCOM Alpaca</dt><dd>${!hw.alpaca.enabled ? `<span class="muted">${t("off")}</span>`
        : hw.alpaca.error ? `<span class="badge">${t("sys.portBusy")}</span> <span class="muted small">${t("sys.portBusyHelp", { port: hw.alpaca.port })}</span>`
        : `<span class="badge ok">${t("on")}</span> ${t("sys.port", { port: hw.alpaca.port })}`}</dd>` : ""}
      ${hw.observatory ? `<dt>N.I.N.A.</dt><dd>${hw.observatory.connected ? `<span class="badge ok">${t("connected")}</span>` : `<span class="badge">${t("unreachable")}</span>`}
        ${hw.observatory.target ? ` · ${esc(hw.observatory.target)}` : ""}${hw.observatory.sequence ? ` · ${Math.round(hw.observatory.sequence.progress * 100)} %` : ""}
        ${hw.observatory.safe != null ? ` · ${hw.observatory.safe ? t("sys.safe") : t("sys.unsafe")}` : ""}</dd>` : ""}
      <dt>${t("clock")}</dt><dd>${hw.time_trusted ? `<span class="badge ok">${t("sys.accurate")}</span>` : `<span class="badge">${t("sys.inaccurate")}</span>`}</dd>`;
  }, 5000);
}

// --- Log, support, backup (L15) ------------------------------------------------------------
// SPEC §7: the camera check as a traffic light.
// The checks are named "check.<id>" in the dictionary.
async function viewCheck() {
  const items = await getJSON("api/check");
  const box = $("check");
  const worst = items.some((i) => i.state === "fail") ? "fail" : items.some((i) => i.state === "warn") ? "warn" : "ok";
  box.hidden = false;
  box.innerHTML = `<h2>${t("check.title")} <span class="light ${worst}" aria-hidden="true"></span></h2>
    <ul class="checks">${items.map((i) => `<li class="${i.state}"><span class="light ${i.state}" aria-label="${tn("state." + i.state) ?? ""}"></span>
      <b>${esc(tn("check." + i.id) || i.id)}</b> <span class="muted small">${esc(i.detail)}</span></li>`).join("")}</ul>`;
}

// SPEC §4.1: pause capture, e.g. to clean the dome; it stays paused until resumed.
async function viewCapture() {
  const st = await getJSON("api/status");
  const show = (paused) => {
    $("capState").textContent = paused
      ? t("cap.paused")
      : t("cap.running");
    const b = $("capToggle");
    b.hidden = false;
    b.textContent = paused ? t("cap.resume") : t("cap.pause");
    b.onclick = async () => {
      const r = await fetch(paused ? "api/capture/resume" : "api/capture/pause", { method: "POST" });
      if (r.status === 401) { location.href = "local-login"; return; }
      if (r.ok) { show((await r.json()).paused); viewCheck().catch(() => {}); }
    };
  };
  show(!!st.paused);
}

async function viewSupport() {
  const box = $("support");
  const log = await getJSON("api/logs?lines=300");
  box.hidden = false;
  box.innerHTML = `<h2>${t("sup.title")}</h2>
    <details><summary>${t(log.lines.length === 1 ? "sup.log1" : "sup.log", { n: log.lines.length })}</summary>
      <pre class="log" id="logBox"></pre>
      <div class="actions"><button type="button" class="secondary small" id="logRefresh">${t("sup.refresh")}</button>
        <label class="check"><input type="checkbox" id="logFollow"> ${t("sup.follow")}</label></div>
    </details>
    <p class="muted small">${t("sup.help")}</p>
    <div class="actions"><a class="button secondary" href="api/support.zip" download>${t("sup.download")}</a></div>
    <h3>${t("sup.backup")}</h3>
    <p class="muted small">${t("sup.backupHelp")}</p>
    <div class="actions">
      <a class="button secondary" href="api/backup.json" download>${t("sup.backupDownload")}</a>
      <label class="button secondary" for="restoreFile">${t("sup.restore")}</label>
      <input type="file" id="restoreFile" accept="application/json,.json" hidden>
    </div>
    <p class="msg" id="restoreMsg" hidden></p>`;
  const show = (lines) => {
    const pre = $("logBox");
    pre.textContent = lines.join("\n");
    pre.scrollTop = pre.scrollHeight;
  };
  show(log.lines);
  let lines = log.lines;
  let seq = log.seq || 0;
  $("logRefresh").onclick = async () => { const l = await getJSON("api/logs?lines=300"); lines = l.lines; seq = l.seq || 0; show(lines); };
  // Follow: every 2 s only what is new, kept to the last 1000 lines.
  let follow = null;
  $("logFollow").onchange = (e) => {
    if (follow) { clearInterval(follow); follow = null; }
    if (!e.target.checked) return;
    follow = setInterval(async () => {
      const n = await getJSON("api/logs?after=" + seq).catch(() => null);
      if (!n || !n.lines.length) return;
      seq = n.seq;
      lines = [...lines, ...n.lines].slice(-1000);
      show(lines);
    }, 2000);
    timers.push(follow);  // stops when the page changes
  };
  $("restoreFile").onchange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const m = $("restoreMsg");
    const say = (msg, good) => { m.textContent = msg; m.className = "msg " + (good ? "ok" : "error"); m.hidden = false; };
    if (!confirm(t("sup.restoreAsk", { f: file.name }))) { e.target.value = ""; return; }
    const r = await fetch("api/restore", { method: "POST", headers: { "Content-Type": "application/json" }, body: await file.text() });
    e.target.value = "";
    if (r.status === 401) { location.href = "local-login"; return; }
    if (r.ok) { say(t("sup.restored"), true); return; }
    const err = await r.json().catch(() => ({}));
    say((err.fields || []).includes("backup") ? t("sup.notBackup")
      : t("sup.restoreInvalid", { f: (err.fields || []).join(", ") }), false);
  };
}

// --- Updates (L14, SPEC §8) -----------------------------------------------------------------
// The updater's states and codes are "upd.state.<state>" and "upd.code.<code>" in the dictionary.

async function viewUpdates() {
  const box = $("upd");
  const u = await getJSON("api/update");
  box.hidden = false;
  const st = u.state;
  const others = u.releases.filter((r) => r !== u.release);
  box.innerHTML = `<h2>${t("upd.title")}</h2>
    <dl class="facts">
      <dt>${t("upd.version")}</dt><dd>${esc(u.release || u.version)}</dd>
      ${st ? `<dt>${t("upd.last")}</dt><dd>${esc(tn("upd.state." + st.state) || st.state)}${st.version ? ` · ${esc(st.version)}` : ""}${st.code ? ` (${esc(tn("upd.code." + st.code) || st.code)})` : ""}
        <span class="muted small">${st.at ? fmtDateTime(st.at) : ""}</span></dd>` : ""}
      ${others.length ? `<dt>${t("upd.older")}</dt><dd>${others.map((r) => `<code>${esc(r)}</code>`).join(" ")}</dd>` : ""}
    </dl>
    ${u.updater ? `
      <p class="muted small">${t("upd.help")}</p>
      <div class="actions">
        <button type="button" class="secondary" data-upd="now"${u.pending ? " disabled" : ""}>${t("upd.now")}</button>
        ${others.length ? `<button type="button" class="secondary" data-upd="rollback"${u.pending ? " disabled" : ""}>${t("upd.rollback")}</button>` : ""}
      </div>
      ${u.pending ? `<p class="msg ok">${t("upd.pending")}</p>` : `<p class="msg" id="updMsg" hidden></p>`}`
      : `<p class="muted small">${t("upd.manual")}</p>`}`;
  for (const b of box.querySelectorAll("[data-upd]")) {
    b.onclick = async () => {
      const word = b.dataset.upd;
      const ask = word === "now" ? t("upd.nowAsk") : t("upd.rollbackAsk");
      if (!confirm(ask)) return;
      const r = await fetch("api/update/" + word, { method: "POST" });
      if (r.status === 401) { location.href = "local-login"; return; }
      if (r.ok) { viewUpdates(); return; }
      const m = $("updMsg");
      m.textContent = (await r.json().catch(() => ({}))).detail || t("failed");
      m.className = "msg error";
      m.hidden = false;
    };
  }
}

// --- Network (L13) ----------------------------------------------------------------------
async function viewNetwork() {
  const box = $("net");
  const n = await getJSON("api/network");
  box.hidden = false;
  const iface = (i) => `<dt>${i.kind === "wifi" ? t("net.wifi") : t("net.lan")} <span class="muted small">${esc(i.name)}</span></dt>
    <dd>${i.up ? (i.addresses.length ? i.addresses.map((a) => `<code>${esc(a.split("/")[0])}</code>`).join(" ") : t("net.noAddress"))
      : `<span class="muted">${t("net.notConnected")}</span>`}${i.default ? ` <span class="badge ok">${t("net.internet")}</span>` : ""}</dd>`;
  box.innerHTML = `<h2>${t("net.title")}</h2>
    <dl class="facts">
      <dt>${t("net.hostname")}</dt><dd><code>${esc(n.hostname)}.local</code></dd>
      ${n.wifi ? `<dt>${t("net.wifi")}</dt><dd>${esc(n.wifi.ssid)}${n.wifi.signal != null ? ` · ${t("net.signal", { s: n.wifi.signal })}` : ""}</dd>` : ""}
      ${n.interfaces.map(iface).join("")}
    </dl>
    ${n.managed ? `
      <form class="form stack" id="wifiForm" novalidate>
        <fieldset><legend>${t("net.other")}</legend>
          <div class="field"><label for="wifiSsid">${t("net.network")}</label>
            <select id="wifiSsid" name="ssid"><option value="">${t("net.searching")}</option></select></div>
          <div class="field"><label for="wifiPw">${t("net.password")}</label>
            <input id="wifiPw" name="password" type="password" autocomplete="off" placeholder="${t("net.passwordEmpty")}"></div>
          <div class="field"><label for="wifiCountry">${t("net.country")}</label>
            <input id="wifiCountry" name="country" maxlength="2" value="${esc(n.country || "AT")}"></div>
        </fieldset>
      </form>
      <p class="muted small">${t("net.fallback")}</p>
      <div class="actions"><button type="button" id="wifiJoin">${t("net.join")}</button></div>
      <p class="msg" id="wifiMsg" hidden></p>`
      : `<p class="muted small">${t("net.unmanaged")}</p>`}`;
  if (!n.managed) return;
  const say = (msg, good) => { const m = $("wifiMsg"); m.textContent = msg; m.className = "msg " + (good ? "ok" : "error"); m.hidden = false; };
  getJSON("api/network/wifi").then((nets) => {
    $("wifiSsid").innerHTML = nets.length
      ? nets.map((w) => `<option value="${esc(w.ssid)}"${n.wifi && w.ssid === n.wifi.ssid ? " selected" : ""}>${esc(w.ssid)} (${w.signal} %${w.secure ? t("net.secured") : ""})</option>`).join("")
      : `<option value="">${t("net.none")}</option>`;
  }).catch(() => { $("wifiSsid").innerHTML = `<option value="">${t("net.searchFailed")}</option>`; });
  $("wifiJoin").onclick = async () => {
    const f = $("wifiForm").elements;
    if (!f.ssid.value) { say(t("net.choose"), false); return; }
    if (!confirm(t("net.joinAsk", { s: f.ssid.value }))) return;
    const r = await fetch("api/network/wifi", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ssid: f.ssid.value, password: f.password.value || null, country: f.country.value }) });
    if (r.status === 401) { location.href = "local-login"; return; }
    if (r.ok) { say(t("net.switching", { s: f.ssid.value }), true); return; }
    const err = await r.json().catch(() => ({}));
    const names = { password: t("net.bad.password"), country: t("net.bad.country"), ssid: t("net.network") };
    say(t("net.notApplied", { why: (err.fields || []).map((k) => names[k] || k).join(", ") || err.detail || t("net.invalid") }), false);
  };
}

// SPEC §7.6: HTTPS for this page (roadmap L24), switched in the settings.
async function viewTls() {
  const box = $("tls");
  const tls = await getJSON("api/tls");
  box.hidden = false;
  const kind = tn("tls.kind." + tls.kind) || t("tls.kind.none");
  const here = location.protocol === "https:";
  box.innerHTML = `<h2>HTTPS</h2>
    <dl class="facts">
      <dt>${t("tls.status")}</dt><dd>${tls.enabled ? `${t("tls.on", { port: tls.port })}${here ? "" : ` · <a href="https://${esc(location.hostname)}:${tls.port}/">${t("tls.toHttps")}</a>`}`
        : `${t("off")} <span class="muted small">${t("tls.where")}</span>`}</dd>
      <dt>${t("tls.certificate")}</dt><dd>${esc(kind)}${tls.not_after ? ` · ${t("tls.validUntil", { d: fmtDateTime(tls.not_after) })}` : ""}</dd>
      ${tls.sha256 ? `<dt>${t("tls.fingerprint")}</dt><dd><code class="small">${esc(tls.sha256)}</code></dd>` : ""}
      ${tls.names ? `<dt>${t("tls.for")}</dt><dd>${tls.names.map((x) => `<code>${esc(x)}</code>`).join(" ")}</dd>` : ""}
    </dl>
    <p class="muted small">${t("tls.selfHelp")}</p>
    <details><summary>${t("tls.own")}</summary>
      <form class="form stack" id="tlsForm" novalidate>
        <div class="field"><label for="tlsCert">${t("tls.cert")}</label>
          <textarea id="tlsCert" name="cert" rows="5" spellcheck="false"></textarea></div>
        <div class="field"><label for="tlsKey">${t("tls.key")}</label>
          <textarea id="tlsKey" name="key" rows="5" spellcheck="false"></textarea></div>
      </form>
      <div class="actions"><button type="button" id="tlsSave">${t("apply")}</button>
        ${tls.kind === "own" ? `<button type="button" class="secondary" id="tlsRemove">${t("tls.remove")}</button>` : ""}</div>
    </details>
    <p class="msg" id="tlsMsg" hidden></p>`;
  const say = (msg, good) => { const m = $("tlsMsg"); m.textContent = msg; m.className = "msg " + (good ? "ok" : "error"); m.hidden = false; };
  const send = async (path, body) => {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (r.status === 401) { location.href = "local-login"; return; }
    if (r.ok) { say(t("tls.applied"), true); return; }
    const err = await r.json().catch(() => ({}));
    const why = err.detail ? tn("tls.why." + err.detail) : null;
    say(why || t("notApplied"), false);
  };
  $("tlsSave").onclick = () => {
    const f = $("tlsForm").elements;
    send("api/tls", { cert: f.cert.value, key: f.key.value });
  };
  if ($("tlsRemove")) $("tlsRemove").onclick = () => send("api/tls/remove", {});
}

// --- router -------------------------------------------------------------------------
const VIEWS = { live: viewLive, archiv: viewArchive, ereignisse: viewEvents, himmel: viewSky,
  einstellungen: viewSettings, system: viewSystem };

function route() {
  clearTimers();
  const [name, arg] = (location.hash.slice(1) || "live").split("/");
  const view = VIEWS[name] || viewLive;
  for (const a of document.querySelectorAll("#nav [data-page]")) {
    const on = a.dataset.page === (VIEWS[name] ? name : "live");
    a.classList.toggle("active", on);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
  Promise.resolve(view(arg)).catch((e) => {
    if (e.message !== "login") page.innerHTML = `<p class="msg error">${t("loadFailed", { e: esc(e.message) })}</p>`;
  });
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);
// SPEC §7: language and temperature unit come first, so the first view is drawn in the
// right language; without settings (none stored, or an error) the page stays German.
const settingsReady = getJSON("api/settings").then((d) => {
  const v = d.values || {};
  if (v.temperature_unit) TEMP_UNIT = v.temperature_unit;
  if (v.language === "en" || v.language === "de") LANG = v.language;
}).catch(() => {}).finally(translateStatic);
const setupReady = getJSON("api/setup").then((st) => {
  try { if (st.timezone) { new Date().toLocaleString("de-AT", { timeZone: st.timezone }); CAMERA_TZ = st.timezone; } } catch { /* unknown zone: browser time */ }
}).catch(() => {});
Promise.all([settingsReady, setupReady]).finally(route);

// Pairing code while unpaired, logout button once paired (SPEC §7, §7.4).
(async function pollSetup() {
  try {
    const s = await getJSON("api/setup");
    $("logout").hidden = !s.paired;
    const show = !s.paired && s.pairing_code;
    $("pairPanel").hidden = !show;
    if (show) $("pairCode").textContent = s.pairing_code.slice(0, 3) + "-" + s.pairing_code.slice(3);
    if (s.paired) return;
  } catch { /* camera restarting */ }
  setTimeout(pollSetup, 3000);
})();
getJSON("api/status").then((s) => { $("version").textContent = s.version; }).catch(() => {});
