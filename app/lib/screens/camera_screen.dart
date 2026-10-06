import 'dart:async';

import 'package:flutter/material.dart';

import '../api/hub_client.dart';

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
    final status = _camera.status;
    final exposure = status['exposure_us'];
    final temp = status['sensor_temp_c'];
    return Scaffold(
      appBar: AppBar(
        title: Text(_camera.name),
        actions: [
          IconButton(
            tooltip: 'Entfernen',
            icon: const Icon(Icons.delete_outline),
            onPressed: _remove,
          ),
        ],
      ),
      body: ListView(
        children: [
          AspectRatio(
            aspectRatio: 1,
            child: ColoredBox(
              color: Colors.black,
              child: _camera.latestImageAt == null
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
          ListTile(
            title: Text(_camera.online ? 'Online' : 'Offline'),
            subtitle: Text(
              status.isEmpty
                  ? 'Noch kein Status'
                  : '${status['mode'] == 'night' ? 'Nacht' : 'Tag'}'
                        '${exposure is num ? ' · ${(exposure / 1e6).toStringAsPrecision(3)} s' : ''}'
                        '${status['gain'] != null ? ' · Gain ${status['gain']}' : ''}'
                        '${temp is num ? ' · ${temp.toStringAsFixed(1)} °C' : ''}',
            ),
          ),
          if (status['time_trusted'] == false)
            const ListTile(
              leading: Icon(Icons.warning_amber),
              title: Text('Die Uhr der Kamera ist nicht synchronisiert.'),
            ),
        ],
      ),
    );
  }
}
