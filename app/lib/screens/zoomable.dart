// A picture that zooms like the web's lightbox: pinch, double tap at a point (and again
// to go back), pan when zoomed. Tells its parent when it is zoomed, so a PageView can stop
// swiping to the next picture meanwhile.

import 'package:flutter/material.dart';

class Zoomable extends StatefulWidget {
  const Zoomable({super.key, required this.child, this.onZoomChanged});

  final Widget child;
  final ValueChanged<bool>? onZoomChanged;

  @override
  State<Zoomable> createState() => _ZoomableState();
}

class _ZoomableState extends State<Zoomable>
    with SingleTickerProviderStateMixin {
  static const _doubleTapScale = 2.5;
  final _controller = TransformationController();
  late final _animation = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 200),
  );
  Animation<Matrix4>? _tween;
  Offset _tapAt = Offset.zero;
  bool _zoomed = false;

  @override
  void initState() {
    super.initState();
    _animation.addListener(() {
      if (_tween != null) _controller.value = _tween!.value;
    });
    _controller.addListener(_report);
  }

  void _report() {
    final zoomed = _controller.value.getMaxScaleOnAxis() > 1.01;
    if (zoomed != _zoomed) {
      _zoomed = zoomed;
      widget.onZoomChanged?.call(zoomed);
    }
  }

  void _toggle() {
    final Matrix4 end;
    if (_zoomed) {
      end = Matrix4.identity();
    } else {
      const s = _doubleTapScale;
      end = Matrix4.identity()
        ..translateByDouble(-_tapAt.dx * (s - 1), -_tapAt.dy * (s - 1), 0, 1)
        ..scaleByDouble(s, s, 1, 1);
    }
    _tween = Matrix4Tween(
      begin: _controller.value,
      end: end,
    ).animate(CurvedAnimation(parent: _animation, curve: Curves.easeOut));
    _animation.forward(from: 0);
  }

  @override
  void dispose() {
    _controller.dispose();
    _animation.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => GestureDetector(
    onDoubleTapDown: (d) => _tapAt = d.localPosition,
    onDoubleTap: _toggle,
    child: InteractiveViewer(
      transformationController: _controller,
      maxScale: 8,
      child: Center(child: widget.child),
    ),
  );
}

/// A gallery of pictures in full screen: swipe between them (not while zoomed), the
/// position as "4 / 46" and a caption per picture.
class PhotoPager extends StatefulWidget {
  const PhotoPager({
    super.key,
    required this.count,
    required this.initial,
    required this.image,
    required this.title,
    this.caption,
  });

  final int count;
  final int initial;
  final Widget Function(int index) image;
  final String Function(int index) title;
  final String Function(int index)? caption;

  @override
  State<PhotoPager> createState() => _PhotoPagerState();
}

class _PhotoPagerState extends State<PhotoPager> {
  late final _pages = PageController(initialPage: widget.initial);
  late int _index = widget.initial;
  bool _zoomed = false;

  @override
  void dispose() {
    _pages.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final caption = widget.caption?.call(_index);
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
        title: Text(widget.title(_index)),
        actions: [
          if (widget.count > 1)
            Center(
              child: Padding(
                padding: const EdgeInsets.only(right: 16),
                child: Text(
                  '${_index + 1} / ${widget.count}',
                  key: const Key('photo-position'),
                  style: const TextStyle(color: Colors.white70),
                ),
              ),
            ),
        ],
      ),
      body: Column(
        children: [
          Expanded(
            child: PageView.builder(
              controller: _pages,
              itemCount: widget.count,
              physics: _zoomed
                  ? const NeverScrollableScrollPhysics()
                  : const PageScrollPhysics(),
              onPageChanged: (i) => setState(() {
                _index = i;
                _zoomed = false;
              }),
              itemBuilder: (context, i) => Zoomable(
                key: ValueKey(i),
                onZoomChanged: (z) => setState(() => _zoomed = z),
                child: widget.image(i),
              ),
            ),
          ),
          if (caption != null && caption.isNotEmpty)
            Padding(
              padding: const EdgeInsets.all(12),
              child: Text(
                caption,
                textAlign: TextAlign.center,
                style: const TextStyle(color: Colors.white70),
              ),
            ),
        ],
      ),
    );
  }
}
