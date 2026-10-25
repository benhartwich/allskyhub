import 'dart:async';

import 'package:flutter/material.dart';

import '../design.dart';

import '../api/hub_client.dart';
import '../l10n.dart';
import '../platform/location.dart';
import '../platform/sky_widget.dart';
import 'camera_settings_screen.dart';
import 'camera_status.dart';
import 'manage_screens.dart';
import 'measurements_screen.dart';
import 'sky_view_screen.dart';
import 'stats_screen.dart';
import 'target_screen.dart';
import 'zoomable.dart';

/// Live view: polls the latest image with `live=true` every few seconds, so the hub asks
/// the camera for every frame while this screen is open (SPEC §6.5).
class CameraScreen extends StatefulWidget {
  const CameraScreen({
    super.key,
    required this.client,
    required this.camera,
    this.widgetChannel,
  });

  final HubClient client;
  final Camera camera;

  /// Roadmap N6; the platform's by default, a fake in tests.
  final SkyWidgetChannel? widgetChannel;

  @override
  State<CameraScreen> createState() => _CameraScreenState();
}

class _CameraScreenState extends State<CameraScreen> {
  static const _interval = Duration(seconds: 5);
  late Camera _camera = widget.camera;
  Timer? _timer;
  int _tick = 0;
  Overview? _overview;

  @override
  void initState() {
    super.initState();
    _timer = Timer.periodic(_interval, (_) => _refresh());
    _loadOverview();
  }

  /// Health, "Heute Nacht" and last night; changes slowly, so once a minute.
  Future<void> _loadOverview() async {
    try {
      final overview = await widget.client.overview(_camera.id);
      if (mounted) setState(() => _overview = overview);
    } on Object {
      // Older hub (no or another answer) or offline: the cards stay hidden.
    }
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
        if (_tick % 12 == 0) _loadOverview();
      }
    } on Exception {
      // Keep showing the last state; the next tick tries again.
    }
  }

  /// The owner unpairs the camera; someone it is shared with leaves it (roadmap C10).
  Future<void> _remove() async {
    final owner = _camera.isOwner;
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(
          owner
              ? tr('Kamera entfernen?', 'Remove camera?')
              : tr('Kamera verlassen?', 'Leave camera?'),
        ),
        content: Text(
          owner
              ? tr(
                  'Die Kamera wird von deinem Konto getrennt und zeigt danach einen neuen Kopplungscode.',
                  'The camera is unpaired from your account and then shows a new pairing code.',
                )
              : tr(
                  'Du siehst die Kamera danach nicht mehr, bis sie wieder mit dir geteilt wird.',
                  'You will no longer see the camera until it is shared with you again.',
                ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: Text(tr('Abbrechen', 'Cancel')),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: Text(
              owner ? tr('Entfernen', 'Remove') : tr('Verlassen', 'Leave'),
            ),
          ),
        ],
      ),
    );
    if (ok != true) return;
    try {
      if (owner) {
        await widget.client.remove(_camera.id);
      } else {
        await widget.client.unshare(_camera.id, await widget.client.myId());
      }
    } on Exception catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              e is HubException
                  ? e.message
                  : tr(
                      'Keine Verbindung zum Hub.',
                      'No connection to the hub.',
                    ),
            ),
          ),
        );
      }
      return;
    }
    if (mounted) Navigator.of(context).pop();
  }

  late final SkyWidgetChannel _widget =
      widget.widgetChannel ?? PlatformSkyWidget();

  Future<void> _showAsWidget() async {
    final messenger = ScaffoldMessenger.of(context);
    String text;
    try {
      text = switch (await _widget.show(widget.client, _camera)) {
        WidgetResult.placed => tr(
          'Das Widget zeigt jetzt ${_camera.name}.',
          'The widget now shows ${_camera.name}.',
        ),
        WidgetResult.asked => tr(
          'Bestätige, um das Widget abzulegen.',
          'Confirm to place the widget.',
        ),
        WidgetResult.manual => tr(
          'Lange auf den Startbildschirm drücken, „Widgets“ wählen und '
              'allskyhub ablegen.',
          'Long-press the home screen, choose “Widgets” and place '
              'allskyhub.',
        ),
      };
    } on Exception {
      text = tr(
        'Das hat nicht geklappt. Bitte später noch einmal versuchen.',
        'That did not work. Please try again later.',
      );
    }
    messenger.showSnackBar(SnackBar(content: Text(text)));
  }

  void _open(Widget Function() screen) => Navigator.of(
    context,
  ).push(MaterialPageRoute<void>(builder: (_) => screen()));

  /// The live picture in full screen, to zoom into (the picture of this moment).
  void _openFullscreen() {
    final url = widget.client
        .imageUrl(_camera.id, live: true, bust: _tick)
        .toString();
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => PhotoPager(
          count: 1,
          initial: 0,
          title: (_) => _camera.name,
          image: (_) => Image.network(
            url,
            headers: widget.client.authHeaders,
            fit: BoxFit.contain,
          ),
        ),
      ),
    );
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
          if (_camera.canChange)
            IconButton(
              tooltip: tr('Einstellungen', 'Settings'),
              icon: const Icon(Icons.settings_outlined),
              onPressed: () => _open(
                () => CameraSettingsScreen(
                  client: widget.client,
                  camera: _camera,
                  locationSource: PlatformLocationSource(),
                ),
              ),
            ),
          IconButton(
            key: const Key('remove-camera'),
            tooltip: _camera.isOwner
                ? tr('Entfernen', 'Remove')
                : tr('Verlassen', 'Leave'),
            icon: Icon(_camera.isOwner ? Icons.delete_outline : Icons.logout),
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
                            ? tr(
                                'Warte auf das erste Bild …',
                                'Waiting for the first picture …',
                              )
                            : tr('Noch kein Bild.', 'No picture yet.'),
                      ),
                    )
                  : GestureDetector(
                      key: const Key('live-image'),
                      onTap: _openFullscreen,
                      child: Image.network(
                        widget.client
                            .imageUrl(_camera.id, live: true, bust: _tick)
                            .toString(),
                        headers: widget.client.authHeaders,
                        fit: BoxFit.contain,
                        gaplessPlayback: true,
                        errorBuilder: (_, _, _) => Center(
                          child: Text(
                            tr('Bild nicht verfügbar', 'Picture not available'),
                          ),
                        ),
                      ),
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
                    color: _camera.online ? Sky.aurora : theme.disabledColor,
                  ),
                  label: Text(_camera.online ? 'Live' : 'Offline'),
                ),
                const SizedBox(width: 12),
                if (image != null)
                  Expanded(
                    child: Text(
                      tr(
                        'Bild ${formatAge(image)}',
                        'Picture ${formatAge(image)}',
                      ),
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
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(row.label, style: theme.textTheme.bodyMedium),
                          const SizedBox(width: 16),
                          Expanded(
                            child: Text(
                              row.value,
                              textAlign: TextAlign.end,
                              style: row.warning
                                  ? TextStyle(
                                      color: theme.colorScheme.error,
                                      fontWeight: FontWeight.w600,
                                    )
                                  : theme.textTheme.bodyMedium?.copyWith(
                                      fontWeight: FontWeight.w600,
                                    ),
                            ),
                          ),
                        ],
                      ),
                    ),
                  if (_camera.status.isEmpty)
                    Text(
                      tr(
                        'Die Kamera hat noch keinen Status gemeldet.',
                        'The camera has not reported its status yet.',
                      ),
                      style: theme.textTheme.bodySmall,
                    ),
                ],
              ),
            ),
          ),
          if (_overview case final overview?) ...[
            _HealthCard(health: overview.health),
            if (overview.nowcast case final nowcast?)
              _NowcastCard(nowcast: nowcast),
            if (overview.tonight case final tonight?)
              _TonightCard(tonight: tonight),
            if (overview.lastNight case final line?)
              Card(
                margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                child: Padding(
                  key: const Key('last-night'),
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        tr('Letzte Nacht', 'Last night'),
                        style: theme.textTheme.titleMedium,
                      ),
                      const SizedBox(height: 6),
                      Text(line),
                    ],
                  ),
                ),
              ),
          ],
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            child: Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                OutlinedButton.icon(
                  key: const Key('open-measurements'),
                  icon: const Icon(Icons.show_chart),
                  label: Text(tr('Messdaten', 'Measurements')),
                  onPressed: () => _open(
                    () => MeasurementsScreen(
                      client: widget.client,
                      cameraId: _camera.id,
                      hours: 24,
                    ),
                  ),
                ),
                OutlinedButton.icon(
                  key: const Key('open-stats'),
                  icon: const Icon(Icons.bar_chart),
                  label: Text(tr('Statistik', 'Statistics')),
                  onPressed: () => _open(
                    () => StatsScreen(client: widget.client, camera: _camera),
                  ),
                ),
                OutlinedButton.icon(
                  key: const Key('open-sky-view'),
                  icon: const Icon(Icons.explore_outlined),
                  label: Text(tr('Himmel', 'Sky')),
                  onPressed: () => _open(
                    () => SkyViewScreen(client: widget.client, camera: _camera),
                  ),
                ),
                OutlinedButton.icon(
                  key: const Key('open-target'),
                  icon: const Icon(Icons.travel_explore),
                  label: Text(tr('Ziel frei?', 'Target clear?')),
                  onPressed: () => _open(
                    () => TargetScreen(client: widget.client, camera: _camera),
                  ),
                ),
                if (_widget.available)
                  OutlinedButton.icon(
                    key: const Key('as-widget'),
                    icon: const Icon(Icons.widgets_outlined),
                    label: Text(tr('Als Widget', 'As widget')),
                    onPressed: _showAsWidget,
                  ),
                OutlinedButton.icon(
                  key: const Key('open-exports'),
                  icon: const Icon(Icons.download_outlined),
                  label: Text(tr('Exporte', 'Exports')),
                  onPressed: () => _open(
                    () => ExportScreen(client: widget.client, camera: _camera),
                  ),
                ),
                if (_camera.isOwner) ...[
                  OutlinedButton.icon(
                    key: const Key('open-sharing'),
                    icon: const Icon(Icons.group_outlined),
                    label: Text(tr('Teilen', 'Share')),
                    onPressed: () => _open(
                      () =>
                          SharingScreen(client: widget.client, camera: _camera),
                    ),
                  ),
                  OutlinedButton.icon(
                    key: const Key('open-discord'),
                    icon: const Icon(Icons.forum_outlined),
                    label: const Text('Discord'),
                    onPressed: () => _open(
                      () =>
                          DiscordScreen(client: widget.client, camera: _camera),
                    ),
                  ),
                  OutlinedButton.icon(
                    key: const Key('open-network'),
                    icon: const Icon(Icons.hub_outlined),
                    label: Text(tr('Kameranetz', 'Camera network')),
                    onPressed: () => _open(
                      () =>
                          NetworkScreen(client: widget.client, camera: _camera),
                    ),
                  ),
                  OutlinedButton.icon(
                    key: const Key('open-public'),
                    icon: const Icon(Icons.public),
                    label: Text(tr('Öffentliche Seite', 'Public page')),
                    onPressed: () => _open(
                      () => PublicPageScreen(
                        client: widget.client,
                        camera: _camera,
                      ),
                    ),
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _HealthCard extends StatelessWidget {
  const _HealthCard({required this.health});

  final List<HealthItem> health;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      key: const Key('health'),
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(tr('Zustand', 'Health'), style: theme.textTheme.titleMedium),
            const SizedBox(height: 8),
            if (health.isEmpty)
              Text(
                tr(
                  'Alles in Ordnung: verbunden, neue Bilder, genug Speicher, '
                      'Temperaturen und Uhr unauffällig.',
                  'All fine: connected, new pictures, enough storage, '
                      'temperatures and clock normal.',
                ),
              ),
            for (final item in health)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 3),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Icon(
                      item.level == 'error'
                          ? Icons.error_outline
                          : Icons.warning_amber_outlined,
                      size: 18,
                      color: item.level == 'error'
                          ? theme.colorScheme.error
                          : Sky.warn,
                    ),
                    const SizedBox(width: 8),
                    Expanded(child: Text(item.text)),
                  ],
                ),
              ),
          ],
        ),
      ),
    );
  }
}

String _hm(DateTime? t) {
  if (t == null) return '–';
  final l = t.toLocal();
  return '${l.hour.toString().padLeft(2, '0')}:${l.minute.toString().padLeft(2, '0')}';
}

/// Roadmap N4: clouds in the next hour.
class _NowcastCard extends StatelessWidget {
  const _NowcastCard({required this.nowcast});

  final Nowcast nowcast;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    String percent(double v) => '${(v * 100).round()} %';
    return Card(
      key: const Key('nowcast'),
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              tr('Die nächste Stunde', 'The next hour'),
              style: theme.textTheme.titleMedium,
            ),
            const SizedBox(height: 6),
            Text(
              tr(
                'Jetzt ${percent(nowcast.cover)} Wolken. ${nowcast.summary}',
                'Now ${percent(nowcast.cover)} clouds. ${nowcast.summary}',
              ),
            ),
            for (final step in nowcast.steps)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  tr(
                    'in ${step.minutes} Min.: etwa ${percent(step.cover)} '
                        '(${step.label})',
                    'in ${step.minutes} min: about ${percent(step.cover)} '
                        '(${step.label})',
                  ),
                  style: theme.textTheme.bodySmall,
                ),
              ),
            const SizedBox(height: 6),
            Text(
              tr(
                '${nowcast.wind} in etwa 3 km Höhe',
                '${nowcast.wind} at about 3 km altitude',
              ),
              style: theme.textTheme.bodySmall?.copyWith(
                color: theme.colorScheme.onSurfaceVariant,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _TonightCard extends StatelessWidget {
  const _TonightCard({required this.tonight});

  final Tonight tonight;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      key: const Key('tonight'),
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              tr('Heute Nacht', 'Tonight'),
              style: theme.textTheme.titleMedium,
            ),
            const SizedBox(height: 6),
            Text(tonight.summary),
            if (tonight.hours.isNotEmpty) ...[
              const SizedBox(height: 10),
              SizedBox(
                height: 36,
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: [
                    for (final h in tonight.hours)
                      Expanded(
                        child: Container(
                          margin: const EdgeInsets.symmetric(horizontal: 1.5),
                          height: h.clouds == null
                              ? 4
                              : 6 + 30 * (100 - h.clouds!) / 100,
                          decoration: BoxDecoration(
                            color: h.good ? Sky.aurora : theme.disabledColor,
                            borderRadius: BorderRadius.circular(3),
                          ),
                        ),
                      ),
                  ],
                ),
              ),
              const SizedBox(height: 3),
              Row(
                children: [
                  for (final h in tonight.hours)
                    Expanded(
                      child: Text(
                        h.at.toLocal().hour.toString().padLeft(2, '0'),
                        textAlign: TextAlign.center,
                        maxLines: 1,
                        overflow: TextOverflow.clip,
                        style: theme.textTheme.labelSmall,
                      ),
                    ),
                ],
              ),
            ],
            const SizedBox(height: 8),
            Text(
              tr(
                'Sonne unter ${_hm(tonight.sunset)}, auf ${_hm(tonight.sunrise)} · '
                    'dunkel ${_hm(tonight.dusk)}–${_hm(tonight.dawn)}\n'
                    'Mond: ${tonight.moonPhase}, ${(tonight.moonIllumination * 100).round()} % beleuchtet',
                'Sunset ${_hm(tonight.sunset)}, sunrise ${_hm(tonight.sunrise)} · '
                    'dark ${_hm(tonight.dusk)}–${_hm(tonight.dawn)}\n'
                    'Moon: ${tonight.moonPhase}, ${(tonight.moonIllumination * 100).round()} % lit',
              ),
              style: theme.textTheme.bodySmall,
            ),
          ],
        ),
      ),
    );
  }
}
