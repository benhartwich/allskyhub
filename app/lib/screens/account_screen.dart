import 'package:flutter/material.dart';

import '../api/hub_client.dart';

/// Roadmap #10: change the password, delete the account.
class AccountScreen extends StatefulWidget {
  const AccountScreen({
    super.key,
    required this.client,
    required this.onDeleted,
  });

  final HubClient client;

  /// Called after the account is gone; the app goes back to the sign-in.
  final Future<void> Function() onDeleted;

  @override
  State<AccountScreen> createState() => _AccountScreenState();
}

class _AccountScreenState extends State<AccountScreen> {
  final _current = TextEditingController();
  final _new = TextEditingController();
  final _new2 = TextEditingController();
  final _deletePassword = TextEditingController();
  bool _busy = false;
  int? _keepDays;
  String? _message;
  bool _messageIsError = false;

  @override
  void initState() {
    super.initState();
    _loadSettings();
  }

  Future<void> _loadSettings() async {
    try {
      final days = await widget.client.eventKeepDays();
      if (mounted) setState(() => _keepDays = days);
    } on Exception {
      // Older hub or offline: the section stays hidden.
    }
  }

  Future<void> _setKeepDays(int days) async {
    final before = _keepDays;
    setState(() => _keepDays = days);
    try {
      final saved = await widget.client.setEventKeepDays(days);
      if (mounted) setState(() => _keepDays = saved);
      _show('Gespeichert.');
    } on Exception catch (e) {
      if (mounted) setState(() => _keepDays = before);
      _show(_describe(e), error: true);
    }
  }

  @override
  void dispose() {
    for (final c in [_current, _new, _new2, _deletePassword]) {
      c.dispose();
    }
    super.dispose();
  }

  void _show(String text, {bool error = false}) => setState(() {
    _message = text;
    _messageIsError = error;
  });

  String _describe(Object e) => e is HubException
      ? (e.statusCode == 429
            ? 'Zu viele Versuche. Bitte später erneut versuchen.'
            : e.message)
      : 'Der Hub ist nicht erreichbar.';

  Future<void> _changePassword() async {
    if (_new.text != _new2.text) {
      _show('Die neuen Passwörter stimmen nicht überein.', error: true);
      return;
    }
    if (_new.text.length < 10) {
      _show('Das neue Passwort braucht mindestens 10 Zeichen.', error: true);
      return;
    }
    setState(() => _busy = true);
    try {
      await widget.client.changePassword(_current.text, _new.text);
      for (final c in [_current, _new, _new2]) {
        c.clear();
      }
      _show(
        'Passwort geändert. Im Web und auf anderen Geräten bist du abgemeldet.',
      );
    } on Exception catch (e) {
      _show(_describe(e), error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _deleteAccount() async {
    final sure = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Konto endgültig löschen?'),
        content: const Text(
          'Deine Kameras werden entkoppelt, alle ihre Bilder, Nachtprodukte und öffentlichen '
          'Seiten gelöscht. Das lässt sich nicht rückgängig machen.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Abbrechen'),
          ),
          FilledButton(
            style: FilledButton.styleFrom(
              backgroundColor: Theme.of(context).colorScheme.error,
            ),
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Löschen'),
          ),
        ],
      ),
    );
    if (sure != true) return;
    setState(() => _busy = true);
    try {
      await widget.client.deleteAccount(_deletePassword.text);
      if (!mounted) return;
      Navigator.of(context).popUntil((route) => route.isFirst);
      await widget.onDeleted();
    } on Exception catch (e) {
      _show(_describe(e), error: true);
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: const Text('Konto')),
      body: ListView(
        padding: const EdgeInsets.all(24),
        children: [
          if (_message != null) ...[
            Text(
              _message!,
              key: const Key('message'),
              style: TextStyle(
                color: _messageIsError
                    ? theme.colorScheme.error
                    : theme.colorScheme.primary,
              ),
            ),
            const SizedBox(height: 16),
          ],
          if (_keepDays != null) ...[
            Text('Ereignisse aufbewahren', style: theme.textTheme.titleMedium),
            const SizedBox(height: 4),
            const Text(
              'Wie lange erkannte Meteore und andere Ereignisse gespeichert bleiben. '
              'Bei mehr als 30 Tagen sichern wir auch jedes Bild in voller Größe.',
            ),
            RadioGroup<int>(
              groupValue: _keepDays,
              onChanged: (days) {
                if (days != null && !_busy) _setKeepDays(days);
              },
              child: Column(
                children: [
                  for (final (days, label) in const [
                    (30, '30 Tage (Standard)'),
                    (90, '90 Tage'),
                    (365, '1 Jahr'),
                  ])
                    RadioListTile<int>(
                      key: ValueKey('keep-$days'),
                      value: days,
                      title: Text(label),
                    ),
                ],
              ),
            ),
            const SizedBox(height: 24),
          ],
          Text('Passwort ändern', style: theme.textTheme.titleMedium),
          TextField(
            key: const Key('current'),
            controller: _current,
            obscureText: true,
            autofillHints: const [AutofillHints.password],
            decoration: const InputDecoration(labelText: 'Aktuelles Passwort'),
          ),
          TextField(
            key: const Key('new'),
            controller: _new,
            obscureText: true,
            autofillHints: const [AutofillHints.newPassword],
            decoration: const InputDecoration(
              labelText: 'Neues Passwort (mindestens 10 Zeichen)',
            ),
          ),
          TextField(
            key: const Key('new2'),
            controller: _new2,
            obscureText: true,
            decoration: const InputDecoration(
              labelText: 'Neues Passwort wiederholen',
            ),
          ),
          const SizedBox(height: 12),
          FilledButton(
            onPressed: _busy ? null : _changePassword,
            child: const Text('Passwort ändern'),
          ),
          const SizedBox(height: 40),
          Text('Konto löschen', style: theme.textTheme.titleMedium),
          const SizedBox(height: 4),
          const Text(
            'Löscht dein Konto sofort, samt Kameras, Bildern und öffentlichen Seiten.',
          ),
          TextField(
            key: const Key('delete-password'),
            controller: _deletePassword,
            obscureText: true,
            decoration: const InputDecoration(labelText: 'Passwort'),
          ),
          const SizedBox(height: 12),
          OutlinedButton(
            style: OutlinedButton.styleFrom(
              foregroundColor: theme.colorScheme.error,
            ),
            onPressed: _busy ? null : _deleteAccount,
            child: const Text('Konto löschen'),
          ),
        ],
      ),
    );
  }
}
