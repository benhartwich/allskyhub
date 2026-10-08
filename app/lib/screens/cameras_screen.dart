import 'package:flutter/material.dart';

import '../api/discovery.dart';
import '../api/hub_client.dart';
import '../api/setup_mode.dart';
import '../platform/location.dart';
import '../platform/wifi_binding.dart';
import 'account_screen.dart';
import 'camera_screen.dart';
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
              title: const Text('Neue Kamera einrichten'),
              subtitle: const Text('Über das WLAN „allskyhub-…“ der Kamera'),
              onTap: () => Navigator.pop(context, _PairWay.setupMode),
            ),
            ListTile(
              leading: const Icon(Icons.wifi),
              title: const Text('Kamera ist schon im WLAN'),
              subtitle: const Text('Die App holt sich den Code von der Kamera'),
              onTap: () => Navigator.pop(context, _PairWay.lan),
            ),
            ListTile(
              leading: const Icon(Icons.pin),
              title: const Text('Code eingeben'),
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
        title: const Text('Meine Kameras'),
        actions: [
          IconButton(
            tooltip: 'Konto',
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
            tooltip: 'Abmelden',
            icon: const Icon(Icons.logout),
            onPressed: widget.onSignOut,
          ),
        ],
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _pair,
        icon: const Icon(Icons.add),
        label: const Text('Kamera koppeln'),
      ),
      body: RefreshIndicator(
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
                'Kameras konnten nicht geladen werden.',
                onRetry: _reload,
              );
            }
            final cameras = snapshot.data;
            if (cameras == null) {
              return const Center(child: CircularProgressIndicator());
            }
            if (cameras.isEmpty) {
              return const _Message(
                'Noch keine Kamera gekoppelt. Schalte deine Kamera ein und tippe auf „Kamera koppeln“.',
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
                child: TextButton.icon(
                  icon: const Icon(Icons.photo_library_outlined),
                  label: const Text('Galerie'),
                  onPressed: () => Navigator.of(context).push(
                    MaterialPageRoute<void>(
                      builder: (_) =>
                          NightsScreen(client: client, camera: camera),
                    ),
                  ),
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
              child: const Text('Erneut versuchen'),
            ),
          ),
      ],
    );
  }
}

enum _PairWay { setupMode, lan, code }
