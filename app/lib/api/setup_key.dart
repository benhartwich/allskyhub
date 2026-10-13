// The camera's setup network `allskyhub-XXXX` and its key (SPEC §7.1): read from the Wi-Fi
// QR code on the camera's label, or typed.

/// SPEC §6.2: the pairing alphabet, also used for the setup key.
const pairingAlphabet = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789';

/// How to join a camera's setup network: its exact name (from a QR code) or any
/// `allskyhub-` network nearby (the phone asks which), with the key or open (an agent
/// from before the setup key).
class SetupWifi {
  const SetupWifi({this.ssid, this.key});

  /// The network's name; null joins any network whose name starts with `allskyhub-`.
  final String? ssid;

  /// The 12-character passphrase; null for an open network (an older camera).
  final String? key;

  bool get open => key == null;
}

const setupPrefix = 'allskyhub-';

/// The typed key without hyphens and spaces, in upper case, or null if it is not 12
/// characters of the pairing alphabet.
String? normalizeSetupKey(String typed) {
  final key = typed.replaceAll(RegExp(r'[\s-]'), '').toUpperCase();
  if (key.length != 12 ||
      key.split('').any((c) => !pairingAlphabet.contains(c))) {
    return null;
  }
  return key;
}

/// SPEC §7.1: the ZXing Wi-Fi format, fields in any order, `H:` optional, `\` escapes;
/// only `T:WPA` with a name starting `allskyhub-`. Null for any other code.
SetupWifi? parseWifiQr(String text) {
  final raw = text.trim();
  if (!raw.toUpperCase().startsWith('WIFI:')) return null;
  final fields = <String, String>{};
  final body = raw.substring(5);
  var key = StringBuffer();
  var value = StringBuffer();
  var inValue = false;
  for (var i = 0; i < body.length; i++) {
    final c = body[i];
    if (c == r'\' && i + 1 < body.length) {
      (inValue ? value : key).write(body[++i]);
    } else if (!inValue && c == ':') {
      inValue = true;
    } else if (inValue && c == ';') {
      fields[key.toString().toUpperCase()] = value.toString();
      key = StringBuffer();
      value = StringBuffer();
      inValue = false;
    } else {
      (inValue ? value : key).write(c);
    }
  }
  final ssid = fields['S'];
  final password = fields['P'];
  if (fields['T']?.toUpperCase() != 'WPA' || ssid == null || password == null) {
    return null;
  }
  if (!ssid.startsWith(setupPrefix) || password.length < 8) return null;
  return SetupWifi(ssid: ssid, key: password);
}
