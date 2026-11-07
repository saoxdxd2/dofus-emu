cordova.define("com.adjust.sdk.AdjustConfig", function(require, exports, module) {
function AdjustConfig(appToken, environment) {
    this.appToken = appToken;
    this.environment = environment;
    this.delayStart = 0.0;
    this.logLevel = null;
    this.referrer = null;
    this.userAgent = null;
    this.isDeviceKnown = null;
    this.defaultTracker = null;
    this.externalDeviceId = null;
    this.sendInBackground = null;
    this.shouldLaunchDeeplink = null;
    this.eventBufferingEnabled = null;
    this.attributionCallbackListener = null;
    this.eventTrackingSucceededCallbackListener = null;
    this.eventTrackingFailedCallbackListener = null;
    this.sessionTrackingSucceededCallbackListener = null;
    this.sessionTrackingFailedCallbackListener = null;
    this.deferredDeeplinkCallbackListener = null;
    this.sdkPrefix = null;
    this.secretId = null;
    this.info1 = null;
    this.info2 = null;
    this.info3 = null;
    this.info4 = null;
    // Android only
    this.processName = null;
    this.readMobileEquipmentIdentity = null;
    // iOS only
    this.allowiAdInfoReading = null;
    this.allowIdfaReading = null;
    // Unbotify
    this.unbotifyLabel = null;
    this.unbotifyProfile = null;
    this.unbotifySessionId = null;
    this.observationPackageCallbackListener = null;
};

AdjustConfig.EnvironmentSandbox    = "sandbox";
AdjustConfig.EnvironmentProduction = "production";
AdjustConfig.LogLevelVerbose       = "VERBOSE";
AdjustConfig.LogLevelDebug         = "DEBUG";
AdjustConfig.LogLevelInfo          = "INFO";
AdjustConfig.LogLevelWarn          = "WARN";
AdjustConfig.LogLevelError         = "ERROR";
AdjustConfig.LogLevelAssert        = "ASSERT";
AdjustConfig.LogLevelSuppress      = "SUPPRESS";

AdjustConfig.prototype.getUserAgent = function() {
    return this.userAgent;
};

AdjustConfig.prototype.getDelayStart = function() {
    return this.delayStart;
};

AdjustConfig.prototype.getReferrer = function() {
    return this.referrer;
};

AdjustConfig.prototype.getSendInBackground = function() {
    return this.sendInBackground;
};

AdjustConfig.prototype.getShouldLaunchDeeplink = function() {
    return this.shouldLaunchDeeplink;
};

AdjustConfig.prototype.getAttributionCallback = function() {
    return this.attributionCallbackListener;
};

AdjustConfig.prototype.getEventTrackingSucceededCallback = function() {
    return this.eventTrackingSucceededCallbackListener;
};

AdjustConfig.prototype.getEventTrackingFailedCallback = function() {
    return this.eventTrackingFailedCallbackListener;
};

AdjustConfig.prototype.getSessionTrackingSucceededCallback = function() {
    return this.sessionTrackingSucceededCallbackListener;
};

AdjustConfig.prototype.getSessionTrackingFailedCallback = function() {
    return this.sessionTrackingFailedCallbackListener;
};

AdjustConfig.prototype.getDeferredDeeplinkCallback = function() {
    return this.deferredDeeplinkCallbackListener;
};

AdjustConfig.prototype.getObservationPackageCallback = function() {
    return this.observationPackageCallbackListener;
};

AdjustConfig.prototype.setEventBufferingEnabled = function(isEnabled) {
    this.eventBufferingEnabled = isEnabled;
};

AdjustConfig.prototype.setLogLevel = function(logLevel) {
    this.logLevel = logLevel;
};

AdjustConfig.prototype.setProcessName = function(processName) {
    this.processName = processName;
};

AdjustConfig.prototype.setDefaultTracker = function(defaultTracker) {
    this.defaultTracker = defaultTracker;
};

AdjustConfig.prototype.setExternalDeviceId = function(externalDeviceId) {
    this.externalDeviceId = externalDeviceId;
};

AdjustConfig.prototype.setUserAgent = function(userAgent) {
    this.userAgent = userAgent;
};

AdjustConfig.prototype.setDeviceKnown = function(isDeviceKnown) {
    this.isDeviceKnown = isDeviceKnown;
};

AdjustConfig.prototype.getSdkPrefix = function() {
    return this.sdkPrefix;
};

AdjustConfig.prototype.setSdkPrefix = function(sdkPrefix) {
    this.sdkPrefix = sdkPrefix;
};

AdjustConfig.prototype.setAllowiAdInfoReading = function(allowiAdInfoReading) {
    this.allowiAdInfoReading = allowiAdInfoReading;
};

AdjustConfig.prototype.setAllowIdfaReading = function(allowIdfaReading) {
    this.allowIdfaReading = allowIdfaReading;
};

// @deprecated
AdjustConfig.prototype.setReadMobileEquipmentIdentity = function(readMobileEquipmentIdentity) {
    console.warn("Calling deprecated function! This functionality has been removed from the SDK.");
    // this.readMobileEquipmentIdentity = readMobileEquipmentIdentity;
};

AdjustConfig.prototype.setAppSecret = function(secretId, info1, info2, info3, info4) {
    if (secretId !== null) {
        this.secretId = secretId.toString();
    }
    if (info1 !== null) {
        this.info1 = info1.toString();
    }
    if (info2 !== null) {
        this.info2 = info2.toString();
    }
    if (info3 !== null) {
        this.info3 = info3.toString();
    }
    if (info4 !== null) {
        this.info4 = info4.toString();
    }
};

AdjustConfig.prototype.setDelayStart = function(delayStart) {
    this.delayStart = delayStart;
};

AdjustConfig.prototype.setReferrer = function(referrer) {
    this.referrer = referrer;
};

AdjustConfig.prototype.setSendInBackground = function(sendInBackground) {
    this.sendInBackground = sendInBackground;
};

AdjustConfig.prototype.setShouldLaunchDeeplink = function(shouldLaunchDeeplink) {
    this.shouldLaunchDeeplink = shouldLaunchDeeplink;
};

AdjustConfig.prototype.setUnbotifyLabel = function(unbotifyLabel) {
    this.unbotifyLabel = unbotifyLabel;
};

AdjustConfig.prototype.setUnbotifyProfile = function(unbotifyProfile) {
    this.unbotifyProfile = unbotifyProfile;
};

AdjustConfig.prototype.setSessionId = function(unbotifySessionId) {
    this.unbotifySessionId = unbotifySessionId;
};

// @deprecated
AdjustConfig.prototype.setCallbackListener = function(callbackListener) {
    console.warn("Calling deprecated function! Use the setAttributionCallbackListener instead. Check adjust_config.js for more info.");
    this.attributionCallbackListener = attributionCallbackListener;
};

AdjustConfig.prototype.setAttributionCallbackListener = function(attributionCallbackListener) {
    this.attributionCallbackListener = attributionCallbackListener;
};

AdjustConfig.prototype.setEventTrackingSucceededCallbackListener = function(eventTrackingSucceededCallbackListener) {
    this.eventTrackingSucceededCallbackListener = eventTrackingSucceededCallbackListener;
};

AdjustConfig.prototype.setEventTrackingFailedCallbackListener = function(eventTrackingFailedCallbackListener) {
    this.eventTrackingFailedCallbackListener = eventTrackingFailedCallbackListener;
};

AdjustConfig.prototype.setSessionTrackingSucceededCallbackListener = function(sessionTrackingSucceededCallbackListener) {
    this.sessionTrackingSucceededCallbackListener = sessionTrackingSucceededCallbackListener;
};

AdjustConfig.prototype.setSessionTrackingFailedCallbackListener = function(sessionTrackingFailedCallbackListener) {
    this.sessionTrackingFailedCallbackListener = sessionTrackingFailedCallbackListener;
};

AdjustConfig.prototype.setDeferredDeeplinkCallbackListener = function(deferredDeeplinkCallbackListener) {
    this.deferredDeeplinkCallbackListener = deferredDeeplinkCallbackListener;
};

AdjustConfig.prototype.setObservationPackageCallbackListener = function(observationPackageCallbackListener) {
    this.observationPackageCallbackListener = observationPackageCallbackListener;
};

// @deprecated
AdjustConfig.prototype.hasListener = function() {
    console.warn("Calling deprecated function! Use the hasAttributionListener instead. Check adjust_config.js for more info");
    return this.attributionCallbackListener !== null;
};

AdjustConfig.prototype.hasAttributionListener = function() {
    return this.attributionCallbackListener !== null;
};

AdjustConfig.prototype.hasEventTrackingSucceededListener = function() {
    return this.eventTrackingSucceededCallbackListener !== null;
};

AdjustConfig.prototype.hasEventTrackingFailedListener = function() {
    return this.eventTrackingFailedCallbackListener !== null;
};

AdjustConfig.prototype.hasSessionTrackingSucceededListener = function() {
    return this.sessionTrackingSucceededCallbackListener !== null;
};

AdjustConfig.prototype.hasSessionTrackingFailedListener = function() {
    return this.sessionTrackingFailedCallbackListener !== null;
};

AdjustConfig.prototype.hasDeferredDeeplinkCallbackListener = function() {
    return this.deferredDeeplinkCallbackListener !== null;
};

AdjustConfig.prototype.hasObservationPackageCallbackListener = function() {
    return this.observationPackageCallbackListener !== null;
};

module.exports = AdjustConfig;

});
