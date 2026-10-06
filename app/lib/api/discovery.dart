// Finding the camera on the home network by DNS-SD (SPEC §7.2).

import 'dart:async';
import 'dart:convert';

import 'package:nsd/nsd.dart' as nsd;

const serviceType = '_allskyhub._tcp';

abstract class CameraDiscovery {
  /// Base URI of the camera's local web UI, or null if it did not show up in time.
  Future<Uri?> find(String deviceId, {Duration timeout});
}

/// The camera announces `id=<device id>` in its TXT record.
Uri? matchService(nsd.Service service, String deviceId) {
  final raw = service.txt?['id'];
  if (raw == null || utf8.decode(raw, allowMalformed: true) != deviceId) {
    return null;
  }
  final addresses = service.addresses ?? const [];
  final host = addresses.isNotEmpty ? addresses.first.address : service.host;
  if (host == null || service.port == null) return null;
  return Uri(scheme: 'http', host: host, port: service.port);
}

class NsdCameraDiscovery implements CameraDiscovery {
  @override
  Future<Uri?> find(
    String deviceId, {
    Duration timeout = const Duration(minutes: 2),
  }) async {
    final found = Completer<Uri?>();
    final discovery = await nsd.startDiscovery(
      serviceType,
      ipLookupType: nsd.IpLookupType.v4,
    );
    void check() {
      for (final service in discovery.services) {
        final uri = matchService(service, deviceId);
        if (uri != null && !found.isCompleted) found.complete(uri);
      }
    }

    discovery.addListener(check);
    check();
    try {
      return await found.future.timeout(timeout, onTimeout: () => null);
    } finally {
      discovery.removeListener(check);
      await nsd.stopDiscovery(discovery);
    }
  }
}
