import 'package:flutter/material.dart';

import '../api/hub_client.dart';
import '../l10n.dart';

/// Roadmap C7: clear nights, sky brightness and detections per month, like the web page.
class StatsScreen extends StatefulWidget {
  const StatsScreen({super.key, required this.client, required this.camera});

  final HubClient client;
  final Camera camera;

  @override
  State<StatsScreen> createState() => _StatsScreenState();
}

class _StatsScreenState extends State<StatsScreen> {
  YearStats? _stats;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load(0);
  }

  Future<void> _load(int year) async {
    try {
      final stats = await widget.client.stats(widget.camera.id, year: year);
      if (mounted) {
        setState(() {
          _stats = stats;
          _error = null;
        });
      }
    } on Exception catch (e) {
      if (mounted) {
        setState(
          () => _error = e is HubException
              ? e.message
              : tr(
                  'Keine Verbindung zum Hub. Bitte später noch einmal versuchen.',
                  'No connection to the hub. Please try again later.',
                ),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final stats = _stats;
    return Scaffold(
      appBar: AppBar(title: Text(tr('Statistik', 'Statistics'))),
      body: stats == null
          ? Center(
              child: _error == null
                  ? const CircularProgressIndicator()
                  : Text(_error!),
            )
          : _body(context, stats),
    );
  }

  Widget _body(BuildContext context, YearStats stats) {
    final theme = Theme.of(context);
    final years = {...stats.years, stats.year}.toList()..sort((a, b) => b - a);
    final total = stats.total;
    final maxHours = stats.months
        .map((m) => m.clearHours)
        .fold(0.0, (a, b) => a > b ? a : b);
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        if (years.length > 1)
          Wrap(
            spacing: 8,
            children: [
              for (final y in years)
                ChoiceChip(
                  label: Text('$y'),
                  selected: y == stats.year,
                  onSelected: (_) => _load(y),
                ),
            ],
          ),
        if (total.nights == 0)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 24),
            child: Text(
              tr(
                'Für ${stats.year} gibt es noch keine Messungen.',
                'There are no measurements for ${stats.year} yet.',
              ),
              key: const Key('stats-empty'),
              textAlign: TextAlign.center,
            ),
          )
        else ...[
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              _Figure(
                tr('Klare Nächte', 'Clear nights'),
                tr(
                  '${total.clearNights} von ${total.nights}',
                  '${total.clearNights} of ${total.nights}',
                ),
              ),
              _Figure(
                tr('Klare Stunden', 'Clear hours'),
                total.clearHours.toStringAsFixed(0),
              ),
              if (total.bestSqm case final sqm?)
                _Figure(
                  tr('Dunkelster Himmel', 'Darkest sky'),
                  '${sqm.toStringAsFixed(2)} mag',
                ),
              _Figure(tr('Meteore', 'Meteors'), '${total.meteors}'),
              if (total.aurora > 0)
                _Figure(tr('Polarlicht', 'Aurora'), '${total.aurora}'),
              if (total.storms > 0)
                _Figure(tr('Gewitter', 'Thunderstorms'), '${total.storms}'),
            ],
          ),
          const SizedBox(height: 24),
          Text(
            tr('Klare Stunden pro Monat', 'Clear hours per month'),
            style: theme.textTheme.titleMedium,
          ),
          const SizedBox(height: 12),
          SizedBox(
            height: 140,
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                for (final m in stats.months)
                  Expanded(
                    child: Column(
                      mainAxisAlignment: MainAxisAlignment.end,
                      children: [
                        Flexible(
                          child: FractionallySizedBox(
                            heightFactor: maxHours > 0
                                ? m.clearHours / maxHours
                                : 0,
                            child: Container(
                              margin: const EdgeInsets.symmetric(horizontal: 2),
                              color: theme.colorScheme.primary,
                            ),
                          ),
                        ),
                        const SizedBox(height: 4),
                        Text(m.label, style: theme.textTheme.labelSmall),
                      ],
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(height: 24),
          Text(tr('Monate', 'Months'), style: theme.textTheme.titleMedium),
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: DataTable(
              columnSpacing: 20,
              columns: [
                DataColumn(label: Text(tr('Monat', 'Month'))),
                DataColumn(label: Text(tr('Klar', 'Clear')), numeric: true),
                DataColumn(label: Text(tr('Stunden', 'Hours')), numeric: true),
                const DataColumn(label: Text('SQM'), numeric: true),
                DataColumn(
                  label: Text(tr('Meteore', 'Meteors')),
                  numeric: true,
                ),
              ],
              rows: [
                for (final m in stats.months)
                  if (m.nights > 0)
                    DataRow(
                      cells: [
                        DataCell(Text(m.label)),
                        DataCell(Text('${m.clearNights}/${m.nights}')),
                        DataCell(Text(m.clearHours.toStringAsFixed(1))),
                        DataCell(Text(m.bestSqm?.toStringAsFixed(2) ?? '–')),
                        DataCell(Text('${m.meteors}')),
                      ],
                    ),
              ],
            ),
          ),
          if (stats.sqmTrendText.isNotEmpty) ...[
            const SizedBox(height: 24),
            Text(
              tr(
                'Himmelshelligkeit über die Jahre',
                'Sky brightness over the years',
              ),
              style: theme.textTheme.titleMedium,
            ),
            const SizedBox(height: 6),
            Text(stats.sqmTrendText, key: const Key('sqm-trend')),
            if (stats.sqmMonths.isNotEmpty)
              Text(
                '${tr('Zuletzt', 'Latest')} ${stats.sqmMonths.last.label}: '
                '${stats.sqmMonths.last.sqm.toStringAsFixed(2)} mag/″²',
                style: theme.textTheme.bodySmall,
              ),
          ],
          if (stats.showers.isNotEmpty) ...[
            const SizedBox(height: 24),
            Text(
              tr('Meteorströme', 'Meteor showers'),
              style: theme.textTheme.titleMedium,
            ),
            for (final MapEntry(:key, :value) in stats.showers.entries)
              ListTile(
                contentPadding: EdgeInsets.zero,
                dense: true,
                title: Text(key),
                trailing: Text('$value'),
              ),
          ],
        ],
      ],
    );
  }
}

class _Figure extends StatelessWidget {
  const _Figure(this.label, this.value);

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(label, style: theme.textTheme.bodySmall),
            Text(value, style: theme.textTheme.titleMedium),
          ],
        ),
      ),
    );
  }
}
