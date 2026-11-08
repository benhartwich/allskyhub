// Fireballs sent later (SPEC §10.8): the owner of a station or camera sends an older frame
// of a fireball, marks its track, and the hub checks it against the stars. The same steps
// as the hub's web page /nachreichen/<id>; the words for a failed check come from the hub.

import 'dart:async';
import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';

import '../api/hub_client.dart';
import '../l10n.dart';

String _when(DateTime t) =>
    '${t.day.toString().padLeft(2, '0')}.${t.month.toString().padLeft(2, '0')}.${t.year}, '
    '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}:'
    '${t.second.toString().padLeft(2, '0')}';

String _deg(double v) => appLocale.languageCode == 'de'
    ? v.toStringAsFixed(2).replaceAll('.', ',')
    : v.toStringAsFixed(2);

({String text, Color color}) _state(BuildContext context, LateFireball f) {
  final scheme = Theme.of(context).colorScheme;
  return switch (f.state) {
    'track' => (text: tr('Spur fehlt', 'Track missing'), color: scheme.outline),
    'checking' => (
      text: tr('wird geprüft', 'being checked'),
      color: scheme.secondary,
    ),
    'accepted' => (text: tr('angenommen', 'accepted'), color: Colors.green),
    _ => (text: tr('nicht bestanden', 'not passed'), color: scheme.error),
  };
}

/// The owner's fireballs sent later for one station or camera, and the way to send one.
class LateFireballsScreen extends StatefulWidget {
  const LateFireballsScreen({
    super.key,
    required this.client,
    required this.deviceId,
    required this.name,
  });

  final HubClient client;
  final String deviceId;
  final String name;

  @override
  State<LateFireballsScreen> createState() => _LateFireballsScreenState();
}

class _LateFireballsScreenState extends State<LateFireballsScreen> {
  late Future<LateFireballs> _list = widget.client.lateFireballs(
    widget.deviceId,
  );

  Future<void> _reload() async {
    final next = widget.client.lateFireballs(widget.deviceId);
    setState(() => _list = next);
    await next.catchError(
      (Object _) => LateFireballs(
        sizes: const [],
        zone: '',
        perDay: 0,
        uploads: const [],
      ),
    );
  }

  Future<void> _open(Widget screen) async {
    await Navigator.of(
      context,
    ).push(MaterialPageRoute<void>(builder: (_) => screen));
    if (mounted) await _reload();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(
        title: Text(tr('Feuerkugel nachreichen', 'Send a fireball later')),
      ),
      body: FutureBuilder(
        future: _list,
        builder: (context, snapshot) {
          if (snapshot.hasError) {
            return Center(child: Text(snapshot.error.toString()));
          }
          final list = snapshot.data;
          if (list == null) {
            return const Center(child: CircularProgressIndicator());
          }
          return RefreshIndicator(
            onRefresh: _reload,
            child: ListView(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
              children: [
                Text(
                  tr(
                    'Eine ältere Aufnahme einer Feuerkugel, bis 12 Monate zurück. Der Hub prüft sie an den Sternen: Passt der Himmel zur Ausrichtung von ${widget.name}, zählt sie wie eine live gemeldete und wird mit anderen Kameras zur selben Zeit vermessen.',
                    'An older recording of a fireball, up to 12 months back. The hub checks it against the stars: if the sky fits the orientation of ${widget.name}, it counts like one sent live and is measured with other cameras at the same time.',
                  ),
                ),
                const SizedBox(height: 16),
                if (list.sizes.isEmpty)
                  Text(
                    tr(
                      'Die Kamera ist noch nicht an den Sternen ausgerichtet. Nach der ersten klaren Nacht geht es.',
                      'The camera is not aligned to the stars yet. After the first clear night it works.',
                    ),
                    style: theme.textTheme.bodyMedium?.copyWith(
                      color: theme.colorScheme.error,
                    ),
                  )
                else
                  FilledButton.icon(
                    key: const Key('late-new'),
                    icon: const Icon(Icons.add_photo_alternate_outlined),
                    label: Text(tr('Aufnahme wählen', 'Choose a recording')),
                    onPressed: () => _open(
                      LateSendScreen(
                        client: widget.client,
                        deviceId: widget.deviceId,
                        list: list,
                      ),
                    ),
                  ),
                const SizedBox(height: 16),
                for (final f in list.uploads)
                  Card(
                    child: ListTile(
                      key: Key('late-${f.id}'),
                      title: Text(_when(f.local)),
                      subtitle: Text(
                        f.state == 'failed' && f.reasonText != null
                            ? f.reasonText!
                            : f.pairs.isNotEmpty
                            ? tr(
                                'vermessen mit ${f.pairs.first.other}',
                                'measured with ${f.pairs.first.other}',
                              )
                            : '',
                      ),
                      trailing: Chip(
                        label: Text(_state(context, f).text),
                        side: BorderSide(color: _state(context, f).color),
                      ),
                      onTap: () => _open(
                        f.state == 'track'
                            ? LateTrackScreen(
                                client: widget.client,
                                deviceId: widget.deviceId,
                                fireball: f,
                              )
                            : LateFireballScreen(
                                client: widget.client,
                                deviceId: widget.deviceId,
                                id: f.id,
                              ),
                      ),
                    ),
                  ),
              ],
            ),
          );
        },
      ),
    );
  }
}

/// Step 1: the frame, its time and exposure.
class LateSendScreen extends StatefulWidget {
  const LateSendScreen({
    super.key,
    required this.client,
    required this.deviceId,
    required this.list,
    this.pick,
  });

  final HubClient client;
  final String deviceId;
  final LateFireballs list;

  /// The picture (bytes, file name); the system's picker unless a test gives one.
  final Future<(List<int>, String)?> Function()? pick;

  @override
  State<LateSendScreen> createState() => _LateSendScreenState();
}

class _LateSendScreenState extends State<LateSendScreen> {
  final _time = TextEditingController();
  late final _zone = TextEditingController(text: widget.list.zone);
  final _exposure = TextEditingController(text: '30');
  List<int>? _image;
  String _name = '';
  ({int width, int height})? _size;
  bool _sending = false;
  String? _error;

  @override
  void dispose() {
    _time.dispose();
    _zone.dispose();
    _exposure.dispose();
    super.dispose();
  }

  bool get _sizeFits => _size != null && widget.list.sizes.contains(_size);

  Future<(List<int>, String)?> _pickFromGallery() async {
    // The full frame, not scaled or recompressed: stars are single pixels.
    final file = await ImagePicker().pickImage(
      source: ImageSource.gallery,
      requestFullMetadata: false,
    );
    return file == null ? null : (await file.readAsBytes(), file.name);
  }

  Future<void> _choose() async {
    final picked = await (widget.pick ?? _pickFromGallery)();
    if (picked == null || !mounted) return;
    final (bytes, name) = picked;
    ({int width, int height})? size;
    try {
      final codec = await ui.instantiateImageCodec(Uint8List.fromList(bytes));
      final frame = await codec.getNextFrame();
      size = (width: frame.image.width, height: frame.image.height);
      frame.image.dispose();
    } on Object {
      size = null;
    }
    if (!mounted) return;
    setState(() {
      _image = bytes;
      _name = name;
      _size = size;
      _error = null;
      if (timeFromName(name) case final t? when _time.text.isEmpty) {
        _time.text = _shown(t);
      }
    });
  }

  String _shown(DateTime t) => wallClock(t).replaceFirst('T', ' ');

  DateTime? _taken() =>
      DateTime.tryParse(_time.text.trim().replaceFirst(' ', 'T'));

  Future<void> _send() async {
    final taken = _taken();
    final exposure = double.tryParse(
      _exposure.text.trim().replaceAll(',', '.'),
    );
    if (_image == null || taken == null || exposure == null) {
      setState(
        () => _error = tr(
          'Bitte Bild, Zeit (JJJJ-MM-TT HH:MM:SS) und Belichtung angeben.',
          'Please give the image, the time (YYYY-MM-DD HH:MM:SS) and the exposure.',
        ),
      );
      return;
    }
    setState(() {
      _sending = true;
      _error = null;
    });
    try {
      final f = await widget.client.sendLateFireball(
        widget.deviceId,
        image: _image!,
        imageName: _name.isEmpty ? 'image.jpg' : _name,
        taken: taken,
        zone: _zone.text.trim(),
        exposureS: exposure,
      );
      if (!mounted) return;
      await Navigator.of(context).pushReplacement(
        MaterialPageRoute<void>(
          builder: (_) => LateTrackScreen(
            client: widget.client,
            deviceId: widget.deviceId,
            fireball: f,
          ),
        ),
      );
    } on HubException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final sizes = widget.list.sizes
        .map((s) => '${s.width} × ${s.height}')
        .join(' / ');
    return Scaffold(
      appBar: AppBar(
        title: Text(tr('Feuerkugel nachreichen', 'Send a fireball later')),
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
        children: [
          OutlinedButton.icon(
            key: const Key('late-pick'),
            icon: const Icon(Icons.image_outlined),
            label: Text(
              _image == null
                  ? tr('Aufnahme wählen', 'Choose a recording')
                  : _name,
            ),
            onPressed: _sending ? null : _choose,
          ),
          const SizedBox(height: 4),
          Text(
            _size == null
                ? tr(
                    'Das volle Bild in der Größe, die die Kamera sendet ($sizes), JPEG oder PNG, höchstens 8 MB. Kein Ausschnitt und kein Stack.',
                    'The full image in the size the camera sends ($sizes), JPEG or PNG, at most 8 MB. Not a crop and not a stack.',
                  )
                : _sizeFits
                ? tr(
                    '${_size!.width} × ${_size!.height}, passt zur Kamera',
                    '${_size!.width} × ${_size!.height}, fits the camera',
                  )
                : tr(
                    '${_size!.width} × ${_size!.height}: Die Kamera sendet $sizes. Bitte das volle Originalbild wählen.',
                    '${_size!.width} × ${_size!.height}: the camera sends $sizes. Please choose the full original image.',
                  ),
            style: theme.textTheme.bodySmall?.copyWith(
              color: _size != null && !_sizeFits
                  ? theme.colorScheme.error
                  : null,
            ),
          ),
          const SizedBox(height: 16),
          TextField(
            key: const Key('late-time'),
            controller: _time,
            keyboardType: TextInputType.datetime,
            decoration: InputDecoration(
              labelText: tr(
                'Mitte der Belichtung (Ortszeit)',
                'Middle of the exposure (local time)',
              ),
              hintText: '2026-11-03 21:45:12',
              helperText: tr(
                'Aus dem Dateinamen, wenn er die Zeit trägt.',
                'From the file name when it carries the time.',
              ),
            ),
          ),
          const SizedBox(height: 8),
          TextField(
            key: const Key('late-zone'),
            controller: _zone,
            decoration: InputDecoration(labelText: tr('Zeitzone', 'Time zone')),
          ),
          const SizedBox(height: 8),
          TextField(
            key: const Key('late-exposure'),
            controller: _exposure,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: InputDecoration(
              labelText: tr('Belichtung (Sekunden)', 'Exposure (seconds)'),
            ),
          ),
          const SizedBox(height: 8),
          Text(
            tr(
              'Die Sterne prüfen die Uhrzeit auf etwa 2 Minuten, nicht das Datum: Das ist deine Angabe. Höchstens ${widget.list.perDay} Aufnahmen am Tag.',
              'The stars check the time to about 2 minutes, not the date: that is your word. At most ${widget.list.perDay} recordings a day.',
            ),
            style: theme.textTheme.bodySmall,
          ),
          if (_error case final e?) ...[
            const SizedBox(height: 12),
            Text(e, style: TextStyle(color: theme.colorScheme.error)),
          ],
          const SizedBox(height: 16),
          FilledButton(
            key: const Key('late-send'),
            onPressed:
                _sending || _image == null || (_size != null && !_sizeFits)
                ? null
                : _send,
            child: Text(tr('Weiter zur Spur', 'On to the track')),
          ),
        ],
      ),
    );
  }
}

/// Step 2: the track, 2 to 32 points along the streak; zoom with two fingers.
class LateTrackScreen extends StatefulWidget {
  const LateTrackScreen({
    super.key,
    required this.client,
    required this.deviceId,
    required this.fireball,
  });

  final HubClient client;
  final String deviceId;
  final LateFireball fireball;

  @override
  State<LateTrackScreen> createState() => _LateTrackScreenState();
}

class _LateTrackScreenState extends State<LateTrackScreen> {
  final _zoom = TransformationController();
  late final List<List<double>> _points = [
    for (final p in widget.fireball.track) [...p],
  ];
  bool _sending = false;

  @override
  void initState() {
    super.initState();
    _zoom.addListener(() => setState(() {}));
  }

  @override
  void dispose() {
    _zoom.dispose();
    super.dispose();
  }

  void _tap(TapUpDetails d, Size box) {
    if (_points.length >= 32) return;
    final f = widget.fireball;
    final x = d.localPosition.dx / box.width * f.width;
    final y = d.localPosition.dy / box.height * f.height;
    if (x < 0 || y < 0 || x > f.width || y > f.height) return;
    setState(() => _points.add([(x * 10).round() / 10, (y * 10).round() / 10]));
  }

  Future<void> _check() async {
    setState(() => _sending = true);
    try {
      final f = await widget.client.setLateTrack(
        widget.deviceId,
        widget.fireball.id,
        _points,
      );
      if (!mounted) return;
      await Navigator.of(context).pushReplacement(
        MaterialPageRoute<void>(
          builder: (_) => LateFireballScreen(
            client: widget.client,
            deviceId: widget.deviceId,
            id: f.id,
          ),
        ),
      );
    } on HubException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(e.message)));
      }
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final f = widget.fireball;
    final theme = Theme.of(context);
    final scale = _zoom.value.getMaxScaleOnAxis();
    return Scaffold(
      appBar: AppBar(title: Text(tr('Spur markieren', 'Mark the track'))),
      body: Column(
        children: [
          Expanded(
            child: Center(
              child: InteractiveViewer(
                transformationController: _zoom,
                maxScale: 12,
                child: AspectRatio(
                  aspectRatio: f.width / f.height,
                  child: LayoutBuilder(
                    builder: (context, box) => GestureDetector(
                      key: const Key('late-frame'),
                      onTapUp: (d) => _tap(d, box.biggest),
                      child: Stack(
                        fit: StackFit.expand,
                        children: [
                          Image.network(
                            widget.client
                                .lateImageUrl(widget.deviceId, f.id)
                                .toString(),
                            headers: widget.client.authHeaders,
                            fit: BoxFit.fill,
                            semanticLabel: tr('Die Aufnahme', 'The recording'),
                            errorBuilder: (_, _, _) =>
                                const ColoredBox(color: Colors.black),
                          ),
                          CustomPaint(
                            painter: _TrackPainter(
                              _points,
                              f.width,
                              f.height,
                              theme.colorScheme.primary,
                              scale,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(
                  tr(
                    'Tippe von einem Ende der Spur zum anderen, 2 bis 32 Punkte (${_points.length}). Die Richtung ist egal. Mit zwei Fingern zoomen.',
                    'Tap from one end of the track to the other, 2 to 32 points (${_points.length}). The direction does not matter. Zoom with two fingers.',
                  ),
                  style: theme.textTheme.bodySmall,
                ),
                const SizedBox(height: 8),
                Row(
                  children: [
                    Expanded(
                      child: OutlinedButton(
                        key: const Key('late-undo'),
                        onPressed: _points.isEmpty
                            ? null
                            : () => setState(_points.removeLast),
                        child: Text(
                          tr(
                            'Letzten Punkt entfernen',
                            'Remove the last point',
                          ),
                        ),
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: FilledButton(
                        key: const Key('late-check'),
                        onPressed: _sending || _points.length < 2
                            ? null
                            : _check,
                        child: Text(tr('Prüfen lassen', 'Have it checked')),
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _TrackPainter extends CustomPainter {
  _TrackPainter(this.points, this.width, this.height, this.color, this.scale);

  final List<List<double>> points;
  final int width;
  final int height;
  final Color color;
  final double scale;

  @override
  void paint(Canvas canvas, Size size) {
    // The marks keep their size on screen at any zoom.
    final stroke = 2 / scale;
    final paint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = stroke;
    final at = [
      for (final p in points)
        Offset(p[0] / width * size.width, p[1] / height * size.height),
    ];
    for (var i = 1; i < at.length; i++) {
      canvas.drawLine(at[i - 1], at[i], paint);
    }
    for (final p in at) {
      canvas.drawCircle(p, 8 / scale, paint);
    }
  }

  @override
  bool shouldRepaint(_TrackPainter old) =>
      old.points.length != points.length ||
      old.scale != scale ||
      old.color != color;
}

/// One fireball sent later: its check, what it was measured with, a new time, deleting.
class LateFireballScreen extends StatefulWidget {
  const LateFireballScreen({
    super.key,
    required this.client,
    required this.deviceId,
    required this.id,
    this.every = const Duration(seconds: 5),
  });

  final HubClient client;
  final String deviceId;
  final String id;

  /// How often it asks again while the hub checks.
  final Duration every;

  @override
  State<LateFireballScreen> createState() => _LateFireballScreenState();
}

class _LateFireballScreenState extends State<LateFireballScreen> {
  LateFireball? _f;
  String? _error;
  Timer? _timer;
  final _time = TextEditingController();
  final _zone = TextEditingController();
  final _exposure = TextEditingController();

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _timer?.cancel();
    _time.dispose();
    _zone.dispose();
    _exposure.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final f = await widget.client.lateFireball(widget.deviceId, widget.id);
      if (!mounted) return;
      final first = _f == null;
      setState(() {
        _f = f;
        _error = null;
        if (first || f.state != 'failed') {
          _time.text = wallClock(f.local).replaceFirst('T', ' ');
          _exposure.text = f.exposureS.toStringAsFixed(
            f.exposureS % 1 == 0 ? 0 : 1,
          );
        }
      });
      if (_zone.text.isEmpty) {
        final list = await widget.client.lateFireballs(widget.deviceId);
        if (mounted) _zone.text = list.zone;
      }
      _timer?.cancel();
      if (f.state == 'checking') _timer = Timer(widget.every, _load);
    } on HubException catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  Future<void> _retime() async {
    final taken = DateTime.tryParse(_time.text.trim().replaceFirst(' ', 'T'));
    final exposure = double.tryParse(
      _exposure.text.trim().replaceAll(',', '.'),
    );
    if (taken == null || exposure == null) return;
    try {
      final f = await widget.client.setLateTime(
        widget.deviceId,
        widget.id,
        taken: taken,
        zone: _zone.text.trim(),
        exposureS: exposure,
      );
      if (!mounted) return;
      setState(() => _f = f);
      _timer?.cancel();
      _timer = Timer(widget.every, _load);
    } on HubException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(e.message)));
      }
    }
  }

  Future<void> _delete() async {
    final sure = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(tr('Löschen?', 'Delete?')),
        content: Text(
          tr(
            'Die Aufnahme und jede Bahn, die sie mit anderen Kameras ergab, verschwinden.',
            'The recording and every path it made with other cameras go.',
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: Text(tr('Abbrechen', 'Cancel')),
          ),
          FilledButton(
            key: const Key('late-delete-ok'),
            onPressed: () => Navigator.pop(context, true),
            child: Text(tr('Löschen', 'Delete')),
          ),
        ],
      ),
    );
    if (sure != true || !mounted) return;
    try {
      await widget.client.deleteLateFireball(widget.deviceId, widget.id);
      if (mounted) Navigator.of(context).pop();
    } on HubException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(e.message)));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final f = _f;
    return Scaffold(
      appBar: AppBar(
        title: Text(
          f == null ? tr('Nachgereicht', 'Sent later') : _when(f.local),
        ),
      ),
      body: f == null
          ? Center(
              child: _error == null
                  ? const CircularProgressIndicator()
                  : Text(_error!),
            )
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
                children: [
                  Row(
                    children: [
                      Chip(
                        key: const Key('late-state'),
                        label: Text(_state(context, f).text),
                        side: BorderSide(color: _state(context, f).color),
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  if (f.state == 'checking')
                    Text(
                      tr(
                        'Der Hub sucht die Sterne. Das dauert meist unter einer Minute.',
                        'The hub looks for the stars. That mostly takes less than a minute.',
                      ),
                    ),
                  if (f.state == 'failed' && f.reasonText != null)
                    Text(f.reasonText!),
                  if (f.state == 'accepted') ...[
                    Text(
                      tr(
                        'Sternprobe: ${f.stars} Sterne, der Himmel liegt ${_deg(f.offsetDeg ?? 0)}° neben der Ausrichtung der Kamera${f.orientationUntil != null ? ', wie sie vor dem Verstellen war' : ''}.',
                        'Star check: ${f.stars} stars, the sky lies ${_deg(f.offsetDeg ?? 0)}° from the camera’s orientation${f.orientationUntil != null ? ' before it was moved' : ''}.',
                      ),
                    ),
                    const SizedBox(height: 8),
                    Text(
                      f.moonConfirmed
                          ? tr(
                              'Der Mond steht im Bild dort, wo er an diesem Tag stand: Auch das Datum stimmt.',
                              'The moon is in the image where it stood that day: the date is right too.',
                            )
                          : tr(
                              'Die Sterne prüfen die Uhrzeit auf etwa 2 Minuten, nicht das Datum: Das ist deine Angabe.',
                              'The stars check the time to about 2 minutes, not the date: that is your word.',
                            ),
                      style: theme.textTheme.bodySmall,
                    ),
                    const SizedBox(height: 8),
                    if (f.pairs.isEmpty)
                      Text(
                        tr(
                          'Noch keine zweite Kamera zur selben Zeit. Reicht eine andere Kamera diese Feuerkugel nach, wird neu abgeglichen.',
                          'No second camera at the same time yet. If another camera sends this fireball later, it is matched again.',
                        ),
                      )
                    else
                      for (final p in f.pairs)
                        ListTile(
                          leading: const Icon(Icons.timeline),
                          title: Text(
                            tr('Bahn mit ${p.other}', 'Path with ${p.other}'),
                          ),
                          subtitle: Text(
                            '${p.highKm.round()} → ${p.lowKm.round()} km',
                          ),
                        ),
                    if (f.hidden)
                      Text(
                        tr(
                          'Ein Admin hat diese Feuerkugel ausgeblendet. Sie zählt nirgends.',
                          'An admin hid this fireball. It counts nowhere.',
                        ),
                        style: TextStyle(color: theme.colorScheme.error),
                      ),
                  ],
                  const SizedBox(height: 12),
                  ClipRRect(
                    borderRadius: BorderRadius.circular(12),
                    child: Image.network(
                      widget.client
                          .lateImageUrl(widget.deviceId, f.id)
                          .toString(),
                      headers: widget.client.authHeaders,
                      semanticLabel: tr('Die Aufnahme', 'The recording'),
                      errorBuilder: (_, _, _) => const SizedBox.shrink(),
                    ),
                  ),
                  if (f.state == 'failed') ...[
                    const SizedBox(height: 16),
                    Text(
                      tr(
                        'Zeit ändern und erneut prüfen',
                        'Change the time and check again',
                      ),
                      style: theme.textTheme.titleMedium,
                    ),
                    TextField(
                      key: const Key('late-retime'),
                      controller: _time,
                      decoration: InputDecoration(
                        labelText: tr(
                          'Mitte der Belichtung (Ortszeit)',
                          'Middle of the exposure (local time)',
                        ),
                      ),
                    ),
                    TextField(
                      controller: _zone,
                      decoration: InputDecoration(
                        labelText: tr('Zeitzone', 'Time zone'),
                      ),
                    ),
                    TextField(
                      controller: _exposure,
                      keyboardType: const TextInputType.numberWithOptions(
                        decimal: true,
                      ),
                      decoration: InputDecoration(
                        labelText: tr(
                          'Belichtung (Sekunden)',
                          'Exposure (seconds)',
                        ),
                      ),
                    ),
                    const SizedBox(height: 8),
                    Wrap(
                      spacing: 8,
                      children: [
                        FilledButton(
                          key: const Key('late-retime-send'),
                          onPressed: _retime,
                          child: Text(tr('Erneut prüfen', 'Check again')),
                        ),
                        OutlinedButton(
                          key: const Key('late-retrack'),
                          onPressed: () =>
                              Navigator.of(context).pushReplacement(
                                MaterialPageRoute<void>(
                                  builder: (_) => LateTrackScreen(
                                    client: widget.client,
                                    deviceId: widget.deviceId,
                                    fireball: f,
                                  ),
                                ),
                              ),
                          child: Text(
                            tr('Spur neu markieren', 'Mark the track again'),
                          ),
                        ),
                      ],
                    ),
                  ],
                  const SizedBox(height: 16),
                  OutlinedButton.icon(
                    key: const Key('late-delete'),
                    onPressed: _delete,
                    icon: Icon(
                      Icons.delete_outline,
                      color: theme.colorScheme.error,
                    ),
                    label: Text(tr('Löschen', 'Delete')),
                  ),
                ],
              ),
            ),
    );
  }
}
