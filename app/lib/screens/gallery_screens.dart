// Per-night gallery of the hub's frame archive: nights → thumbnails → full image.

import 'package:flutter/material.dart';

import '../api/hub_client.dart';
import 'event_screens.dart';
import 'measurements_screen.dart';
import 'product_screens.dart';
import 'sky_charts.dart';
import 'zoomable.dart';
import '../l10n.dart';

String _two(int n) => n.toString().padLeft(2, '0');

/// "Di 06.10. → Mi 07.10." ("Tue 6 Oct → Wed 7 Oct"): a night spans two dates (SPEC §4.5
/// night id = evening).
String nightTitle(Night night) {
  final evening = night.evening;
  final morning = evening.add(const Duration(days: 1));
  String day(DateTime d) => '${weekdaysShort[d.weekday - 1]} ${dayMonth(d)}';
  return '${day(evening)} → ${day(morning)}';
}

/// "120 Bilder · 19:02–05:41 · 3 Produkte"; first/last are null for nights with only products.
String nightSubtitle(Night night) {
  final first = night.first;
  final last = night.last;
  return [
    if (night.frames > 0)
      tr('${night.frames} Bilder', '${night.frames} pictures'),
    if (first != null && last != null) '${clock(first)}–${clock(last)}',
    if (night.products > 0)
      tr(
        '${night.products} Produkt${night.products == 1 ? '' : 'e'}',
        '${night.products} product${night.products == 1 ? '' : 's'}',
      ),
    if (night.sky > 0) tr('Himmelsdaten', 'Sky data'),
    if (night.events > 0)
      tr(
        '${night.events} Ereignis${night.events == 1 ? '' : 'se'}',
        '${night.events} event${night.events == 1 ? '' : 's'}',
      ),
  ].join(' · ');
}

String exposure(int microseconds) => microseconds >= 1000000
    ? '${(microseconds / 1e6).toStringAsPrecision(3)} s'
    : '${(microseconds / 1e3).toStringAsPrecision(3)} ms';

String clock(DateTime t) {
  final local = t.toLocal();
  return '${_two(local.hour)}:${_two(local.minute)}';
}

class NightsScreen extends StatefulWidget {
  const NightsScreen({super.key, required this.client, required this.camera});

  final HubClient client;
  final Camera camera;

  @override
  State<NightsScreen> createState() => _NightsScreenState();
}

class _NightsScreenState extends State<NightsScreen> {
  late Future<List<Night>> _nights = widget.client.nights(widget.camera.id);

  Future<void> _reload() async {
    final next = widget.client.nights(widget.camera.id);
    setState(() => _nights = next);
    await next;
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text('${tr('Galerie', 'Gallery')} · ${widget.camera.name}'),
      ),
      body: RefreshIndicator(
        onRefresh: _reload,
        child: FutureBuilder<List<Night>>(
          future: _nights,
          builder: (context, snapshot) {
            if (snapshot.hasError) {
              return _Message(
                tr(
                  'Die Galerie konnte nicht geladen werden.',
                  'The gallery could not be loaded.',
                ),
                onRetry: _reload,
              );
            }
            final nights = snapshot.data;
            if (nights == null) {
              return const Center(child: CircularProgressIndicator());
            }
            if (nights.isEmpty) {
              return _Message(
                tr(
                  'Noch keine Bilder im Archiv. Der Hub sammelt etwa jede Minute ein Vorschaubild, '
                      'solange die Kamera verbunden ist.',
                  'No pictures in the archive yet. The hub collects a preview about every '
                      'minute while the camera is connected.',
                ),
              );
            }
            return ListView.separated(
              itemCount: nights.length,
              separatorBuilder: (_, _) => const Divider(height: 1),
              itemBuilder: (context, i) {
                final night = nights[i];
                return ListTile(
                  leading: const Icon(Icons.nights_stay_outlined),
                  title: Text(nightTitle(night)),
                  subtitle: Text(nightSubtitle(night)),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => Navigator.of(context).push(
                    MaterialPageRoute<void>(
                      builder: (_) => NightScreen(
                        client: widget.client,
                        camera: widget.camera,
                        night: night,
                      ),
                    ),
                  ),
                );
              },
            );
          },
        ),
      ),
    );
  }
}

class NightScreen extends StatefulWidget {
  const NightScreen({
    super.key,
    required this.client,
    required this.camera,
    required this.night,
  });

  final HubClient client;
  final Camera camera;
  final Night night;

  @override
  State<NightScreen> createState() => _NightScreenState();
}

class _NightScreenState extends State<NightScreen> {
  late final Future<List<FrameItem>> _frames = widget.client.frames(
    widget.camera.id,
    widget.night.nightId,
  );
  late final Future<List<SkySample>> _sky = widget.night.sky == 0
      ? Future.value(const <SkySample>[])
      : widget.client.nightSky(widget.camera.id, widget.night.nightId);
  late final Future<List<SkyEvent>> _events = widget.night.events == 0
      ? Future.value(const <SkyEvent>[])
      : widget.client.nightEvents(widget.camera.id, widget.night.nightId);
  late final Future<List<Product>> _products = widget.night.products == 0
      ? Future.value(const <Product>[])
      : widget.client.products(widget.camera.id, widget.night.nightId);

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(nightTitle(widget.night))),
      body: FutureBuilder<List<FrameItem>>(
        future: _frames,
        builder: (context, snapshot) {
          if (snapshot.hasError) {
            return _Message(
              tr(
                'Die Bilder konnten nicht geladen werden.',
                'The pictures could not be loaded.',
              ),
            );
          }
          final frames = snapshot.data;
          if (frames == null) {
            return const Center(child: CircularProgressIndicator());
          }
          return CustomScrollView(
            slivers: [
              SliverToBoxAdapter(
                child: FutureBuilder<List<Product>>(
                  future: _products,
                  builder: (context, snapshot) {
                    final products = snapshot.data ?? const <Product>[];
                    if (products.isEmpty) return const SizedBox.shrink();
                    return ProductStrip(
                      client: widget.client,
                      cameraId: widget.camera.id,
                      nightId: widget.night.nightId,
                      products: products,
                    );
                  },
                ),
              ),
              SliverToBoxAdapter(
                child: FutureBuilder<List<SkySample>>(
                  future: _sky,
                  builder: (context, snapshot) => Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      SkyCharts(samples: snapshot.data ?? const <SkySample>[]),
                      if (widget.night.sky > 0)
                        Padding(
                          padding: const EdgeInsets.fromLTRB(12, 0, 12, 8),
                          child: TextButton.icon(
                            key: const Key('night-measurements'),
                            icon: const Icon(Icons.show_chart),
                            label: Text(
                              tr(
                                'Alle Messdaten dieser Nacht',
                                'All measurements of this night',
                              ),
                            ),
                            onPressed: () => Navigator.of(context).push(
                              MaterialPageRoute<void>(
                                builder: (_) => MeasurementsScreen(
                                  client: widget.client,
                                  cameraId: widget.camera.id,
                                  night: widget.night.nightId,
                                ),
                              ),
                            ),
                          ),
                        ),
                    ],
                  ),
                ),
              ),
              SliverToBoxAdapter(
                child: FutureBuilder<List<SkyEvent>>(
                  future: _events,
                  builder: (context, snapshot) {
                    final events = snapshot.data ?? const <SkyEvent>[];
                    if (events.isEmpty) return const SizedBox.shrink();
                    final meteors = meteorSummary(events);
                    return Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        if (meteors != null)
                          Padding(
                            padding: const EdgeInsets.fromLTRB(12, 8, 12, 0),
                            child: Text(meteors, key: const Key('meteors')),
                          ),
                        EventStrip(
                          client: widget.client,
                          cameraId: widget.camera.id,
                          events: events,
                        ),
                      ],
                    );
                  },
                ),
              ),
              SliverPadding(
                padding: const EdgeInsets.all(4),
                sliver: SliverGrid.builder(
                  gridDelegate: const SliverGridDelegateWithMaxCrossAxisExtent(
                    maxCrossAxisExtent: 140,
                    mainAxisSpacing: 4,
                    crossAxisSpacing: 4,
                  ),
                  itemCount: frames.length,
                  itemBuilder: (context, i) {
                    final frame = frames[i];
                    return InkWell(
                      key: ValueKey(frame.name),
                      onTap: () => Navigator.of(context).push(
                        MaterialPageRoute<void>(
                          builder: (_) => FrameViewer(
                            client: widget.client,
                            cameraId: widget.camera.id,
                            nightId: widget.night.nightId,
                            frames: frames,
                            initial: i,
                          ),
                        ),
                      ),
                      child: Stack(
                        fit: StackFit.expand,
                        children: [
                          _ArchiveImage(
                            client: widget.client,
                            url: widget.client.frameUrl(
                              widget.camera.id,
                              widget.night.nightId,
                              frame.name,
                              thumb: true,
                            ),
                            fit: BoxFit.cover,
                          ),
                          Positioned(
                            left: 4,
                            bottom: 4,
                            child: DecoratedBox(
                              decoration: BoxDecoration(
                                color: Colors.black54,
                                borderRadius: BorderRadius.circular(4),
                              ),
                              child: Padding(
                                padding: const EdgeInsets.symmetric(
                                  horizontal: 4,
                                  vertical: 1,
                                ),
                                child: Text(
                                  clock(frame.capturedAt),
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
                    );
                  },
                ),
              ),
            ],
          );
        },
      ),
    );
  }
}

/// Full images to swipe through and zoom; falls back to the thumbnail when the full image
/// has already been removed from the archive.
class FrameViewer extends StatefulWidget {
  const FrameViewer({
    super.key,
    required this.client,
    required this.cameraId,
    required this.nightId,
    required this.frames,
    required this.initial,
  });

  final HubClient client;
  final String cameraId;
  final String nightId;
  final List<FrameItem> frames;
  final int initial;

  @override
  State<FrameViewer> createState() => _FrameViewerState();
}

class _FrameViewerState extends State<FrameViewer> {
  @override
  Widget build(BuildContext context) => PhotoPager(
    count: widget.frames.length,
    initial: widget.initial,
    title: (i) => clock(widget.frames[i].capturedAt),
    image: (i) {
      final f = widget.frames[i];
      return _ArchiveImage(
        client: widget.client,
        url: widget.client.frameUrl(
          widget.cameraId,
          widget.nightId,
          f.name,
          thumb: !f.hasFull,
        ),
        fit: BoxFit.contain,
      );
    },
    caption: (i) {
      final frame = widget.frames[i];
      final mode = frame.mode == 'night'
          ? tr('Nacht', 'Night')
          : tr('Tag', 'Day');
      final sun = frame.sunElevation.toStringAsFixed(1);
      final previewOnly = tr(
        '\nNur Vorschau: das Vollbild ist nicht mehr im Archiv.',
        '\nPreview only: the full picture is no longer in the archive.',
      );
      return '$mode · ${exposure(frame.exposureUs)}'
          ' · Gain ${frame.gain.round()} · ${tr('Sonne', 'Sun')} $sun°'
          '${frame.hasFull ? '' : previewOnly}';
    },
  );
}

class _ArchiveImage extends StatelessWidget {
  const _ArchiveImage({
    required this.client,
    required this.url,
    required this.fit,
  });

  final HubClient client;
  final Uri url;
  final BoxFit fit;

  @override
  Widget build(BuildContext context) => Image.network(
    url.toString(),
    headers: client.authHeaders,
    fit: fit,
    gaplessPlayback: true,
    errorBuilder: (_, _, _) => const ColoredBox(
      color: Colors.black,
      child: Center(child: Icon(Icons.broken_image)),
    ),
  );
}

class _Message extends StatelessWidget {
  const _Message(this.text, {this.onRetry});

  final String text;
  final Future<void> Function()? onRetry;

  @override
  Widget build(BuildContext context) => ListView(
    padding: const EdgeInsets.all(32),
    children: [
      Text(text, textAlign: TextAlign.center),
      if (onRetry != null)
        Center(
          child: TextButton(
            onPressed: onRetry,
            child: Text(tr('Erneut versuchen', 'Try again')),
          ),
        ),
    ],
  );
}
