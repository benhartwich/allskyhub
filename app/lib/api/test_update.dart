// Android test builds from the hub's server (deploy/android/README.md): a test build knows
// its build number and offers the newer one the hub has.

/// Set by deploy/android/allskyhub-android-build.sh; empty for store builds.
const testChannel = String.fromEnvironment('ALLSKYHUB_CHANNEL') == 'test';
const appBuild = int.fromEnvironment('ALLSKYHUB_BUILD');

class TestBuild {
  const TestBuild({
    required this.build,
    required this.version,
    required this.apk,
  });

  factory TestBuild.fromJson(Map<String, dynamic> json, String baseUrl) =>
      TestBuild(
        build: json['build'] as int,
        version: json['version'] as String? ?? '',
        apk: Uri.parse(baseUrl).resolve(json['url'] as String),
      );

  final int build;
  final String version;
  final Uri apk;

  /// Only a test build updates, and only to a newer one.
  bool newerThan(int current) => build > current;
}
