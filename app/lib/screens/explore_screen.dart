// "Entdecken": the public cameras for everyone, with or without an account. The globe
// shows where they are and where it is night, below come the newest detections
// ("Gerade passiert") and the cameras themselves; the search filters by name and place.

import 'package:flutter/material.dart';

import '../api/hub_client.dart';
import '../design.dart';
import '../l10n.dart';
import 'camera_status.dart';
import 'globe.dart';
import 'public_camera_screen.dart';

class ExploreScreen extends StatefulWidget {
  const ExploreScreen({super.key, required this.client, this.onSignIn});

  /// Any client: public calls need no token.
  final HubClient client;

  /// For guests: the sign-in button in the app bar.
  final VoidCallback? onSignIn;

  @override
  State<ExploreScreen> createState() => _ExploreScreenState();
}

class _ExploreScreenState extends State<ExploreScreen> {
  late Future<(List<PublicCamera>, List<PublicEvent>)> _data = _load();
  String _query = '';

  Future<(List<PublicCamera>, List<PublicEvent>)> _load() async {
    final cameras = widget.client.publicCameras();
    final feed = widget.client.publicFeed().catchError(
      (Object _) => <PublicEvent>[],
    );
    return (await cameras, await feed);
  }

  Future<void> _reload() async {
    final next = _load();
    setState(() => _data = next);
    await next;
  }

  void _open(String slug, String title) => Navigator.of(context).push(
    MaterialPageRoute<void>(
      builder: (_) =>
          PublicCameraScreen(client: widget.client, slug: slug, title: title),
    ),
  );

  bool _matches(PublicCamera c) {
    final q = _query.trim().toLowerCase();
    return q.isEmpty ||
        c.title.toLowerCase().contains(q) ||
        c.place.toLowerCase().contains(q);
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: Text(tr('Entdecken', 'Explore')),
      actions: [
        if (widget.onSignIn case final signIn?)
          TextButton(
            key: const Key('sign-in'),
            onPressed: signIn,
            child: Text(tr('Anmelden', 'Sign in')),
          ),
      ],
    ),
    body: RefreshIndicator(
      onRefresh: _reload,
      child: FutureBuilder(
        future: _data,
        builder: (context, snapshot) {
          if (snapshot.hasError) {
            return ListView(
              padding: const EdgeInsets.all(32),
              children: [
                Text(
                  tr(
                    'Die öffentlichen Kameras konnten nicht geladen werden.',
                    'Could not load the public cameras.',
                  ),
                  textAlign: TextAlign.center,
                ),
                TextButton(
                  onPressed: _reload,
                  child: Text(tr('Erneut versuchen', 'Try again')),
                ),
              ],
            );
          }
          final data = snapshot.data;
          if (data == null) {
            return const Center(child: CircularProgressIndicator());
          }
          final (all, feed) = data;
          final cameras = all.where(_matches).toList();
          final onGlobe = [
            for (final c in cameras)
              if (c.lat != null && c.lon != null) c,
          ];
          final theme = Theme.of(context);
          return ListView(
            padding: const EdgeInsets.only(bottom: 24),
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 4, 16, 0),
                child: TextField(
                  key: const Key('explore-search'),
                  onChanged: (v) => setState(() => _query = v),
                  decoration: InputDecoration(
                    prefixIcon: const Icon(Icons.search),
                    hintText: tr('Kamera oder Ort', 'Camera or place'),
                  ),
                ),
              ),
              SizedBox(
                height: 340,
                child: Globe(
                  markers: [
                    for (final c in onGlobe)
                      GlobeMarker(
                        lat: c.lat!,
                        lon: c.lon!,
                        night: c.online && (c.night ?? false),
                      ),
                  ],
                  onTap: (i) => _open(onGlobe[i].slug, onGlobe[i].title),
                ),
              ),
              const _Legend(),
              if (feed.isNotEmpty) ...[
                _Heading(tr('Gerade passiert', 'Just happened')),
                SizedBox(
                  height: 150,
                  child: ListView.separated(
                    scrollDirection: Axis.horizontal,
                    padding: const EdgeInsets.symmetric(horizontal: 16),
                    itemCount: feed.length,
                    separatorBuilder: (_, _) => const SizedBox(width: 10),
                    itemBuilder: (context, i) => _FeedTile(
                      client: widget.client,
                      event: feed[i],
                      onTap: () => _open(feed[i].slug, feed[i].camera),
                    ),
                  ),
                ),
              ],
              _Heading(tr('Öffentliche Kameras', 'Public cameras')),
              if (all.isEmpty)
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 16),
                  child: Text(
                    tr(
                      'Noch keine öffentliche Kamera. Vielleicht wird deine die erste?',
                      'No public camera yet. Maybe yours will be the first?',
                    ),
                    style: theme.textTheme.bodyMedium,
                  ),
                )
              else if (cameras.isEmpty)
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 16),
                  child: Text(tr('Nichts gefunden.', 'Nothing found.')),
                ),
              for (final c in cameras)
                _CameraTile(
                  client: widget.client,
                  camera: c,
                  onTap: () => _open(c.slug, c.title),
                ),
            ],
          );
        },
      ),
    ),
  );
}

class _Heading extends StatelessWidget {
  const _Heading(this.text);

  final String text;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.fromLTRB(16, 20, 16, 10),
    child: Text(text, style: Theme.of(context).textTheme.titleMedium),
  );
}

class _Legend extends StatelessWidget {
  const _Legend();

  @override
  Widget build(BuildContext context) {
    final style = Theme.of(context).textTheme.bodySmall;
    Widget dot(Color color, String text) => Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(Icons.circle, size: 10, color: color),
        const SizedBox(width: 6),
        Text(text, style: style),
      ],
    );
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16),
      child: Wrap(
        spacing: 16,
        runSpacing: 4,
        children: [
          dot(Sky.aurora, tr('Nacht, live', 'Night, live')),
          dot(Sky.warn, tr('Tag oder offline', 'Day or offline')),
          Text(tr('Drehen, zoomen, antippen', 'Turn, zoom, tap'), style: style),
        ],
      ),
    );
  }
}

class _FeedTile extends StatelessWidget {
  const _FeedTile({
    required this.client,
    required this.event,
    required this.onTap,
  });

  final HubClient client;
  final PublicEvent event;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return SizedBox(
      width: 150,
      child: Card(
        margin: EdgeInsets.zero,
        clipBehavior: Clip.antiAlias,
        child: InkWell(
          onTap: onTap,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Image.network(
                  client.site(event.url).toString(),
                  fit: BoxFit.cover,
                  width: double.infinity,
                  errorBuilder: (_, _, _) =>
                      const ColoredBox(color: Colors.black),
                ),
              ),
              Padding(
                padding: const EdgeInsets.fromLTRB(8, 6, 8, 6),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(event.title, style: theme.textTheme.labelLarge),
                    Text(
                      '${formatAge(event.at)} · ${event.camera}',
                      style: theme.textTheme.bodySmall,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _CameraTile extends StatelessWidget {
  const _CameraTile({
    required this.client,
    required this.camera,
    required this.onTap,
  });

  final HubClient client;
  final PublicCamera camera;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final image = camera.imageAt;
    final state = !camera.online
        ? 'offline'
        : camera.night == true
        ? tr('Nacht', 'night')
        : camera.night == false
        ? tr('Tag', 'day')
        : 'online';
    return Card(
      margin: const EdgeInsets.fromLTRB(16, 0, 16, 12),
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: Row(
          children: [
            SizedBox(
              width: 96,
              height: 96,
              child: image == null
                  ? const ColoredBox(color: Colors.black)
                  : Image.network(
                      client
                          .site(
                            '/sky/${camera.slug}/latest.jpg'
                            '?thumb=1&t=${image.millisecondsSinceEpoch ~/ 1000}',
                          )
                          .toString(),
                      fit: BoxFit.cover,
                      errorBuilder: (_, _, _) =>
                          const ColoredBox(color: Colors.black),
                    ),
            ),
            Expanded(
              child: ListTile(
                title: Text(camera.title),
                subtitle: Text(
                  [
                    if (camera.place.isNotEmpty) camera.place,
                    state,
                    if (image != null) formatAge(image),
                  ].join(' · '),
                ),
                trailing: const Icon(Icons.chevron_right),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
