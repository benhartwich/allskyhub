// SharedSky in the app (SPEC §10), the same as on the web (start_choose.html, station_new.html,
// station.html): the way in (a camera or not → which software → join or switch), "Kamera
// verbinden" for indi-allsky, an image address (Allsky and others) or other software, and a
// station's page. Creating an account stays on the web (Cloudflare Turnstile); the app opens
// it in its in-app browser and signs in afterwards.

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:geolocator/geolocator.dart';
import 'package:image_picker/image_picker.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api/hub_client.dart';
import '../design.dart';
import '../l10n.dart';
import '../platform/location.dart';
import 'cameras_screen.dart';
import 'join_screen.dart';
import 'late_fireball_screens.dart';
import 'network_screens.dart';

/// The answers of the second question, and what "Kamera verbinden" sends for them.
const _software = {
  'allsky': 'image-url',
  'indi-allsky': 'indi-allsky',
  'andere': 'other',
};

/// The phone's place as exact as it gets (the camera's place enters the trajectories;
/// [LocationSource] rounds to about 1 km); null without permission.
typedef PreciseLocation =
    Future<({double lat, double lon, double? elevation})?> Function();

Future<({double lat, double lon, double? elevation})?>
_platformPrecise() async {
  try {
    if (!await Geolocator.isLocationServiceEnabled()) return null;
    var permission = await Geolocator.checkPermission();
    if (permission == LocationPermission.denied) {
      permission = await Geolocator.requestPermission();
    }
    if (permission == LocationPermission.denied ||
        permission == LocationPermission.deniedForever) {
      return null;
    }
    final at = await Geolocator.getCurrentPosition(
      locationSettings: const LocationSettings(accuracy: LocationAccuracy.high),
    );
    return (
      lat: at.latitude,
      lon: at.longitude,
      elevation: at.altitude == 0 ? null : at.altitude,
    );
  } on Object {
    return null;
  }
}

void _open(Uri uri) => launchUrl(uri, mode: LaunchMode.inAppBrowserView);

/// One answer of the way in: a big card with a title and a sentence.
class _Choice extends StatelessWidget {
  const _Choice({
    super.key,
    required this.title,
    required this.text,
    required this.onTap,
    this.note,
  });

  final String title;
  final String text;
  final String? note;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(18),
          child: Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(title, style: theme.textTheme.titleMedium),
                    const SizedBox(height: 6),
                    Text(text, style: theme.textTheme.bodyMedium),
                    if (note != null) ...[
                      const SizedBox(height: 6),
                      Text(
                        note!,
                        style: theme.textTheme.bodySmall?.copyWith(
                          color: Sky.text2,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
              const Icon(Icons.chevron_right),
            ],
          ),
        ),
      ),
    );
  }
}

Widget _question(BuildContext context, String title, List<Widget> children) =>
    Scaffold(
      appBar: AppBar(),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(20, 4, 20, 24),
        children: [
          Semantics(
            header: true,
            child: Text(
              title,
              style: Theme.of(context).textTheme.headlineSmall,
            ),
          ),
          const SizedBox(height: 16),
          ...children,
        ],
      ),
    );

/// Step 1: "Hast du schon eine Allsky-Kamera?"
class CameraWizardScreen extends StatelessWidget {
  const CameraWizardScreen({super.key, required this.client, this.location});

  final HubClient client;
  final LocationSource? location;

  @override
  Widget build(BuildContext context) => _question(
    context,
    tr('Hast du schon eine Allsky-Kamera?', 'Do you have an all-sky camera?'),
    [
      _Choice(
        key: const Key('wizard-camera-yes'),
        title: tr('Ja, ich habe eine Kamera', 'Yes, I have a camera'),
        text: tr(
          'Mit allskyhub, Allsky, indi-allsky oder eigener Software.',
          'With allskyhub, Allsky, indi-allsky or your own software.',
        ),
        onTap: () => Navigator.of(context).push(
          MaterialPageRoute<bool>(
            builder: (_) =>
                SoftwareStepScreen(client: client, location: location),
          ),
        ),
      ),
      _Choice(
        key: const Key('wizard-camera-no'),
        title: tr('Nein, noch nicht', 'Not yet'),
        text: tr(
          'Was du kaufen musst, der Zusammenbau und die Speicherkarte, Schritt für Schritt.',
          'What to buy, putting it together and the memory card, step by step.',
        ),
        onTap: () => Navigator.of(context).push(
          MaterialPageRoute<void>(
            builder: (_) => OwnCameraScreen(client: client),
          ),
        ),
      ),
    ],
  );
}

/// Step 2: which software runs on the camera. Pops true when a camera or station was
/// added (for "Meine Kameras" to reload).
class SoftwareStepScreen extends StatelessWidget {
  const SoftwareStepScreen({super.key, required this.client, this.location});

  final HubClient client;
  final LocationSource? location;

  Future<void> _allskyhub(BuildContext context) async {
    if (client.token == null) {
      await Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => AccountGateScreen(client: client, forStation: false),
        ),
      );
      return;
    }
    final paired = await pairCamera(context, client);
    if (paired != null && context.mounted) Navigator.of(context).pop(true);
  }

  @override
  Widget build(BuildContext context) {
    Widget software(String key, String title, String text) => _Choice(
      key: Key('wizard-software-$key'),
      title: title,
      text: text,
      onTap: () async {
        final added = await Navigator.of(context).push<bool>(
          MaterialPageRoute(
            builder: (_) => GoalStepScreen(
              client: client,
              software: key,
              location: location,
            ),
          ),
        );
        if ((added ?? false) && context.mounted) {
          Navigator.of(context).pop(true);
        }
      },
    );
    return _question(
      context,
      tr('Welche Software läuft darauf?', 'Which software runs on it?'),
      [
        _Choice(
          key: const Key('wizard-software-allskyhub'),
          title: 'allskyhub',
          text: tr(
            'Das fertige Image für den Raspberry Pi. Weiter zum Koppeln.',
            'The ready image for the Raspberry Pi. On to pairing.',
          ),
          onTap: () => _allskyhub(context),
        ),
        software(
          'allsky',
          'Allsky',
          tr(
            'Die Software des AllskyTeams (Thomas Jacquin).',
            "The AllskyTeam's software (Thomas Jacquin).",
          ),
        ),
        software(
          'indi-allsky',
          'indi-allsky',
          tr('Mit INDI, von Aaron Morris.', 'With INDI, by Aaron Morris.'),
        ),
        software(
          'andere',
          tr('Andere oder eigene', 'Other or your own'),
          tr(
            'Gekaufte Kamera mit eigener Software oder selbst geschrieben.',
            'A bought camera with its own software, or written yourself.',
          ),
        ),
      ],
    );
  }
}

/// Step 3: join SharedSky or switch to allskyhub.
class GoalStepScreen extends StatelessWidget {
  const GoalStepScreen({
    super.key,
    required this.client,
    required this.software,
    this.location,
  });

  final HubClient client;

  /// allsky, indi-allsky or andere (the second question's answer).
  final String software;
  final LocationSource? location;

  @override
  Widget build(BuildContext context) {
    final beside = switch (software) {
      'allsky' => 'Allsky',
      'indi-allsky' => 'indi-allsky',
      _ => tr('deiner Software', 'your software'),
    };
    return _question(context, tr('Was möchtest du?', 'What would you like?'), [
      _Choice(
        key: const Key('wizard-join'),
        title: tr('Mitmachen (SharedSky)', 'Join (SharedSky)'),
        text: tr(
          'Deine Kamera bleibt, wie sie ist. Sie misst im Netz mit: Feuerkugeln, Meteore, Himmelshelligkeit.',
          'Your camera stays as it is. It measures in the network: fireballs, meteors, sky brightness.',
        ),
        note: tr('Dauert etwa 5 Minuten.', 'Takes about 5 minutes.'),
        onTap: () async {
          final added = await Navigator.of(context).push<bool>(
            MaterialPageRoute(
              builder: (_) => client.token == null
                  ? AccountGateScreen(client: client)
                  : StationConnectScreen(
                      client: client,
                      software: _software[software]!,
                      location: location,
                    ),
            ),
          );
          if ((added ?? false) && context.mounted) {
            Navigator.of(context).pop(true);
          }
        },
      ),
      _Choice(
        key: const Key('wizard-switch'),
        title: tr('Auf allskyhub umsteigen', 'Switch to allskyhub'),
        text: tr(
          'allskyhub läuft auf deinem Pi neben $beside und kann deine bisherigen Bilder übernehmen. Mit Livebild in der App, Alarmen und allem anderen.',
          'allskyhub runs on your Pi next to $beside and can take over your earlier pictures. With the live view in the app, alerts and everything else.',
        ),
        onTap: () => _open(client.site('/allsky')),
      ),
      TextButton(
        onPressed: () => _open(client.site('/mitmachen')),
        child: Text(tr('Was ist SharedSky?', 'What is SharedSky?')),
      ),
    ]);
  }
}

/// For guests: an account first. Creating one is on the web (behind Turnstile); signing
/// in is in the app.
class AccountGateScreen extends StatefulWidget {
  const AccountGateScreen({
    super.key,
    required this.client,
    this.forStation = true,
  });

  final HubClient client;
  final bool forStation;

  @override
  State<AccountGateScreen> createState() => _AccountGateScreenState();
}

class _AccountGateScreenState extends State<AccountGateScreen> {
  late final Future<HubInfo?> _info = widget.client.hubInfo();

  @override
  Widget build(BuildContext context) {
    final signIn = SignInScope.of(context);
    return Scaffold(
      appBar: AppBar(),
      body: FutureBuilder<HubInfo?>(
        future: _info,
        builder: (context, snapshot) {
          final info = snapshot.data;
          final open = info?.openSignup ?? false;
          return ListView(
            padding: const EdgeInsets.fromLTRB(20, 4, 20, 24),
            children: [
              Semantics(
                header: true,
                child: Text(
                  tr('Erst ein Konto', 'An account first'),
                  style: Theme.of(context).textTheme.headlineSmall,
                ),
              ),
              const SizedBox(height: 12),
              Text(
                widget.forStation
                    ? tr(
                        'Deine Station gehört zu deinem Konto. Leg es auf allskyhub.org an, bestätige den Link in der E-Mail und melde dich dann hier an.',
                        'Your station belongs to your account. Create it on allskyhub.org, confirm the link in the e-mail and then sign in here.',
                      )
                    : tr(
                        'Eine Kamera gehört zu deinem Konto. Leg es auf allskyhub.org an, bestätige den Link in der E-Mail und melde dich dann hier an.',
                        'A camera belongs to your account. Create it on allskyhub.org, confirm the link in the e-mail and then sign in here.',
                      ),
              ),
              const SizedBox(height: 20),
              FilledButton(
                key: const Key('gate-signup'),
                onPressed: snapshot.connectionState != ConnectionState.done
                    ? null
                    : () => _open(
                        info?.signupUrl ?? widget.client.site('/zugang'),
                      ),
                child: Text(
                  open
                      ? tr('Kostenloses Konto anlegen', 'Create a free account')
                      : tr('Zugang anfragen', 'Request access'),
                ),
              ),
              if (signIn != null) ...[
                const SizedBox(height: 10),
                OutlinedButton(
                  key: const Key('gate-sign-in'),
                  onPressed: signIn,
                  child: Text(
                    tr(
                      'Ich habe ein Konto · Anmelden',
                      'I have an account · Sign in',
                    ),
                  ),
                ),
              ],
            ],
          );
        },
      ),
    );
  }
}

/// "Kamera verbinden" (SPEC §10.1, §10.7). Pops true when the station exists.
class StationConnectScreen extends StatefulWidget {
  const StationConnectScreen({
    super.key,
    required this.client,
    required this.software,
    this.location,
    this.precise,
    this.pickPhoto,
  });

  final HubClient client;

  /// indi-allsky, image-url or other.
  final String software;
  final LocationSource? location;
  final PreciseLocation? precise;

  /// The proof picture (tests give their own); JPEG bytes and a file name.
  final Future<(Uint8List, String)?> Function()? pickPhoto;

  @override
  State<StationConnectScreen> createState() => _StationConnectScreenState();
}

class _StationConnectScreenState extends State<StationConnectScreen> {
  final _name = TextEditingController();
  final _url = TextEditingController();
  final _lat = TextEditingController();
  final _lon = TextEditingController();
  final _elevation = TextEditingController();
  final _timezone = TextEditingController();
  final _focal = TextEditingController();
  final _proof = TextEditingController();
  (Uint8List, String)? _photo;
  bool _terms = false;
  var _visibility = const StationVisibility();
  bool _sending = false;
  bool _locating = false;
  String? _error;

  bool get _byAddress => widget.software == 'image-url';

  @override
  void initState() {
    super.initState();
    if (_byAddress) {
      (widget.location ?? PlatformLocationSource()).timezone().then((tz) {
        if (mounted && tz != null && _timezone.text.isEmpty) {
          _timezone.text = tz;
        }
      }, onError: (Object _) {});
    }
  }

  @override
  void dispose() {
    for (final c in [
      _name,
      _url,
      _lat,
      _lon,
      _elevation,
      _timezone,
      _focal,
      _proof,
    ]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _here() async {
    setState(() => _locating = true);
    final at = await (widget.precise ?? _platformPrecise)();
    if (!mounted) return;
    setState(() {
      _locating = false;
      if (at == null) {
        _error = tr(
          'Der Standort ist nicht verfügbar. Trag die Koordinaten der Kamera bitte selbst ein.',
          "The location is not available. Please enter the camera's coordinates yourself.",
        );
        return;
      }
      _lat.text = at.lat.toStringAsFixed(5);
      _lon.text = at.lon.toStringAsFixed(5);
      if (at.elevation case final elevation?) {
        _elevation.text = elevation.toStringAsFixed(0);
      }
    });
  }

  Future<(Uint8List, String)?> _pick() async {
    final file = await ImagePicker().pickImage(
      source: ImageSource.gallery,
      maxWidth: 3000,
      maxHeight: 3000,
      imageQuality: 90,
    );
    return file == null ? null : (await file.readAsBytes(), file.name);
  }

  double? _number(TextEditingController c) =>
      double.tryParse(c.text.trim().replaceAll(',', '.'));

  Future<void> _send() async {
    setState(() {
      _sending = true;
      _error = null;
    });
    try {
      final created = await widget.client.createStation(
        software: widget.software,
        name: _name.text.trim(),
        terms: _terms,
        url: _url.text.trim(),
        latitude: _number(_lat),
        longitude: _number(_lon),
        elevationM: _number(_elevation),
        timezone: _timezone.text.trim(),
        focalMm: _focal.text.trim(),
        proofText: _proof.text.trim(),
        proof: _photo?.$1,
        proofName: _photo?.$2 ?? 'proof.jpg',
        visibility: _visibility,
      );
      if (!mounted) return;
      await Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => created.secret == null
              ? StationScreen(client: widget.client, station: created.station)
              : StationAccessScreen(created: created),
        ),
      );
      if (mounted) Navigator.of(context).pop(true);
    } on HubException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } on Object {
      if (mounted) {
        setState(
          () => _error = tr(
            'Der Hub ist gerade nicht erreichbar.',
            'The hub cannot be reached right now.',
          ),
        );
      }
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    InputDecoration field(String label, {String? hint}) =>
        InputDecoration(labelText: label, hintText: hint);
    final title = switch (widget.software) {
      'indi-allsky' => tr('indi-allsky verbinden', 'Connect indi-allsky'),
      'image-url' => tr(
        'Über die Bild-Adresse verbinden',
        'Connect by the image address',
      ),
      _ => tr('Eigene Software verbinden', 'Connect your own software'),
    };
    return Scaffold(
      appBar: AppBar(title: Text(title)),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(20, 8, 20, 32),
        children: [
          Text(switch (widget.software) {
            'indi-allsky' => tr(
              'Danach zeigt die App drei Werte, die du in indi-allsky unter Konfiguration → SyncAPI einträgst. An der Kamera installierst du nichts.',
              'Then the app shows three values to enter in indi-allsky under Config → SyncAPI. Nothing to install on the camera.',
            ),
            'image-url' => tr(
              'Der Hub holt das neueste Bild deiner Kamera von ihrer Adresse ab, zum Beispiel deiner Allsky-Website. Ein Admin prüft die Station vorher von Hand.',
              "The hub fetches your camera's latest picture from its address, for example your Allsky website. An admin checks the station by hand first.",
            ),
            _ => tr(
              'Danach zeigt die App eine Station-ID und ein Geheimnis für die offene Schnittstelle.',
              'Then the app shows a station ID and a secret for the open interface.',
            ),
          }),
          const SizedBox(height: 16),
          TextField(
            key: const Key('station-name'),
            controller: _name,
            textInputAction: TextInputAction.next,
            decoration: field(
              tr('Name der Station', 'Name of the station'),
              hint: tr(
                'z. B. Sternwarte Musterberg',
                'e.g. Hilltop Observatory',
              ),
            ),
          ),
          if (_byAddress) ...[
            const SizedBox(height: 12),
            TextField(
              key: const Key('station-url'),
              controller: _url,
              keyboardType: TextInputType.url,
              autocorrect: false,
              decoration: field(
                tr(
                  'Adresse des neuesten Bilds',
                  'Address of the latest picture',
                ),
                hint: 'https://…/image.jpg',
              ),
            ),
            const SizedBox(height: 16),
            Text(
              tr('Ort der Kamera', "The camera's place"),
              style: theme.textTheme.titleSmall,
            ),
            const SizedBox(height: 4),
            Text(
              tr(
                'So genau wie möglich: Er geht in die Bahnen der Feuerkugeln ein. Öffentlich steht er nur gerundet.',
                'As exact as you can: it goes into the fireball trajectories. Publicly it is only shown rounded.',
              ),
              style: theme.textTheme.bodySmall,
            ),
            const SizedBox(height: 8),
            OutlinedButton.icon(
              key: const Key('station-here'),
              onPressed: _locating ? null : _here,
              icon: const Icon(Icons.my_location),
              label: Text(
                tr(
                  'Ich stehe bei der Kamera: Standort übernehmen',
                  "I'm at the camera: use this location",
                ),
              ),
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                Expanded(
                  child: TextField(
                    key: const Key('station-lat'),
                    controller: _lat,
                    keyboardType: const TextInputType.numberWithOptions(
                      decimal: true,
                      signed: true,
                    ),
                    decoration: field(tr('Breite', 'Latitude')),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: TextField(
                    key: const Key('station-lon'),
                    controller: _lon,
                    keyboardType: const TextInputType.numberWithOptions(
                      decimal: true,
                      signed: true,
                    ),
                    decoration: field(tr('Länge', 'Longitude')),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _elevation,
                    keyboardType: TextInputType.number,
                    decoration: InputDecoration(
                      labelText: tr('Höhe in m', 'Elevation in m'),
                      helperText: tr('optional', 'optional'),
                    ),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: TextField(
                    controller: _focal,
                    keyboardType: const TextInputType.numberWithOptions(
                      decimal: true,
                    ),
                    decoration: InputDecoration(
                      labelText: tr('Brennweite in mm', 'Focal length in mm'),
                      helperText: tr('optional', 'optional'),
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            TextField(
              key: const Key('station-timezone'),
              controller: _timezone,
              decoration: field(
                tr('Zeitzone', 'Time zone'),
                hint: 'Europe/Vienna',
              ),
            ),
            const SizedBox(height: 16),
            Text(
              tr(
                'Nachweis, dass es deine Kamera ist',
                'Proof that it is your camera',
              ),
              style: theme.textTheme.titleSmall,
            ),
            const SizedBox(height: 4),
            Text(
              tr(
                'Ein Satz, ein Link auf deine Website oder ein Screenshot der Einstellungsseite deiner Kamera, auf dem die Adresse steht.',
                "A sentence, a link to your website or a screenshot of your camera's settings page showing the address.",
              ),
              style: theme.textTheme.bodySmall,
            ),
            const SizedBox(height: 8),
            TextField(
              key: const Key('station-proof'),
              controller: _proof,
              minLines: 2,
              maxLines: 5,
              decoration: field(tr('Nachweis', 'Proof')),
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                OutlinedButton.icon(
                  onPressed: () async {
                    final photo = await (widget.pickPhoto ?? _pick)();
                    if (photo != null && mounted) {
                      setState(() => _photo = photo);
                    }
                  },
                  icon: const Icon(Icons.image_outlined),
                  label: Text(
                    _photo == null
                        ? tr('Bild wählen', 'Choose a picture')
                        : tr('Anderes Bild', 'Another picture'),
                  ),
                ),
                if (_photo != null) ...[
                  const SizedBox(width: 12),
                  Expanded(
                    child: Text(
                      _photo!.$2,
                      overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.bodySmall,
                    ),
                  ),
                ],
              ],
            ),
          ],
          const SizedBox(height: 16),
          Text(
            tr('Was öffentlich zu sehen ist', 'What the public sees'),
            style: Theme.of(context).textTheme.titleSmall,
          ),
          StationVisibilitySwitches(
            visibility: _visibility,
            onChanged: (v) => setState(() => _visibility = v),
          ),
          Text(
            tr(
              'Das kannst du später bei der Station ändern.',
              'You can change this later on the station.',
            ),
            style: Theme.of(context).textTheme.bodySmall,
          ),
          const SizedBox(height: 8),
          CheckboxListTile(
            key: const Key('station-terms'),
            contentPadding: EdgeInsets.zero,
            controlAffinity: ListTileControlAffinity.leading,
            value: _terms,
            onChanged: (v) => setState(() => _terms = v ?? false),
            title: Text(
              tr(
                'Ich stimme den Teilnahmebedingungen zu: Meine Bilder bleiben meine, die Messwerte werden mit dem Namen meiner Station unter CC BY 4.0 geteilt.',
                'I agree to the terms: my pictures stay mine, the measurements are shared under CC BY 4.0 with my station’s name.',
              ),
            ),
          ),
          TextButton(
            onPressed: () => _open(widget.client.site('/mitmachen')),
            child: Text(tr('Teilnahmebedingungen lesen', 'Read the terms')),
          ),
          if (_error != null)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Semantics(
                liveRegion: true,
                child: Text(
                  _error!,
                  key: const Key('station-error'),
                  style: TextStyle(color: theme.colorScheme.error),
                ),
              ),
            ),
          const SizedBox(height: 12),
          FilledButton(
            key: const Key('station-create'),
            onPressed: _sending ? null : _send,
            child: Text(
              _byAddress
                  ? tr('Zur Prüfung schicken', 'Send for review')
                  : tr('Station anlegen', 'Create station'),
            ),
          ),
        ],
      ),
    );
  }
}

/// The values to enter in the camera's software, shown this once.
class StationAccessScreen extends StatelessWidget {
  const StationAccessScreen({super.key, required this.created});

  final StationCreated created;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final station = created.station;
    final indi = station.software == 'indi-allsky';
    Widget value(String label, String text, Key key) => Card(
      margin: const EdgeInsets.only(bottom: 10),
      child: ListTile(
        title: Text(label, style: theme.textTheme.labelMedium),
        subtitle: SelectableText(text, key: key),
        trailing: IconButton(
          tooltip: tr('$label kopieren', 'Copy $label'),
          icon: const Icon(Icons.copy),
          onPressed: () async {
            await Clipboard.setData(ClipboardData(text: text));
            if (context.mounted) {
              ScaffoldMessenger.of(
                context,
              ).showSnackBar(SnackBar(content: Text(tr('Kopiert', 'Copied'))));
            }
          },
        ),
      ),
    );
    return Scaffold(
      appBar: AppBar(title: Text(station.name)),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(20, 8, 20, 32),
        children: [
          Text(
            indi
                ? tr('In indi-allsky eintragen', 'Enter in indi-allsky')
                : tr('Zugangsdaten', 'Access'),
            style: theme.textTheme.headlineSmall,
          ),
          const SizedBox(height: 8),
          Text(
            indi
                ? tr(
                    'In indi-allsky unter Konfiguration → SyncAPI: einschalten, Bilder auf „jedes Bild“ stellen und diese drei Werte eintragen.',
                    'In indi-allsky under Config → SyncAPI: switch it on, set images to “every image” and enter these three values.',
                  )
                : tr(
                    'Jede Anfrage trägt „Authorization: Bearer <Station-ID>:<Geheimnis>“. Alles Weitere steht in der Entwickler-Doku, mit einem Testmodus.',
                    'Every request carries “Authorization: Bearer <station ID>:<secret>”. Everything else is in the developer docs, with a test mode.',
                  ),
          ),
          const SizedBox(height: 16),
          if (indi) ...[
            value('Base URL', created.baseUrl ?? '', const Key('access-base')),
            value('Username', station.id, const Key('access-user')),
            value('API Key', created.secret ?? '', const Key('access-secret')),
          ] else ...[
            value(
              tr('Station-ID', 'Station ID'),
              station.id,
              const Key('access-user'),
            ),
            value(
              tr('Geheimnis', 'Secret'),
              created.secret ?? '',
              const Key('access-secret'),
            ),
            TextButton(
              onPressed: () => _open(Uri.parse(created.docsUrl)),
              child: Text(
                tr('Entwickler-Doku öffnen', 'Open the developer docs'),
              ),
            ),
          ],
          const SizedBox(height: 8),
          Card(
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(Sky.radius),
              side: const BorderSide(color: Sky.warn),
            ),
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Text(
                tr(
                  'Das Geheimnis ist nur jetzt sichtbar. Notier es dir; sonst kannst du auf der Seite der Station ein neues erzeugen.',
                  'The secret is visible only now. Note it down; otherwise you can make a new one on the station’s page.',
                ),
              ),
            ),
          ),
          const SizedBox(height: 16),
          FilledButton(
            key: const Key('access-done'),
            onPressed: () => Navigator.of(context).pop(),
            child: Text(tr('Fertig', 'Done')),
          ),
        ],
      ),
    );
  }
}

String _stateText(Station s) => switch (s.state) {
  'connected' => tr('verbunden', 'connected'),
  'waiting' => tr('noch nichts empfangen', 'nothing received yet'),
  'pending' => tr('wartet auf Prüfung', 'waiting for review'),
  'declined' => tr('abgelehnt', 'declined'),
  'paused' => tr('pausiert', 'paused'),
  _ => s.state,
};

Color _stateColor(Station s) => switch (s.state) {
  'connected' => Sky.aurora,
  'declined' => Sky.danger,
  _ => Sky.warn,
};

/// A station on "Meine Kameras".
class StationCard extends StatelessWidget {
  const StationCard({
    super.key,
    required this.client,
    required this.station,
    required this.onChanged,
  });

  final HubClient client;
  final Station station;
  final Future<void> Function() onChanged;

  @override
  Widget build(BuildContext context) {
    final software = switch (station.software) {
      'indi-allsky' => 'indi-allsky',
      'image-url' => tr('Bild-Adresse', 'image address'),
      _ => tr('eigene Software', 'own software'),
    };
    return Card(
      key: Key('station-${station.id}'),
      child: ListTile(
        leading: const Icon(Icons.hub_outlined, size: 30),
        title: Text(station.name),
        subtitle: Text(
          '$software · ${tr('heute', 'tonight')} ${station.framesToday} '
          '${tr('Bilder', 'pictures')}, ${station.meteorsToday} '
          '${tr('Meteore', 'meteors')}',
        ),
        trailing: Chip(
          label: Text(_stateText(station)),
          side: BorderSide(color: _stateColor(station)),
        ),
        onTap: () async {
          await Navigator.of(context).push(
            MaterialPageRoute<void>(
              builder: (_) => StationScreen(client: client, station: station),
            ),
          );
          await onChanged();
        },
      ),
    );
  }
}

/// A station's page: its state, the latest picture, a new secret, removing it.
class StationScreen extends StatefulWidget {
  const StationScreen({super.key, required this.client, required this.station});

  final HubClient client;
  final Station station;

  @override
  State<StationScreen> createState() => _StationScreenState();
}

class _StationScreenState extends State<StationScreen> {
  late Station _station = widget.station;
  late Future<NetworkPart?> _part = _loadPart();
  bool _saving = false;

  Future<NetworkPart?> _loadPart() =>
      widget.client.networkPart(_station.id).catchError((Object _) => null);

  Future<void> _refresh() async {
    try {
      final next = await widget.client.station(_station.id);
      if (mounted) {
        setState(() {
          _station = next;
          _part = _loadPart();
        });
      }
    } on Object {
      // keep what we have
    }
  }

  Future<void> _show(StationVisibility visibility) async {
    setState(() => _saving = true);
    try {
      final next = await widget.client.setStationVisibility(
        _station.id,
        visibility,
      );
      if (mounted) setState(() => _station = next);
    } on HubException catch (e) {
      if (mounted) _say(e.message);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<String?> _password(String action) => showDialog<String>(
    context: context,
    builder: (context) {
      final controller = TextEditingController();
      return AlertDialog(
        title: Text(action),
        content: TextField(
          key: const Key('station-password'),
          controller: controller,
          obscureText: true,
          autofocus: true,
          decoration: InputDecoration(
            labelText: tr('Dein Passwort', 'Your password'),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: Text(tr('Abbrechen', 'Cancel')),
          ),
          FilledButton(
            key: const Key('station-password-ok'),
            onPressed: () => Navigator.pop(context, controller.text),
            child: Text(tr('Weiter', 'Continue')),
          ),
        ],
      );
    },
  );

  void _say(String text) =>
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));

  Future<void> _renew() async {
    final password = await _password(tr('Neues Geheimnis', 'New secret'));
    if (password == null || !mounted) return;
    try {
      final secret = await widget.client.renewStationSecret(
        _station.id,
        password,
      );
      if (!mounted) return;
      await Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => StationAccessScreen(
            created: StationCreated(
              station: _station,
              secret: secret,
              baseUrl: _station.software == 'indi-allsky'
                  ? widget.client.site('/indi-allsky').toString()
                  : null,
              docsUrl: widget.client.site('/entwickler/connect').toString(),
            ),
          ),
        ),
      );
    } on HubException catch (e) {
      if (mounted) _say(e.message);
    }
  }

  Future<void> _remove() async {
    final password = await _password(tr('Station entfernen', 'Remove station'));
    if (password == null || !mounted) return;
    try {
      await widget.client.deleteStation(_station.id, password);
      if (mounted) Navigator.of(context).pop();
    } on HubException catch (e) {
      if (mounted) _say(e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final s = _station;
    Widget fact(String label, String value) => ListTile(
      dense: true,
      title: Text(label, style: theme.textTheme.labelMedium),
      subtitle: Text(value, style: theme.textTheme.bodyMedium),
    );
    return Scaffold(
      appBar: AppBar(title: Text(s.name)),
      body: RefreshIndicator(
        onRefresh: _refresh,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
          children: [
            Row(
              children: [
                Chip(
                  key: const Key('station-state'),
                  label: Text(_stateText(s)),
                  side: BorderSide(color: _stateColor(s)),
                ),
              ],
            ),
            if (s.state == 'pending')
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 8),
                child: Text(
                  tr(
                    'Ein Admin sieht sich deine Station an und meldet sich per E-Mail. Bis dahin holt der Hub nichts ab.',
                    'An admin looks at your station and answers by e-mail. Until then the hub fetches nothing.',
                  ),
                ),
              ),
            if (s.reason case final reason? when reason.isNotEmpty)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 8),
                child: Text(reason),
              ),
            if (s.hasImage)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 8),
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(12),
                  child: Image.network(
                    widget.client
                        .stationImageUrl(
                          s.id,
                          bust: s.lastSeenAt?.millisecondsSinceEpoch,
                        )
                        .toString(),
                    headers: widget.client.authHeaders,
                    fit: BoxFit.cover,
                    semanticLabel: tr(
                      'Letztes Bild von ${s.name}',
                      'Latest picture from ${s.name}',
                    ),
                    errorBuilder: (_, _, _) => const SizedBox.shrink(),
                  ),
                ),
              ),
            Card(
              child: Column(
                children: [
                  fact(
                    tr('Ausrichtung', 'Orientation'),
                    s.oriented
                        ? tr(
                            'an den Sternen ausgerichtet',
                            'aligned to the stars',
                          )
                        : tr(
                            'wartet auf eine klare Nacht',
                            'waiting for a clear night',
                          ),
                  ),
                  fact(
                    tr('Uhr', 'Clock'),
                    s.clockTrusted
                        ? tr('gestellt', 'set')
                        : tr(
                            'noch nicht bestätigt: Meteore zählen für Ströme und Nacht',
                            'not confirmed yet: meteors count for showers and the night',
                          ),
                  ),
                  fact(
                    tr('Heute Nacht', 'Tonight'),
                    tr(
                      '${s.framesToday} Bilder, ${s.meteorsToday} Meteore',
                      '${s.framesToday} pictures, ${s.meteorsToday} meteors',
                    ),
                  ),
                  if (s.url != null) fact(tr('Adresse', 'Address'), s.url!),
                ],
              ),
            ),
            FutureBuilder(
              future: _part,
              builder: (context, snapshot) {
                final part = snapshot.data;
                if (part == null) return const SizedBox.shrink();
                return Column(
                  children: [
                    NetworkPartCard(part: part),
                    if (part.comparison case final c?)
                      SkyComparisonCard(comparison: c, name: s.name),
                  ],
                );
              },
            ),
            if (s.visibility case final visibility?)
              StationVisibilityCard(
                key: const Key('station-visibility'),
                visibility: visibility,
                publicUrl: s.publicUrl,
                publicFrom: s.publicFrom,
                waiting: s.publicWaiting,
                onChanged: _saving ? null : _show,
              ),
            const SizedBox(height: 16),
            OutlinedButton.icon(
              key: const Key('station-late'),
              onPressed: () => Navigator.of(context).push(
                MaterialPageRoute<void>(
                  builder: (_) => LateFireballsScreen(
                    client: widget.client,
                    deviceId: s.id,
                    name: s.name,
                  ),
                ),
              ),
              icon: const Icon(Icons.auto_awesome),
              label: Text(
                tr('Feuerkugel nachreichen', 'Send a fireball later'),
              ),
            ),
            const SizedBox(height: 8),
            if (s.software != 'image-url')
              OutlinedButton.icon(
                key: const Key('station-renew'),
                onPressed: _renew,
                icon: const Icon(Icons.key),
                label: Text(tr('Neues Geheimnis', 'New secret')),
              ),
            const SizedBox(height: 8),
            OutlinedButton.icon(
              key: const Key('station-remove'),
              onPressed: _remove,
              icon: Icon(Icons.delete_outline, color: theme.colorScheme.error),
              label: Text(tr('Station entfernen', 'Remove station')),
            ),
            const SizedBox(height: 8),
            Text(
              tr(
                'Beim Entfernen werden Bilder, genauer Ort und Geheimnis sofort gelöscht. Messwerte, die schon in gemeinsame Ergebnisse eingeflossen sind, bleiben dort.',
                'Removing deletes the pictures, the exact place and the secret at once. Measurements already in joint results stay there.',
              ),
              style: theme.textTheme.bodySmall,
            ),
          ],
        ),
      ),
    );
  }
}
