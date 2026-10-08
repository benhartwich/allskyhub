// Per-night gallery of the hub's frame archive: nights → thumbnails → full image.

import 'package:flutter/material.dart';

import '../api/hub_client.dart';
import 'event_screens.dart';
import 'product_screens.dart';

const _weekdays = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'];

String _two(int n) => n.toString().padLeft(2, '0');

/// "Di 06.10. → Mi 07.10.": a night spans two dates (SPEC §4.5 night id = evening).
String nightTitle(Night night) {
  final evening = night.evening;
  final morning = evening.add(const Duration(days: 1));
  String day(DateTime d) =>
      '${_weekdays[d.weekday - 1]} ${_two(d.day)}.${_two(d.month)}.';
  return '${day(evening)} → ${day(morning)}';
}

/// "120 Bilder · 19:02–05:41 · 3 Produkte"; first/last are null for nights with only products.
String nightSubtitle(Night night) {
  final first = night.first;
  final last = night.last;
  return [
    if (night.frames > 0) '${night.frames} Bilder',
    if (first != null && last != null) '${clock(first)}–${clock(last)}',
    if (night.products > 0)
      '${night.products} Produkt${night.products == 1 ? '' : 'e'}',
    if (night.events > 0)
      '${night.events} Ereignis${night.events == 1 ? '' : 'se'}',
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
      appBar: AppBar(title: Text('Galerie · ${widget.camera.name}')),
      body: RefreshIndicator(
        onRefresh: _reload,
        child: FutureBuilder<List<Night>>(
          future: _nights,
          builder: (context, snapshot) {
            if (snapshot.hasError) {
              return _Message(
                'Die Galerie konnte nicht geladen werden.',
                onRetry: _reload,
              );
            }
            final nights = snapshot.data;
            if (nights == null) {
              return const Center(child: CircularProgressIndicator());
            }
            if (nights.isEmpty) {
              return const _Message(
                'Noch keine Bilder im Archiv. Der Hub sammelt etwa jede Minute ein Vorschaubild, '
                'solange die Kamera verbunden ist.',
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
            return const _Message('Die Bilder konnten nicht geladen werden.');
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
                child: FutureBuilder<List<SkyEvent>>(
                  future: _events,
                  builder: (context, snapshot) {
                    final events = snapshot.data ?? const <SkyEvent>[];
                    if (events.isEmpty) return const SizedBox.shrink();
                    return EventStrip(
                      client: widget.client,
                      cameraId: widget.camera.id,
                      events: events,
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
  late final _pages = PageController(initialPage: widget.initial);
  late int _index = widget.initial;

  @override
  void dispose() {
    _pages.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final frame = widget.frames[_index];
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
        title: Text(clock(frame.capturedAt)),
      ),
      body: Column(
        children: [
          Expanded(
            child: PageView.builder(
              controller: _pages,
              itemCount: widget.frames.length,
              onPageChanged: (i) => setState(() => _index = i),
              itemBuilder: (context, i) {
                final f = widget.frames[i];
                return InteractiveViewer(
                  maxScale: 6,
                  child: _ArchiveImage(
                    client: widget.client,
                    url: widget.client.frameUrl(
                      widget.cameraId,
                      widget.nightId,
                      f.name,
                      thumb: !f.hasFull,
                    ),
                    fit: BoxFit.contain,
                  ),
                );
              },
            ),
          ),
          Padding(
            padding: const EdgeInsets.all(12),
            child: Text(
              '${frame.mode == 'night' ? 'Nacht' : 'Tag'} · ${exposure(frame.exposureUs)}'
              ' · Gain ${frame.gain.round()} · Sonne ${frame.sunElevation.toStringAsFixed(1)}°'
              '${frame.hasFull ? '' : '\nNur Vorschau: das Vollbild ist nicht mehr im Archiv.'}',
              textAlign: TextAlign.center,
              style: const TextStyle(color: Colors.white70),
            ),
          ),
        ],
      ),
    );
  }
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
            child: const Text('Erneut versuchen'),
          ),
        ),
    ],
  );
}
