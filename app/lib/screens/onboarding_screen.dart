import 'package:flutter/material.dart';

import '../api/camera_setup.dart';
import '../api/hub_client.dart';
import '../api/onboarding.dart';

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
        () => _inputError =
            'Bitte die Adresse der Kamera angeben, z. B. 192.168.1.50.',
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
          title: const Text('Standort fehlt'),
          content: const Text(
            'Die Kamera ist gekoppelt, kennt aber ihren Standort noch nicht und nimmt deshalb '
            'noch nicht auf. Richte sie über ihr WLAN „allskyhub-…“ neu ein (dort fragt die App '
            'nach dem Standort) oder trage ihn in der Einrichtungsdatei auf der SD-Karte ein.',
          ),
          actions: [
            FilledButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('Verstanden'),
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
      appBar: AppBar(title: const Text('Kamera einrichten')),
      body: ListView(
        padding: const EdgeInsets.all(24),
        children: controller == null
            ? _form(context)
            : _progress(context, controller),
      ),
    );
  }

  List<Widget> _form(BuildContext context) => [
    const Text(
      'Verbinde dein Handy mit demselben WLAN wie die Kamera und gib ihre Adresse ein. '
      'Die App holt sich den Kopplungscode dann selbst.',
    ),
    const SizedBox(height: 20),
    TextField(
      key: const Key('address'),
      controller: _address,
      keyboardType: TextInputType.url,
      autocorrect: false,
      decoration: InputDecoration(
        labelText: 'Adresse der Kamera',
        hintText: '192.168.1.50',
        errorText: _inputError,
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
    const SizedBox(height: 20),
    FilledButton(onPressed: _start, child: const Text('Verbinden')),
  ];

  List<Widget> _progress(BuildContext context, OnboardingController c) {
    final code = c.info?.pairingCode;
    final text = switch (c.phase) {
      OnboardingPhase.connecting => 'Suche die Kamera …',
      OnboardingPhase.waitingForCode =>
        'Kamera gefunden. Sie meldet sich beim Hub an …',
      OnboardingPhase.claiming => 'Kopple die Kamera mit deinem Konto …',
      OnboardingPhase.waitingForCamera =>
        'Gekoppelt. Warte, bis die Kamera es bestätigt …',
      OnboardingPhase.done => 'Fertig.',
      OnboardingPhase.failed => c.error ?? 'Das Koppeln ist fehlgeschlagen.',
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
        child: Text(failed ? 'Erneut versuchen' : 'Abbrechen'),
      ),
    ];
  }
}
