import 'package:flutter/material.dart';

import '../api/camera_setup.dart';
import '../api/hub_client.dart';
import '../api/onboarding.dart';
import '../l10n.dart';

/// Pairing without typing the code (SPEC §6.2): the phone is in the same Wi-Fi as the camera
/// (or in its setup hotspot); the app reads the code from the camera and claims it.
class OnboardingScreen extends StatefulWidget {
  const OnboardingScreen({
    super.key,
    required this.client,
    this.setupClientFor,
    this.cameraUri,
    this.name = '',
  });

  final HubClient client;

  /// Known from discovery (SPEC §7.2): skips typing the address.
  final Uri? cameraUri;
  final String name;

  /// For tests; defaults to a real HTTP client.
  final CameraSetupClient Function(Uri base)? setupClientFor;

  @override
  State<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends State<OnboardingScreen> {
  final _address = TextEditingController();
  final _name = TextEditingController();
  OnboardingController? _controller;
  String? _inputError;

  @override
  void initState() {
    super.initState();
    _name.text = widget.name;
    final uri = widget.cameraUri;
    if (uri != null) {
      _address.text = uri.authority;
      _start(fromInitState: true);
    }
  }

  @override
  void dispose() {
    _controller?.dispose();
    _address.dispose();
    _name.dispose();
    super.dispose();
  }

  void _start({bool fromInitState = false}) {
    final Uri base;
    try {
      base = cameraBaseUri(_address.text);
      if (base.host.isEmpty) throw const FormatException();
    } on FormatException {
      setState(
        () => _inputError = tr(
          'Bitte die Adresse der Kamera angeben, z. B. 192.168.1.50.',
          'Please enter the camera\'s address, e.g. 192.168.1.50.',
        ),
      );
      return;
    }
    final setup = widget.setupClientFor?.call(base) ?? CameraSetupClient(base);
    final controller = OnboardingController(
      setup: setup,
      hub: widget.client,
      name: _name.text.trim(),
    )..addListener(_changed);
    _inputError = null;
    _controller = controller;
    controller.start();
    // From initState (address from discovery) the first build follows anyway.
    if (!fromInitState) setState(() {});
  }

  bool _finishing = false;

  void _changed() {
    final controller = _controller!;
    if (controller.phase == OnboardingPhase.done) {
      if (!_finishing) _finish(controller);
      return;
    }
    setState(() {});
  }

  /// Paired. If the camera has no location yet it will not capture (SPEC §7.1): say so.
  Future<void> _finish(OnboardingController controller) async {
    _finishing = true;
    if (controller.info?.locationSet == false) {
      await showDialog<void>(
        context: context,
        builder: (context) => AlertDialog(
          title: Text(tr('Standort fehlt', 'Location missing')),
          content: Text(
            tr(
              'Die Kamera ist gekoppelt, kennt aber ihren Standort noch nicht und nimmt deshalb '
                  'noch nicht auf. Richte sie über ihr WLAN „allskyhub-…“ neu ein (dort fragt die App '
                  'nach dem Standort) oder trage ihn in der Einrichtungsdatei auf der SD-Karte ein.',
              'The camera is paired but does not know its location yet, so it does not capture '
                  'yet. Set it up again through its Wi-Fi "allskyhub-…" (the app asks for the '
                  'location there) or enter it in the setup file on the SD card.',
            ),
          ),
          actions: [
            FilledButton(
              onPressed: () => Navigator.pop(context),
              child: Text(tr('Verstanden', 'Got it')),
            ),
          ],
        ),
      );
    }
    if (mounted) Navigator.of(context).pop(controller.camera);
  }

  void _restart() {
    _controller?.dispose();
    setState(() => _controller = null);
  }

  @override
  Widget build(BuildContext context) {
    final controller = _controller;
    return Scaffold(
      appBar: AppBar(title: Text(tr('Kamera einrichten', 'Set up camera'))),
      body: ListView(
        padding: const EdgeInsets.all(24),
        children: controller == null
            ? _form(context)
            : _progress(context, controller),
      ),
    );
  }

  List<Widget> _form(BuildContext context) => [
    Text(
      tr(
        'Verbinde dein Handy mit demselben WLAN wie die Kamera und gib ihre Adresse ein. '
            'Die App holt sich den Kopplungscode dann selbst.',
        'Connect your phone to the same Wi-Fi as the camera and enter its address. '
            'The app then fetches the pairing code itself.',
      ),
    ),
    const SizedBox(height: 20),
    TextField(
      key: const Key('address'),
      controller: _address,
      keyboardType: TextInputType.url,
      autocorrect: false,
      decoration: InputDecoration(
        labelText: tr('Adresse der Kamera', 'Camera address'),
        hintText: '192.168.1.50',
        errorText: _inputError,
      ),
    ),
    const SizedBox(height: 12),
    TextField(
      key: const Key('name'),
      controller: _name,
      decoration: InputDecoration(
        labelText: tr('Name der Kamera', 'Camera name'),
        hintText: tr('z. B. Garten', 'e.g. Garden'),
      ),
    ),
    const SizedBox(height: 20),
    FilledButton(onPressed: _start, child: Text(tr('Verbinden', 'Connect'))),
  ];

  List<Widget> _progress(BuildContext context, OnboardingController c) {
    final code = c.info?.pairingCode;
    final text = switch (c.phase) {
      OnboardingPhase.connecting => tr(
        'Suche die Kamera …',
        'Looking for the camera …',
      ),
      OnboardingPhase.waitingForCode => tr(
        'Kamera gefunden. Sie meldet sich beim Hub an …',
        'Camera found. It is signing in to the hub …',
      ),
      OnboardingPhase.claiming => tr(
        'Kopple die Kamera mit deinem Konto …',
        'Pairing the camera with your account …',
      ),
      OnboardingPhase.waitingForCamera => tr(
        'Gekoppelt. Warte, bis die Kamera es bestätigt …',
        'Paired. Waiting for the camera to confirm …',
      ),
      OnboardingPhase.done => tr('Fertig.', 'Done.'),
      OnboardingPhase.failed =>
        c.error ?? tr('Das Koppeln ist fehlgeschlagen.', 'Pairing failed.'),
    };
    final failed = c.phase == OnboardingPhase.failed;
    return [
      if (!failed) const Center(child: CircularProgressIndicator()),
      const SizedBox(height: 20),
      Text(
        text,
        key: const Key('phase'),
        textAlign: TextAlign.center,
        style: failed
            ? TextStyle(color: Theme.of(context).colorScheme.error)
            : null,
      ),
      if (code != null) ...[
        const SizedBox(height: 12),
        Text(
          'Code: ${code.substring(0, 3)}-${code.substring(3)}',
          textAlign: TextAlign.center,
          style: Theme.of(context).textTheme.titleLarge,
        ),
      ],
      const SizedBox(height: 20),
      OutlinedButton(
        onPressed: _restart,
        child: Text(
          failed
              ? tr('Erneut versuchen', 'Try again')
              : tr('Abbrechen', 'Cancel'),
        ),
      ),
    ];
  }
}
