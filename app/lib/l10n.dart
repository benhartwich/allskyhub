// The app's language (Benjamin, 2026-10-25): German on a phone set to German, English
// everywhere else. Each text sits in the code as a pair, German first, so a change to one
// is seen next to the other: `tr('Anmelden', 'Sign in')`.

import 'dart:ui';

/// Whether the app speaks English; read once from the phone's languages at start.
/// Tests set it themselves (test/flutter_test_config.dart).
bool appEnglish = !_prefersGerman(PlatformDispatcher.instance.locales);

bool _prefersGerman(List<Locale> locales) {
  // The first of the phone's languages that the app speaks decides.
  for (final l in locales) {
    if (l.languageCode == 'de') return true;
    if (l.languageCode == 'en') return false;
  }
  return false;
}

/// The text in the app's language.
String tr(String de, String en) => appEnglish ? en : de;

/// The language for Flutter's own texts (date picker, tooltips) and for the hub.
Locale get appLocale => Locale(appEnglish ? 'en' : 'de');

/// Short weekday names, Monday first.
List<String> get weekdaysShort => appEnglish
    ? const ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    : const ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'];

/// Day and month, "25.10." or "25 Oct".
String dayMonth(DateTime d) => appEnglish
    ? '${d.day} ${const ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][d.month - 1]}'
    : '${d.day.toString().padLeft(2, '0')}.${d.month.toString().padLeft(2, '0')}.';

/// A decimal number with the language's separator ("1,5" or "1.5").
String decimal(num value, int digits) {
  final text = value.toStringAsFixed(digits);
  return appEnglish ? text : text.replaceAll('.', ',');
}
