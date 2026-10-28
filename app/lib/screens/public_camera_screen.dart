// A public camera for everyone (its public page, /sky/<slug>, in the app): the latest
// picture, the last hour as a loop, last night's products, its detections and what the
// owner wrote about it. Only the sections the owner made public come from the hub.

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:share_plus/share_plus.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:video_player/video_player.dart';

import '../api/hub_client.dart';
import '../design.dart';
import '../l10n.dart';
import 'camera_status.dart';
import 'join_screen.dart';
import 'product_screens.dart' show productTitles;
import 'zoomable.dart';

enum _Tab { now, night, events, about }

Widget _broken(BuildContext context, Object error, StackTrace? stack) =>
    const ColoredBox(color: Colors.black);

class PublicCameraScreen extends StatefulWidget {
  const PublicCameraScreen({
    super.key,
    required this.client,
    required this.slug,
    required this.title,
  });

  final HubClient client;
  final String slug;
  final String title;

  @override
  State<PublicCameraScreen> createState() => _PublicCameraScreenState();
}

class _PublicCameraScreenState extends State<PublicCameraScreen> {
  late Future<PublicCameraDetail> _camera = widget.client.publicCamera(
    widget.slug,
  );
  Timer? _refresh;
  _Tab _tab = _Tab.now;

  @override
  void initState() {
    super.initState();
    // A new picture comes about every few minutes.
    _refresh = Timer.periodic(const Duration(minutes: 1), (_) => _reload());
  }

  @override
  void dispose() {
    _refresh?.cancel();
    super.dispose();
  }

  Future<void> _reload() async {
    final next = widget.client.publicCamera(widget.slug);
    try {
      await next;
      if (mounted) setState(() => _camera = next);
    } on Exception {
      // Keep the last answer; the next tick tries again.
    }
  }

  String _url(String path) => widget.client.site(path).toString();

  void _fullscreen(
    List<String> urls,
    int initial,
    String Function(int) title,
  ) => Navigator.of(context).push(
    MaterialPageRoute<void>(
      builder: (_) => PhotoPager(
        count: urls.length,
        initial: initial,
        title: title,
        image: (i) => Image.network(_url(urls[i]), fit: BoxFit.contain),
      ),
    ),
  );

  @override
  Widget build(BuildContext context) => FutureBuilder<PublicCameraDetail>(
    future: _camera,
    builder: (context, snapshot) {
      final camera = snapshot.data;
      return Scaffold(
        appBar: AppBar(
          title: Text(camera?.title ?? widget.title),
          actions: [
            if (camera != null)
              IconButton(
                tooltip: tr('Teilen', 'Share'),
                icon: const Icon(Icons.share_outlined),
                onPressed: () => SharePlus.instance.share(
                  ShareParams(uri: widget.client.site(camera.pageUrl)),
                ),
              ),
          ],
        ),
        body: snapshot.hasError
            ? Center(
                child: Padding(
                  padding: const EdgeInsets.all(32),
                  child: Text(
                    tr(
                      'Diese Kamera ist nicht (mehr) öffentlich.',
                      'This camera is not public (any more).',
                    ),
                    textAlign: TextAlign.center,
                  ),
                ),
              )
            : camera == null
            ? const Center(child: CircularProgressIndicator())
            : _body(camera),
      );
    },
  );

  Widget _body(PublicCameraDetail camera) {
    final tabs = {
      _Tab.now: tr('Jetzt', 'Now'),
      if (camera.lastNight != null || camera.videoUrl != null)
        _Tab.night: tr('Letzte Nacht', 'Last night'),
      if (camera.events.isNotEmpty) _Tab.events: tr('Ereignisse', 'Events'),
      _Tab.about: tr('Über', 'About'),
    };
    final tab = tabs.containsKey(_tab) ? _tab : _Tab.now;
    return ListView(
      padding: const EdgeInsets.only(bottom: 24),
      children: [
        _LiveImage(
          url: camera.liveUrl == null ? null : _url(camera.liveUrl!),
          camera: camera,
          onTap: camera.liveUrl == null
              ? null
              : () => _fullscreen([camera.liveUrl!], 0, (_) => camera.title),
        ),
        SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
          child: Row(
            children: [
              for (final MapEntry(key: t, value: label) in tabs.entries)
                Padding(
                  padding: const EdgeInsets.only(right: 8),
                  child: ChoiceChip(
                    key: Key('tab-${t.name}'),
                    label: Text(label),
                    selected: t == tab,
                    onSelected: (_) => setState(() => _tab = t),
                  ),
                ),
            ],
          ),
        ),
        ...switch (tab) {
          _Tab.now => _now(camera),
          _Tab.night => _night(camera),
          _Tab.events => _events(camera),
          _Tab.about => _about(camera),
        },
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 20, 16, 0),
          child: OutlinedButton.icon(
            key: const Key('own-camera'),
            icon: const Icon(Icons.photo_camera_outlined, color: Sky.aurora),
            label: Text(
              tr(
                'So eine Kamera kannst du auch betreiben',
                'You can run a camera like this too',
              ),
            ),
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) => OwnCameraScreen(client: widget.client),
              ),
            ),
          ),
        ),
      ],
    );
  }

  List<Widget> _now(PublicCameraDetail camera) {
    final f = camera.facts;
    num? n(String key) => f[key] is num ? f[key] as num : null;
    String comma(String s) => appEnglish ? s : s.replaceAll('.', ',');
    final cloud = n('cloud_cover'), sqm = n('sqm_mag'), stars = n('stars');
    final exposure = n('exposure_s'), sun = n('sun_elevation');
    final facts = [
      if (cloud != null)
        (tr('Bewölkung', 'Clouds'), '${(cloud * 100).round()} %'),
      if (stars != null) (tr('Sterne', 'Stars'), '$stars'),
      if (sqm != null)
        (tr('Himmel', 'Sky'), '${comma(sqm.toStringAsFixed(2))} mag'),
      if (exposure != null)
        (tr('Belichtung', 'Exposure'), formatExposure(exposure * 1e6)),
      if (sun != null) (tr('Sonne', 'Sun'), '${sun.round()}°'),
    ];
    return [
      if (facts.isNotEmpty)
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
          child: Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [for (final (label, value) in facts) _Fact(label, value)],
          ),
        ),
      if (camera.loop.length > 1)
        _Card(
          title: tr('Die letzte Stunde', 'The last hour'),
          child: _Loop(urls: [for (final u in camera.loop) _url(u)]),
        ),
      if (_explain(camera) case final text?)
        _Card(
          title: tr('Was du hier siehst', 'What you see here'),
          child: Text(text),
        ),
    ];
  }

  /// A sentence that explains the picture, for the curious.
  String? _explain(PublicCameraDetail camera) {
    final f = camera.facts;
    final sun = f['sun_elevation'], exposure = f['exposure_s'];
    if (sun is! num || exposure is! num) return null;
    final s = exposure >= 1
        ? exposure.toStringAsFixed(exposure >= 10 ? 0 : 1)
        : null;
    if (sun > 0) {
      return tr(
        'Tag: Die Sonne steht ${sun.round()}° hoch. Die Kamera sieht den ganzen Himmel durch ein Fischauge, der Rand des Bildes ist der Horizont.',
        'Day: the sun is ${sun.round()}° high. The camera sees the whole sky through a fisheye lens; the edge of the picture is the horizon.',
      );
    }
    if (sun > -12) {
      return tr(
        'Dämmerung: Die Sonne steht ${(-sun).round()}° unter dem Horizont. Die Kamera belichtet jetzt immer länger.',
        'Twilight: the sun is ${(-sun).round()}° below the horizon. The camera now exposes longer and longer.',
      );
    }
    return tr(
      'Nacht: Die Sonne steht ${(-sun).round()}° unter dem Horizont. ${s == null ? '' : 'Jedes Bild sammelt $s s lang Licht, deshalb sieht die Kamera mehr Sterne als dein Auge. '}Die Mitte des Bildes ist der Zenit, direkt über der Kamera.',
      'Night: the sun is ${(-sun).round()}° below the horizon. ${s == null ? '' : 'Each picture collects light for $s s, so the camera sees more stars than your eye. '}The centre of the picture is the zenith, straight above the camera.',
    );
  }

  List<Widget> _night(PublicCameraDetail camera) {
    final products = camera.products.entries.toList();
    String title(String name) =>
        productTitles[name.replaceAll('.jpg', '')] ?? name;
    return [
      if (camera.videoUrl case final video?)
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
          child: FilledButton.icon(
            icon: const Icon(Icons.play_arrow),
            label: Text(tr('Zeitraffer der Nacht', "The night's timelapse")),
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) => _VideoScreen(
                  url: widget.client.site(video),
                  title: camera.title,
                ),
              ),
            ),
          ),
        ),
      for (final (i, p) in products.indexed)
        _Card(
          title: title(p.key),
          note: p.key == 'keogram.jpg'
              ? tr(
                  'Ein Keogramm ist die ganze Nacht in einem Bild: von jedem Foto ein Streifen von Nord nach Süd, nebeneinander. Wolken, Dämmerung und Polarlicht sieht man auf einen Blick.',
                  'A keogram is the whole night in one picture: a north-south strip from every photo, side by side. Clouds, twilight and aurora show at a glance.',
                )
              : tr(
                  'Sternspuren: alle Bilder der Nacht übereinander. Weil sich die Erde dreht, ziehen die Sterne Kreise um den Himmelspol.',
                  'Star trails: all pictures of the night stacked. As the earth turns, the stars draw circles around the celestial pole.',
                ),
          child: GestureDetector(
            onTap: () => _fullscreen(
              [for (final q in products) '${q.value}?full=1'],
              i,
              (j) => title(products[j].key),
            ),
            child: Image.network(
              _url(p.value),
              fit: BoxFit.contain,
              errorBuilder: _broken,
            ),
          ),
        ),
    ];
  }

  List<Widget> _events(PublicCameraDetail camera) => [
    Padding(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
      child: GridView.count(
        crossAxisCount: 3,
        shrinkWrap: true,
        physics: const NeverScrollableScrollPhysics(),
        mainAxisSpacing: 6,
        crossAxisSpacing: 6,
        children: [
          for (final (i, e) in camera.events.indexed)
            GestureDetector(
              onTap: () => _fullscreen(
                [for (final e in camera.events) e.url],
                i,
                (j) =>
                    '${camera.events[j].title} · ${formatAge(camera.events[j].at)}',
              ),
              child: Stack(
                fit: StackFit.expand,
                children: [
                  Image.network(
                    _url(e.url),
                    fit: BoxFit.cover,
                    errorBuilder: _broken,
                  ),
                  Align(
                    alignment: Alignment.bottomLeft,
                    child: Container(
                      color: Colors.black54,
                      padding: const EdgeInsets.all(4),
                      child: Text(
                        e.title,
                        style: Theme.of(context).textTheme.labelSmall,
                      ),
                    ),
                  ),
                ],
              ),
            ),
        ],
      ),
    ),
  ];

  List<Widget> _about(PublicCameraDetail camera) {
    final rows = [
      (tr('Ort', 'Place'), camera.locationLabel),
      (tr('Kamera', 'Camera'), camera.camera),
      (tr('Objektiv', 'Lens'), camera.lens),
      (tr('Rechner', 'Computer'), camera.computer),
      (tr('Betrieben von', 'Run by'), camera.owner),
    ].where((r) => r.$2.isNotEmpty);
    return [
      _Card(
        title: tr('Steckbrief', 'Profile'),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            for (final (label, value) in rows)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 3),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    SizedBox(width: 120, child: Text(label)),
                    Expanded(child: Text(value)),
                  ],
                ),
              ),
            if (camera.notes.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(camera.notes),
            ],
            if (rows.isEmpty && camera.notes.isEmpty)
              Text(tr('Keine Angaben.', 'No details.')),
          ],
        ),
      ),
      Padding(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
        child: TextButton.icon(
          icon: const Icon(Icons.open_in_new),
          label: Text(
            tr('Öffentliche Seite im Browser', 'Public page in the browser'),
          ),
          onPressed: () => launchUrl(
            widget.client.site(camera.pageUrl),
            mode: LaunchMode.externalApplication,
          ),
        ),
      ),
    ];
  }
}

class _LiveImage extends StatelessWidget {
  const _LiveImage({required this.url, required this.camera, this.onTap});

  final String? url;
  final PublicCameraDetail camera;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final url = this.url;
    return AspectRatio(
      aspectRatio: 1,
      child: ColoredBox(
        color: Colors.black,
        child: Stack(
          fit: StackFit.expand,
          children: [
            if (url == null)
              Center(
                child: Text(
                  tr('Kein öffentliches Livebild.', 'No public live picture.'),
                ),
              )
            else
              GestureDetector(
                key: const Key('public-live'),
                onTap: onTap,
                child: Image.network(
                  url,
                  fit: BoxFit.contain,
                  gaplessPlayback: true,
                  errorBuilder: (_, _, _) => Center(
                    child: Text(
                      tr('Bild nicht verfügbar', 'Picture not available'),
                    ),
                  ),
                ),
              ),
            Positioned(
              left: 12,
              top: 12,
              child: Chip(
                avatar: Icon(
                  Icons.circle,
                  size: 12,
                  color: camera.online ? Sky.aurora : Colors.grey,
                ),
                label: Text(
                  camera.online
                      ? 'Live · ${formatAge(camera.imageAt)}'
                      : 'Offline · ${formatAge(camera.imageAt)}',
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _Fact extends StatelessWidget {
  const _Fact(this.label, this.value);

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Container(
      constraints: const BoxConstraints(minWidth: 100),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: Sky.surface,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: theme.textTheme.bodySmall),
          Text(value, style: theme.textTheme.titleMedium),
        ],
      ),
    );
  }
}

class _Card extends StatelessWidget {
  const _Card({required this.title, required this.child, this.note});

  final String title;
  final Widget child;
  final String? note;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      margin: const EdgeInsets.fromLTRB(16, 12, 16, 0),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title, style: theme.textTheme.titleMedium),
            const SizedBox(height: 8),
            child,
            if (note case final note?) ...[
              const SizedBox(height: 8),
              Text(note, style: theme.textTheme.bodySmall),
            ],
          ],
        ),
      ),
    );
  }
}

/// The last hour's thumbnails as a loop; tap to pause.
class _Loop extends StatefulWidget {
  const _Loop({required this.urls});

  final List<String> urls;

  @override
  State<_Loop> createState() => _LoopState();
}

class _LoopState extends State<_Loop> {
  Timer? _timer;
  int _i = 0;
  bool _playing = false;

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  void _toggle() {
    if (_playing) {
      _timer?.cancel();
    } else {
      for (final url in widget.urls) {
        precacheImage(NetworkImage(url), context);
      }
      _timer = Timer.periodic(
        const Duration(milliseconds: 150),
        (_) => setState(() => _i = (_i + 1) % widget.urls.length),
      );
    }
    setState(() => _playing = !_playing);
  }

  @override
  Widget build(BuildContext context) => GestureDetector(
    key: const Key('loop'),
    onTap: _toggle,
    child: AspectRatio(
      aspectRatio: 1,
      child: Stack(
        fit: StackFit.expand,
        children: [
          Image.network(
            widget.urls[_i.clamp(0, widget.urls.length - 1)],
            fit: BoxFit.contain,
            gaplessPlayback: true,
            errorBuilder: _broken,
          ),
          if (!_playing)
            const Center(child: Icon(Icons.play_circle_outline, size: 64)),
        ],
      ),
    ),
  );
}

class _VideoScreen extends StatefulWidget {
  const _VideoScreen({required this.url, required this.title});

  final Uri url;
  final String title;

  @override
  State<_VideoScreen> createState() => _VideoScreenState();
}

class _VideoScreenState extends State<_VideoScreen> {
  late final _video = VideoPlayerController.networkUrl(widget.url);
  String? _error;

  @override
  void initState() {
    super.initState();
    _video
        .initialize()
        .then((_) async {
          await _video.setLooping(true);
          await _video.play();
          if (mounted) setState(() {});
        })
        .catchError((Object _) {
          if (mounted) {
            setState(
              () => _error = tr(
                'Das Video konnte nicht abgespielt werden.',
                'The video could not be played.',
              ),
            );
          }
        });
  }

  @override
  void dispose() {
    _video.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    backgroundColor: Colors.black,
    appBar: AppBar(backgroundColor: Colors.black, title: Text(widget.title)),
    body: Center(
      child: _error != null
          ? Text(_error!)
          : _video.value.isInitialized
          ? AspectRatio(
              aspectRatio: _video.value.aspectRatio,
              child: VideoPlayer(_video),
            )
          : const CircularProgressIndicator(),
    ),
  );
}
