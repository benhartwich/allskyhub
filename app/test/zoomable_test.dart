import 'package:allskyhub_app/screens/zoomable.dart';
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

Widget _pager() => MaterialApp(
  home: PhotoPager(
    count: 3,
    initial: 0,
    title: (i) => 'Bild $i',
    caption: (i) => 'Unterschrift $i',
    image: (i) => SizedBox(
      width: 300,
      height: 300,
      child: ColoredBox(color: Colors.blue, child: Text('pic $i')),
    ),
  ),
);

Future<void> _doubleTap(WidgetTester tester) async {
  final center = tester.getCenter(find.byType(PageView));
  await tester.tapAt(center);
  await tester.pump(kDoubleTapMinTime);
  await tester.tapAt(center);
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('swipes through the pictures and shows the position', (
    tester,
  ) async {
    await tester.pumpWidget(_pager());
    expect(find.text('1 / 3'), findsOneWidget);
    expect(find.text('Unterschrift 0'), findsOneWidget);
    await tester.fling(find.byType(PageView), const Offset(-400, 0), 1000);
    await tester.pumpAndSettle();
    expect(find.text('2 / 3'), findsOneWidget);
    expect(find.text('Bild 1'), findsOneWidget);
  });

  testWidgets('double tap zooms; while zoomed a swipe pans instead of paging', (
    tester,
  ) async {
    await tester.pumpWidget(_pager());
    await _doubleTap(tester);
    final viewer = tester.widget<PageView>(find.byType(PageView));
    expect(viewer.physics, isA<NeverScrollableScrollPhysics>());
    await tester.fling(find.byType(PageView), const Offset(-400, 0), 1000);
    await tester.pumpAndSettle();
    expect(find.text('1 / 3'), findsOneWidget);

    await _doubleTap(tester); // back to the whole picture
    await tester.fling(find.byType(PageView), const Offset(-400, 0), 1000);
    await tester.pumpAndSettle();
    expect(find.text('2 / 3'), findsOneWidget);
  });
}
