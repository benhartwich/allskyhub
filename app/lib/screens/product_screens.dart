// Night products (SPEC §5.2): keogram, startrails and timelapse of one night.

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:video_player/video_player.dart';

import '../api/hub_client.dart';

const productTitles = {
  'keogram': 'Keogramm',
  'startrails': 'Sternspuren',
  'timelapse': 'Zeitraffer',
};

String formatSize(int bytes) {
  if (bytes >= 1024 * 1024) {
    return '${(bytes / (1024 * 1024)).toStringAsFixed(1)} MB';
  }
  return '${(bytes / 1024).ceil()} KB';
}

String formatDuration(double seconds) {
  final s = seconds.round();
  return '${s ~/ 60}:${(s % 60).toString().padLeft(2, '0')}';
}

/// Asks the hub for a product until it has it (it fetches it from the camera meanwhile).
class ProductFetcher {
  ProductFetcher(
    this.client,
    this.cameraId,
    this.nightId,
    this.name, {
    this.interval = const Duration(seconds: 3),
    this.timeout = const Duration(minutes: 15),
  });

  final HubClient client;
  final String cameraId;
  final String nightId;
  final String name;
  final Duration interval;
  final Duration timeout;
  bool _cancelled = false;

  void cancel() => _cancelled = true;

  /// True when the product is available, false when cancelled or timed out.
  Future<bool> run() async {
    final deadline = DateTime.now().add(timeout);
    while (!_cancelled && DateTime.now().isBefore(deadline)) {
      if (await client.ensureProduct(cameraId, nightId, name)) return true;
      await Future<void>.delayed(interval);
    }
    return false;
  }
}

/// The products row above a night's thumbnails.
class ProductStrip extends StatelessWidget {
  const ProductStrip({
    super.key,
    required this.client,
    required this.cameraId,
    required this.nightId,
    required this.products,
  });

  final HubClient client;
  final String cameraId;
  final String nightId;
  final List<Product> products;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 150,
      child: ListView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.all(8),
        children: [
          for (final product in products)
            Padding(
              padding: const EdgeInsets.only(right: 8),
              child: SizedBox(
                width: 180,
                child: Card(
                  key: ValueKey(product.name),
                  clipBehavior: Clip.antiAlias,
                  margin: EdgeInsets.zero,
                  child: InkWell(
                    onTap: () => Navigator.of(context).push(
                      MaterialPageRoute<void>(
                        builder: (_) => product.isVideo
                            ? TimelapseScreen(
                                client: client,
                                cameraId: cameraId,
                                nightId: nightId,
                                product: product,
                              )
                            : ProductImageScreen(
                                client: client,
                                cameraId: cameraId,
                                nightId: nightId,
                                product: product,
                              ),
                      ),
                    ),
                    child: Stack(
                      fit: StackFit.expand,
                      children: [
                        if (product.hasThumb)
                          Image.network(
                            client
                                .productUrl(
                                  cameraId,
                                  nightId,
                                  product.name,
                                  thumb: true,
                                )
                                .toString(),
                            headers: client.authHeaders,
                            fit: BoxFit.cover,
                            errorBuilder: (_, _, _) =>
                                const ColoredBox(color: Colors.black),
                          )
                        else
                          const ColoredBox(color: Colors.black),
                        if (product.isVideo)
                          const Center(
                            child: Icon(
                              Icons.play_circle_outline,
                              size: 48,
                              color: Colors.white,
                            ),
                          ),
                        Positioned(
                          left: 0,
                          right: 0,
                          bottom: 0,
                          child: ColoredBox(
                            color: Colors.black54,
                            child: Padding(
                              padding: const EdgeInsets.symmetric(
                                horizontal: 8,
                                vertical: 4,
                              ),
                              child: Text(
                                '${productTitles[product.kind] ?? product.kind}'
                                '${product.durationS != null ? ' · ${formatDuration(product.durationS!)}' : ''}'
                                ' · ${formatSize(product.size)}',
                                style: const TextStyle(
                                  color: Colors.white,
                                  fontSize: 12,
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
            ),
        ],
      ),
    );
  }
}

/// Shared waiting state: the hub fetches the product from the camera.
mixin _FetchesProduct<T extends StatefulWidget> on State<T> {
  ProductFetcher? _fetcher;
  bool ready = false;
  String? error;

  Future<void> fetch(
    HubClient client,
    String cameraId,
    String nightId,
    Product product,
  ) async {
    if (product.hasFull) {
      setState(() => ready = true);
      return;
    }
    final fetcher = ProductFetcher(client, cameraId, nightId, product.name);
    _fetcher = fetcher;
    try {
      final ok = await fetcher.run();
      if (!mounted) return;
      setState(() {
        ready = ok;
        error = ok
            ? null
            : 'Die Kamera hat die Datei nicht rechtzeitig geliefert.';
      });
    } on HubException catch (e) {
      if (!mounted) return;
      setState(
        () => error = e.statusCode == 404
            ? 'Die Kamera ist offline. Die Datei kommt, sobald sie wieder verbunden ist.'
            : 'Die Datei konnte nicht geladen werden (${e.statusCode}).',
      );
    }
  }

  @override
  void dispose() {
    _fetcher?.cancel();
    super.dispose();
  }

  Widget waiting(Product product) => Center(
    child: Padding(
      padding: const EdgeInsets.all(32),
      child: error != null
          ? Text(
              error!,
              textAlign: TextAlign.center,
              style: const TextStyle(color: Colors.white70),
            )
          : Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const CircularProgressIndicator(),
                const SizedBox(height: 16),
                Text(
                  'Wird von der Kamera geholt … (${formatSize(product.size)})',
                  textAlign: TextAlign.center,
                  style: const TextStyle(color: Colors.white70),
                ),
              ],
            ),
    ),
  );
}

class ProductImageScreen extends StatefulWidget {
  const ProductImageScreen({
    super.key,
    required this.client,
    required this.cameraId,
    required this.nightId,
    required this.product,
  });

  final HubClient client;
  final String cameraId;
  final String nightId;
  final Product product;

  @override
  State<ProductImageScreen> createState() => _ProductImageScreenState();
}

class _ProductImageScreenState extends State<ProductImageScreen>
    with _FetchesProduct<ProductImageScreen> {
  @override
  void initState() {
    super.initState();
    fetch(widget.client, widget.cameraId, widget.nightId, widget.product);
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    backgroundColor: Colors.black,
    appBar: AppBar(
      backgroundColor: Colors.black,
      foregroundColor: Colors.white,
      title: Text(productTitles[widget.product.kind] ?? widget.product.kind),
    ),
    body: !ready
        ? waiting(widget.product)
        : InteractiveViewer(
            maxScale: 8,
            child: Center(
              child: Image.network(
                widget.client
                    .productUrl(
                      widget.cameraId,
                      widget.nightId,
                      widget.product.name,
                    )
                    .toString(),
                headers: widget.client.authHeaders,
                fit: BoxFit.contain,
              ),
            ),
          ),
  );
}

class TimelapseScreen extends StatefulWidget {
  const TimelapseScreen({
    super.key,
    required this.client,
    required this.cameraId,
    required this.nightId,
    required this.product,
  });

  final HubClient client;
  final String cameraId;
  final String nightId;
  final Product product;

  @override
  State<TimelapseScreen> createState() => _TimelapseScreenState();
}

class _TimelapseScreenState extends State<TimelapseScreen>
    with _FetchesProduct<TimelapseScreen> {
  VideoPlayerController? _video;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    await fetch(widget.client, widget.cameraId, widget.nightId, widget.product);
    if (!ready || !mounted) return;
    // Streams with HTTP range requests, so it starts before the whole file is loaded.
    final video = VideoPlayerController.networkUrl(
      widget.client.productUrl(
        widget.cameraId,
        widget.nightId,
        widget.product.name,
      ),
      httpHeaders: widget.client.authHeaders,
    );
    _video = video;
    try {
      await video.initialize();
      await video.setLooping(true);
      await video.play();
    } on Exception {
      if (mounted) {
        setState(() => error = 'Das Video konnte nicht abgespielt werden.');
      }
      return;
    }
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    _video?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final video = _video;
    final playing = video != null && video.value.isInitialized;
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
        title: const Text('Zeitraffer'),
      ),
      body: !playing || error != null
          ? (error == null && ready
                ? const Center(child: CircularProgressIndicator())
                : waiting(widget.product))
          : Column(
              children: [
                Expanded(
                  child: Center(
                    child: AspectRatio(
                      aspectRatio: video.value.aspectRatio,
                      child: GestureDetector(
                        onTap: () async {
                          await (video.value.isPlaying
                              ? video.pause()
                              : video.play());
                          if (mounted) setState(() {});
                        },
                        child: VideoPlayer(video),
                      ),
                    ),
                  ),
                ),
                VideoProgressIndicator(video, allowScrubbing: true),
                const SizedBox(height: 24),
              ],
            ),
    );
  }
}
