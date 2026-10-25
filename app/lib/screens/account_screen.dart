import 'package:flutter/material.dart';

import '../api/hub_client.dart';
import '../l10n.dart';

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
  AccountSettings? _settings;
  String? _message;
  bool _messageIsError = false;

  @override
  void initState() {
    super.initState();
    _loadSettings();
  }

  Future<void> _loadSettings() async {
    try {
      final settings = await widget.client.accountSettings();
      if (mounted) {
        setState(() {
          _settings = settings;
          _keepDays = settings.keepDays;
        });
      }
    } on Exception {
      // Older hub or offline: the section stays hidden.
    }
  }

  Future<void> _setKeepDays(int days) async {
    final before = _keepDays;
    setState(() => _keepDays = days);
    try {
      final saved = await widget.client.updateAccountSettings({
        'event_keep_days': days,
      });
      if (mounted) setState(() => _keepDays = saved.keepDays);
      _show(tr('Gespeichert.', 'Saved.'));
    } on Exception catch (e) {
      if (mounted) setState(() => _keepDays = before);
      _show(_describe(e), error: true);
    }
  }

  Future<void> _setNotify(String key, bool on) async {
    final before = _settings;
    if (before == null) return;
    setState(() => _settings = _withNotify(before, key, on));
    try {
      final saved = await widget.client.updateAccountSettings({key: on});
      if (mounted) setState(() => _settings = saved);
      _show(tr('Gespeichert.', 'Saved.'));
    } on Exception catch (e) {
      if (mounted) setState(() => _settings = before);
      _show(_describe(e), error: true);
    }
  }

  AccountSettings _withNotify(AccountSettings s, String key, bool on) =>
      AccountSettings.fromJson({
        'event_keep_days': s.keepDays,
        'push_available': s.pushAvailable,
        'mail_available': s.mailAvailable,
        ...s.notify,
        key: on,
      });

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
            ? tr(
                'Zu viele Versuche. Bitte später erneut versuchen.',
                'Too many attempts. Please try again later.',
              )
            : e.message)
      : tr('Der Hub ist nicht erreichbar.', 'The hub cannot be reached.');

  Future<void> _changePassword() async {
    if (_new.text != _new2.text) {
      _show(
        tr(
          'Die neuen Passwörter stimmen nicht überein.',
          'The new passwords do not match.',
        ),
        error: true,
      );
      return;
    }
    if (_new.text.length < 10) {
      _show(
        tr(
          'Das neue Passwort braucht mindestens 10 Zeichen.',
          'The new password needs at least 10 characters.',
        ),
        error: true,
      );
      return;
    }
    setState(() => _busy = true);
    try {
      await widget.client.changePassword(_current.text, _new.text);
      for (final c in [_current, _new, _new2]) {
        c.clear();
      }
      _show(
        tr(
          'Passwort geändert. Im Web und auf anderen Geräten bist du abgemeldet.',
          'Password changed. You are signed out on the web and on other devices.',
        ),
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
        title: Text(
          tr('Konto endgültig löschen?', 'Delete the account for good?'),
        ),
        content: Text(
          tr(
            'Deine Kameras werden entkoppelt, alle ihre Bilder, Nachtprodukte und öffentlichen '
                'Seiten gelöscht. Das lässt sich nicht rückgängig machen.',
            'Your cameras are unpaired, and all their pictures, night products and public '
                'pages are deleted. This cannot be undone.',
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: Text(tr('Abbrechen', 'Cancel')),
          ),
          FilledButton(
            style: FilledButton.styleFrom(
              backgroundColor: Theme.of(context).colorScheme.error,
            ),
            onPressed: () => Navigator.pop(context, true),
            child: Text(tr('Löschen', 'Delete')),
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
      appBar: AppBar(title: Text(tr('Konto', 'Account'))),
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
            Text(
              tr('Ereignisse aufbewahren', 'Keep events'),
              style: theme.textTheme.titleMedium,
            ),
            const SizedBox(height: 4),
            Text(
              tr(
                'Wie lange erkannte Meteore und andere Ereignisse gespeichert bleiben. '
                    'Bei mehr als 30 Tagen sichern wir auch jedes Bild in voller Größe.',
                'How long detected meteors and other events are kept. '
                    'Beyond 30 days we also keep every picture in full size.',
              ),
            ),
            RadioGroup<int>(
              groupValue: _keepDays,
              onChanged: (days) {
                if (days != null && !_busy) _setKeepDays(days);
              },
              child: Column(
                children: [
                  for (final (days, label) in [
                    (30, tr('30 Tage (Standard)', '30 days (default)')),
                    (90, tr('90 Tage', '90 days')),
                    (365, tr('1 Jahr', '1 year')),
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
          if (_settings case final settings?
              when settings.pushAvailable || settings.mailAvailable) ...[
            Text(
              tr('Benachrichtigungen', 'Notifications'),
              style: theme.textTheme.titleMedium,
            ),
            const SizedBox(height: 4),
            Text(
              settings.pushAvailable
                  ? tr(
                      'Auf diesem Telefon${settings.mailAvailable ? ' und auf Wunsch per E-Mail' : ''}.',
                      'On this phone${settings.mailAvailable ? ', and by e-mail if you like' : ''}.',
                    )
                  : tr(
                      'Per E-Mail, wenn du das unten einschaltest.',
                      'By e-mail, if you switch it on below.',
                    ),
            ),
            for (final (key, title, subtitle) in [
              if (settings.pushAvailable)
                (
                  'notify_events',
                  tr('Neue Ereignisse', 'New events'),
                  tr(
                    'z. B. Meteor, höchstens alle 10 Minuten pro Kamera, nur in der App',
                    'e.g. a meteor, at most every 10 minutes per camera, in the app only',
                  ),
                ),
              (
                'notify_offline',
                tr('Probleme mit einer Kamera', 'Problems with a camera'),
                tr(
                  'offline, keine Bilder, Speicher fast voll, zu heiß, Uhr',
                  'offline, no pictures, storage almost full, too hot, clock',
                ),
              ),
              (
                'notify_aurora',
                tr('Polarlicht', 'Aurora'),
                tr(
                  'Kp hoch genug oder im Kameranetz gesichtet, und der Himmel über '
                      'der Kamera klar',
                  'Kp high enough or seen in the camera network, and a clear sky '
                      'over the camera',
                ),
              ),
              (
                'notify_summary',
                tr('Zusammenfassung der Nacht', 'Summary of the night'),
                tr(
                  'morgens nach Sonnenaufgang',
                  'in the morning after sunrise',
                ),
              ),
              (
                'notify_tonight',
                tr('Heute Nacht klar', 'Clear tonight'),
                tr(
                  'abends, wenn mindestens 2 Stunden klar und dunkel vorhergesagt sind',
                  'in the evening, when at least 2 clear and dark hours are forecast',
                ),
              ),
              (
                'notify_fireball',
                tr(
                  'Feuerkugel mit möglichem Meteoritenfall',
                  'Fireball with a possible meteorite fall',
                ),
                tr(
                  'von zwei Kameras gesehen, in 300 km um deine Kameras',
                  'seen by two cameras, within 300 km of your cameras',
                ),
              ),
              if (settings.mailAvailable)
                (
                  'notify_by_mail',
                  tr('Auch per E-Mail', 'By e-mail too'),
                  tr(
                    'alles außer einzelnen Ereignissen',
                    'everything except single events',
                  ),
                ),
            ])
              if (settings.notify.containsKey(key))
                SwitchListTile(
                  key: ValueKey(key),
                  contentPadding: EdgeInsets.zero,
                  title: Text(title),
                  subtitle: Text(subtitle),
                  value: settings.notify[key]!,
                  onChanged: _busy ? null : (on) => _setNotify(key, on),
                ),
            const SizedBox(height: 24),
          ],
          Text(
            tr('Passwort ändern', 'Change password'),
            style: theme.textTheme.titleMedium,
          ),
          TextField(
            key: const Key('current'),
            controller: _current,
            obscureText: true,
            autofillHints: const [AutofillHints.password],
            decoration: InputDecoration(
              labelText: tr('Aktuelles Passwort', 'Current password'),
            ),
          ),
          TextField(
            key: const Key('new'),
            controller: _new,
            obscureText: true,
            autofillHints: const [AutofillHints.newPassword],
            decoration: InputDecoration(
              labelText: tr(
                'Neues Passwort (mindestens 10 Zeichen)',
                'New password (at least 10 characters)',
              ),
            ),
          ),
          TextField(
            key: const Key('new2'),
            controller: _new2,
            obscureText: true,
            decoration: InputDecoration(
              labelText: tr(
                'Neues Passwort wiederholen',
                'Repeat new password',
              ),
            ),
          ),
          const SizedBox(height: 12),
          FilledButton(
            onPressed: _busy ? null : _changePassword,
            child: Text(tr('Passwort ändern', 'Change password')),
          ),
          const SizedBox(height: 40),
          Text(
            tr('Konto löschen', 'Delete account'),
            style: theme.textTheme.titleMedium,
          ),
          const SizedBox(height: 4),
          Text(
            tr(
              'Löscht dein Konto sofort, samt Kameras, Bildern und öffentlichen Seiten.',
              'Deletes your account right away, with cameras, pictures and public pages.',
            ),
          ),
          TextField(
            key: const Key('delete-password'),
            controller: _deletePassword,
            obscureText: true,
            decoration: InputDecoration(labelText: tr('Passwort', 'Password')),
          ),
          const SizedBox(height: 12),
          OutlinedButton(
            style: OutlinedButton.styleFrom(
              foregroundColor: theme.colorScheme.error,
            ),
            onPressed: _busy ? null : _deleteAccount,
            child: Text(tr('Konto löschen', 'Delete account')),
          ),
        ],
      ),
    );
  }
}
