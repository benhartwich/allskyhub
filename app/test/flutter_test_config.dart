// Runs before every test file: the tests read the German texts unless they switch to
// English themselves (lib/l10n.dart).

import 'dart:async';

import 'package:allskyhub_app/l10n.dart';

Future<void> testExecutable(FutureOr<void> Function() testMain) async {
  appEnglish = false;
  await testMain();
}
