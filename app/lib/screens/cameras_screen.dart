import 'package:flutter/material.dart';

import '../api/hub_client.dart';
import 'camera_screen.dart';
import 'pair_screen.dart';

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
    final paired = await Navigator.of(context).push<Camera>(
      MaterialPageRoute(builder: (_) => PairScreen(client: widget.client)),
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
