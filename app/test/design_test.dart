import 'package:allskyhub_app/design.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('pages end above the system bar at the bottom', (tester) async {
    tester.view.physicalSize = const Size(400, 800);
    tester.view.devicePixelRatio = 1;
    tester.view.padding = const FakeViewPadding(top: 24, bottom: 48);
    addTearDown(tester.view.reset);
    await tester.pumpWidget(
      MaterialApp(
        theme: skyTheme(),
        home: Scaffold(
          appBar: AppBar(title: const Text('Seite')),
          body: ListView(
            padding: const EdgeInsets.all(16),
            children: [for (var i = 0; i < 80; i++) Text('Zeile $i')],
          ),
        ),
      ),
    );
    await tester.drag(find.byType(ListView), const Offset(0, -5000));
    await tester.pumpAndSettle();
    // The list's own padding (16) sits above the bar (48), not under it.
    expect(
      tester.getBottomLeft(find.text('Zeile 79')).dy,
      lessThanOrEqualTo(800 - 48 - 16),
    );
  });
}
