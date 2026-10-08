import 'dart:async';

import 'package:flutter/material.dart';

import '../api/hub_client.dart';
import '../platform/location.dart';
import 'camera_settings_screen.dart';
import 'camera_status.dart';

/// Live view: polls the latest image with `live=true` every few seconds, so the hub asks
/// the camera for every frame while this screen is open (SPEC §6.5).
class CameraScreen extends StatefulWidget {
  const CameraScreen({super.key, required this.client, required this.camera});

  final HubClient client;
  final Camera camera;

  @override
  State<CameraScreen> createState() => _CameraScreenState();
}

class _CameraScreenState extends State<CameraScreen> {
  static const _interval = Duration(seconds: 5);
  late Camera _camera = widget.camera;
  Timer? _timer;
  int _tick = 0;

  @override
  void initState() {
    super.initState();
    _timer = Timer.periodic(_interval, (_) => _refresh());
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _refresh() async {
    try {
      final camera = await widget.client.camera(_camera.id);
      if (mounted) {
        setState(() {
          _camera = camera;
          _tick++;
        });
      }
    } on Exception {
      // Keep showing the last state; the next tick tries again.
    }
  }

  Future<void> _remove() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Kamera entfernen?'),
        content: const Text(
          'Die Kamera wird von deinem Konto getrennt und zeigt danach einen neuen Kopplungscode.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Abbrechen'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Entfernen'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    await widget.client.remove(_camera.id);
    if (mounted) Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final image = _camera.latestImageAt;
    final rows = statusRows(
      status: _camera.status,
      frame: _camera.frame,
      lastSeen: _camera.lastSeenAt,
    );
    return Scaffold(
      appBar: AppBar(
        title: Text(_camera.name),
        actions: [
          IconButton(
            tooltip: 'Einstellungen',
            icon: const Icon(Icons.settings_outlined),
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) => CameraSettingsScreen(
                  client: widget.client,
                  camera: _camera,
                  locationSource: PlatformLocationSource(),
                ),
              ),
            ),
          ),
          IconButton(
            tooltip: 'Entfernen',
            icon: const Icon(Icons.delete_outline),
            onPressed: _remove,
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.only(bottom: 24),
        children: [
          AspectRatio(
            aspectRatio: 1,
            child: ColoredBox(
              color: Colors.black,
              child: image == null
                  ? Center(
                      child: Text(
                        _camera.online
                            ? 'Warte auf das erste Bild …'
                            : 'Noch kein Bild.',
                      ),
                    )
                  : Image.network(
                      widget.client
                          .imageUrl(_camera.id, live: true, bust: _tick)
                          .toString(),
                      headers: widget.client.authHeaders,
                      fit: BoxFit.contain,
                      gaplessPlayback: true,
                      errorBuilder: (_, _, _) =>
                          const Center(child: Text('Bild nicht verfügbar')),
                    ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
            child: Row(
              children: [
                Chip(
                  key: const Key('online'),
                  avatar: Icon(
                    Icons.circle,
                    size: 12,
                    color: _camera.online
                        ? Colors.greenAccent
                        : theme.disabledColor,
                  ),
                  label: Text(_camera.online ? 'Live' : 'Offline'),
                ),
                const SizedBox(width: 12),
                if (image != null)
                  Expanded(
                    child: Text(
                      'Bild ${formatAge(image)}',
                      style: theme.textTheme.bodySmall,
                    ),
                  ),
              ],
            ),
          ),
          Card(
            margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Status', style: theme.textTheme.titleMedium),
                  const SizedBox(height: 8),
                  for (final row in rows)
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 3),
                      child: Row(
                        children: [
                          Expanded(
                            child: Text(
                              row.label,
                              style: theme.textTheme.bodyMedium,
                            ),
                          ),
                          Text(
                            row.value,
                            style: row.warning
                                ? TextStyle(
                                    color: theme.colorScheme.error,
                                    fontWeight: FontWeight.w600,
                                  )
                                : theme.textTheme.bodyMedium?.copyWith(
                                    fontWeight: FontWeight.w600,
                                  ),
                          ),
                        ],
                      ),
                    ),
                  if (_camera.status.isEmpty)
                    Text(
                      'Die Kamera hat noch keinen Status gemeldet.',
                      style: theme.textTheme.bodySmall,
                    ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}
