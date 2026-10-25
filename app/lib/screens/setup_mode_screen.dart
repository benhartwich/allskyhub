import 'package:flutter/material.dart';

import '../api/camera_setup.dart';
import '../api/hub_client.dart';
import '../api/setup_key.dart';
import '../api/setup_mode.dart';
import '../platform/location.dart';
import 'onboarding_screen.dart';
import 'qr_scan_screen.dart';
import '../l10n.dart';

/// A new camera without network: set it up through its own Wi-Fi (SPEC §7.1), then pair.
class SetupModeScreen extends StatefulWidget {
  const SetupModeScreen({
    super.key,
    required this.controller,
    this.scanner = QrScanScreen.new,
  });

  final SetupModeController controller;

  /// The QR scanner page; answers the code's text (replaced in tests).
  final Widget Function() scanner;

  @override
  State<SetupModeScreen> createState() => _SetupModeScreenState();
}

class _SetupModeScreenState extends State<SetupModeScreen> {
  final _password = TextEditingController();
  final _setupKey = TextEditingController();
  String? _keyProblem;
  final _country = TextEditingController();
  final _name = TextEditingController();
  final _address = TextEditingController();
  final _latitude = TextEditingController();
  final _longitude = TextEditingController();
  WifiNetwork? _network;

  SetupModeController get c => widget.controller;

  /// SPEC §7.1: WPA passwords have 8 to 63 characters; open networks none.
  bool get _canSend {
    final network = _network;
    if (network == null || _country.text.trim().length != 2) return false;
    if (c.location == null) {
      return false; // the camera does not capture without it
    }
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
    _setupKey.dispose();
    _country.dispose();
    _name.dispose();
    _address.dispose();
    _latitude.dispose();
    _longitude.dispose();
    super.dispose();
  }

  void _changed() {
    // Show a position from the phone in the fields, so it can be checked or corrected.
    final location = c.location;
    if (location != null &&
        CameraLocation.tryParse(_latitude.text, _longitude.text) == null) {
      _latitude.text = location.latitude.toStringAsFixed(2);
      _longitude.text = location.longitude.toStringAsFixed(2);
    }
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
      appBar: AppBar(
        title: Text(tr('Neue Kamera einrichten', 'Set up a new camera')),
      ),
      body: ListView(
        padding: const EdgeInsets.all(24),
        children: [
          ...switch (c.phase) {
            SetupPhase.joinCameraNetwork => _join(),
            SetupPhase.connecting => _busy(
              tr('Verbinde mit der Kamera …', 'Connecting to the camera …'),
            ),
            SetupPhase.chooseNetwork => _choose(),
            SetupPhase.sending => _busy(
              tr(
                'Sende die WLAN-Daten an die Kamera …',
                'Sending the Wi-Fi details to the camera …',
              ),
            ),
            SetupPhase.searching => _busy(
              tr(
                'Verbinde dein Handy wieder mit deinem WLAN „${_network?.ssid ?? ''}“. '
                    'Die Kamera wählt sich jetzt dort ein; die App sucht sie …',
                'Connect your phone to your Wi-Fi "${_network?.ssid ?? ''}" again. '
                    'The camera is joining it now; the app is looking for it …',
              ),
            ),
            SetupPhase.found => _busy(tr('Kamera gefunden.', 'Camera found.')),
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

  Future<void> _scan() async {
    final code = await Navigator.of(
      context,
    ).push<String>(MaterialPageRoute(builder: (_) => widget.scanner()));
    if (code == null || !mounted) return;
    final wifi = parseWifiQr(code);
    if (wifi == null) {
      setState(
        () => _keyProblem = tr(
          'Das ist kein WLAN-Code einer allskyhub-Kamera. Der Aufkleber zeigt '
              'einen QR-Code mit „allskyhub-…“.',
          'This is not the Wi-Fi code of an allskyhub camera. The sticker shows '
              'a QR code with "allskyhub-…".',
        ),
      );
      return;
    }
    setState(() => _keyProblem = null);
    await c.join(wifi);
  }

  Future<void> _typed() async {
    final key = normalizeSetupKey(_setupKey.text);
    if (key == null) {
      setState(
        () => _keyProblem = tr(
          'Der Schlüssel hat 12 Zeichen, etwa ABCD-EFGH-JKMN (ohne 0, 1, I, L, O).',
          'The key has 12 characters, like ABCD-EFGH-JKMN (no 0, 1, I, L, O).',
        ),
      );
      return;
    }
    setState(() => _keyProblem = null);
    await c.join(SetupWifi(key: key));
  }

  List<Widget> _join() => [
    Text(
      tr(
        'Schalte die Kamera ein und warte etwa eine Minute. Dann öffnet sie ihr eigenes '
            'WLAN „allskyhub-…“, geschützt mit dem Einrichtungs-Schlüssel vom Aufkleber.',
        'Switch the camera on and wait about a minute. It then opens its own '
            'Wi-Fi "allskyhub-…", protected by the setup key on the sticker.',
      ),
    ),
    const SizedBox(height: 16),
    FilledButton.icon(
      key: const Key('scan-qr'),
      onPressed: _scan,
      icon: const Icon(Icons.qr_code_scanner),
      label: Text(
        tr('QR-Code auf der Kamera scannen', 'Scan the QR code on the camera'),
      ),
    ),
    const SizedBox(height: 16),
    TextField(
      key: const Key('setup-key'),
      controller: _setupKey,
      textCapitalization: TextCapitalization.characters,
      autocorrect: false,
      enableSuggestions: false,
      decoration: InputDecoration(
        labelText: tr(
          'Oder Einrichtungs-Schlüssel eintippen',
          'Or type the setup key',
        ),
        hintText: 'XXXX-XXXX-XXXX',
        errorText: _keyProblem,
        errorMaxLines: 3,
      ),
      onSubmitted: (_) => _typed(),
    ),
    const SizedBox(height: 8),
    OutlinedButton(
      key: const Key('join-typed'),
      onPressed: _typed,
      child: Text(tr('Mit der Kamera verbinden', 'Connect to the camera')),
    ),
    const SizedBox(height: 20),
    Text(
      tr(
        'Schon in den WLAN-Einstellungen mit „allskyhub-…“ verbunden (etwa über die '
            'Kamera-App des Handys)? Meldet das Handy, das Netz habe kein Internet, bleib '
            'trotzdem verbunden.',
        'Already connected to "allskyhub-…" in the Wi-Fi settings (for example with the '
            'phone\'s camera app)? If the phone says the network has no internet, stay '
            'connected anyway.',
      ),
      style: Theme.of(context).textTheme.bodySmall,
    ),
    TextButton(
      key: const Key('joined-manually'),
      onPressed: c.connect,
      child: Text(tr('Ich bin verbunden', 'I\'m connected')),
    ),
    TextButton(
      key: const Key('join-open'),
      onPressed: () => c.join(const SetupWifi()),
      child: Text(
        tr(
          'Kamera ohne Schlüssel (ältere Software)',
          'Camera without a key (older software)',
        ),
      ),
    ),
  ];

  List<Widget> _choose() {
    final info = c.info!;
    final outdated = c.outdatedCamera;
    return [
      Text(
        tr(
          'Verbunden mit ${info.setupSsid}. In welches WLAN soll die Kamera?',
          'Connected to ${info.setupSsid}. Which Wi-Fi should the camera join?',
        ),
      ),
      if (outdated) ...[
        const SizedBox(height: 8),
        Text(
          tr(
            'Diese Kamera hat noch ein offenes Einrichtungs-WLAN: Bitte nach der '
                'Einrichtung ihre Software aktualisieren.',
            'This camera still has an open setup Wi-Fi: please update its '
                'software after the setup.',
          ),
          key: const Key('outdated-camera'),
          style: TextStyle(color: Theme.of(context).colorScheme.tertiary),
        ),
      ],
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
                  semanticLabel: n.secure
                      ? tr('verschlüsselt', 'encrypted')
                      : tr('offen', 'open'),
                ),
                subtitle: LinearProgressIndicator(value: n.signal / 100),
              ),
          ],
        ),
      ),
      TextButton.icon(
        onPressed: c.refreshNetworks,
        icon: const Icon(Icons.refresh),
        label: Text(tr('Liste aktualisieren', 'Refresh list')),
      ),
      if (_network?.secure ?? false)
        TextField(
          key: const Key('wifi-password'),
          controller: _password,
          obscureText: true,
          onChanged: (_) => setState(() {}),
          decoration: InputDecoration(
            labelText: tr('WLAN-Passwort', 'Wi-Fi password'),
            helperText: tr('8 bis 63 Zeichen', '8 to 63 characters'),
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
              decoration: InputDecoration(
                labelText: tr('Land', 'Country'),
                counterText: '',
              ),
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: TextField(
              key: const Key('name'),
              controller: _name,
              decoration: InputDecoration(
                labelText: tr('Name der Kamera', 'Camera name'),
                hintText: tr('z. B. Garten', 'e.g. Garden'),
              ),
            ),
          ),
        ],
      ),
      const SizedBox(height: 20),
      ..._locationSection(),
      const SizedBox(height: 20),
      FilledButton(
        onPressed: !_canSend
            ? null
            : () => c.send(
                ssid: _network!.ssid,
                password: _network!.secure ? _password.text : null,
                country: _country.text.trim(),
              ),
        child: Text(tr('Kamera verbinden', 'Connect camera')),
      ),
    ];
  }

  List<Widget> _locationSection() {
    final theme = Theme.of(context);
    final problem = c.locationProblem;
    void manual(String _) =>
        c.setLocation(CameraLocation.tryParse(_latitude.text, _longitude.text));
    return [
      Text(
        tr('Standort der Kamera', 'Camera location'),
        style: theme.textTheme.titleSmall,
      ),
      const SizedBox(height: 4),
      Text(
        tr(
          'Die Kamera braucht ihren Standort für Sonnenstand und Tag/Nacht. Etwa 1 km genau genügt.',
          'The camera needs its location for the sun and day/night. About 1 km is precise enough.',
        ),
      ),
      const SizedBox(height: 8),
      OutlinedButton.icon(
        key: const Key('locate'),
        onPressed: c.locating ? null : c.locate,
        icon: const Icon(Icons.my_location),
        label: Text(
          c.locating
              ? tr('Standort wird bestimmt …', 'Finding the location …')
              : tr(
                  'Standort des Handys übernehmen',
                  'Use the phone\'s location',
                ),
        ),
      ),
      if (problem != null) ...[
        const SizedBox(height: 8),
        Text(
          describeLocationProblem(problem),
          style: TextStyle(color: theme.colorScheme.error),
        ),
      ],
      const SizedBox(height: 8),
      Row(
        children: [
          Expanded(
            child: TextField(
              key: const Key('latitude'),
              controller: _latitude,
              keyboardType: const TextInputType.numberWithOptions(
                decimal: true,
                signed: true,
              ),
              onChanged: manual,
              decoration: InputDecoration(
                labelText: tr('Breite', 'Latitude'),
                hintText: '50.04',
              ),
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: TextField(
              key: const Key('longitude'),
              controller: _longitude,
              keyboardType: const TextInputType.numberWithOptions(
                decimal: true,
                signed: true,
              ),
              onChanged: manual,
              decoration: InputDecoration(
                labelText: tr('Länge', 'Longitude'),
                hintText: '10.39',
              ),
            ),
          ),
        ],
      ),
      if (c.timezone != null) ...[
        const SizedBox(height: 8),
        Text(
          tr('Zeitzone: ${c.timezone}', 'Time zone: ${c.timezone}'),
          style: theme.textTheme.bodySmall,
        ),
      ],
    ];
  }

  List<Widget> _notFound() => [
    Text(
      tr(
        'Die Kamera hat sich im WLAN noch nicht gemeldet.',
        'The camera has not shown up on the Wi-Fi yet.',
      ),
    ),
    const SizedBox(height: 8),
    Text(
      tr(
        'Taucht wieder das Netz „${c.info?.setupSsid ?? 'allskyhub-…'}“ auf, konnte sie sich '
            'nicht verbinden: dann noch einmal von vorn – die App zeigt den Grund an.',
        'If the network "${c.info?.setupSsid ?? 'allskyhub-…'}" shows up again, it could not '
            'connect: start over, and the app shows the reason.',
      ),
    ),
    const SizedBox(height: 16),
    FilledButton(
      onPressed: c.search,
      child: Text(tr('Weiter suchen', 'Keep searching')),
    ),
    OutlinedButton(
      onPressed: c.restart,
      child: Text(tr('Von vorn beginnen', 'Start over')),
    ),
    const SizedBox(height: 16),
    TextField(
      key: const Key('address'),
      controller: _address,
      keyboardType: TextInputType.url,
      decoration: InputDecoration(
        labelText: tr(
          'Oder Adresse der Kamera eingeben',
          'Or enter the camera\'s address',
        ),
        hintText: '192.168.1.50',
      ),
    ),
    TextButton(
      onPressed: () {
        if (_address.text.trim().isNotEmpty) {
          _pair(cameraBaseUri(_address.text));
        }
      },
      child: Text(tr('Mit dieser Adresse koppeln', 'Pair with this address')),
    ),
  ];
}
