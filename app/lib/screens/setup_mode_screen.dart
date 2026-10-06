import 'package:flutter/material.dart';

import '../api/camera_setup.dart';
import '../api/hub_client.dart';
import '../api/setup_mode.dart';
import 'onboarding_screen.dart';

/// A new camera without network: set it up through its own Wi-Fi (SPEC §7.1), then pair.
class SetupModeScreen extends StatefulWidget {
  const SetupModeScreen({super.key, required this.controller});

  final SetupModeController controller;

  @override
  State<SetupModeScreen> createState() => _SetupModeScreenState();
}

class _SetupModeScreenState extends State<SetupModeScreen> {
  final _password = TextEditingController();
  final _country = TextEditingController();
  final _name = TextEditingController();
  final _address = TextEditingController();
  WifiNetwork? _network;

  SetupModeController get c => widget.controller;

  /// SPEC §7.1: WPA passwords have 8 to 63 characters; open networks none.
  bool get _canSend {
    final network = _network;
    if (network == null || _country.text.trim().length != 2) return false;
    final length = _password.text.length;
    return !network.secure || (length >= 8 && length <= 63);
  }

  HubClient get hub => c.hub;

  @override
  void initState() {
    super.initState();
    _country.text =
        WidgetsBinding.instance.platformDispatcher.locale.countryCode ?? 'DE';
    c.addListener(_changed);
  }

  @override
  void dispose() {
    c.removeListener(_changed);
    _password.dispose();
    _country.dispose();
    _name.dispose();
    _address.dispose();
    super.dispose();
  }

  void _changed() {
    if (c.phase == SetupPhase.found) {
      _pair(c.cameraUri!);
      return;
    }
    setState(() {});
  }

  Future<void> _pair(Uri uri) async {
    final camera = await Navigator.of(context).push<Camera>(
      MaterialPageRoute(
        builder: (_) => OnboardingScreen(
          client: hub,
          cameraUri: uri,
          name: _name.text.trim(),
        ),
      ),
    );
    if (mounted) Navigator.of(context).pop(camera);
  }

  @override
  Widget build(BuildContext context) {
    final errorStyle = TextStyle(color: Theme.of(context).colorScheme.error);
    return Scaffold(
      appBar: AppBar(title: const Text('Neue Kamera einrichten')),
      body: ListView(
        padding: const EdgeInsets.all(24),
        children: [
          ...switch (c.phase) {
            SetupPhase.joinCameraNetwork => _join(),
            SetupPhase.connecting => _busy('Verbinde mit der Kamera …'),
            SetupPhase.chooseNetwork => _choose(),
            SetupPhase.sending => _busy('Sende die WLAN-Daten an die Kamera …'),
            SetupPhase.searching => _busy(
              'Verbinde dein Handy wieder mit deinem WLAN „${_network?.ssid ?? ''}“. '
              'Die Kamera wählt sich jetzt dort ein; die App sucht sie …',
            ),
            SetupPhase.found => _busy('Kamera gefunden.'),
            SetupPhase.notFound => _notFound(),
          },
          if (c.error != null) ...[
            const SizedBox(height: 16),
            Text(c.error!, key: const Key('error'), style: errorStyle),
          ],
        ],
      ),
    );
  }

  List<Widget> _busy(String text) => [
    const Center(child: CircularProgressIndicator()),
    const SizedBox(height: 20),
    Text(text, textAlign: TextAlign.center),
  ];

  List<Widget> _join() => [
    const Text('1. Schalte die Kamera ein und warte etwa eine Minute.'),
    const SizedBox(height: 8),
    const Text(
      '2. Öffne die WLAN-Einstellungen deines Handys und verbinde dich mit dem '
      'Netz „allskyhub-…“. Meldet das Handy, das Netz habe kein Internet, '
      'bleib trotzdem verbunden.',
    ),
    const SizedBox(height: 8),
    const Text('3. Komm hierher zurück und tippe auf „Weiter“.'),
    const SizedBox(height: 20),
    FilledButton(onPressed: c.connect, child: const Text('Weiter')),
  ];

  List<Widget> _choose() {
    final info = c.info!;
    return [
      Text('Verbunden mit ${info.setupSsid}. In welches WLAN soll die Kamera?'),
      const SizedBox(height: 12),
      RadioGroup<String>(
        groupValue: _network?.ssid,
        onChanged: (ssid) => setState(
          () => _network = c.networks.firstWhere((n) => n.ssid == ssid),
        ),
        child: Column(
          children: [
            for (final n in c.networks)
              RadioListTile<String>(
                value: n.ssid,
                title: Text(n.ssid),
                secondary: Icon(
                  n.secure ? Icons.lock_outline : Icons.lock_open,
                  semanticLabel: n.secure ? 'verschlüsselt' : 'offen',
                ),
                subtitle: LinearProgressIndicator(value: n.signal / 100),
              ),
          ],
        ),
      ),
      TextButton.icon(
        onPressed: c.refreshNetworks,
        icon: const Icon(Icons.refresh),
        label: const Text('Liste aktualisieren'),
      ),
      if (_network?.secure ?? false)
        TextField(
          key: const Key('wifi-password'),
          controller: _password,
          obscureText: true,
          onChanged: (_) => setState(() {}),
          decoration: const InputDecoration(
            labelText: 'WLAN-Passwort',
            helperText: '8 bis 63 Zeichen',
          ),
        ),
      const SizedBox(height: 12),
      Row(
        children: [
          SizedBox(
            width: 96,
            child: TextField(
              key: const Key('country'),
              controller: _country,
              maxLength: 2,
              onChanged: (_) => setState(() {}),
              textCapitalization: TextCapitalization.characters,
              decoration: const InputDecoration(
                labelText: 'Land',
                counterText: '',
              ),
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: TextField(
              key: const Key('name'),
              controller: _name,
              decoration: const InputDecoration(
                labelText: 'Name der Kamera',
                hintText: 'z. B. Garten',
              ),
            ),
          ),
        ],
      ),
      const SizedBox(height: 20),
      FilledButton(
        onPressed: !_canSend
            ? null
            : () => c.send(
                ssid: _network!.ssid,
                password: _network!.secure ? _password.text : null,
                country: _country.text.trim(),
              ),
        child: const Text('Kamera verbinden'),
      ),
    ];
  }

  List<Widget> _notFound() => [
    const Text('Die Kamera hat sich im WLAN noch nicht gemeldet.'),
    const SizedBox(height: 8),
    Text(
      'Taucht wieder das Netz „${c.info?.setupSsid ?? 'allskyhub-…'}“ auf, konnte sie sich '
      'nicht verbinden: dann noch einmal von vorn – die App zeigt den Grund an.',
    ),
    const SizedBox(height: 16),
    FilledButton(onPressed: c.search, child: const Text('Weiter suchen')),
    OutlinedButton(
      onPressed: c.restart,
      child: const Text('Von vorn beginnen'),
    ),
    const SizedBox(height: 16),
    TextField(
      key: const Key('address'),
      controller: _address,
      keyboardType: TextInputType.url,
      decoration: const InputDecoration(
        labelText: 'Oder Adresse der Kamera eingeben',
        hintText: '192.168.1.50',
      ),
    ),
    TextButton(
      onPressed: () {
        if (_address.text.trim().isNotEmpty) {
          _pair(cameraBaseUri(_address.text));
        }
      },
      child: const Text('Mit dieser Adresse koppeln'),
    ),
  ];
}
