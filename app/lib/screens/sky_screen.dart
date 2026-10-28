// "Himmel": the sky over a place for everyone. The next three nights (cloud forecast,
// darkness, moon), aurora chances, the nearest public camera's measured sky and what
// comes next (calendar). The place is the phone's location or a chosen one; it goes to the
// hub rounded to 0.1°.

import 'package:flutter/material.dart';

import '../api/hub_client.dart';
import '../api/place_store.dart';
import '../design.dart';
import '../l10n.dart';
import '../platform/location.dart';
import 'gallery_screens.dart' show clock;
import 'public_camera_screen.dart';
import 'sky_calendar_screen.dart';

typedef _Loaded = ({
  String label,
  SkyForecast sky,
  List<CalendarItem> calendar,
});

class SkyScreen extends StatefulWidget {
  const SkyScreen({
    super.key,
    required this.client,
    required this.places,
    required this.location,
  });

  final HubClient client;
  final PlaceStore places;
  final LocationSource location;

  @override
  State<SkyScreen> createState() => _SkyScreenState();
}

class _SkyScreenState extends State<SkyScreen> {
  SkyPlace? _place;
  bool _started = false;
  Future<_Loaded>? _data;
  String? _tz;

  @override
  void initState() {
    super.initState();
    _start();
  }

  Future<void> _start() async {
    final place = await widget.places.load();
    _tz = await widget.location.timezone();
    if (!mounted) return;
    setState(() {
      _started = true;
      _place = place;
      if (place != null) _data = _load(place)..ignore();
    });
  }

  Future<_Loaded> _load(SkyPlace place) async {
    late final double lat, lon;
    late final String label;
    if (place.place case final p?) {
      (lat, lon, label) = (p.lat, p.lon, p.name.split(',').first);
    } else {
      final found = await widget.location.current();
      final at = found.location;
      if (at == null) throw _NoLocation(found.problem!);
      (lat, lon) = (at.latitude, at.longitude);
      label = tr('Mein Standort', 'My location');
    }
    final sky = widget.client.publicSky(lat, lon, tz: _tz);
    final calendar = widget.client
        .publicCalendar(lat, lon, tz: _tz)
        .catchError((Object _) => <CalendarItem>[]);
    return (label: label, sky: await sky, calendar: await calendar);
  }

  Future<void> _reload() async {
    final place = _place;
    if (place == null) return;
    final next = _load(place)..ignore();
    setState(() => _data = next);
    await next.then((_) {}, onError: (Object _) {});
  }

  Future<void> _choose() async {
    final chosen = await Navigator.of(context).push<SkyPlace>(
      MaterialPageRoute(builder: (_) => PlaceScreen(client: widget.client)),
    );
    if (chosen == null || !mounted) return;
    await widget.places.save(chosen);
    setState(() {
      _place = chosen;
      _data = _load(chosen)..ignore();
    });
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: Text(tr('Himmel', 'Sky')),
      actions: [
        if (_place != null)
          IconButton(
            key: const Key('sky-place'),
            tooltip: tr('Ort ändern', 'Change place'),
            icon: const Icon(Icons.place_outlined),
            onPressed: _choose,
          ),
      ],
    ),
    body: !_started
        ? const SizedBox.shrink()
        : _place == null
        ? _Welcome(onChoose: _choose)
        : RefreshIndicator(
            onRefresh: _reload,
            child: FutureBuilder<_Loaded>(
              future: _data,
              builder: (context, snapshot) {
                if (snapshot.hasError) {
                  final error = snapshot.error;
                  return ListView(
                    padding: const EdgeInsets.all(32),
                    children: [
                      Text(
                        error is _NoLocation
                            ? describeSkyLocationProblem(error.problem)
                            : tr(
                                'Der Himmel konnte nicht geladen werden.',
                                'Could not load the sky.',
                              ),
                        textAlign: TextAlign.center,
                      ),
                      TextButton(
                        onPressed: _choose,
                        child: Text(tr('Ort wählen', 'Choose a place')),
                      ),
                    ],
                  );
                }
                final data = snapshot.data;
                if (data == null) {
                  return const Center(child: CircularProgressIndicator());
                }
                return _Sky(
                  client: widget.client,
                  data: data,
                  onChoose: _choose,
                );
              },
            ),
          ),
  );
}

class _NoLocation implements Exception {
  const _NoLocation(this.problem);

  final LocationProblem problem;
}

String describeSkyLocationProblem(LocationProblem problem) => switch (problem) {
  LocationProblem.serviceOff => tr(
    'Die Standortdienste des Handys sind aus. Schalte sie ein oder wähle einen Ort.',
    "The phone's location services are off. Turn them on or choose a place.",
  ),
  LocationProblem.denied || LocationProblem.deniedForever => tr(
    'Ohne Freigabe kennt die App deinen Standort nicht. Wähle stattdessen einen Ort.',
    'Without permission the app does not know where you are. Choose a place instead.',
  ),
  LocationProblem.failed => tr(
    'Der Standort konnte nicht bestimmt werden. Wähle einen Ort.',
    'Could not determine the location. Choose a place.',
  ),
};

class _Welcome extends StatelessWidget {
  const _Welcome({required this.onChoose});

  final VoidCallback onChoose;

  @override
  Widget build(BuildContext context) => ListView(
    padding: const EdgeInsets.all(24),
    children: [
      const Icon(Icons.nights_stay_outlined, size: 56, color: Sky.accent),
      const SizedBox(height: 16),
      Text(
        tr(
          'Wie wird die Nacht bei dir? Wolken, Dunkelheit, Mond, Polarlicht und was am Himmel kommt.',
          'What will the night be like where you are? Clouds, darkness, moon, aurora and what comes in the sky.',
        ),
        textAlign: TextAlign.center,
      ),
      const SizedBox(height: 20),
      FilledButton(
        key: const Key('sky-choose'),
        onPressed: onChoose,
        child: Text(tr('Ort wählen', 'Choose a place')),
      ),
    ],
  );
}

String verdictLabel(String verdict) => switch (verdict) {
  'good' => tr('gut', 'good'),
  'fair' => tr('mäßig', 'fair'),
  'cloudy' => tr('bewölkt', 'cloudy'),
  'bright' => tr('hell', 'bright'),
  _ => tr('unklar', 'unknown'),
};

Color verdictColor(String verdict) => switch (verdict) {
  'good' => Sky.aurora,
  'fair' => Sky.warn,
  _ => Sky.text2,
};

String weekday(DateTime day) {
  const de = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'];
  const en = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  return (appEnglish ? en : de)[day.weekday - 1];
}

class _Sky extends StatelessWidget {
  const _Sky({
    required this.client,
    required this.data,
    required this.onChoose,
  });

  final HubClient client;
  final _Loaded data;
  final VoidCallback onChoose;

  void _night(BuildContext context, int i) => Navigator.of(context).push(
    MaterialPageRoute<void>(
      builder: (_) =>
          NightScreen(nights: data.sky.nights, initial: i, place: data.label),
    ),
  );

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final sky = data.sky;
    final nights = sky.nights;
    final tonight = nights.isEmpty ? null : nights.first;
    final near = sky.nearby;
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 24),
      children: [
        TextButton.icon(
          style: TextButton.styleFrom(alignment: Alignment.centerLeft),
          icon: const Icon(Icons.place_outlined, size: 18),
          label: Text('${data.label} · ${tr('ändern', 'change')}'),
          onPressed: onChoose,
        ),
        Row(
          children: [
            for (final (i, n) in nights.indexed)
              Expanded(
                child: Padding(
                  padding: EdgeInsets.only(left: i == 0 ? 0 : 8),
                  child: Card(
                    margin: EdgeInsets.zero,
                    child: InkWell(
                      key: Key('night-$i'),
                      onTap: () => _night(context, i),
                      child: Padding(
                        padding: const EdgeInsets.all(10),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              i == 0
                                  ? tr('Heute Nacht', 'Tonight')
                                  : weekday(n.evening),
                              style: theme.textTheme.bodySmall,
                            ),
                            Text(
                              verdictLabel(n.verdict),
                              style: theme.textTheme.titleLarge?.copyWith(
                                color: verdictColor(n.verdict),
                              ),
                            ),
                            Text(
                              n.goodHours > 0
                                  ? tr(
                                      '${n.goodHours} klare Std.',
                                      '${n.goodHours} clear h',
                                    )
                                  : n.maxClouds == null
                                  ? '–'
                                  : '${n.maxClouds} %',
                              style: theme.textTheme.bodySmall,
                            ),
                          ],
                        ),
                      ),
                    ),
                  ),
                ),
              ),
          ],
        ),
        if (tonight != null)
          Card(
            margin: const EdgeInsets.only(top: 12),
            child: InkWell(
              onTap: () => _night(context, 0),
              child: Padding(
                padding: const EdgeInsets.all(14),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            tr('Heute Nacht', 'Tonight'),
                            style: theme.textTheme.titleMedium,
                          ),
                        ),
                        if (tonight.dusk != null && tonight.dawn != null)
                          Text(
                            tr(
                              'dunkel ${clock(tonight.dusk!)} – ${clock(tonight.dawn!)}',
                              'dark ${clock(tonight.dusk!)} – ${clock(tonight.dawn!)}',
                            ),
                            style: theme.textTheme.bodySmall,
                          ),
                      ],
                    ),
                    const SizedBox(height: 10),
                    CloudBars(hours: tonight.hours),
                    const SizedBox(height: 8),
                    Text(tonight.summary),
                  ],
                ),
              ),
            ),
          ),
        if (near != null)
          Card(
            margin: const EdgeInsets.only(top: 12),
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(16),
              side: BorderSide(color: Sky.aurora.withValues(alpha: .4)),
            ),
            child: ListTile(
              key: const Key('sky-nearby'),
              onTap: () => Navigator.of(context).push(
                MaterialPageRoute<void>(
                  builder: (_) => PublicCameraScreen(
                    client: client,
                    slug: near['slug'] as String,
                    title: near['title'] as String,
                  ),
                ),
              ),
              title: Text(
                tr(
                  'GEMESSEN · ${(near['distance_km'] as num).round()} km entfernt',
                  'MEASURED · ${(near['distance_km'] as num).round()} km away',
                ),
                style: theme.textTheme.labelSmall?.copyWith(color: Sky.aurora),
              ),
              subtitle: Text(
                [
                  near['title'] as String,
                  if (near['cloud_cover'] case final num c)
                    tr(
                      '${(c * 100).round()} % Wolken',
                      '${(c * 100).round()} % clouds',
                    ),
                  if (near['sqm_mag'] case final num m)
                    '${appEnglish ? m.toStringAsFixed(1) : m.toStringAsFixed(1).replaceAll('.', ',')} mag',
                ].join(' · '),
                style: theme.textTheme.titleSmall,
              ),
              trailing: const Icon(Icons.chevron_right),
            ),
          ),
        const SizedBox(height: 12),
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(child: _Aurora(sky: sky)),
            const SizedBox(width: 10),
            if (tonight != null) Expanded(child: _Moon(night: tonight)),
          ],
        ),
        if (sky.planets.isNotEmpty)
          Card(
            key: const Key('sky-planets'),
            margin: const EdgeInsets.only(top: 12),
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    tr('Planeten heute Nacht', 'Planets tonight'),
                    style: theme.textTheme.titleMedium,
                  ),
                  const SizedBox(height: 6),
                  for (final p in sky.planets)
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 2),
                      child: Text(
                        tr(
                          '${p.name}: ${clock(p.start)}–${clock(p.end)}, am höchsten ${clock(p.best)} im ${compass(p.azimuth)} (${p.altitude.round()}°)',
                          '${p.name}: ${clock(p.start)}–${clock(p.end)}, highest ${clock(p.best)} in the ${compass(p.azimuth)} (${p.altitude.round()}°)',
                        ),
                      ),
                    ),
                ],
              ),
            ),
          ),
        Card(
          margin: const EdgeInsets.only(top: 12),
          child: InkWell(
            key: const Key('sky-calendar'),
            onTap: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) => SkyCalendarScreen(items: data.calendar),
              ),
            ),
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: Text(
                          tr('Als Nächstes', 'Coming up'),
                          style: theme.textTheme.titleMedium,
                        ),
                      ),
                      Text(
                        tr('Kalender ›', 'Calendar ›'),
                        style: theme.textTheme.labelLarge?.copyWith(
                          color: Sky.accent,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 6),
                  if (data.calendar.isEmpty)
                    Text(tr('Nichts Besonderes.', 'Nothing special.')),
                  for (final item in data.calendar.take(3))
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 2),
                      child: Text(
                        '${calendarWhen(item)} · ${item.title}',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                ],
              ),
            ),
          ),
        ),
        Padding(
          padding: const EdgeInsets.only(top: 12),
          child: Text(
            tr(
              'Wolkenvorhersage: Open-Meteo. Polarlicht: NOAA. Ort auf etwa 10 km gerundet.',
              'Cloud forecast: Open-Meteo. Aurora: NOAA. Place rounded to about 10 km.',
            ),
            style: theme.textTheme.bodySmall,
          ),
        ),
      ],
    );
  }
}

/// Cloud cover per hour as bars: tall and green when clear and dark.
class CloudBars extends StatelessWidget {
  const CloudBars({super.key, required this.hours});

  final List<SkyHour> hours;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    if (hours.isEmpty) {
      return Text(
        tr('Es wird nicht richtig dunkel.', 'It does not get properly dark.'),
      );
    }
    return Column(
      children: [
        SizedBox(
          height: 36,
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              for (final h in hours)
                Expanded(
                  child: Container(
                    margin: const EdgeInsets.symmetric(horizontal: 1.5),
                    height: h.clouds == null
                        ? 4
                        : 6 + 30 * (100 - h.clouds!) / 100,
                    decoration: BoxDecoration(
                      color: h.good
                          ? Sky.aurora
                          : (h.clouds ?? 100) < 60
                          ? Sky.accent
                          : theme.disabledColor,
                      borderRadius: BorderRadius.circular(3),
                    ),
                  ),
                ),
            ],
          ),
        ),
        const SizedBox(height: 3),
        Row(
          children: [
            for (final h in hours)
              Expanded(
                child: Text(
                  h.at.toLocal().hour.toString().padLeft(2, '0'),
                  textAlign: TextAlign.center,
                  maxLines: 1,
                  overflow: TextOverflow.clip,
                  style: theme.textTheme.labelSmall,
                ),
              ),
          ],
        ),
      ],
    );
  }
}

class _Aurora extends StatelessWidget {
  const _Aurora({required this.sky});

  final SkyForecast sky;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final days = sky.kpDays;
    final top = days.isEmpty
        ? sky.kpNow
        : days.map((d) => d.kp).reduce((a, b) => a > b ? a : b);
    final needed = sky.kpNeeded;
    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(tr('Polarlicht', 'Aurora'), style: theme.textTheme.bodySmall),
            Text(
              top == null ? '–' : 'Kp ${top.round()}',
              style: theme.textTheme.titleLarge?.copyWith(
                color: needed != null && top != null && top >= needed
                    ? Sky.aurora
                    : null,
              ),
            ),
            if (needed != null)
              Text(
                tr(
                  'bei dir ab Kp ${needed.round()}',
                  'here from Kp ${needed.round()}',
                ),
                style: theme.textTheme.bodySmall,
              ),
          ],
        ),
      ),
    );
  }
}

class _Moon extends StatelessWidget {
  const _Moon({required this.night});

  final SkyNight night;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final set = night.moonset, rise = night.moonrise;
    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(tr('Mond', 'Moon'), style: theme.textTheme.bodySmall),
            Text(
              '${(night.moonIllumination * 100).round()} %',
              style: theme.textTheme.titleLarge,
            ),
            Text(
              [
                night.moonPhase,
                if (rise != null)
                  tr('auf ${clock(rise)}', 'rises ${clock(rise)}'),
                if (set != null)
                  tr('unter ${clock(set)}', 'sets ${clock(set)}'),
              ].join(' · '),
              style: theme.textTheme.bodySmall,
            ),
          ],
        ),
      ),
    );
  }
}

/// The astro weather of one night, hour by hour; swipe to the next nights.
class NightScreen extends StatelessWidget {
  const NightScreen({
    super.key,
    required this.nights,
    required this.initial,
    required this.place,
  });

  final List<SkyNight> nights;
  final int initial;
  final String place;

  @override
  Widget build(BuildContext context) => DefaultTabController(
    length: nights.length,
    initialIndex: initial,
    child: Scaffold(
      appBar: AppBar(
        title: Text(tr('Astro-Wetter', 'Astro weather')),
        bottom: TabBar(
          tabs: [
            for (final (i, n) in nights.indexed)
              Tab(
                text: i == 0
                    ? tr('Heute', 'Tonight')
                    : '${weekday(n.evening)} ${n.evening.day}.',
              ),
          ],
        ),
      ),
      body: TabBarView(
        children: [for (final n in nights) _NightTable(night: n, place: place)],
      ),
    ),
  );
}

class _NightTable extends StatelessWidget {
  const _NightTable({required this.night, required this.place});

  final SkyNight night;
  final String place;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final small = theme.textTheme.labelSmall;
    Widget layer(int? v) => Container(
      height: 22,
      margin: const EdgeInsets.symmetric(horizontal: 2),
      decoration: BoxDecoration(
        color: Sky.text2.withValues(
          alpha: v == null ? .05 : .1 + .75 * v / 100,
        ),
        borderRadius: BorderRadius.circular(4),
      ),
    );
    Widget row(List<Widget> cells, {Color? background}) => Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 3),
      decoration: BoxDecoration(
        color: background,
        borderRadius: BorderRadius.circular(6),
      ),
      child: Row(
        children: [
          SizedBox(width: 30, child: cells[0]),
          Expanded(child: cells[1]),
          SizedBox(width: 30, child: cells[2]),
          SizedBox(width: 30, child: cells[3]),
          SizedBox(width: 30, child: cells[4]),
          SizedBox(width: 38, child: cells[5]),
          SizedBox(width: 32, child: cells[6]),
          SizedBox(width: 20, child: cells[7]),
        ],
      ),
    );
    return ListView(
      padding: const EdgeInsets.fromLTRB(10, 12, 10, 24),
      children: [
        Text(place, style: theme.textTheme.bodySmall),
        const SizedBox(height: 6),
        Container(
          padding: const EdgeInsets.all(10),
          decoration: BoxDecoration(
            color: verdictColor(night.verdict).withValues(alpha: .12),
            borderRadius: BorderRadius.circular(12),
          ),
          child: Text(night.summary),
        ),
        const SizedBox(height: 10),
        row([
          Text(tr('Uhr', 'h'), style: small),
          Text(tr('Wolken', 'Clouds'), style: small),
          Text(tr('tief', 'low'), style: small, textAlign: TextAlign.center),
          Text(tr('mittel', 'mid'), style: small, textAlign: TextAlign.center),
          Text(tr('hoch', 'high'), style: small, textAlign: TextAlign.center),
          Text(tr('Regen', 'Rain'), style: small, textAlign: TextAlign.end),
          Text('Wind', style: small, textAlign: TextAlign.end),
          Text(tr('Mond', 'Moon'), style: small, textAlign: TextAlign.center),
        ]),
        for (final h in night.hours)
          row([
            Text(h.at.toLocal().hour.toString().padLeft(2, '0')),
            ClipRRect(
              borderRadius: BorderRadius.circular(5),
              child: LinearProgressIndicator(
                value: (h.clouds ?? 0) / 100,
                minHeight: 10,
                color: h.good
                    ? Sky.aurora
                    : (h.clouds ?? 100) < 60
                    ? Sky.accent
                    : Sky.text2,
                backgroundColor: Sky.accent.withValues(alpha: .15),
              ),
            ),
            layer(h.low),
            layer(h.mid),
            layer(h.high),
            Text(
              h.rain == null ? '–' : '${h.rain} %',
              style: small,
              textAlign: TextAlign.end,
            ),
            Text(
              h.windKmh == null ? '–' : '${h.windKmh!.round()}',
              style: small,
              textAlign: TextAlign.end,
            ),
            Icon(
              h.moonUp ? Icons.brightness_2 : null,
              size: 14,
              color: Sky.warn,
            ),
          ], background: h.good ? Sky.aurora.withValues(alpha: .1) : null),
        const SizedBox(height: 12),
        Text(
          tr(
            'Grün: klar, dunkel, kein heller Mond. Wind in km/h auf etwa 3 km Höhe: unruhige Luft lässt Sterne flackern. Hohe Wolken sind oft dünn, Sterne scheinen durch, Fotos werden flau.',
            'Green: clear, dark, no bright moon. Wind in km/h at about 3 km: turbulent air makes stars twinkle. High clouds are often thin: stars shine through, photos turn hazy.',
          ),
          style: theme.textTheme.bodySmall,
        ),
        const SizedBox(height: 6),
        Text(
          [
            if (night.sunset != null)
              tr(
                'Sonne unter ${clock(night.sunset!)}',
                'sunset ${clock(night.sunset!)}',
              ),
            if (night.dusk != null && night.dawn != null)
              tr(
                'dunkel ${clock(night.dusk!)}–${clock(night.dawn!)}',
                'dark ${clock(night.dusk!)}–${clock(night.dawn!)}',
              ),
            if (night.sunrise != null)
              tr(
                'Sonne auf ${clock(night.sunrise!)}',
                'sunrise ${clock(night.sunrise!)}',
              ),
          ].join(' · '),
          style: theme.textTheme.bodySmall,
        ),
      ],
    );
  }
}

/// Where "Himmel" looks: the phone's location, a searched place.
class PlaceScreen extends StatefulWidget {
  const PlaceScreen({super.key, required this.client});

  final HubClient client;

  @override
  State<PlaceScreen> createState() => _PlaceScreenState();
}

class _PlaceScreenState extends State<PlaceScreen> {
  final _query = TextEditingController();
  List<Place> _found = const [];
  bool _searching = false;
  String? _error;

  @override
  void dispose() {
    _query.dispose();
    super.dispose();
  }

  Future<void> _search() async {
    final q = _query.text.trim();
    if (q.length < 2) return;
    setState(() {
      _searching = true;
      _error = null;
    });
    try {
      final found = await widget.client.searchPlaces(q);
      if (mounted) setState(() => _found = found);
    } on Exception {
      if (mounted) {
        setState(
          () => _error = tr('Die Suche ging nicht.', 'The search failed.'),
        );
      }
    } finally {
      if (mounted) setState(() => _searching = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(tr('Für welchen Ort?', 'Which place?'))),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Card(
            margin: EdgeInsets.zero,
            child: ListTile(
              key: const Key('place-here'),
              leading: const Icon(Icons.my_location, color: Sky.aurora),
              title: Text(tr('Mein Standort', 'My location')),
              subtitle: Text(
                tr(
                  'Nur auf etwa 10 km genau, gerundet an den Hub',
                  'Only to about 10 km, rounded before it goes to the hub',
                ),
              ),
              onTap: () => Navigator.pop(context, const SkyPlace.here()),
            ),
          ),
          const SizedBox(height: 20),
          TextField(
            key: const Key('place-query'),
            controller: _query,
            textInputAction: TextInputAction.search,
            onSubmitted: (_) => _search(),
            decoration: InputDecoration(
              labelText: tr('Ort suchen', 'Search a place'),
              suffixIcon: IconButton(
                tooltip: tr('Suchen', 'Search'),
                icon: const Icon(Icons.search),
                onPressed: _search,
              ),
            ),
          ),
          if (_searching) const LinearProgressIndicator(),
          if (_error != null)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(
                _error!,
                style: TextStyle(color: theme.colorScheme.error),
              ),
            ),
          for (final p in _found)
            ListTile(
              title: Text(p.name.split(',').first),
              subtitle: Text(
                p.name.split(',').skip(1).join(',').trim(),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
              onTap: () => Navigator.pop(context, SkyPlace.at(p)),
            ),
          const SizedBox(height: 16),
          Text(
            tr(
              'Die Suche läuft über den Hub, kein Kartendienst sieht dich. Der gewählte Ort bleibt auf dem Handy.',
              'The search goes through the hub; no map service sees you. The chosen place stays on the phone.',
            ),
            style: theme.textTheme.bodySmall,
          ),
        ],
      ),
    );
  }
}

/// Compass direction of an azimuth: "Süden", "Südwesten" …
String compass(double azimuth) {
  const de = [
    'Norden',
    'Nordosten',
    'Osten',
    'Südosten',
    'Süden',
    'Südwesten',
    'Westen',
    'Nordwesten',
  ];
  const en = [
    'north',
    'north-east',
    'east',
    'south-east',
    'south',
    'south-west',
    'west',
    'north-west',
  ];
  return (appEnglish ? en : de)[(azimuth / 45).round() % 8];
}

/// "Heute 19:42", "Do 30.10.", for the calendar.
String calendarWhen(CalendarItem item) {
  final t = item.at.toLocal();
  final now = DateTime.now();
  final today = DateTime(now.year, now.month, now.day);
  final day = DateTime(t.year, t.month, t.day);
  final date = day == today
      ? tr('Heute', 'Today')
      : day == today.add(const Duration(days: 1))
      ? tr('Morgen', 'Tomorrow')
      : '${weekday(t)} ${t.day}.${t.month}.';
  return item.allDay ? date : '$date ${clock(item.at)}';
}
