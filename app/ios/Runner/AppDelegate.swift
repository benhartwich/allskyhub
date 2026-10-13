import Flutter
import NetworkExtension
import UIKit

@main
@objc class AppDelegate: FlutterAppDelegate, FlutterImplicitEngineDelegate {
  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  func didInitializeImplicitFlutterEngine(_ engineBridge: FlutterImplicitEngineBridge) {
    GeneratedPluginRegistrant.register(with: engineBridge.pluginRegistry)
    guard let registrar = engineBridge.pluginRegistry.registrar(forPlugin: "AllskyhubWifi")
    else { return }
    // lib/platform/wifi_binding.dart: iOS routes the local subnet over Wi-Fi by itself, so
    // only joining the camera's setup network needs code here (SPEC §7.1).
    let channel = FlutterMethodChannel(
      name: "org.allskyhub.app/wifi", binaryMessenger: registrar.messenger())
    channel.setMethodCallHandler { call, result in
      switch call.method {
      case "bindToWifi":
        result(true)
      case "unbind":
        result(nil)
      case "joinSetup":
        let args = call.arguments as? [String: Any] ?? [:]
        let ssid = args["ssid"] as? String
        let prefix = args["prefix"] as? String ?? "allskyhub-"
        let key = args["key"] as? String
        let config: NEHotspotConfiguration
        switch (ssid, key) {
        case let (ssid?, key?): config = NEHotspotConfiguration(ssid: ssid, passphrase: key, isWEP: false)
        case let (ssid?, nil): config = NEHotspotConfiguration(ssid: ssid)
        case let (nil, key?): config = NEHotspotConfiguration(ssidPrefix: prefix, passphrase: key, isWEP: false)
        case (nil, nil): config = NEHotspotConfiguration(ssidPrefix: prefix)
        }
        config.joinOnce = true
        NEHotspotConfigurationManager.shared.apply(config) { error in
          if let error = error as NSError?,
            error.code != NEHotspotConfigurationError.alreadyAssociated.rawValue
          {
            result("failed")
          } else {
            result("joined")
          }
        }
      default:
        result(FlutterMethodNotImplemented)
      }
    }
  }
}
