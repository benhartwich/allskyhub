// What comes in the sky (hub `/api/v1/public/calendar`): shower maxima, new and full moon,
// aurora chances, bright satellite passes and the clear nights of the forecast.

import 'package:flutter/material.dart';

import '../api/hub_client.dart';
import '../design.dart';
import '../l10n.dart';
import 'sky_screen.dart' show calendarWhen;

const _filters = {
  'shower': ['shower'],
  'moon': [
    'new_moon',
    'full_moon',
    'moon_planet',
    'lunar_eclipse',
    'solar_eclipse',
  ],
  'pass': ['pass'],
  'aurora': ['aurora', 'good_night'],
};

Color _color(String kind) => switch (kind) {
  'shower' => Sky.accent2,
  'full_moon' || 'lunar_eclipse' || 'solar_eclipse' => Sky.warn,
  'moon_planet' => Sky.accent2,
  'new_moon' => Sky.text2,
  'pass' => Sky.accent,
  _ => Sky.aurora,
};

class SkyCalendarScreen extends StatefulWidget {
  const SkyCalendarScreen({super.key, required this.items});

  final List<CalendarItem> items;

  @override
  State<SkyCalendarScreen> createState() => _SkyCalendarScreenState();
}

class _SkyCalendarScreenState extends State<SkyCalendarScreen> {
  String? _filter;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final labels = {
      'shower': tr('Meteore', 'Meteors'),
      'moon': tr('Mond, Planeten, Finsternisse', 'Moon, planets, eclipses'),
      'pass': tr('Überflüge', 'Passes'),
      'aurora': tr('Polarlicht & klare Nächte', 'Aurora & clear nights'),
    };
    final kinds = _filter == null ? null : _filters[_filter]!;
    final items = [
      for (final i in widget.items)
        if (kinds == null || kinds.contains(i.kind)) i,
    ];
    return Scaffold(
      appBar: AppBar(
        title: Text(tr('Was am Himmel kommt', 'Coming up in the sky')),
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 4, 16, 24),
        children: [
          Wrap(
            spacing: 8,
            runSpacing: 4,
            children: [
              ChoiceChip(
                label: Text(tr('Alles', 'All')),
                selected: _filter == null,
                onSelected: (_) => setState(() => _filter = null),
              ),
              for (final MapEntry(key: key, value: label) in labels.entries)
                ChoiceChip(
                  key: Key('filter-$key'),
                  label: Text(label),
                  selected: _filter == key,
                  onSelected: (_) => setState(() => _filter = key),
                ),
            ],
          ),
          const SizedBox(height: 8),
          if (items.isEmpty)
            Padding(
              padding: const EdgeInsets.all(24),
              child: Text(
                tr(
                  'Nichts in den nächsten Wochen.',
                  'Nothing in the coming weeks.',
                ),
                textAlign: TextAlign.center,
              ),
            ),
          for (final item in items)
            Card(
              margin: const EdgeInsets.only(bottom: 8),
              child: IntrinsicHeight(
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Container(
                      width: 6,
                      margin: const EdgeInsets.symmetric(vertical: 10),
                      decoration: BoxDecoration(
                        color: _color(item.kind),
                        borderRadius: BorderRadius.circular(3),
                      ),
                    ),
                    Expanded(
                      child: ListTile(
                        title: Text(item.title),
                        subtitle: Text('${calendarWhen(item)}\n${item.text}'),
                        isThreeLine: true,
                      ),
                    ),
                  ],
                ),
              ),
            ),
          Text(
            tr(
              'Meteorzahlen gelten für einen sehr dunklen Himmel, den Radiant hoch am Himmel. Unter Stadtlicht sieht man nur einen Bruchteil.',
              'Meteor rates are for a very dark sky with the radiant high up. Under city lights you see only a fraction.',
            ),
            style: theme.textTheme.bodySmall,
          ),
        ],
      ),
    );
  }
}
