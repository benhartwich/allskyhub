import 'package:flutter/material.dart';

import 'api/hub_client.dart';
import 'api/session_store.dart';
import 'screens/cameras_screen.dart';
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
  }

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
      title: 'allskyhub',
      debugShowCheckedModeBanner: false,
      theme: _theme(Brightness.light),
      darkTheme: _theme(Brightness.dark),
      themeMode: ThemeMode.dark,
      home: _loading
          ? const Scaffold(body: Center(child: CircularProgressIndicator()))
          : client == null
          ? LoginScreen(clientFor: widget.clientFor, onSignedIn: _signedIn)
          : CamerasScreen(client: client, onSignOut: _signOut),
    );
  }
}

ThemeData _theme(Brightness brightness) => ThemeData(
  colorScheme: ColorScheme.fromSeed(
    seedColor: const Color(0xFF7AA2F7),
    brightness: brightness,
    surface: brightness == Brightness.dark ? const Color(0xFF0B1020) : null,
  ),
  useMaterial3: true,
);
