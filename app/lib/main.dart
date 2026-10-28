import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';

import 'design.dart';
import 'l10n.dart';
import 'api/hub_client.dart';
import 'api/session_store.dart';
import 'screens/cameras_screen.dart';
import 'screens/explore_screen.dart';
import 'screens/join_screen.dart';
import 'screens/login_screen.dart';

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
  const AllskyApp({super.key, required this.store, required this.clientFor});

  final SessionStore store;
  final ClientFactory clientFor;

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
              mine: client == null
                  ? null
                  : CamerasScreen(client: client, onSignOut: _signOut),
              onSignIn: client == null ? _signIn : null,
            ),
    );
  }
}

/// The tabs at the bottom: "Meine" after sign-in, then Entdecken and Mitmachen for
/// everyone.
class HomeShell extends StatefulWidget {
  const HomeShell({super.key, required this.client, this.mine, this.onSignIn});

  final HubClient client;
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
      JoinScreen(client: widget.client),
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
            icon: const Icon(Icons.star_outline),
            selectedIcon: const Icon(Icons.star),
            label: tr('Mitmachen', 'Take part'),
          ),
        ],
      ),
    );
  }
}
