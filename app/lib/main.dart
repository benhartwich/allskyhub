import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';

import 'design.dart';
import 'l10n.dart';
import 'api/hub_client.dart';
import 'api/place_store.dart';
import 'api/session_store.dart';
import 'platform/location.dart';
import 'platform/push.dart';
import 'platform/reminders.dart';
import 'screens/cameras_screen.dart';
import 'screens/explore_screen.dart';
import 'screens/join_screen.dart';
import 'screens/login_screen.dart';
import 'screens/sky_screen.dart';

void main() {
  runApp(
    AllskyApp(
      store: SecureSessionStore(),
      clientFor: (url, token) => HubClient(baseUrl: url, token: token),
    ),
  );
}

typedef ClientFactory = HubClient Function(String baseUrl, String? token);

class AllskyApp extends StatefulWidget {
  const AllskyApp({
    super.key,
    required this.store,
    required this.clientFor,
    this.places,
    this.location,
    this.reminders,
  });

  final SessionStore store;
  final ClientFactory clientFor;

  /// "Himmel": the chosen place and the phone's location (fakes in tests).
  final PlaceStore? places;
  final LocationSource? location;
  final Reminders? reminders;

  @override
  State<AllskyApp> createState() => _AllskyAppState();
}

class _AllskyAppState extends State<AllskyApp> {
  HubClient? _client;
  bool _loading = true;
  final _navigator = GlobalKey<NavigatorState>();

  /// Public calls for guests go to the default hub.
  late final HubClient _guest = widget.clientFor(defaultHubUrl, null);

  @override
  void initState() {
    super.initState();
    _restore();
  }

  Future<void> _restore() async {
    final session = await widget.store.load();
    setState(() {
      _client = session == null
          ? null
          : widget.clientFor(session.hubUrl, session.token);
      _loading = false;
    });
    // Keep this phone's push token fresh if permission was given before (never asks).
    final client = _client;
    if (client != null) unawaited(pushService.register(client, ask: false));
  }

  Future<void> _signedIn(HubClient client) async {
    await widget.store.save(StoredSession(client.baseUrl, client.token!));
    setState(() => _client = client);
    _navigator.currentState?.popUntil((route) => route.isFirst);
  }

  void _signIn() => _navigator.currentState?.push(
    MaterialPageRoute<void>(
      builder: (_) =>
          LoginScreen(clientFor: widget.clientFor, onSignedIn: _signedIn),
    ),
  );

  Future<void> _signOut() async {
    final client = _client;
    if (client != null) await pushService.unregister(client);
    try {
      await _client?.logout();
    } on Exception {
      // Offline or token already gone: forget it locally anyway.
    }
    await widget.store.clear();
    setState(() => _client = null);
  }

  @override
  Widget build(BuildContext context) {
    final client = _client;
    return MaterialApp(
      navigatorKey: _navigator,
      title: 'allskyhub',
      debugShowCheckedModeBanner: false,
      theme: skyTheme(),
      locale: appLocale,
      supportedLocales: const [Locale('de'), Locale('en')],
      localizationsDelegates: GlobalMaterialLocalizations.delegates,
      builder: (context, child) => client == null
          ? SignInScope(onSignIn: _signIn, child: child!)
          : child!,
      home: _loading
          ? const Scaffold(body: Center(child: CircularProgressIndicator()))
          : HomeShell(
              key: ValueKey(client),
              client: client ?? _guest,
              places: widget.places ?? SecurePlaceStore(),
              location: widget.location ?? PlatformLocationSource(),
              reminders: widget.reminders ?? LocalReminders(),
              mine: client == null
                  ? null
                  : CamerasScreen(client: client, onSignOut: _signOut),
              onSignIn: client == null ? _signIn : null,
            ),
    );
  }
}

/// The tabs at the bottom: "Meine" after sign-in, then Entdecken, Himmel and Mitmachen
/// for everyone.
class HomeShell extends StatefulWidget {
  const HomeShell({
    super.key,
    required this.client,
    required this.places,
    required this.location,
    required this.reminders,
    this.mine,
    this.onSignIn,
  });

  final HubClient client;
  final PlaceStore places;
  final LocationSource location;
  final Reminders reminders;
  final Widget? mine;
  final VoidCallback? onSignIn;

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int _tab = 0;

  @override
  Widget build(BuildContext context) {
    final mine = widget.mine;
    final pages = [
      ?mine,
      ExploreScreen(client: widget.client, onSignIn: widget.onSignIn),
      SkyScreen(
        client: widget.client,
        places: widget.places,
        location: widget.location,
        reminders: widget.reminders,
      ),
      JoinScreen(client: widget.client, location: widget.location),
    ];
    return Scaffold(
      body: IndexedStack(index: _tab, children: pages),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _tab,
        onDestinationSelected: (i) => setState(() => _tab = i),
        destinations: [
          if (mine != null)
            NavigationDestination(
              icon: const Icon(Icons.photo_camera_outlined),
              selectedIcon: const Icon(Icons.photo_camera),
              label: tr('Meine', 'Mine'),
            ),
          NavigationDestination(
            icon: const Icon(Icons.public_outlined),
            selectedIcon: const Icon(Icons.public),
            label: tr('Entdecken', 'Explore'),
          ),
          NavigationDestination(
            icon: const Icon(Icons.nights_stay_outlined),
            selectedIcon: const Icon(Icons.nights_stay),
            label: tr('Himmel', 'Sky'),
          ),
          NavigationDestination(
            icon: const Icon(Icons.star_outline),
            selectedIcon: const Icon(Icons.star),
            label: tr('Mitmachen', 'Take part'),
          ),
        ],
      ),
    );
  }
}
