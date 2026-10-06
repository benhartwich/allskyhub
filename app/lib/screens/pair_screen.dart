import 'package:flutter/material.dart';

import '../api/hub_client.dart';

/// Pairing by code (SPEC §6.2 step 3). Reading the code from the camera's local setup API
/// (SPEC §7) comes with the onboarding flow; typing it is the fallback.
class PairScreen extends StatefulWidget {
  const PairScreen({super.key, required this.client});

  final HubClient client;

  @override
  State<PairScreen> createState() => _PairScreenState();
}

class _PairScreenState extends State<PairScreen> {
  final _code = TextEditingController();
  final _name = TextEditingController();
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _code.dispose();
    _name.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final camera = await widget.client.claim(
        _code.text,
        name: _name.text.trim(),
      );
      if (mounted) Navigator.of(context).pop(camera);
    } on HubException catch (e) {
      setState(
        () => _error = e.statusCode == 429
            ? 'Zu viele Versuche. Bitte später erneut versuchen.'
            : e.message,
      );
    } on Exception {
      setState(() => _error = 'Der Hub ist nicht erreichbar.');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Kamera koppeln')),
      body: ListView(
        padding: const EdgeInsets.all(24),
        children: [
          const Text(
            'Den Kopplungscode zeigt die Einrichtungsseite deiner Kamera im WLAN an.',
          ),
          const SizedBox(height: 20),
          TextField(
            key: const Key('code'),
            controller: _code,
            textCapitalization: TextCapitalization.characters,
            autocorrect: false,
            style: const TextStyle(fontSize: 24, letterSpacing: 4),
            decoration: const InputDecoration(
              labelText: 'Kopplungscode',
              hintText: 'ABC-DEF',
            ),
          ),
          const SizedBox(height: 12),
          TextField(
            key: const Key('name'),
            controller: _name,
            decoration: const InputDecoration(
              labelText: 'Name der Kamera',
              hintText: 'z. B. Garten',
            ),
          ),
          if (_error != null) ...[
            const SizedBox(height: 12),
            Text(
              _error!,
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
          ],
          const SizedBox(height: 20),
          FilledButton(
            onPressed: _busy ? null : _submit,
            child: Text(_busy ? 'Koppeln …' : 'Koppeln'),
          ),
        ],
      ),
    );
  }
}
