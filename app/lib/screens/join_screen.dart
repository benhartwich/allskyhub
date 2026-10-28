// "Mitmachen": what anyone can do for the night sky, and how to run an own camera.
// Reporting the sky brightness and sightings comes later (with its own reporter
// accounts); for now those entries say so.

import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api/hub_client.dart';
import '../design.dart';
import '../l10n.dart';

/// Lets screens deep in a guest's navigation open the sign-in (null when signed in).
class SignInScope extends InheritedWidget {
  const SignInScope({super.key, required this.onSignIn, required super.child});

  final VoidCallback onSignIn;

  static VoidCallback? of(BuildContext context) =>
      context.dependOnInheritedWidgetOfExactType<SignInScope>()?.onSignIn;

  @override
  bool updateShouldNotify(SignInScope old) => old.onSignIn != onSignIn;
}

class JoinScreen extends StatelessWidget {
  const JoinScreen({super.key, required this.client});

  final HubClient client;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    Widget soon(IconData icon, Color color, String title, String text) => Card(
      margin: const EdgeInsets.fromLTRB(16, 0, 16, 12),
      child: ListTile(
        leading: Icon(icon, color: color, size: 30),
        title: Text(title),
        subtitle: Text(text),
        trailing: Chip(label: Text(tr('bald', 'soon'))),
      ),
    );
    return Scaffold(
      appBar: AppBar(title: Text(tr('Mitmachen', 'Take part'))),
      body: ListView(
        padding: const EdgeInsets.only(top: 4, bottom: 24),
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
            child: Text(
              tr(
                'Hilf mit, den Nachthimmel zu beobachten und zu vermessen.',
                'Help to watch and measure the night sky.',
              ),
              style: theme.textTheme.bodyMedium,
            ),
          ),
          Card(
            margin: const EdgeInsets.fromLTRB(16, 0, 16, 12),
            clipBehavior: Clip.antiAlias,
            child: InkWell(
              key: const Key('join-own-camera'),
              onTap: () => Navigator.of(context).push(
                MaterialPageRoute<void>(
                  builder: (_) => OwnCameraScreen(client: client),
                ),
              ),
              child: Container(
                decoration: const BoxDecoration(
                  gradient: LinearGradient(
                    colors: [Color(0xF2243766), Color(0xF233295A)],
                  ),
                ),
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      tr('EIGENE KAMERA', 'OWN CAMERA'),
                      style: theme.textTheme.labelSmall?.copyWith(
                        color: Sky.aurora,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      tr('Allsky-Kamera betreiben', 'Run an all-sky camera'),
                      style: theme.textTheme.titleLarge,
                    ),
                    const SizedBox(height: 4),
                    Text(
                      tr(
                        'Jede Nacht den ganzen Himmel aufnehmen: Meteore, Polarlicht, Wolken und wie dunkel es bei dir ist.',
                        'Record the whole sky every night: meteors, aurora, clouds and how dark it is where you live.',
                      ),
                    ),
                    const SizedBox(height: 8),
                    Text(
                      tr('So geht’s ›', 'How it works ›'),
                      style: theme.textTheme.labelLarge,
                    ),
                  ],
                ),
              ),
            ),
          ),
          soon(
            Icons.nights_stay_outlined,
            Sky.accent,
            tr('Himmelshelligkeit melden', 'Report the sky brightness'),
            tr(
              'Sterne in einem Sternbild zählen, ganz ohne Kamera. Daraus wird ein Punkt auf der Lichtverschmutzungs-Karte.',
              'Count the stars in a constellation, no camera needed. It becomes a point on the light pollution map.',
            ),
          ),
          soon(
            Icons.auto_awesome,
            Sky.warn,
            tr('Feuerkugel gesehen?', 'Seen a fireball?'),
            tr(
              'Zeit und Richtung melden, wir gleichen mit den Kameras ab.',
              'Report time and direction; we compare with the cameras.',
            ),
          ),
          soon(
            Icons.waves,
            Sky.aurora,
            tr('Polarlicht gesehen?', 'Seen the aurora?'),
            tr(
              'Ort und Foto teilen, andere in der Nähe erfahren es.',
              'Share place and photo; others nearby learn about it.',
            ),
          ),
        ],
      ),
    );
  }
}

/// How to get an own camera: hardware, image, pairing with this app.
class OwnCameraScreen extends StatelessWidget {
  const OwnCameraScreen({super.key, required this.client});

  final HubClient client;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final signIn = SignInScope.of(context);
    void open(String path) =>
        launchUrl(client.site(path), mode: LaunchMode.externalApplication);
    Widget step(int n, String title, String text) => Card(
      margin: const EdgeInsets.only(bottom: 10),
      child: ListTile(
        leading: CircleAvatar(
          backgroundColor: theme.colorScheme.primaryContainer,
          child: Text('$n'),
        ),
        title: Text(title),
        subtitle: Text(text),
      ),
    );
    return Scaffold(
      appBar: AppBar(title: Text(tr('Eigene Kamera', 'Own camera'))),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(20, 8, 20, 24),
        children: [
          Text(
            tr(
              'Dein Himmel, jede Nacht im Netz',
              'Your sky, online every night',
            ),
            style: theme.textTheme.headlineSmall,
          ),
          const SizedBox(height: 8),
          Text(
            tr(
              'Eine Allsky-Kamera nimmt den ganzen Himmel auf: Sterne, Meteore, Polarlicht, Wolken. allskyhub macht daraus Zeitraffer, meldet Ereignisse aufs Handy und zeigt sie, wenn du willst, hier für alle.',
              'An all-sky camera records the whole sky: stars, meteors, aurora, clouds. allskyhub turns it into timelapses, sends events to your phone and, if you like, shows them here for everyone.',
            ),
          ),
          const SizedBox(height: 16),
          step(
            1,
            tr('Hardware besorgen', 'Get the hardware'),
            tr(
              'Raspberry Pi und eine Kamera aus unserer Liste',
              'A Raspberry Pi and a camera from our list',
            ),
          ),
          step(
            2,
            tr('Image auf die SD-Karte', 'Image onto the SD card'),
            tr(
              'Fertiges System, nichts zu installieren',
              'A ready system, nothing to install',
            ),
          ),
          step(
            3,
            tr('Mit dieser App koppeln', 'Pair with this app'),
            tr(
              'WLAN eintragen, Standort setzen, fertig. Keine Portfreigabe.',
              'Enter the Wi-Fi, set the place, done. No port forwarding.',
            ),
          ),
          TextButton(
            onPressed: () => open('/allsky'),
            child: Text(
              tr(
                'Du hast schon eine Allsky-Kamera? So steigst du um ›',
                'Already have an all-sky camera? How to switch ›',
              ),
            ),
          ),
          const SizedBox(height: 12),
          FilledButton(
            key: const Key('own-guide'),
            onPressed: () => open('/start'),
            child: Text(
              tr(
                'Anleitung und Hardware ansehen',
                'See the guide and hardware',
              ),
            ),
          ),
          if (signIn != null) ...[
            const SizedBox(height: 10),
            OutlinedButton(
              onPressed: () => open('/zugang'),
              child: Text(tr('Zugang anfragen', 'Request access')),
            ),
            TextButton(
              key: const Key('own-sign-in'),
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
      ),
    );
  }
}
