// The sky view (roadmap N6): hold the phone up and see what lies in that direction above
// the camera: constellations, moon and planets, and where last night's meteors flew.
//
// The phone's orientation comes from gravity (accelerometer) and the magnetic field
// (magnetometer): east = field × up, north = up × east, each in the phone's coordinates
// (x right, y to the top of the screen, z out of the screen). The back of the phone looks
// along −z; a direction is drawn where that ray would meet the screen (a pinhole camera
// with a fixed field of view). Magnetic north differs from true north by a few degrees in
// Europe; the view does not correct for it.
//
// AR: the phone's own camera runs behind the drawing (no picture is stored). Its field
// of view differs between phones, so it can be matched with a slider.

import 'dart:async';
import 'dart:math' as math;

import 'package:camera/camera.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:sensors_plus/sensors_plus.dart';

import '../api/hub_client.dart';
import '../design.dart';
import '../l10n.dart';

typedef Vec = ({double x, double y, double z});

Vec _cross(Vec a, Vec b) => (
  x: a.y * b.z - a.z * b.y,
  y: a.z * b.x - a.x * b.z,
  z: a.x * b.y - a.y * b.x,
);

Vec? _unit(Vec a) {
  final n = math.sqrt(a.x * a.x + a.y * a.y + a.z * a.z);
  return n < 1e-6 ? null : (x: a.x / n, y: a.y / n, z: a.z / n);
}

/// East, north and up of the world in the phone's coordinates.
class Pose {
  const Pose(this.east, this.north, this.up);

  final Vec east;
  final Vec north;
  final Vec up;

  /// From gravity (pointing up while the phone rests) and the magnetic field; null when
  /// they do not give a direction (in free fall, near a magnet, pointing at the pole).
  static Pose? from(Vec gravity, Vec magnetic) {
    final up = _unit(gravity);
    if (up == null) return null;
    final east = _unit(_cross(magnetic, up));
    if (east == null) return null;
    return Pose(east, _cross(up, east), up);
  }

  /// A sky direction in the phone's coordinates.
  Vec toPhone(SkyDir d) {
    final alt = d.alt * math.pi / 180, az = d.az * math.pi / 180;
    final e = math.cos(alt) * math.sin(az);
    final n = math.cos(alt) * math.cos(az);
    final u = math.sin(alt);
    return (
      x: e * east.x + n * north.x + u * up.x,
      y: e * east.y + n * north.y + u * up.y,
      z: e * east.z + n * north.z + u * up.z,
    );
  }

  /// Where the back of the phone points.
  SkyDir get looking {
    // −z of the phone in world terms: the world axes' z components, negated.
    final e = -east.z, n = -north.z, u = -up.z;
    return (
      alt: math.asin(u.clamp(-1.0, 1.0)) * 180 / math.pi,
      az: (math.atan2(e, n) * 180 / math.pi + 360) % 360,
    );
  }
}

/// The phone's orientation; the sensors by default, a fixed pose in tests.
abstract class PoseSource {
  Stream<Pose> get poses;
}

class SensorPoseSource implements PoseSource {
  /// Low-pass filter: the share of each new reading.
  static const _alpha = 0.15;

  @override
  Stream<Pose> get poses {
    late StreamController<Pose> out;
    StreamSubscription<AccelerometerEvent>? acc;
    StreamSubscription<MagnetometerEvent>? mag;
    Vec? g, m;
    Vec smooth(Vec? old, double x, double y, double z) => old == null
        ? (x: x, y: y, z: z)
        : (
            x: old.x + _alpha * (x - old.x),
            y: old.y + _alpha * (y - old.y),
            z: old.z + _alpha * (z - old.z),
          );
    void emit() {
      if (g == null || m == null) return;
      final pose = Pose.from(g!, m!);
      if (pose != null) out.add(pose);
    }

    out = StreamController<Pose>(
      onListen: () {
        acc =
            accelerometerEventStream(
              samplingPeriod: SensorInterval.uiInterval,
            ).listen((e) {
              g = smooth(g, e.x, e.y, e.z);
              emit();
            }, onError: out.addError);
        mag = magnetometerEventStream(
          samplingPeriod: SensorInterval.uiInterval,
        ).listen((e) => m = smooth(m, e.x, e.y, e.z), onError: out.addError);
      },
      onCancel: () async {
        await acc?.cancel();
        await mag?.cancel();
      },
    );
    return out.stream;
  }
}

List<String> get _names => appEnglish
    ? const ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
    : const ['N', 'NO', 'O', 'SO', 'S', 'SW', 'W', 'NW'];

String compassName(double az) => _names[((az + 22.5) % 360 ~/ 45)];

class SkyViewScreen extends StatefulWidget {
  const SkyViewScreen({
    super.key,
    required this.client,
    required this.camera,
    this.poseSource,
    this.useCamera = true,
  });

  final HubClient client;
  final Camera camera;
  final PoseSource? poseSource;

  /// The phone's camera behind the drawing (AR); off in tests.
  final bool useCamera;

  @override
  State<SkyViewScreen> createState() => _SkyViewScreenState();
}

class _SkyViewScreenState extends State<SkyViewScreen>
    with WidgetsBindingObserver {
  SkyViewData? _data;
  String? _error;
  Pose? _pose;
  bool _noSensors = false;
  StreamSubscription<Pose>? _sub;
  CameraController? _cam;
  bool _camFailed = false;
  bool _showCamera = true;
  bool _showLines = true;
  bool _showMeteors = true;
  bool _showOverflights = true;

  /// Vertical field of view of what the screen shows, in degrees.
  double _fov = 64;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    // The phone's axes are those of portrait; a turning screen would swap them.
    SystemChrome.setPreferredOrientations([DeviceOrientation.portraitUp]);
    if (widget.useCamera) _startCamera();
    _load();
    _sub = (widget.poseSource ?? SensorPoseSource()).poses.listen(
      (pose) {
        if (mounted) setState(() => _pose = pose);
      },
      onError: (Object _) {
        if (mounted) setState(() => _noSensors = true);
      },
    );
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _sub?.cancel();
    _cam?.dispose();
    SystemChrome.setPreferredOrientations([]);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // The camera is released while the app is in the background.
    if (!widget.useCamera) return;
    if (state == AppLifecycleState.inactive ||
        state == AppLifecycleState.paused) {
      final cam = _cam;
      _cam = null;
      cam?.dispose();
      if (mounted) setState(() {});
    } else if (state == AppLifecycleState.resumed && _cam == null) {
      _startCamera();
    }
  }

  Future<void> _startCamera() async {
    try {
      final cams = await availableCameras();
      final back = cams.where(
        (c) => c.lensDirection == CameraLensDirection.back,
      );
      if (back.isEmpty) throw CameraException('none', 'no back camera');
      final cam = CameraController(
        back.first,
        ResolutionPreset.medium,
        enableAudio: false,
      );
      await cam.initialize();
      if (!mounted) {
        await cam.dispose();
        return;
      }
      setState(() => _cam = cam);
    } on Exception {
      // No permission, no camera, or a plugin missing (tests): the drawing alone.
      if (mounted) setState(() => _camFailed = true);
    }
  }

  Future<void> _load() async {
    try {
      final data = await widget.client.skyView(widget.camera.id);
      if (mounted) setState(() => _data = data);
    } on HubException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } on Exception {
      if (mounted) {
        setState(
          () => _error = tr(
            'Keine Verbindung zum Hub.',
            'No connection to the hub.',
          ),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final pose = _pose;
    final data = _data;
    final looking = pose?.looking;
    final cam = _cam;
    final ar = cam != null && cam.value.isInitialized && _showCamera;
    final shadow = [
      const Shadow(color: Colors.black, blurRadius: 4),
      const Shadow(color: Colors.black, blurRadius: 8),
    ];
    return Scaffold(
      backgroundColor: Sky.bg,
      appBar: AppBar(title: Text(tr('Himmel', 'Sky'))),
      body: Stack(
        children: [
          if (ar)
            Positioned.fill(
              key: const Key('sky-camera'),
              child: ClipRect(
                child: FittedBox(
                  fit: BoxFit.cover,
                  child: SizedBox(
                    // The preview size is landscape; the screen is portrait.
                    width: cam.value.previewSize?.height ?? 480,
                    height: cam.value.previewSize?.width ?? 640,
                    child: CameraPreview(cam),
                  ),
                ),
              ),
            ),
          if (data != null && pose != null)
            Positioned.fill(
              child: CustomPaint(
                key: const Key('sky-view'),
                painter: SkyPainter(
                  data,
                  pose,
                  fov: ar ? _fov : SkyPainter.defaultFov,
                  lines: _showLines,
                  meteors: _showMeteors,
                  overflights: _showOverflights,
                  shadow: ar,
                  fontFamily: Theme.of(
                    context,
                  ).textTheme.bodyMedium?.fontFamily,
                ),
              ),
            ),
          Positioned(
            left: 8,
            right: 8,
            top: 8,
            child: Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                if (widget.useCamera && !_camFailed)
                  FilterChip(
                    key: const Key('sky-toggle-camera'),
                    label: Text(tr('Kamerabild', 'Camera view')),
                    selected: _showCamera,
                    onSelected: (v) => setState(() => _showCamera = v),
                  ),
                FilterChip(
                  label: Text(tr('Sternbilder', 'Constellations')),
                  selected: _showLines,
                  onSelected: (v) => setState(() => _showLines = v),
                ),
                FilterChip(
                  label: Text(tr('Meteore', 'Meteors')),
                  selected: _showMeteors,
                  onSelected: (v) => setState(() => _showMeteors = v),
                ),
                FilterChip(
                  key: const Key('sky-toggle-overflights'),
                  label: Text(tr('Überflüge', 'Overflights')),
                  selected: _showOverflights,
                  onSelected: (v) => setState(() => _showOverflights = v),
                ),
              ],
            ),
          ),
          Positioned(
            left: 16,
            right: 16,
            bottom: 24,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                if (ar)
                  Row(
                    children: [
                      Text(
                        tr('Bildwinkel', 'Field of view'),
                        style: theme.textTheme.bodySmall?.copyWith(
                          shadows: shadow,
                        ),
                      ),
                      Expanded(
                        child: Slider(
                          key: const Key('sky-fov'),
                          value: _fov,
                          min: 40,
                          max: 90,
                          onChanged: (v) => setState(() => _fov = v),
                        ),
                      ),
                    ],
                  ),
                Text(
                  _error ??
                      (_noSensors
                          ? tr(
                              'Dieses Telefon meldet keine Lage- oder Kompasssensoren.',
                              'This phone reports no motion or compass sensors.',
                            )
                          : data == null
                          ? tr('Lädt …', 'Loading …')
                          : looking == null
                          ? tr(
                              'Halte das Telefon in Richtung Himmel.',
                              'Hold the phone up to the sky.',
                            )
                          : tr(
                              'Blick nach ${compassName(looking.az)} '
                                  '(${looking.az.round()}°), '
                                  '${looking.alt.round()}° hoch. Letzte 12 Stunden: '
                                  'Meteore violett, Satelliten blau, Flugzeuge gelb.',
                              'Looking ${compassName(looking.az)} '
                                  '(${looking.az.round()}°), '
                                  '${looking.alt.round()}° up. Last 12 hours: '
                                  'meteors violet, satellites blue, aircraft yellow.',
                            )),
                  key: const Key('sky-hint'),
                  textAlign: TextAlign.center,
                  style: theme.textTheme.bodyMedium?.copyWith(
                    shadows: ar ? shadow : null,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class SkyPainter extends CustomPainter {
  SkyPainter(
    this.data,
    this.pose, {
    this.fov = defaultFov,
    this.lines = true,
    this.meteors = true,
    this.overflights = true,
    this.shadow = false,
    this.fontFamily,
  });

  final SkyViewData data;
  final Pose pose;

  /// Vertical field of view in degrees.
  final double fov;
  final bool lines;
  final bool meteors;
  final bool overflights;

  /// Dark edges under lines and text, readable over the camera picture.
  final bool shadow;

  /// The app's text font for the labels (a canvas knows no theme).
  final String? fontFamily;

  static const defaultFov = 60.0;

  static const _overflightColors = {
    'satellite': Sky.accent,
    'aircraft': Sky.warn,
  };

  Offset? _at(SkyDir d, Size size) {
    final p = pose.toPhone(d);
    if (p.z > -0.05) return null; // behind the phone or at its edge
    final f = size.height / 2 / math.tan(fov / 2 * math.pi / 180);
    return Offset(
      size.width / 2 + f * p.x / -p.z,
      size.height / 2 - f * p.y / -p.z,
    );
  }

  void _line(Canvas canvas, Size size, List<SkyDir> dirs, Paint paint) {
    Offset? last;
    for (final d in dirs) {
      final at = _at(d, size);
      if (at != null && last != null) canvas.drawLine(last, at, paint);
      last = at;
    }
  }

  void _text(Canvas canvas, Offset at, String text, TextStyle style) {
    style = style.copyWith(fontFamily: fontFamily);
    final tp = TextPainter(
      text: TextSpan(
        text: text,
        style: shadow
            ? style.copyWith(
                shadows: const [Shadow(color: Colors.black, blurRadius: 4)],
              )
            : style,
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    tp.paint(canvas, at - Offset(tp.width / 2, tp.height + 4));
  }

  @override
  void paint(Canvas canvas, Size size) {
    final linePaint = Paint()
      ..color = Sky.accent.withValues(alpha: .45)
      ..strokeWidth = 1.2;
    if (lines) {
      for (final line in data.lines) {
        _line(canvas, size, line, linePaint);
      }
    }
    final horizon = Paint()
      ..color = Sky.aurora.withValues(alpha: .6)
      ..strokeWidth = 2;
    _line(canvas, size, [
      for (var az = 0.0; az <= 360; az += 5) (alt: 0.0, az: az),
    ], horizon);
    for (final (i, name)
        in (appEnglish ? ['N', 'E', 'S', 'W'] : ['N', 'O', 'S', 'W']).indexed) {
      final at = _at((alt: 2.0, az: i * 90.0), size);
      if (at != null) {
        _text(
          canvas,
          at,
          name,
          const TextStyle(
            color: Sky.aurora,
            fontSize: 18,
            fontWeight: FontWeight.w700,
          ),
        );
      }
    }
    final meteor = Paint()
      ..color = Sky.accent2
      ..strokeWidth = 3
      ..strokeCap = StrokeCap.round;
    if (meteors) {
      for (final m in data.meteors) {
        _line(canvas, size, _track(m.start, m.end), meteor);
      }
    }
    if (overflights) {
      for (final o in data.overflights) {
        final color = _overflightColors[o.kind] ?? Sky.text2;
        final paint = Paint()
          ..color = color.withValues(alpha: .9)
          ..strokeWidth = 2
          ..strokeCap = StrokeCap.round;
        _line(canvas, size, _track(o.start, o.end), paint);
        final end = _at(o.end, size);
        if (end != null) {
          canvas.drawCircle(end, 3.5, Paint()..color = color);
          final name = o.name;
          if (name != null) {
            _text(canvas, end, name, TextStyle(color: color, fontSize: 11));
          }
        }
      }
    }
    for (final o in data.objects) {
      final at = _at(o.dir, size);
      if (at == null) continue;
      final constellation = o.kind == 'constellation';
      if (!constellation) {
        canvas.drawCircle(
          at,
          o.kind == 'moon' ? 9 : 5,
          Paint()..color = o.kind == 'moon' ? Sky.text : Sky.warn,
        );
      }
      _text(
        canvas,
        at,
        o.label,
        TextStyle(
          color: constellation ? Sky.text2 : Sky.text,
          fontSize: constellation ? 12 : 14,
          fontWeight: constellation ? FontWeight.w400 : FontWeight.w600,
        ),
      );
    }
    // Cross hairs in the middle.
    final centre = size.center(Offset.zero);
    final cross = Paint()
      ..color = Sky.text.withValues(alpha: .5)
      ..strokeWidth = 1;
    canvas.drawLine(
      centre - const Offset(12, 0),
      centre + const Offset(12, 0),
      cross,
    );
    canvas.drawLine(
      centre - const Offset(0, 12),
      centre + const Offset(0, 12),
      cross,
    );
  }

  /// Along the great circle, in a few steps, so long tracks bend like the sky.
  static List<SkyDir> _track(SkyDir start, SkyDir end) => [
    for (var i = 0; i <= 8; i++)
      (
        alt: start.alt + (end.alt - start.alt) * i / 8,
        az: start.az + (((end.az - start.az + 540) % 360) - 180) * i / 8,
      ),
  ];

  @override
  bool shouldRepaint(SkyPainter oldDelegate) =>
      oldDelegate.pose != pose ||
      oldDelegate.data != data ||
      oldDelegate.fov != fov ||
      oldDelegate.lines != lines ||
      oldDelegate.meteors != meteors ||
      oldDelegate.overflights != overflights ||
      oldDelegate.shadow != shadow ||
      oldDelegate.fontFamily != fontFamily;
}
