'use strict';

var CDV_PATH_IN_CSS = 'cdvfile://localhost/persistent/data/';
var CDV_PATH_IOS = 'cdvfile://localhost/persistent/data/';
var CDV_PATH_ANDROID = 'file:///__cdvfile_persistent__/data/';

function autoUpdate(cordova) {
	var head = document.getElementsByTagName('head')[0];
	var now = Date.now();

	// All errors seem to originate from Android Browser 4.1-4.4
	if (!window.performance || !window.performance.now) {
		var startTimestamp = Date.now();
		window.performance = window.performance || {};
		window.performance.now = function now() {
			return Date.now() - startTimestamp;
		};
	}

	/**
	 * @type {AppLoader}
	 */
	var loader;

	/**
	 * @type {AppLoader}
	 */
	var assetLoader;
	var isLoaded = false;
	var splashscreen;

	function resetServer(err) {
		console.error(new Error('resetServer ' + err));
		// TODO: show a proper page (html or image)
		var errorMsg = 'Server cannot be reached.';
		// window.Connection is provided by cordova-plugin-network-information.
		if (window.navigator.connection.type === window.Connection.NONE || !window.navigator.onLine) {
			errorMsg += ' Check your internet connection.';
		} else {
			// If not an internet issue reset the url.
			localStorage.removeItem('lscs');
		}
		setTimeout(function () {
			// window.navigator.notification is provided by cordova-plugin-dialogs.
			window.navigator.notification.alert(
				errorMsg,
				function () {
					loader.reset();
				},
				window.APPLICATION_NAME_DISPLAY,
				'Ok'
			);
		}, 0);
	}

	function getNativePath(cb) {
		if (window.WkWebView) {
			// iOS
			return window.resolveLocalFileSystemURL(
				CDV_PATH_IOS,
				function successCb(entry) {
					var nativePath = window.WkWebView.convertFilePath(entry.toURL());
					return cb(null, nativePath);
				},
				cb
			);
		}
		// Android
		return window.resolveLocalFileSystemURL(
			CDV_PATH_ANDROID,
			function successCb(entry) {
				var nativePath = entry.toURL();
				return cb(null, nativePath);
			},
			cb
		);
	}

	function readFile(fileUrl, cb) {
		return window.resolveLocalFileSystemURL(
			fileUrl,
			function (fileEntry) {
				return fileEntry.file(function (file) {
					var reader = new FileReader();

					reader.onloadend = function () {
						return cb(null, this.result);
					};

					reader.onerror = function (e) {
						return cb('Failed file read: ' + e.toString());
					};

					reader.readAsText(file);
				}, cb);
			},
			cb
		);
	}

	function parseUrl(fileUrl) {
		var split = fileUrl.split('/');
		var filename = split.pop();
		var dirname = split.join('/');
		return {
			filename: filename,
			dirname: dirname
		};
	}

	function writeFile(fileUrl, strContent, cb) {
		var parse = parseUrl(fileUrl);
		var dirPath = parse.dirname;
		var filename = parse.filename;
		return window.resolveLocalFileSystemURL(
			dirPath,
			function (dirEntry) {
				dirEntry.getFile(
					filename,
					{ create: true, exclusive: false },
					function (fileEntry) {
						// Create a FileWriter object for our FileEntry (log.txt).
						return fileEntry.createWriter(function (fileWriter) {
							fileWriter.onwriteend = function () {
								return cb();
							};

							fileWriter.onerror = function (e) {
								return cb('Failed file write: ' + e.toString());
							};

							fileWriter.write(new Blob([strContent], { type: 'text/plain' }));
						});
					},
					cb
				);
			},
			cb
		);
	}

	function getNewCssUrl(url) {
		if (url.substr(-4) !== '.css') {
			return url;
		}
		// the link will become "styles-native.app_file.css"
		var split = url.split('.');
		split.pop();
		split.push('app_file');
		split.push('css');
		return split.join('.');
	}

	function replacePathInCss(url, cb) {
		if (url.substr(-4) !== '.css') {
			return cb(null, url);
		}
		// window.CDV_ASSETS_URL is only iOS
		var urlFile = url.replace(window.CDV_ASSETS_URL + '/_app_file_', 'file://');
		var newUrlFile = getNewCssUrl(urlFile);
		return getNativePath(function (error, nativePath) {
			if (error) {
				return cb(error);
			}
			return readFile(urlFile, function (error, fileStr) {
				if (error) {
					return cb(error);
				}
				var newCss = fileStr.replace(new RegExp(CDV_PATH_IN_CSS, 'gu'), nativePath);
				return writeFile(newUrlFile, newCss, function (error) {
					if (error) {
						return cb(error);
					}
					return cb(null, getNewCssUrl(url));
				});
			});
		});
	}

	// Load Files
	function loadFile(src, id) {
		if (!src) {
			console.error('loadFile source missing.');
			return;
		}
		id = 'source_' + id;
		var el = document.createElement('script');
		var hasSource = document.getElementById(id);
		if (hasSource) {
			// prevent to load multiple time the same source
			return;
		}
		replacePathInCss(src, function (error, fileUrl) {
			if (error) {
				console.error(new Error('Cannot replace: code ' + error.code));
			}
			// Load javascript
			if (fileUrl.substr(-3) === '.js') {
				el = document.createElement('script');
				el.type = 'text/javascript';
				el.src = fileUrl + '?' + now;
				el.async = false;
				// Load CSS
			} else if (fileUrl.substr(-4) === '.css') {
				el = document.createElement('link');
				el.rel = 'stylesheet';
				el.href = fileUrl + '?' + now;
				el.type = 'text/css';
			} else {
				console.error('Format not handled', src);
			}
			el.setAttribute('id', id);
			head.appendChild(el);
		});
	}

	function check() {
		console.log('Looking for update');
		// Check if there is an update compared to the manifest in cache
		return loader
			.check()
			.then(function () {
				if (loader.corruptNewManifest) {
					return loader.reset();
				}
				return loader.download();
			})
			.then(function () {
				var hasUpdate = loader.update(false);
				console.log('Update available:', hasUpdate);
				if (isLoaded) {
					// If update detected while in game
					if (hasUpdate) {
						window.cordova.fireDocumentEvent('sourceUpdated');
					}
				}
				isLoaded = true;
			});
	}

	function setupLoader(serverURL) {
		var currentServer = serverURL;
		var cServer = localStorage.getItem('lscs');
		if (cServer) {
			currentServer = cServer;
		}
		window.appInfo.server = currentServer;
		loader = new window.AppLoader('source', {
			localRoot: 'js/',
			serverRoot: currentServer,
			mode: 'mirror',
			cacheBuster: true
		});
		window.loader = loader;
		assetLoader = new window.AppLoader('ui', {
			localRoot: '',
			serverRoot: currentServer,
			mode: 'mirror',
			cacheBuster: true,
			manifest: 'assetMap.json'
		});

		var loadingProgress = new window.AppLoadingProgress();

		var currentProgress = 0;
		function onProgress(status) {
			if (status.percentage > currentProgress) {
				loadingProgress.setProgress(status.percentage);
				currentProgress = status.percentage;
			}
		}

		check()
			.then(function () {
				splashscreen.hide();
				return assetLoader
					.check()
					.then(function (needUpdate) {
						if (assetLoader.corruptNewManifest) {
							return assetLoader.reset();
						}
						if (!needUpdate) {
							return;
						}
						loadingProgress.show('Downloading User Interface');
						return assetLoader.download(onProgress);
					})
					.then(function () {
						assetLoader.update(false);
						loadingProgress.destroy();
					})
					.catch(loadingProgress.destroy);
			})
			.then(function () {
				// Load the files from the server's manifest

				for (var fileId in loader.manifest.files) {
					if (loader.manifest.files.hasOwnProperty(fileId)) {
						var file = loader.manifest.files[fileId];
						var uri;
						if (window.WkWebView) {
							// On WkWebView (iOS), We need the full path and convert it.
							uri = loader.cache.toURL(file.filename);
							uri = window.WkWebView.convertFilePath(uri);
						} else {
							// The Android case.
							uri = loader.cache.toURL(file.filename);
							if (uri.indexOf('file://') !== 0) {
								throw new Error('File not local: ' + uri);
							}
						}
						console.log('Loading file:', file.filename, uri);
						loadFile(uri, fileId);
					}
				}
			})
			.catch(resetServer);

		document.addEventListener('resume', function () {
			check().then(splashscreen.hide).catch(resetServer);
		});
	}

	function start() {
		// For debug
		// window.onerror = function (message, url, lineno, colno, error) {
		// 	console.error(message + ' ' + url + ' ' + lineno + ' ' + colno + ' ' + error);
		// };
		// var consoleLog = window.console || {};
		// consoleLog.info = function () {
		// 	var str = Array.prototype.slice.call(arguments).join(' ');
		// 	alert('info: ' + str);
		// };
		// consoleLog.error = function () {
		// 	var str = Array.prototype.slice.call(arguments).join(' ');
		// 	alert('error: ' + str);
		// };
		// consoleLog.warn = function () {
		// 	var str = Array.prototype.slice.call(arguments).join(' ');
		// 	alert('warn: ' + str);
		// };
		// consoleLog.log = function () {
		// 	var str = Array.prototype.slice.call(arguments).join(' ');
		// 	alert('log: ' + str);
		// };
		splashscreen = window.navigator.splashscreen;
		splashscreen.show();
		cordova.plugins.Keyboard.hideKeyboardAccessoryBar(true);
		window.StatusBar.hide();

		window.navigator.appInfo.getAppInfo(function (appInfo) {
			window.appInfo = appInfo;
			window.AppSettings.get(
				function (settings) {
					setupLoader(settings.server);
				},
				function (e) {
					console.error(e);
					// eslint-disable-next-line no-alert
					window.alert('Could not load local settings');
				},
				['server']
			);
		});
		document.addEventListener('pause', splashscreen.show);
	}

	document.addEventListener('deviceready', start, false);
	// For debug
	// window.addEventListener('cordovacallbackerror', function (msg) {
	// 	console.error('cordovacallbackerror: ' + msg.message);
	// }, false);
}
autoUpdate(window.cordova);
