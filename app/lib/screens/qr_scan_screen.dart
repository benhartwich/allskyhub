import 'package:flutter/material.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

import '../l10n.dart';

/// Reads the Wi-Fi QR code on the camera's label (SPEC §7.1) and answers its text.
class QrScanScreen extends StatefulWidget {
  const QrScanScreen({super.key});

  @override
  State<QrScanScreen> createState() => _QrScanScreenState();
}

class _QrScanScreenState extends State<QrScanScreen> {
  final _controller = MobileScannerController(formats: [BarcodeFormat.qrCode]);
  bool _done = false;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _found(BarcodeCapture capture) {
    if (_done) return;
    final text = capture.barcodes
        .map((b) => b.rawValue)
        .whereType<String>()
        .firstOrNull;
    if (text == null) return;
    _done = true;
    Navigator.of(context).pop(text);
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: Text(tr('QR-Code scannen', 'Scan QR code'))),
    body: Stack(
      children: [
        MobileScanner(
          controller: _controller,
          onDetect: _found,
          errorBuilder: (context, error) => Center(
            child: Padding(
              padding: const EdgeInsets.all(24),
              child: Text(
                tr(
                  'Die Kamera des Handys ist nicht verfügbar. Tippe den Schlüssel vom '
                      'Aufkleber stattdessen ein.',
                  "The phone's camera is not available. Type the key from the "
                      'label instead.',
                ),
                textAlign: TextAlign.center,
              ),
            ),
          ),
        ),
        Align(
          alignment: Alignment.bottomCenter,
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Text(
              tr(
                'Halte das Handy über den QR-Code auf dem Aufkleber der Kamera.',
                "Hold the phone over the QR code on the camera's label.",
              ),
              textAlign: TextAlign.center,
              style: const TextStyle(
                color: Colors.white,
                shadows: [Shadow(blurRadius: 4)],
              ),
            ),
          ),
        ),
      ],
    ),
  );
}
