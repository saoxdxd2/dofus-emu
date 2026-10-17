cordova.define('cordova/plugin_list', function(require, exports, module) {
  module.exports = [
    {
      "id": "cordova-plugin-splashscreen.SplashScreen",
      "file": "plugins/cordova-plugin-splashscreen/www/splashscreen.js",
      "pluginId": "cordova-plugin-splashscreen",
      "clobbers": [
        "navigator.splashscreen"
      ]
    },
    {
      "id": "ionic-plugin-keyboard.keyboard",
      "file": "plugins/ionic-plugin-keyboard/www/android/keyboard.js",
      "pluginId": "ionic-plugin-keyboard",
      "clobbers": [
        "cordova.plugins.Keyboard"
      ],
      "runs": true
    },
    {
      "id": "cordova-plugin-statusbar.statusbar",
      "file": "plugins/cordova-plugin-statusbar/www/statusbar.js",
      "pluginId": "cordova-plugin-statusbar",
      "clobbers": [
        "window.StatusBar"
      ]
    },
    {
      "id": "cordova-plugin-file.DirectoryEntry",
      "file": "plugins/cordova-plugin-file/www/DirectoryEntry.js",
      "pluginId": "cordova-plugin-file",
      "clobbers": [
        "window.DirectoryEntry"
      ]
    },
    {
      "id": "cordova-plugin-file.DirectoryReader",
      "file": "plugins/cordova-plugin-file/www/DirectoryReader.js",
      "pluginId": "cordova-plugin-file",
      "clobbers": [
        "window.DirectoryReader"
      ]
    },
    {
      "id": "cordova-plugin-file.Entry",
      "file": "plugins/cordova-plugin-file/www/Entry.js",
      "pluginId": "cordova-plugin-file",
      "clobbers": [
        "window.Entry"
      ]
    },
    {
      "id": "cordova-plugin-file.File",
      "file": "plugins/cordova-plugin-file/www/File.js",
      "pluginId": "cordova-plugin-file",
      "clobbers": [
        "window.File"
      ]
    },
    {
      "id": "cordova-plugin-file.FileEntry",
      "file": "plugins/cordova-plugin-file/www/FileEntry.js",
      "pluginId": "cordova-plugin-file",
      "clobbers": [
        "window.FileEntry"
      ]
    },
    {
      "id": "cordova-plugin-file.FileError",
      "file": "plugins/cordova-plugin-file/www/FileError.js",
      "pluginId": "cordova-plugin-file",
      "clobbers": [
        "window.FileError"
      ]
    },
    {
      "id": "cordova-plugin-file.FileReader",
      "file": "plugins/cordova-plugin-file/www/FileReader.js",
      "pluginId": "cordova-plugin-file",
      "clobbers": [
        "window.FileReader"
      ]
    },
    {
      "id": "cordova-plugin-file.FileSystem",
      "file": "plugins/cordova-plugin-file/www/FileSystem.js",
      "pluginId": "cordova-plugin-file",
      "clobbers": [
        "window.FileSystem"
      ]
    },
    {
      "id": "cordova-plugin-file.FileUploadOptions",
      "file": "plugins/cordova-plugin-file/www/FileUploadOptions.js",
      "pluginId": "cordova-plugin-file",
      "clobbers": [
        "window.FileUploadOptions"
      ]
    },
    {
      "id": "cordova-plugin-file.FileUploadResult",
      "file": "plugins/cordova-plugin-file/www/FileUploadResult.js",
      "pluginId": "cordova-plugin-file",
      "clobbers": [
        "window.FileUploadResult"
      ]
    },
    {
      "id": "cordova-plugin-file.FileWriter",
      "file": "plugins/cordova-plugin-file/www/FileWriter.js",
      "pluginId": "cordova-plugin-file",
      "clobbers": [
        "window.FileWriter"
      ]
    },
    {
      "id": "cordova-plugin-file.Flags",
      "file": "plugins/cordova-plugin-file/www/Flags.js",
      "pluginId": "cordova-plugin-file",
      "clobbers": [
        "window.Flags"
      ]
    },
    {
      "id": "cordova-plugin-file.LocalFileSystem",
      "file": "plugins/cordova-plugin-file/www/LocalFileSystem.js",
      "pluginId": "cordova-plugin-file",
      "clobbers": [
        "window.LocalFileSystem"
      ],
      "merges": [
        "window"
      ]
    },
    {
      "id": "cordova-plugin-file.Metadata",
      "file": "plugins/cordova-plugin-file/www/Metadata.js",
      "pluginId": "cordova-plugin-file",
      "clobbers": [
        "window.Metadata"
      ]
    },
    {
      "id": "cordova-plugin-file.ProgressEvent",
      "file": "plugins/cordova-plugin-file/www/ProgressEvent.js",
      "pluginId": "cordova-plugin-file",
      "clobbers": [
        "window.ProgressEvent"
      ]
    },
    {
      "id": "cordova-plugin-file.fileSystems",
      "file": "plugins/cordova-plugin-file/www/fileSystems.js",
      "pluginId": "cordova-plugin-file"
    },
    {
      "id": "cordova-plugin-file.requestFileSystem",
      "file": "plugins/cordova-plugin-file/www/requestFileSystem.js",
      "pluginId": "cordova-plugin-file",
      "clobbers": [
        "window.requestFileSystem"
      ]
    },
    {
      "id": "cordova-plugin-file.resolveLocalFileSystemURI",
      "file": "plugins/cordova-plugin-file/www/resolveLocalFileSystemURI.js",
      "pluginId": "cordova-plugin-file",
      "merges": [
        "window"
      ]
    },
    {
      "id": "cordova-plugin-file.isChrome",
      "file": "plugins/cordova-plugin-file/www/browser/isChrome.js",
      "pluginId": "cordova-plugin-file",
      "runs": true
    },
    {
      "id": "cordova-plugin-file.androidFileSystem",
      "file": "plugins/cordova-plugin-file/www/android/FileSystem.js",
      "pluginId": "cordova-plugin-file",
      "merges": [
        "FileSystem"
      ]
    },
    {
      "id": "cordova-plugin-file.fileSystems-roots",
      "file": "plugins/cordova-plugin-file/www/fileSystems-roots.js",
      "pluginId": "cordova-plugin-file",
      "runs": true
    },
    {
      "id": "cordova-plugin-file.fileSystemPaths",
      "file": "plugins/cordova-plugin-file/www/fileSystemPaths.js",
      "pluginId": "cordova-plugin-file",
      "merges": [
        "cordova"
      ],
      "runs": true
    },
    {
      "id": "cordova-plugin-network-information.network",
      "file": "plugins/cordova-plugin-network-information/www/network.js",
      "pluginId": "cordova-plugin-network-information",
      "clobbers": [
        "navigator.connection",
        "navigator.network.connection"
      ]
    },
    {
      "id": "cordova-plugin-network-information.Connection",
      "file": "plugins/cordova-plugin-network-information/www/Connection.js",
      "pluginId": "cordova-plugin-network-information",
      "clobbers": [
        "Connection"
      ]
    },
    {
      "id": "cordova-plugin-dialogs.notification",
      "file": "plugins/cordova-plugin-dialogs/www/notification.js",
      "pluginId": "cordova-plugin-dialogs",
      "merges": [
        "navigator.notification"
      ]
    },
    {
      "id": "cordova-plugin-dialogs.notification_android",
      "file": "plugins/cordova-plugin-dialogs/www/android/notification.js",
      "pluginId": "cordova-plugin-dialogs",
      "merges": [
        "navigator.notification"
      ]
    },
    {
      "id": "cordova-plugin-file-transfer.FileTransferError",
      "file": "plugins/cordova-plugin-file-transfer/www/FileTransferError.js",
      "pluginId": "cordova-plugin-file-transfer",
      "clobbers": [
        "window.FileTransferError"
      ]
    },
    {
      "id": "cordova-plugin-file-transfer.FileTransfer",
      "file": "plugins/cordova-plugin-file-transfer/www/FileTransfer.js",
      "pluginId": "cordova-plugin-file-transfer",
      "clobbers": [
        "window.FileTransfer"
      ]
    },
    {
      "id": "cordova-plugin-navigationbar.navigationbar",
      "file": "plugins/cordova-plugin-navigationbar/www/navigationbar.js",
      "pluginId": "cordova-plugin-navigationbar",
      "clobbers": [
        "window.navigationbar"
      ]
    },
    {
      "id": "cordova-plugin-chrome-apps-system-memory.system.memory",
      "file": "plugins/cordova-plugin-chrome-apps-system-memory/system.memory.js",
      "pluginId": "cordova-plugin-chrome-apps-system-memory",
      "clobbers": [
        "chrome.system.memory"
      ]
    },
    {
      "id": "cordova-plugin-appavailability.AppAvailability",
      "file": "plugins/cordova-plugin-appavailability/www/AppAvailability.js",
      "pluginId": "cordova-plugin-appavailability",
      "clobbers": [
        "appAvailability"
      ]
    },
    {
      "id": "cordova-plugin-media.MediaError",
      "file": "plugins/cordova-plugin-media/www/MediaError.js",
      "pluginId": "cordova-plugin-media",
      "clobbers": [
        "window.MediaError"
      ]
    },
    {
      "id": "cordova-plugin-media.Media",
      "file": "plugins/cordova-plugin-media/www/Media.js",
      "pluginId": "cordova-plugin-media",
      "clobbers": [
        "window.Media"
      ]
    },
    {
      "id": "jp.wizcorp.phonegap.plugin.wizAssetsPlugin.wizAssetsPlugin",
      "file": "plugins/jp.wizcorp.phonegap.plugin.wizAssetsPlugin/www/phonegap/plugin/wizAssets/wizAssets.js",
      "pluginId": "jp.wizcorp.phonegap.plugin.wizAssetsPlugin",
      "clobbers": [
        "window.wizAssets"
      ]
    },
    {
      "id": "jp.wizcorp.phonegap.plugin.wizAssetsPlugin.WizAssetsError",
      "file": "plugins/jp.wizcorp.phonegap.plugin.wizAssetsPlugin/www/phonegap/plugin/wizAssets/WizAssetsError.js",
      "pluginId": "jp.wizcorp.phonegap.plugin.wizAssetsPlugin",
      "clobbers": [
        "window.WizAssetsError"
      ]
    },
    {
      "id": "at.gofg.sportscomputer.powermanagement.device",
      "file": "plugins/at.gofg.sportscomputer.powermanagement/www/powermanagement.js",
      "pluginId": "at.gofg.sportscomputer.powermanagement",
      "clobbers": [
        "window.powerManagement"
      ]
    },
    {
      "id": "cordova-plugin-device.device",
      "file": "plugins/cordova-plugin-device/www/device.js",
      "pluginId": "cordova-plugin-device",
      "clobbers": [
        "device"
      ]
    },
    {
      "id": "phonegap-plugin-mobile-accessibility.mobile-accessibility",
      "file": "plugins/phonegap-plugin-mobile-accessibility/www/mobile-accessibility.js",
      "pluginId": "phonegap-plugin-mobile-accessibility",
      "clobbers": [
        "window.MobileAccessibility"
      ]
    },
    {
      "id": "phonegap-plugin-mobile-accessibility.MobileAccessibilityNotifications",
      "file": "plugins/phonegap-plugin-mobile-accessibility/www/MobileAccessibilityNotifications.js",
      "pluginId": "phonegap-plugin-mobile-accessibility",
      "clobbers": [
        "MobileAccessibilityNotifications"
      ]
    },
    {
      "id": "cordova-plugin-yanap.Yanap",
      "file": "plugins/cordova-plugin-yanap/www/Yanap.js",
      "pluginId": "cordova-plugin-yanap",
      "clobbers": [
        "cordova.plugins.Yanap"
      ]
    },
    {
      "id": "cordova-plugin-inappbrowser.inappbrowser",
      "file": "plugins/cordova-plugin-inappbrowser/www/inappbrowser.js",
      "pluginId": "cordova-plugin-inappbrowser",
      "clobbers": [
        "cordova.InAppBrowser.open"
      ]
    },
    {
      "id": "pushwoosh-cordova-plugin.PushNotification",
      "file": "plugins/pushwoosh-cordova-plugin/www/PushNotification.js",
      "pluginId": "pushwoosh-cordova-plugin",
      "clobbers": [
        "plugins.pushNotification"
      ]
    },
    {
      "id": "cordova-launch-review.LaunchReview",
      "file": "plugins/cordova-launch-review/www/launchreview.js",
      "pluginId": "cordova-launch-review",
      "clobbers": [
        "LaunchReview"
      ]
    },
    {
      "id": "com.adjust.sdk.Adjust",
      "file": "plugins/com.adjust.sdk/www/adjust.js",
      "pluginId": "com.adjust.sdk",
      "clobbers": [
        "Adjust"
      ]
    },
    {
      "id": "com.adjust.sdk.AdjustConfig",
      "file": "plugins/com.adjust.sdk/www/adjust_config.js",
      "pluginId": "com.adjust.sdk",
      "clobbers": [
        "AdjustConfig"
      ]
    },
    {
      "id": "com.adjust.sdk.AdjustEvent",
      "file": "plugins/com.adjust.sdk/www/adjust_event.js",
      "pluginId": "com.adjust.sdk",
      "clobbers": [
        "AdjustEvent"
      ]
    },
    {
      "id": "cordova-plugin-appsettings.AppSettings",
      "file": "plugins/cordova-plugin-appsettings/www/appsettings.js",
      "pluginId": "cordova-plugin-appsettings",
      "clobbers": [
        "AppSettings"
      ]
    },
    {
      "id": "cordova-plugin-appinfo.AppInfo",
      "file": "plugins/cordova-plugin-appinfo/www/appinfo.js",
      "pluginId": "cordova-plugin-appinfo",
      "merges": [
        "navigator.appInfo"
      ]
    },
    {
      "id": "cordova-plugin-wizpurchase.WizPurchase",
      "file": "plugins/cordova-plugin-wizpurchase/www/WizPurchase.js",
      "pluginId": "cordova-plugin-wizpurchase",
      "clobbers": [
        "window.wizPurchase"
      ]
    },
    {
      "id": "cordova-plugin-wizpurchase.WizPurchaseError",
      "file": "plugins/cordova-plugin-wizpurchase/www/WizPurchaseError.js",
      "pluginId": "cordova-plugin-wizpurchase",
      "clobbers": [
        "window.WizPurchaseError"
      ]
    },
    {
      "id": "cordova-plugin-purchase.InAppBillingPlugin",
      "file": "plugins/cordova-plugin-purchase/www/store-android.js",
      "pluginId": "cordova-plugin-purchase",
      "clobbers": [
        "store"
      ]
    }
  ];
  module.exports.metadata = {
    "cordova-plugin-whitelist": "1.3.5",
    "cordova-plugin-splashscreen": "6.0.0",
    "ionic-plugin-keyboard": "1.0.9",
    "cordova-plugin-statusbar": "2.4.3",
    "cordova-plugin-file": "6.0.2",
    "cordova-plugin-network-information": "2.0.2",
    "cordova-plugin-dialogs": "2.0.2",
    "cordova-plugin-file-transfer": "1.7.2-dev",
    "cordova-plugin-navigationbar": "1.0.31",
    "cordova-plugin-chrome-apps-system-memory": "1.1.1",
    "cordova-plugin-appavailability": "0.4.2",
    "cordova-plugin-media": "5.0.3",
    "jp.wizcorp.phonegap.plugin.wizAssetsPlugin": "6.1.1",
    "at.gofg.sportscomputer.powermanagement": "1.1.2",
    "cordova-plugin-device": "2.1.1-dev",
    "phonegap-plugin-mobile-accessibility": "1.0.5-dev",
    "cordova-plugin-yanap": "0.8.9",
    "cordova-custom-config": "5.1.0",
    "cordova-plugin-inappbrowser": "4.0.0",
    "pushwoosh-cordova-plugin": "8.1.1",
    "cordova-launch-review": "3.1.1",
    "com.adjust.sdk": "4.22.1",
    "cordova-plugin-wkwebview-file-xhr": "3.0.0",
    "cordova-plugin-appsettings": "1.0.2",
    "cordova-plugin-androidx-adapter": "1.1.3",
    "cordova-plugin-appinfo": "2.1.2",
    "cordova-plugin-wizpurchase": "2.2.0",
    "cordova-plugin-purchase": "10.6.1"
  };
});