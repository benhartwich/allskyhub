// Detections from the camera (SPEC §6.4): newest list, strip per night, detail with picture.

import 'dart:async';

import 'package:flutter/material.dart';

import '../api/hub_client.dart';

const eventTitles = {
  'meteor': 'Meteor',
  'lightning': 'Blitz',
  'aurora': 'Polarlicht',
  'nlc': 'Leuchtende Nachtwolken',
  'satellite': 'Satellit',
  'clouds': 'Wolken',
  'sky_quality': 'Himmelsqualität',
};

// SPEC §6.4: direction_deg is the trail's axis in the image (0..180, 0 = up), no sense of travel.
const _axes = [
  'senkrecht',
  'diagonal, oben rechts – unten links',
  'waagrecht',
  'diagonal, oben links – unten rechts',
];

const _compassPoints = ['N', 'NO', 'O', 'SO', 'S', 'SW', 'W', 'NW'];

/// Azimuth (0 = north, clockwise) as one of eight German compass points.
String compassPoint(num azimuth) =>
    _compassPoints[((azimuth % 360) / 45).round() % 8];

String _two(int n) => n.toString().padLeft(2, '0');

String eventTime(DateTime t) {
  final l = t.toLocal();
  return '${_two(l.day)}.${_two(l.month)}. ${_two(l.hour)}:${_two(l.minute)}:${_two(l.second)}';
}

/// German label/value rows for an event's `data`; meteor keys per SPEC §6.4, other keys raw.
List<(String, String)> eventRows(SkyEvent event) {
  final rows = <(String, String)>[
    ('Zeit', eventTime(event.start)),
    ('Sicherheit', '${(event.confidence * 100).round()} %'),
  ];
  final seconds = event.end.difference(event.start).inMilliseconds / 1000;
  if (seconds > 0) rows.add(('Dauer', '${seconds.toStringAsFixed(1)} s'));
  final data = Map<String, dynamic>.of(event.data);
  if (event.kind == 'meteor') {
    final length = data.remove('length_px');
    if (length is num) rows.add(('Spurlänge', '${length.round()} px'));
    final peak = data.remove('peak');
    if (peak is num) rows.add(('Helligkeit', '${(peak * 100).round()} %'));
    final frames = data.remove('frames');
    if (frames is num) rows.add(('Bilder', '${frames.round()}'));
    final direction = data.remove('direction_deg');
    if (direction is num) {
      final axis = ((direction % 180) / 45).round() % 4;
      rows.add(('Richtung', '${direction.round()}° (${_axes[axis]})'));
    }
    final shower = data.remove('shower');
    if (shower is String && shower.isNotEmpty) {
      rows.add(('Meteorstrom', shower));
    }
  }
  if (event.kind == 'lightning') {
    final area = data.remove('area_frac');
    if (area is num) {
      rows.add(('Erhellter Himmel', '${(area * 100).round()} %'));
    }
    final peak = data.remove('peak');
    if (peak is num) rows.add(('Aufhellung', '${(peak * 100).round()} %'));
    final flashes = data.remove('storm_flashes');
    if (flashes is num) rows.add(('Blitze in 30 Min.', '${flashes.round()}'));
    data.remove('storm');
  }
  if (event.kind == 'aurora' || event.kind == 'nlc') {
    final index = data.remove('peak_index');
    if (index is num) rows.add(('Stärke', '${index.round()} %'));
    final green = data.remove('green');
    if (green is num) rows.add(('Grünanteil', green.toStringAsFixed(0)));
    final blue = data.remove('blue');
    if (blue is num) rows.add(('Blauanteil', blue.toStringAsFixed(0)));
    final frames = data.remove('frames');
    if (frames is num) rows.add(('Bilder', '${frames.round()}'));
    // Image-relative (0 = up), not a compass bearing: no N/E/S/W without calibration.
    final direction = data.remove('direction_deg');
    if (direction is num) {
      rows.add(('Bildrichtung', '${direction.round() % 360}°'));
    }
    if (data.remove('ongoing') == true) rows.add(('Status', 'läuft noch'));
  }
  data.remove('image_rev');
  // With a calibrated camera (SPEC §4.8) the agent adds the real direction on the sky.
  final azimuth = data.remove('azimuth_deg');
  if (azimuth is num) {
    rows.add((
      'Himmelsrichtung',
      '${azimuth.round() % 360}° (${compassPoint(azimuth)})',
    ));
  }
  final altitude = data.remove('altitude_deg');
  if (altitude is num) {
    rows.add(('Höhe über dem Horizont', '${altitude.round()}°'));
  }
  for (final entry in data.entries) {
    if (entry.value != null) rows.add((entry.key, '${entry.value}'));
  }
  return rows;
}

final _stormKey = RegExp(r'^storm-\d{8}T\d{6}Z$');

/// All flashes of one thunderstorm (SPEC §6.4 lightning `data.storm`), shown as one entry.
class StormGroup {
  StormGroup(this.key);

  final String key;
  final flashes = <SkyEvent>[];

  DateTime get start =>
      flashes.map((e) => e.start).reduce((a, b) => a.isBefore(b) ? a : b);
  DateTime get end =>
      flashes.map((e) => e.end).reduce((a, b) => a.isAfter(b) ? a : b);

  /// The flash that lit up the largest part of the sky.
  SkyEvent get cover => flashes.reduce((a, b) => _area(b) > _area(a) ? b : a);

  static double _area(SkyEvent e) =>
      (e.data['area_frac'] as num?)?.toDouble() ?? 0;
}

/// Events in order, with the flashes of each storm collapsed into one [StormGroup] at the
/// place of its first (newest) flash. Items are [SkyEvent] or [StormGroup].
List<Object> groupStorms(List<SkyEvent> events) {
  final storms = <String, StormGroup>{};
  final items = <Object>[];
  for (final event in events) {
    final key = event.kind == 'lightning' ? event.data['storm'] : null;
    if (key is! String || !_stormKey.hasMatch(key)) {
      items.add(event);
      continue;
    }
    final storm = storms.putIfAbsent(key, () {
      final group = StormGroup(key);
      items.add(group);
      return group;
    });
    storm.flashes.add(event);
  }
  return items;
}

String _stormLabel(StormGroup storm) =>
    '${storm.flashes.length} Blitz${storm.flashes.length == 1 ? '' : 'e'}';

void _openStorm(
  BuildContext context,
  HubClient client,
  String cameraId,
  StormGroup storm,
) => Navigator.of(context).push(
  MaterialPageRoute<void>(
    builder: (_) =>
        StormScreen(client: client, cameraId: cameraId, storm: storm),
  ),
);

Widget _thumb(
  HubClient client,
  String cameraId,
  SkyEvent event, {
  BoxFit fit = BoxFit.cover,
}) => event.hasThumb
    ? Image.network(
        client.eventImageUrl(cameraId, event, thumb: true).toString(),
        headers: client.authHeaders,
        fit: fit,
        errorBuilder: (_, _, _) => const ColoredBox(color: Colors.black),
      )
    : const ColoredBox(
        color: Colors.black,
        child: Center(child: Icon(Icons.auto_awesome, color: Colors.white54)),
      );

void _open(
  BuildContext context,
  HubClient client,
  String cameraId,
  SkyEvent event,
) => Navigator.of(context).push(
  MaterialPageRoute<void>(
    builder: (_) =>
        EventScreen(client: client, cameraId: cameraId, event: event),
  ),
);

/// Newest detections of a camera, loading older ones while scrolling.
class EventsScreen extends StatefulWidget {
  const EventsScreen({super.key, required this.client, required this.camera});

  final HubClient client;
  final Camera camera;

  @override
  State<EventsScreen> createState() => _EventsScreenState();
}

class _EventsScreenState extends State<EventsScreen> {
  static const _page = 30;
  final _events = <SkyEvent>[];
  bool _loading = false;
  bool _done = false;
  bool _showAll = false;
  String? _error;

  void _toggleAll() {
    setState(() {
      _showAll = !_showAll;
      _events.clear();
      _done = false;
    });
    _more();
  }

  @override
  void initState() {
    super.initState();
    _more();
  }

  Future<void> _more() async {
    if (_loading || _done) return;
    setState(() => _loading = true);
    try {
      final next = await widget.client.events(
        widget.camera.id,
        before: _events.isEmpty ? null : _events.last.start,
        limit: _page,
        hideFalse: !_showAll,
      );
      setState(() {
        _events.addAll(next);
        _done = next.length < _page;
        _error = null;
      });
    } on Exception {
      setState(() => _error = 'Die Ereignisse konnten nicht geladen werden.');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final items = groupStorms(_events);
    return Scaffold(
      appBar: AppBar(
        title: Text('Ereignisse · ${widget.camera.name}'),
        actions: [
          IconButton(
            key: const Key('show-all'),
            tooltip: _showAll
                ? 'Aussortierte ausblenden'
                : 'Aussortierte zeigen',
            icon: Icon(_showAll ? Icons.filter_alt_off : Icons.filter_alt),
            onPressed: _toggleAll,
          ),
        ],
      ),
      body: _events.isEmpty && !_loading
          ? Center(
              child: Padding(
                padding: const EdgeInsets.all(32),
                child: Text(
                  _error ??
                      'Noch keine Ereignisse. Erkennt die Kamera einen Meteor, erscheint er hier.',
                  textAlign: TextAlign.center,
                ),
              ),
            )
          : NotificationListener<ScrollNotification>(
              onNotification: (n) {
                if (n.metrics.pixels > n.metrics.maxScrollExtent - 300) _more();
                return false;
              },
              child: ListView.builder(
                itemCount: items.length + (_loading ? 1 : 0),
                itemBuilder: (context, i) {
                  if (i >= items.length) {
                    return const Padding(
                      padding: EdgeInsets.all(16),
                      child: Center(child: CircularProgressIndicator()),
                    );
                  }
                  final item = items[i];
                  if (item is StormGroup) {
                    return ListTile(
                      key: ValueKey(item.key),
                      leading: SizedBox(
                        width: 64,
                        height: 64,
                        child: ClipRRect(
                          borderRadius: BorderRadius.circular(6),
                          child: _thumb(
                            widget.client,
                            widget.camera.id,
                            item.cover,
                          ),
                        ),
                      ),
                      title: const Text('Gewitter'),
                      subtitle: Text(
                        '${_stormLabel(item)} · ${eventTime(item.start)}–'
                        '${eventTime(item.end).substring(7, 12)}',
                      ),
                      onTap: () => _openStorm(
                        context,
                        widget.client,
                        widget.camera.id,
                        item,
                      ),
                    );
                  }
                  final event = item as SkyEvent;
                  return ListTile(
                    key: ValueKey(event.id),
                    leading: SizedBox(
                      width: 64,
                      height: 64,
                      child: ClipRRect(
                        borderRadius: BorderRadius.circular(6),
                        child: _thumb(widget.client, widget.camera.id, event),
                      ),
                    ),
                    title: Text(eventTitles[event.kind] ?? event.kind),
                    subtitle: Text(
                      '${eventTime(event.start)} · ${(event.confidence * 100).round()} %'
                      '${event.ongoing ? ' · läuft noch' : ''}'
                      '${event.label == 'false_positive'
                          ? ' · aussortiert'
                          : event.label == 'confirmed'
                          ? ' · bestätigt'
                          : ''}',
                    ),
                    onTap: () =>
                        _open(context, widget.client, widget.camera.id, event),
                  );
                },
              ),
            ),
    );
  }
}

/// The detections of one night, above its thumbnails.
class EventStrip extends StatelessWidget {
  const EventStrip({
    super.key,
    required this.client,
    required this.cameraId,
    required this.events,
  });

  final HubClient client;
  final String cameraId;
  final List<SkyEvent> events;

  @override
  Widget build(BuildContext context) => SizedBox(
    height: 110,
    child: ListView(
      scrollDirection: Axis.horizontal,
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      children: [
        for (final item in groupStorms(events))
          if (item is StormGroup)
            _tile(
              context,
              key: item.key,
              event: item.cover,
              label: 'Gewitter · ${_stormLabel(item)}',
              onTap: () => _openStorm(context, client, cameraId, item),
            )
          else
            _tile(
              context,
              key: (item as SkyEvent).id,
              event: item,
              label:
                  '${eventTitles[item.kind] ?? item.kind} '
                  '${eventTime(item.start).substring(7, 12)}',
              onTap: () => _open(context, client, cameraId, item),
            ),
      ],
    ),
  );

  Widget _tile(
    BuildContext context, {
    required String key,
    required SkyEvent event,
    required String label,
    required VoidCallback onTap,
  }) => Padding(
    key: ValueKey(key),
    padding: const EdgeInsets.only(right: 8),
    child: InkWell(
      onTap: onTap,
      child: SizedBox(
        width: 100,
        child: Stack(
          fit: StackFit.expand,
          children: [
            ClipRRect(
              borderRadius: BorderRadius.circular(8),
              child: _thumb(client, cameraId, event),
            ),
            Positioned(
              left: 4,
              bottom: 4,
              child: ColoredBox(
                color: Colors.black54,
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 4),
                  child: Text(
                    label,
                    style: const TextStyle(color: Colors.white, fontSize: 11),
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    ),
  );
}

/// All flashes of one thunderstorm.
class StormScreen extends StatelessWidget {
  const StormScreen({
    super.key,
    required this.client,
    required this.cameraId,
    required this.storm,
  });

  final HubClient client;
  final String cameraId;
  final StormGroup storm;

  @override
  Widget build(BuildContext context) {
    final flashes = [...storm.flashes]
      ..sort((a, b) => a.start.compareTo(b.start));
    return Scaffold(
      appBar: AppBar(title: const Text('Gewitter')),
      body: ListView(
        padding: const EdgeInsets.all(8),
        children: [
          Padding(
            padding: const EdgeInsets.all(8),
            child: Text(
              '${_stormLabel(storm)} · ${eventTime(storm.start)}–'
              '${eventTime(storm.end).substring(7, 12)}',
            ),
          ),
          GridView.count(
            crossAxisCount: 3,
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            mainAxisSpacing: 6,
            crossAxisSpacing: 6,
            children: [
              for (final flash in flashes)
                InkWell(
                  key: ValueKey(flash.id),
                  onTap: () => _open(context, client, cameraId, flash),
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(8),
                    child: _thumb(client, cameraId, flash),
                  ),
                ),
            ],
          ),
        ],
      ),
    );
  }
}

/// One detection: the full picture (fetched from the camera on demand) and its data.
class EventScreen extends StatefulWidget {
  const EventScreen({
    super.key,
    required this.client,
    required this.cameraId,
    required this.event,
    this.pollInterval = const Duration(seconds: 3),
  });

  final HubClient client;
  final String cameraId;
  final SkyEvent event;
  final Duration pollInterval;

  @override
  State<EventScreen> createState() => _EventScreenState();
}

class _EventScreenState extends State<EventScreen> {
  bool _full = false;
  bool _cancelled = false;
  String? _note;
  late String? _label = widget.event.label;
  bool _saving = false;

  Future<void> _setLabel(String? label) async {
    setState(() => _saving = true);
    try {
      final updated = await widget.client.labelEvent(
        widget.cameraId,
        widget.event,
        label,
      );
      if (mounted) setState(() => _label = updated.label);
    } on Exception {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Die Markierung konnte nicht gespeichert werden.'),
          ),
        );
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Widget _labelCard(String title) => Padding(
    padding: const EdgeInsets.fromLTRB(16, 16, 16, 0),
    child: _label == null
        ? Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                'Stimmt die Erkennung? Deine Antwort hilft, sie zu verbessern.',
              ),
              const SizedBox(height: 8),
              Wrap(
                spacing: 8,
                children: [
                  FilledButton(
                    key: const Key('confirm'),
                    onPressed: _saving ? null : () => _setLabel('confirmed'),
                    child: const Text('Echt'),
                  ),
                  OutlinedButton(
                    key: const Key('false-positive'),
                    onPressed: _saving
                        ? null
                        : () => _setLabel('false_positive'),
                    child: Text('Kein $title'),
                  ),
                ],
              ),
            ],
          )
        : Row(
            children: [
              Expanded(
                child: Text(
                  _label == 'false_positive'
                      ? 'Als „kein $title“ markiert.'
                      : 'Als echt bestätigt.',
                  style: const TextStyle(fontWeight: FontWeight.w600),
                ),
              ),
              TextButton(
                key: const Key('clear-label'),
                onPressed: _saving ? null : () => _setLabel(null),
                child: const Text('Markierung entfernen'),
              ),
            ],
          ),
  );

  @override
  void initState() {
    super.initState();
    if (widget.event.hasImage) _fetch();
  }

  @override
  void dispose() {
    _cancelled = true;
    super.dispose();
  }

  Future<void> _fetch() async {
    final deadline = DateTime.now().add(const Duration(minutes: 5));
    try {
      while (!_cancelled && DateTime.now().isBefore(deadline)) {
        if (await widget.client.ensureEventImage(
          widget.cameraId,
          widget.event,
        )) {
          if (mounted) setState(() => _full = true);
          return;
        }
        if (mounted && _note == null) {
          setState(() => _note = 'Vollbild wird von der Kamera geholt …');
        }
        await Future<void>.delayed(widget.pollInterval);
      }
    } on HubException catch (e) {
      if (mounted) {
        setState(
          () => _note = e.statusCode == 404
              ? 'Das Vollbild ist nicht verfügbar (Kamera offline oder Bild gelöscht).'
              : null,
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final event = widget.event;
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(eventTitles[event.kind] ?? event.kind)),
      body: ListView(
        children: [
          if (event.hasImage)
            AspectRatio(
              aspectRatio: 1,
              child: ColoredBox(
                color: Colors.black,
                child: InteractiveViewer(
                  maxScale: 6,
                  child: _full
                      ? Image.network(
                          widget.client
                              .eventImageUrl(widget.cameraId, event)
                              .toString(),
                          headers: widget.client.authHeaders,
                          fit: BoxFit.contain,
                          errorBuilder: (_, _, _) => _thumb(
                            widget.client,
                            widget.cameraId,
                            event,
                            fit: BoxFit.contain,
                          ),
                        )
                      : _thumb(
                          widget.client,
                          widget.cameraId,
                          event,
                          fit: BoxFit.contain,
                        ),
                ),
              ),
            ),
          if (_note != null && !_full)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
              child: Text(_note!, style: theme.textTheme.bodySmall),
            ),
          _labelCard(eventTitles[event.kind] ?? event.kind),
          Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              children: [
                for (final (label, value) in eventRows(event))
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 3),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Expanded(child: Text(label)),
                        const SizedBox(width: 12),
                        // Long values (e.g. the axis) wrap instead of overflowing.
                        Flexible(
                          flex: 2,
                          child: Text(
                            value,
                            textAlign: TextAlign.end,
                            style: const TextStyle(fontWeight: FontWeight.w600),
                          ),
                        ),
                      ],
                    ),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
