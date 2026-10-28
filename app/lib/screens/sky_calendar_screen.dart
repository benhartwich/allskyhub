// What comes in the sky (hub `/api/v1/public/calendar`): shower maxima, new and full moon,
// aurora chances, bright satellite passes and the clear nights of the forecast.

import 'package:flutter/material.dart';

import '../api/hub_client.dart';
import '../design.dart';
import '../l10n.dart';
import '../platform/reminders.dart';
import 'sky_screen.dart' show calendarWhen;

const _filters = {
  'shower': ['shower'],
  'moon': ['new_moon', 'full_moon'],
  'pass': ['pass'],
  'aurora': ['aurora', 'good_night'],
};

Color _color(String kind) => switch (kind) {
  'shower' => Sky.accent2,
  'full_moon' => Sky.warn,
  'new_moon' => Sky.text2,
  'pass' => Sky.accent,
  _ => Sky.aurora,
};

class SkyCalendarScreen extends StatefulWidget {
  const SkyCalendarScreen({
    super.key,
    required this.items,
    required this.reminders,
    this.forecasts = const {},
  });

  final List<CalendarItem> items;
  final Reminders reminders;

  /// Evening → forecast summary, for the reminder's text.
  final Map<DateTime, String> forecasts;

  @override
  State<SkyCalendarScreen> createState() => _SkyCalendarScreenState();
}

class _SkyCalendarScreenState extends State<SkyCalendarScreen> {
  String? _filter;
  Set<int> _pending = {};

  @override
  void initState() {
    super.initState();
    widget.reminders.pending().then((p) {
      if (mounted) setState(() => _pending = p);
    });
  }

  Future<void> _toggle(CalendarItem item) async {
    final id = reminderId(item);
    if (_pending.contains(id)) {
      await widget.reminders.forget(item);
      if (mounted) setState(() => _pending = {..._pending}..remove(id));
      return;
    }
    final ok = await widget.reminders.remind(
      item,
      forecast: widget.forecasts[nightOf(item)],
    );
    if (!mounted) return;
    if (ok) {
      setState(() => _pending = {..._pending, id});
    } else {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            tr(
              'Erinnern geht nicht: Benachrichtigungen sind für die App aus.',
              'Cannot remind you: notifications are off for the app.',
            ),
          ),
        ),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final labels = {
      'shower': tr('Meteore', 'Meteors'),
      'moon': tr('Mond', 'Moon'),
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
                        trailing: reminderTime(item) == null
                            ? null
                            : IconButton(
                                key: Key('remind-${reminderId(item)}'),
                                tooltip: _pending.contains(reminderId(item))
                                    ? tr('Nicht erinnern', 'Do not remind')
                                    : tr('Erinnern', 'Remind me'),
                                icon: Icon(
                                  _pending.contains(reminderId(item))
                                      ? Icons.notifications_active
                                      : Icons.notifications_none,
                                  color: _pending.contains(reminderId(item))
                                      ? Sky.aurora
                                      : null,
                                ),
                                onPressed: () => _toggle(item),
                              ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          Text(
            tr(
              'Glocke: die App erinnert dich am Abend (ganztägige Ereignisse um 18 Uhr, sonst 30 Minuten vorher), mit der neuesten Vorhersage, wenn du „Himmel“ vorher noch einmal geöffnet hast. ',
              'Bell: the app reminds you in the evening (all-day events at 6 pm, others 30 minutes before), with the newest forecast if you opened “Sky” again before. ',
            ),
            style: theme.textTheme.bodySmall,
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
