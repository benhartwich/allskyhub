import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api/discovery.dart';
import '../api/hub_client.dart';
import '../api/setup_mode.dart';
import '../api/test_update.dart';
import '../l10n.dart';
import '../platform/location.dart';
import '../platform/wifi_binding.dart';
import 'account_screen.dart';
import 'camera_screen.dart';
import 'event_screens.dart';
import 'gallery_screens.dart';
import 'onboarding_screen.dart';
import 'pair_screen.dart';
import 'setup_mode_screen.dart';

class CamerasScreen extends StatefulWidget {
  const CamerasScreen({
    super.key,
    required this.client,
    required this.onSignOut,
  });

  final HubClient client;
  final Future<void> Function() onSignOut;

  @override
  State<CamerasScreen> createState() => _CamerasScreenState();
}

class _CamerasScreenState extends State<CamerasScreen> {
  late Future<List<Camera>> _cameras = widget.client.cameras();
  TestBuild? _update;

  /// A reporter account (sky-brightness reports only) cannot pair cameras.
  bool _reporter = false;

  @override
  void initState() {
    super.initState();
    if (testChannel) _checkUpdate();
    widget.client.accountSettings().then((s) {
      if (mounted && s.kind == 'reporter') setState(() => _reporter = true);
    }, onError: (Object _) {});
  }

  /// A test build from the hub's server offers the newer one (deploy/android).
  Future<void> _checkUpdate() async {
    try {
      final build = await widget.client.testBuild();
      if (build != null && build.newerThan(appBuild) && mounted) {
        setState(() => _update = build);
      }
    } on Object {
      // Offline or an older hub: no hint.
    }
  }

  Future<void> _reload() async {
    final next = widget.client.cameras();
    setState(() => _cameras = next);
    await next;
  }

  Future<void> _pair() async {
    final choice = await showModalBottomSheet<_PairWay>(
      context: context,
      builder: (context) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: const Icon(Icons.add_circle_outline),
              title: Text(tr('Neue Kamera einrichten', 'Set up a new camera')),
              subtitle: Text(
                tr(
                  'Über das WLAN „allskyhub-…“ der Kamera',
                  "Through the camera's Wi-Fi “allskyhub-…”",
                ),
              ),
              onTap: () => Navigator.pop(context, _PairWay.setupMode),
            ),
            ListTile(
              leading: const Icon(Icons.wifi),
              title: Text(
                tr('Kamera ist schon im WLAN', 'Camera is already on Wi-Fi'),
              ),
              subtitle: Text(
                tr(
                  'Die App holt sich den Code von der Kamera',
                  'The app gets the code from the camera',
                ),
              ),
              onTap: () => Navigator.pop(context, _PairWay.lan),
            ),
            ListTile(
              leading: const Icon(Icons.pin),
              title: Text(tr('Code eingeben', 'Enter code')),
              onTap: () => Navigator.pop(context, _PairWay.code),
            ),
          ],
        ),
      ),
    );
    if (choice == null || !mounted) return;
    final paired = await Navigator.of(context).push<Camera>(
      MaterialPageRoute(
        builder: (_) => switch (choice) {
          _PairWay.setupMode => SetupModeScreen(
            controller: SetupModeController(
              hub: widget.client,
              binding: PlatformWifiBinding(),
              discovery: NsdCameraDiscovery(),
              locationSource: PlatformLocationSource(),
            ),
          ),
          _PairWay.lan => OnboardingScreen(client: widget.client),
          _PairWay.code => PairScreen(client: widget.client),
        },
      ),
    );
    if (paired != null) await _reload();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(tr('Meine Kameras', 'My cameras')),
        actions: [
          IconButton(
            tooltip: tr('Konto', 'Account'),
            icon: const Icon(Icons.person_outline),
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) => AccountScreen(
                  client: widget.client,
                  onDeleted: widget.onSignOut,
                ),
              ),
            ),
          ),
          IconButton(
            tooltip: tr('Abmelden', 'Sign out'),
            icon: const Icon(Icons.logout),
            onPressed: widget.onSignOut,
          ),
        ],
      ),
      floatingActionButton: _reporter
          ? null
          : FloatingActionButton.extended(
              onPressed: _pair,
              icon: const Icon(Icons.add),
              label: Text(tr('Kamera koppeln', 'Pair camera')),
            ),
      body: Column(
        children: [
          if (_update case final update?) _UpdateBanner(update: update),
          Expanded(child: _list()),
        ],
      ),
    );
  }

  Widget _list() {
    return RefreshIndicator(
      onRefresh: _reload,
      child: FutureBuilder<List<Camera>>(
        future: _cameras,
        builder: (context, snapshot) {
          if (snapshot.hasError) {
            final error = snapshot.error;
            if (error is HubException && error.unauthorized) {
              WidgetsBinding.instance.addPostFrameCallback(
                (_) => widget.onSignOut(),
              );
            }
            return _Message(
              tr(
                'Kameras konnten nicht geladen werden.',
                'Could not load the cameras.',
              ),
              onRetry: _reload,
            );
          }
          final cameras = snapshot.data;
          if (cameras == null) {
            return const Center(child: CircularProgressIndicator());
          }
          if (cameras.isEmpty && _reporter) {
            return _Message(
              tr(
                'Mit deinem Melder-Konto meldest du die Himmelshelligkeit (Mitmachen). Für eine eigene Kamera frag auf allskyhub.org Zugang an.',
                'With your reporter account you report the sky brightness (Take part). For your own camera, request access on allskyhub.org.',
              ),
            );
          }
          if (cameras.isEmpty) {
            return _Message(
              tr(
                'Noch keine Kamera gekoppelt. Schalte deine Kamera ein und tippe auf „Kamera koppeln“.',
                'No camera paired yet. Switch your camera on and tap “Pair camera”.',
              ),
            );
          }
          return ListView.separated(
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 96),
            itemCount: cameras.length,
            separatorBuilder: (_, _) => const SizedBox(height: 12),
            itemBuilder: (context, i) => _CameraCard(
              client: widget.client,
              camera: cameras[i],
              onChanged: _reload,
            ),
          );
        },
      ),
    );
  }
}

class _CameraCard extends StatelessWidget {
  const _CameraCard({
    required this.client,
    required this.camera,
    required this.onChanged,
  });

  final HubClient client;
  final Camera camera;
  final Future<void> Function() onChanged;

  @override
  Widget build(BuildContext context) {
    final image = camera.latestImageAt;
    return Card(
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: () async {
          await Navigator.of(context).push(
            MaterialPageRoute<void>(
              builder: (_) => CameraScreen(client: client, camera: camera),
            ),
          );
          await onChanged();
        },
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            AspectRatio(
              aspectRatio: 16 / 9,
              child: image == null
                  ? const ColoredBox(color: Colors.black)
                  : Image.network(
                      client
                          .imageUrl(
                            camera.id,
                            thumb: true,
                            bust: image.millisecondsSinceEpoch,
                          )
                          .toString(),
                      headers: client.authHeaders,
                      fit: BoxFit.cover,
                      errorBuilder: (_, _, _) =>
                          const ColoredBox(color: Colors.black),
                    ),
            ),
            ListTile(
              title: Text(camera.name),
              subtitle: Text(camera.profile),
              trailing: Chip(label: Text(camera.online ? 'online' : 'offline')),
            ),
            Align(
              alignment: Alignment.centerLeft,
              child: Padding(
                padding: const EdgeInsets.fromLTRB(8, 0, 8, 8),
                child: Wrap(
                  spacing: 8,
                  children: [
                    TextButton.icon(
                      icon: const Icon(Icons.photo_library_outlined),
                      label: Text(tr('Galerie', 'Gallery')),
                      onPressed: () => Navigator.of(context).push(
                        MaterialPageRoute<void>(
                          builder: (_) =>
                              NightsScreen(client: client, camera: camera),
                        ),
                      ),
                    ),
                    TextButton.icon(
                      icon: const Icon(Icons.auto_awesome),
                      label: Text(tr('Ereignisse', 'Events')),
                      onPressed: () => Navigator.of(context).push(
                        MaterialPageRoute<void>(
                          builder: (_) =>
                              EventsScreen(client: client, camera: camera),
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _Message extends StatelessWidget {
  const _Message(this.text, {this.onRetry});

  final String text;
  final Future<void> Function()? onRetry;

  @override
  Widget build(BuildContext context) {
    // A ListView, so pull-to-refresh works on the message too.
    return ListView(
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
}

enum _PairWay { setupMode, lan, code }

class _UpdateBanner extends StatelessWidget {
  const _UpdateBanner({required this.update});

  final TestBuild update;

  @override
  Widget build(BuildContext context) {
    return Card(
      key: const Key('test-update'),
      margin: const EdgeInsets.fromLTRB(16, 12, 16, 0),
      child: ListTile(
        leading: Icon(
          Icons.system_update,
          color: Theme.of(context).colorScheme.tertiary,
        ),
        title: Text(
          tr(
            'Neue Testversion: Build ${update.build}',
            'New test version: build ${update.build}',
          ),
        ),
        subtitle: Text(
          tr(
            'Lädt die App im Browser; danach „Installieren“ antippen.',
            'Downloads the app in the browser; then tap “Install”.',
          ),
        ),
        trailing: FilledButton(
          onPressed: () =>
              launchUrl(update.apk, mode: LaunchMode.externalApplication),
          child: Text(tr('Laden', 'Download')),
        ),
      ),
    );
  }
}
