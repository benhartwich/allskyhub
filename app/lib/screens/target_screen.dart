import 'package:flutter/material.dart';

import '../design.dart';

import '../api/hub_client.dart';
import '../l10n.dart';

String _clock(DateTime t) {
  final l = t.toLocal();
  return '${l.hour.toString().padLeft(2, '0')}:${l.minute.toString().padLeft(2, '0')}';
}

/// Roadmap H4: is an object clear now and in the next hours at this camera?
class TargetScreen extends StatefulWidget {
  const TargetScreen({super.key, required this.client, required this.camera});

  final HubClient client;
  final Camera camera;

  @override
  State<TargetScreen> createState() => _TargetScreenState();
}

class _TargetScreenState extends State<TargetScreen> {
  List<TargetItem>? _items;
  TargetPlan? _plan;
  String? _error;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _loadItems();
  }

  String _describe(Object e) => e is HubException
      ? e.message
      : tr(
          'Keine Verbindung zum Hub. Bitte später noch einmal versuchen.',
          'No connection to the hub. Please try again later.',
        );

  Future<void> _loadItems() async {
    try {
      final items = await widget.client.targets();
      if (mounted) setState(() => _items = items);
    } on Exception catch (e) {
      if (mounted) setState(() => _error = _describe(e));
    }
  }

  Future<void> _choose(String? key) async {
    if (key == null) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final plan = await widget.client.target(widget.camera.id, key);
      if (mounted) setState(() => _plan = plan);
    } on Exception catch (e) {
      if (mounted) {
        setState(() {
          _plan = null;
          _error = _describe(e);
        });
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Color? _color(BuildContext context, String verdict) => switch (verdict) {
    'clear' => Sky.aurora,
    'partly' => Sky.warn,
    'low' || 'bright' => Theme.of(context).disabledColor,
    _ => null,
  };

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final items = _items;
    final plan = _plan;
    return Scaffold(
      appBar: AppBar(title: Text(tr('Ziel frei?', 'Target clear?'))),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text(
            tr(
              'Steht ein Objekt jetzt und in den nächsten Stunden hoch genug, im '
                  'Dunkeln und ohne Wolken?',
              'Is an object high enough, in the dark and free of clouds, now and '
                  'in the next hours?',
            ),
            style: theme.textTheme.bodyMedium,
          ),
          const SizedBox(height: 16),
          if (items == null && _error == null)
            const Center(child: CircularProgressIndicator())
          else if (items != null)
            DropdownMenu<String>(
              key: const Key('target-choice'),
              expandedInsets: EdgeInsets.zero,
              enableFilter: true,
              requestFocusOnTap: true,
              label: Text(tr('Objekt', 'Object')),
              menuHeight: 360,
              onSelected: _choose,
              dropdownMenuEntries: [
                for (final t in items)
                  DropdownMenuEntry(value: t.key, label: t.label),
              ],
            ),
          if (_busy)
            const Padding(
              padding: EdgeInsets.all(16),
              child: Center(child: CircularProgressIndicator()),
            ),
          if (_error case final error?)
            Padding(
              padding: const EdgeInsets.only(top: 12),
              child: Text(
                error,
                key: const Key('target-error'),
                style: TextStyle(color: theme.colorScheme.error),
              ),
            ),
          if (plan != null) ...[
            const SizedBox(height: 16),
            Card(
              margin: EdgeInsets.zero,
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      plan.summary,
                      key: const Key('target-summary'),
                      style: theme.textTheme.titleMedium,
                    ),
                    const SizedBox(height: 6),
                    Text.rich(
                      TextSpan(
                        children: [
                          TextSpan(
                            text: tr(
                              'Jetzt: ${plan.alt.round()}° hoch im ${plan.direction}',
                              'Now: ${plan.alt.round()}° high in the ${plan.direction}',
                            ),
                          ),
                          if (plan.cloudCover case final c?)
                            TextSpan(
                              text:
                                  tr(
                                    ' · die Kamera sieht ${(c * 100).round()} % Wolken',
                                    ' · the camera sees ${(c * 100).round()} % clouds',
                                  ) +
                                  (plan.cloudSource.isEmpty
                                      ? ''
                                      : ' (${plan.cloudSource})'),
                            ),
                          const TextSpan(text: ' · '),
                          TextSpan(
                            text: plan.verdictText,
                            style: TextStyle(
                              fontWeight: FontWeight.w600,
                              color: _color(context, plan.verdict),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 16),
            Text(
              tr('Die nächsten Stunden', 'The next hours'),
              style: theme.textTheme.titleMedium,
            ),
            for (final h in plan.hours)
              ListTile(
                dense: true,
                contentPadding: EdgeInsets.zero,
                leading: Text(_clock(h.at)),
                title: Text(
                  h.verdictText +
                      (h.moonNear ? tr(' · Mond nah', ' · moon close') : ''),
                  style: TextStyle(
                    fontWeight: FontWeight.w600,
                    color: _color(context, h.verdict),
                  ),
                ),
                trailing: Text(
                  '${h.alt.round()}° ${h.direction}'
                  '${h.clouds == null ? '' : ' · ${h.clouds} %'}',
                ),
              ),
            Text(
              tr(
                '„Zu tief“: unter 20°. „Zu hell“: Sonne höher als −12°. '
                    'Wolken: Vorhersage von Open-Meteo.',
                '"Too low": below 20°. "Too bright": sun higher than −12°. '
                    'Clouds: forecast from Open-Meteo.',
              ),
              style: theme.textTheme.bodySmall,
            ),
          ],
        ],
      ),
    );
  }
}
