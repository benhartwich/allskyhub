// Red night light for the whole app (draft 3, 2026-11-13): off, on, or on by itself
// while the sun is below -6° at the place of "Himmel". Stays on the phone.

import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'l10n.dart';

/// Night red: keeps the eyes adapted (bortle's red mode uses the same colour).
const nightRed = Color(0xFFFF5A4E);

/// The sun this far below the horizon counts as night for "automatisch".
const nightSunAltitude = -6.0;

/// Everything in shades of red, like a red torch.
Widget redNight(Widget child) => ColorFiltered(
  colorFilter: const ColorFilter.matrix([
    0.3, 0.6, 0.1, 0, 0, //
    0, 0, 0, 0, 0,
    0, 0, 0, 0, 0,
    0, 0, 0, 1, 0,
  ]),
  child: child,
);

enum NightSetting { off, on, auto }

abstract class NightStore {
  Future<NightSetting?> load();
  Future<void> save(NightSetting setting);
}

class SecureNightStore implements NightStore {
  SecureNightStore([FlutterSecureStorage? storage])
    : _storage = storage ?? const FlutterSecureStorage();

  final FlutterSecureStorage _storage;
  static const _key = 'night_red';

  @override
  Future<NightSetting?> load() async {
    try {
      final raw = await _storage.read(key: _key);
      for (final s in NightSetting.values) {
        if (s.name == raw) return s;
      }
      return null;
    } on Exception {
      return null;
    }
  }

  @override
  Future<void> save(NightSetting setting) =>
      _storage.write(key: _key, value: setting.name);
}

/// For tests.
class MemoryNightStore implements NightStore {
  MemoryNightStore([this.setting]);

  NightSetting? setting;

  @override
  Future<NightSetting?> load() async => setting;

  @override
  Future<void> save(NightSetting setting) async => this.setting = setting;
}

/// Where "automatisch" looks at the sun: (latitude, longitude), or null when unknown.
typedef NightPlace = Future<(double, double)?> Function();

/// Altitude of the sun's centre in degrees (low precision, about 0.1°; enough for dusk).
double sunAltitude(DateTime when, double lat, double lon) {
  double rad(double d) => d * math.pi / 180;
  double deg(double r) => r * 180 / math.pi;
  final d =
      when.toUtc().difference(DateTime.utc(2000, 1, 1, 12)).inMilliseconds /
      86400000.0;
  final g = rad(357.529 + 0.98560028 * d);
  final q = 280.459 + 0.98564736 * d;
  final l = rad(q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g));
  final e = rad(23.439 - 0.00000036 * d);
  final ra = math.atan2(math.cos(e) * math.sin(l), math.cos(l));
  final dec = math.asin(math.sin(e) * math.sin(l));
  final gmst = 280.46061837 + 360.98564736629 * d;
  final h = rad(gmst + lon) - ra;
  final la = rad(lat);
  return deg(
    math.asin(
      math.sin(la) * math.sin(dec) + math.cos(la) * math.cos(dec) * math.cos(h),
    ),
  );
}

/// The app's red light: what the user chose and whether it is on now.
class NightMode extends ChangeNotifier {
  NightMode({
    required this._store,
    required this._place,
    DateTime Function()? clock,
  }) : _clock = clock ?? DateTime.now;

  final NightStore _store;
  final NightPlace _place;
  final DateTime Function() _clock;
  Timer? _timer;

  NightSetting _setting = NightSetting.off;
  bool _dark = false;
  bool _placeKnown = false;
  (double, double)? _placeCache;
  DateTime? _placeAt;

  /// The place changes rarely (a place chosen under "Himmel", or the phone's last known
  /// one): read it at most every 30 minutes, not with every minute's look at the sun.
  Future<(double, double)?> _cachedPlace() async {
    final at = _placeAt;
    final now = _clock();
    if (at == null || now.difference(at).abs() >= const Duration(minutes: 30)) {
      _placeCache = await _place();
      _placeAt = now;
    }
    return _placeCache;
  }

  NightSetting get setting => _setting;

  /// "automatisch" without a place stays off; the settings sheet says so.
  bool get placeKnown => _placeKnown;

  bool get active => switch (_setting) {
    NightSetting.on => true,
    NightSetting.off => false,
    NightSetting.auto => _dark,
  };

  Future<void> load() async {
    _setting = await _store.load() ?? NightSetting.off;
    notifyListeners();
    await _follow();
  }

  Future<void> choose(NightSetting setting) async {
    _placeAt = null; // a new choice looks at the place again
    _setting = setting;
    notifyListeners();
    await _store.save(setting);
    await _follow();
  }

  /// Only "automatisch" looks at the sun, once a minute.
  Future<void> _follow() async {
    if (_setting != NightSetting.auto) {
      _timer?.cancel();
      _timer = null;
      return;
    }
    _timer ??= Timer.periodic(const Duration(minutes: 1), (_) => refresh());
    await refresh();
  }

  /// The quick switch: the opposite of what shows now, as a fixed choice.
  Future<void> toggle() => choose(active ? NightSetting.off : NightSetting.on);

  /// Looks at the sun again (every minute while "automatisch").
  Future<void> refresh() async {
    if (_setting != NightSetting.auto) return;
    final where = await _cachedPlace();
    final dark =
        where != null &&
        sunAltitude(_clock(), where.$1, where.$2) < nightSunAltitude;
    if (dark != _dark || (where != null) != _placeKnown) {
      _dark = dark;
      _placeKnown = where != null;
      notifyListeners();
    }
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }
}

/// Makes the [NightMode] reachable from every screen.
class NightScope extends InheritedNotifier<NightMode> {
  const NightScope({super.key, required NightMode mode, required super.child})
    : super(notifier: mode);

  static NightMode? of(BuildContext context) =>
      context.dependOnInheritedWidgetOfExactType<NightScope>()?.notifier;
}

/// The red light around the whole app, when on.
class NightFilter extends StatelessWidget {
  const NightFilter({super.key, required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    final mode = NightScope.of(context);
    return mode != null && mode.active ? redNight(child) : child;
  }
}

/// Red unless the whole app already is (screens that are always red, like reporting).
Widget alwaysRed(BuildContext context, Widget child) =>
    (NightScope.of(context)?.active ?? false) ? child : redNight(child);

/// The moon in the app bar: tap switches the red light, long press offers "automatisch".
class NightButton extends StatelessWidget {
  const NightButton({super.key});

  @override
  Widget build(BuildContext context) {
    final mode = NightScope.of(context);
    if (mode == null) return const SizedBox.shrink();
    final label = tr(
      'Rotlicht (lange drücken für mehr)',
      'Red light (long press for more)',
    );
    // No tooltip: it would take the long press.
    return Semantics(
      button: true,
      toggled: mode.active,
      label: label,
      child: InkResponse(
        key: const Key('night-button'),
        radius: 22,
        onTap: mode.toggle,
        onLongPress: () => showNightSettings(context, mode),
        child: Padding(
          padding: const EdgeInsets.all(8),
          child: Icon(
            mode.active ? Icons.dark_mode : Icons.dark_mode_outlined,
            semanticLabel: label,
          ),
        ),
      ),
    );
  }
}

Future<void> showNightSettings(
  BuildContext context,
  NightMode mode,
) => showModalBottomSheet<void>(
  context: context,
  builder: (context) => ListenableBuilder(
    listenable: mode,
    builder: (context, _) => SafeArea(
      child: RadioGroup<NightSetting>(
        groupValue: mode.setting,
        onChanged: (s) {
          if (s != null) mode.choose(s);
        },
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              title: Text(tr('Rotlicht', 'Red light')),
              subtitle: Text(
                tr(
                  'Alles in Rot hält die Augen an die Dunkelheit gewöhnt. Dreh auch die Helligkeit des Handys herunter.',
                  'Everything in red keeps your eyes used to the dark. Turn the phone’s brightness down too.',
                ),
              ),
            ),
            RadioListTile<NightSetting>(
              value: NightSetting.off,
              title: Text(tr('Aus', 'Off')),
            ),
            RadioListTile<NightSetting>(
              value: NightSetting.on,
              title: Text(tr('Ein', 'On')),
            ),
            RadioListTile<NightSetting>(
              value: NightSetting.auto,
              title: Text(tr('Automatisch nachts', 'Automatically at night')),
              subtitle: Text(
                mode.setting == NightSetting.auto && !mode.placeKnown
                    ? tr(
                        'Wähle unter „Himmel“ einen Ort, dann geht es nach der Sonne.',
                        'Choose a place under “Sky”, then it follows the sun.',
                      )
                    : tr(
                        'An, sobald die Sonne am Ort unter „Himmel“ unter −6° steht.',
                        'On once the sun is below −6° at the place under “Sky”.',
                      ),
              ),
            ),
          ],
        ),
      ),
    ),
  ),
);
