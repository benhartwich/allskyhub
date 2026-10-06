import 'package:flutter/material.dart';

import '../api/hub_client.dart';
import '../main.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({
    super.key,
    required this.clientFor,
    required this.onSignedIn,
  });

  final ClientFactory clientFor;
  final Future<void> Function(HubClient client) onSignedIn;

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _email = TextEditingController();
  final _password = TextEditingController();
  final _hub = TextEditingController(text: defaultHubUrl);
  bool _busy = false;
  bool _ownHub = false;
  String? _error;

  @override
  void dispose() {
    _email.dispose();
    _password.dispose();
    _hub.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    final client = widget.clientFor(_hub.text.trim(), null);
    try {
      await client.login(
        _email.text.trim(),
        _password.text,
        label: 'allskyhub-App',
      );
      await widget.onSignedIn(client);
    } on HubException catch (e) {
      setState(
        () => _error = e.unauthorized
            ? 'E-Mail-Adresse oder Passwort ist falsch.'
            : 'Anmeldung fehlgeschlagen (${e.statusCode}).',
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
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 420),
              child: AutofillGroup(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Text(
                      'allskyhub',
                      style: Theme.of(context).textTheme.headlineMedium,
                    ),
                    const SizedBox(height: 8),
                    const Text(
                      'Melde dich an, um deine Allsky-Kamera zu koppeln und ihre Bilder zu sehen.',
                    ),
                    const SizedBox(height: 24),
                    TextField(
                      key: const Key('email'),
                      controller: _email,
                      keyboardType: TextInputType.emailAddress,
                      autofillHints: const [AutofillHints.email],
                      decoration: const InputDecoration(
                        labelText: 'E-Mail-Adresse',
                      ),
                    ),
                    const SizedBox(height: 12),
                    TextField(
                      key: const Key('password'),
                      controller: _password,
                      obscureText: true,
                      autofillHints: const [AutofillHints.password],
                      decoration: const InputDecoration(labelText: 'Passwort'),
                      onSubmitted: (_) => _submit(),
                    ),
                    if (_ownHub) ...[
                      const SizedBox(height: 12),
                      TextField(
                        key: const Key('hub'),
                        controller: _hub,
                        keyboardType: TextInputType.url,
                        decoration: const InputDecoration(
                          labelText: 'Hub-Adresse',
                        ),
                      ),
                    ],
                    if (_error != null) ...[
                      const SizedBox(height: 12),
                      Text(
                        _error!,
                        style: TextStyle(
                          color: Theme.of(context).colorScheme.error,
                        ),
                      ),
                    ],
                    const SizedBox(height: 20),
                    FilledButton(
                      onPressed: _busy ? null : _submit,
                      child: Text(_busy ? 'Anmelden …' : 'Anmelden'),
                    ),
                    TextButton(
                      onPressed: () => setState(() => _ownHub = !_ownHub),
                      child: Text(
                        _ownHub ? 'allskyhub.org verwenden' : 'Eigener Hub',
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
