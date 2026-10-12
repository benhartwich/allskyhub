import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api/hub_client.dart';

String _describe(Object e) => e is HubException
    ? e.message
    : 'Keine Verbindung zum Hub. Bitte später noch einmal versuchen.';

/// Roadmap C10: the owner shares the camera with existing accounts, to look (`viewer`) or
/// also to change settings and mark detections (`admin`).
class SharingScreen extends StatefulWidget {
  const SharingScreen({super.key, required this.client, required this.camera});

  final HubClient client;
  final Camera camera;

  @override
  State<SharingScreen> createState() => _SharingScreenState();
}

class _SharingScreenState extends State<SharingScreen> {
  static const roles = {'viewer': 'Ansehen', 'admin': 'Verwalten'};

  final _email = TextEditingController();
  String _role = 'viewer';
  List<Member>? _members;
  String? _error;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _email.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    await _run(() => widget.client.members(widget.camera.id));
  }

  Future<void> _run(Future<List<Member>?> Function() action) async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final members = await action();
      if (mounted && members != null) setState(() => _members = members);
    } on Exception catch (e) {
      if (mounted) setState(() => _error = _describe(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _add() async {
    final email = _email.text.trim();
    if (email.isEmpty) return;
    await _run(() async {
      final members = await widget.client.share(
        widget.camera.id,
        email,
        role: _role,
      );
      _email.clear();
      return members;
    });
  }

  Future<void> _remove(Member member) async {
    await _run(() async {
      await widget.client.unshare(widget.camera.id, member.userId);
      return widget.client.members(widget.camera.id);
    });
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final members = _members;
    return Scaffold(
      appBar: AppBar(title: const Text('Teilen')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text(
            'Teile ${widget.camera.name} mit Familie oder Verein. Die Person '
            'braucht ein eigenes Konto bei allskyhub.',
            style: theme.textTheme.bodyMedium,
          ),
          const SizedBox(height: 16),
          TextField(
            key: const Key('share-email'),
            controller: _email,
            keyboardType: TextInputType.emailAddress,
            autocorrect: false,
            decoration: const InputDecoration(
              labelText: 'E-Mail-Adresse',
              border: OutlineInputBorder(),
            ),
            onSubmitted: (_) => _add(),
          ),
          const SizedBox(height: 12),
          SegmentedButton<String>(
            segments: [
              for (final MapEntry(:key, :value) in roles.entries)
                ButtonSegment(value: key, label: Text(value)),
            ],
            selected: {_role},
            onSelectionChanged: (s) => setState(() => _role = s.first),
          ),
          const SizedBox(height: 4),
          Text(
            _role == 'admin'
                ? 'Darf auch Einstellungen ändern und Erkennungen markieren.'
                : 'Sieht Bilder, Nächte und Erkennungen.',
            style: theme.textTheme.bodySmall,
          ),
          const SizedBox(height: 12),
          FilledButton(
            key: const Key('share-add'),
            onPressed: _busy ? null : _add,
            child: const Text('Hinzufügen'),
          ),
          if (_error case final error?)
            Padding(
              padding: const EdgeInsets.only(top: 12),
              child: Text(
                error,
                key: const Key('share-error'),
                style: TextStyle(color: theme.colorScheme.error),
              ),
            ),
          const SizedBox(height: 24),
          Text('Geteilt mit', style: theme.textTheme.titleMedium),
          if (members == null)
            const Padding(
              padding: EdgeInsets.all(16),
              child: Center(child: CircularProgressIndicator()),
            )
          else if (members.isEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 8),
              child: Text('Noch niemand.', style: theme.textTheme.bodySmall),
            )
          else
            for (final m in members)
              ListTile(
                contentPadding: EdgeInsets.zero,
                title: Text(m.email),
                subtitle: Text(roles[m.role] ?? m.role),
                trailing: IconButton(
                  tooltip: 'Nicht mehr teilen',
                  icon: const Icon(Icons.person_remove_outlined),
                  onPressed: _busy ? null : () => _remove(m),
                ),
              ),
        ],
      ),
    );
  }
}

/// Roadmap #9: the opt-in public sky page, the same fields as on the web and in the
/// camera's local UI (SPEC §6.8).
class PublicPageScreen extends StatefulWidget {
  const PublicPageScreen({
    super.key,
    required this.client,
    required this.camera,
  });

  final HubClient client;
  final Camera camera;

  @override
  State<PublicPageScreen> createState() => _PublicPageScreenState();
}

class _PublicPageScreenState extends State<PublicPageScreen> {
  static const texts = {
    'title': 'Titel',
    'location_label': 'Ort',
    'camera': 'Kamera',
    'lens': 'Objektiv',
    'computer': 'Rechner',
    'owner': 'Betrieben von',
    'notes': 'Notizen',
  };
  static const sections = {
    'live': 'Aktuelles Bild',
    'loop': 'Die letzte Stunde als Loop',
    'events': 'Meteore, Polarlicht und Blitze',
    'sky': 'Himmelsqualität (Wolken, SQM, Sterne)',
    'aurora': 'Polarlicht-Aussichten (Kp-Index)',
    'archive': 'Nachtarchiv mit Keogramm, Sternspuren und Zeitraffer',
    'observatory': 'Was das Teleskop gerade aufnimmt (N.I.N.A.)',
  };

  final _fields = {for (final key in texts.keys) key: TextEditingController()};
  PublicState? _state;
  Map<String, dynamic> _profile = {};
  Map<String, dynamic> _sections = {};
  String? _message;
  bool _messageIsError = false;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    for (final c in _fields.values) {
      c.dispose();
    }
    super.dispose();
  }

  void _take(PublicState state) {
    _state = state;
    _profile = Map.of(state.profile);
    _sections = Map.of(
      (state.profile['sections'] as Map<String, dynamic>?) ?? const {},
    );
    for (final MapEntry(:key, :value) in _fields.entries) {
      value.text = _profile[key] as String? ?? '';
    }
  }

  Future<void> _load() async {
    try {
      final state = await widget.client.publicState(widget.camera.id);
      if (mounted) setState(() => _take(state));
    } on Exception catch (e) {
      _show(_describe(e), error: true);
    }
  }

  void _show(String text, {bool error = false}) {
    if (!mounted) return;
    setState(() {
      _message = text;
      _messageIsError = error;
    });
  }

  Future<void> _save({bool? enabled}) async {
    setState(() => _busy = true);
    try {
      final profile = {
        ..._profile,
        for (final MapEntry(:key, :value) in _fields.entries) key: value.text,
        'sections': _sections,
      };
      final state = await widget.client.updatePublic(
        widget.camera.id,
        enabled: enabled,
        profile: profile,
      );
      if (mounted) setState(() => _take(state));
      _show(switch (enabled) {
        true => 'Die Seite ist jetzt öffentlich.',
        false => 'Die Seite ist abgeschaltet, der Link gilt nicht mehr.',
        null => 'Gespeichert.',
      });
    } on Exception catch (e) {
      _show(_describe(e), error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final state = _state;
    return Scaffold(
      appBar: AppBar(title: const Text('Öffentliche Seite')),
      body: state == null
          ? Center(
              child: _message == null
                  ? const CircularProgressIndicator()
                  : Text(_message!),
            )
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                SwitchListTile(
                  key: const Key('public-enabled'),
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Öffentlich zeigen'),
                  subtitle: const Text(
                    'Jeder mit dem Link sieht, was du unten auswählst.',
                  ),
                  value: state.enabled,
                  onChanged: _busy ? null : (on) => _save(enabled: on),
                ),
                if (state.url case final url?)
                  ListTile(
                    key: const Key('public-url'),
                    contentPadding: EdgeInsets.zero,
                    title: SelectableText(url),
                    trailing: IconButton(
                      tooltip: 'Link kopieren',
                      icon: const Icon(Icons.copy),
                      onPressed: () async {
                        await Clipboard.setData(ClipboardData(text: url));
                        _show('Link kopiert.');
                      },
                    ),
                  ),
                if (_message case final message?)
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 8),
                    child: Text(
                      message,
                      key: const Key('public-message'),
                      style: _messageIsError
                          ? TextStyle(color: theme.colorScheme.error)
                          : theme.textTheme.bodyMedium,
                    ),
                  ),
                const SizedBox(height: 8),
                Text('Was die Seite zeigt', style: theme.textTheme.titleMedium),
                for (final MapEntry(:key, :value) in sections.entries)
                  CheckboxListTile(
                    key: Key('section-$key'),
                    contentPadding: EdgeInsets.zero,
                    title: Text(value),
                    value: _sections[key] as bool? ?? false,
                    onChanged: (on) =>
                        setState(() => _sections[key] = on ?? false),
                  ),
                CheckboxListTile(
                  key: const Key('show-coordinates'),
                  contentPadding: EdgeInsets.zero,
                  title: const Text(
                    'Koordinaten zeigen und auf der Kamerakarte erscheinen',
                  ),
                  subtitle: const Text('Auf etwa 10 km gerundet.'),
                  value: _profile['show_coordinates'] as bool? ?? false,
                  onChanged: (on) => setState(
                    () => _profile['show_coordinates'] = on ?? false,
                  ),
                ),
                const SizedBox(height: 16),
                Text('Über die Kamera', style: theme.textTheme.titleMedium),
                const SizedBox(height: 8),
                for (final MapEntry(:key, :value) in texts.entries)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 12),
                    child: TextField(
                      key: Key('public-$key'),
                      controller: _fields[key],
                      maxLines: key == 'notes' ? 4 : 1,
                      maxLength: switch (key) {
                        'title' => 100,
                        'notes' => 2000,
                        _ => 200,
                      },
                      decoration: InputDecoration(
                        labelText: value,
                        border: const OutlineInputBorder(),
                        counterText: '',
                      ),
                    ),
                  ),
                FilledButton(
                  key: const Key('public-save'),
                  onPressed: _busy ? null : _save,
                  child: const Text('Speichern'),
                ),
              ],
            ),
    );
  }
}
