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

const _compass = [
  'oben',
  'oben rechts',
  'rechts',
  'unten rechts',
  'unten',
  'unten links',
  'links',
  'oben links',
];

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
      final sector = ((direction % 360) / 45).round() % 8;
      rows.add((
        'Richtung',
        '${direction.round()}° (nach ${_compass[sector]})',
      ));
    }
    final shower = data.remove('shower');
    if (shower is String && shower.isNotEmpty) {
      rows.add(('Meteorstrom', shower));
    }
  }
  for (final entry in data.entries) {
    if (entry.value != null) rows.add((entry.key, '${entry.value}'));
  }
  return rows;
}

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
  String? _error;

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
    return Scaffold(
      appBar: AppBar(title: Text('Ereignisse · ${widget.camera.name}')),
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
                itemCount: _events.length + (_loading ? 1 : 0),
                itemBuilder: (context, i) {
                  if (i >= _events.length) {
                    return const Padding(
                      padding: EdgeInsets.all(16),
                      child: Center(child: CircularProgressIndicator()),
                    );
                  }
                  final event = _events[i];
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
                      '${eventTime(event.start)} · ${(event.confidence * 100).round()} %',
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
        for (final event in events)
          Padding(
            key: ValueKey(event.id),
            padding: const EdgeInsets.only(right: 8),
            child: InkWell(
              onTap: () => _open(context, client, cameraId, event),
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
                            '${eventTitles[event.kind] ?? event.kind} '
                            '${eventTime(event.start).substring(7, 12)}',
                            style: const TextStyle(
                              color: Colors.white,
                              fontSize: 11,
                            ),
                          ),
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
      ],
    ),
  );
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
          Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              children: [
                for (final (label, value) in eventRows(event))
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 3),
                    child: Row(
                      children: [
                        Expanded(child: Text(label)),
                        Text(
                          value,
                          style: const TextStyle(fontWeight: FontWeight.w600),
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
