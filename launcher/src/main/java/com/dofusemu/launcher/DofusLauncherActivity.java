package com.dofusemu.launcher;

import android.app.Activity;
import android.content.ComponentName;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.util.Log;

/**
 * Minimal HOME launcher that starts the game directly.
 *
 * <p>Registering {@code CATEGORY_HOME} makes this the default launcher, so the
 * stock Launcher3 / Launcher2 never starts and no home/all-apps surface is
 * ever allocated. When the system binds this as HOME it is shown as a
 * transparent activity, launches the game, and finishes immediately.
 *
 * <p>Scope note: this removes the launcher's own surfaces, not the compositor.
 * SurfaceFlinger and WindowManager in system_server own the display buffers
 * regardless of which app is foreground, so the memory saving here is real but
 * modest (tens of MB, mostly the launcher process and its prebuilt drawables).
 *
 * <p>Games commonly hard-code a package name, so the target is resolved by
 * package with a component fallback rather than only by activity name.
 */
public class DofusLauncherActivity extends Activity {

    private static final String TAG = "DofusLauncher";

    /** Known Dofus Touch package. Overridable via intent extra for testing. */
    private static final String GAME_PACKAGE = "com.ankama.dofustouch";

    /** Fallback activity, used when the package resolves but the class differs. */
    private static final String GAME_ACTIVITY = "com.ankama.dofustouch.DofusTouchActivity";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // No UI. This activity exists only to hand off to the game.
        String pkg = getIntent().getStringExtra("game_package");
        if (pkg == null) {
            pkg = GAME_PACKAGE;
        }

        if (launchGame(pkg, GAME_ACTIVITY)) {
            Log.i(TAG, "launched game package=" + pkg);
        } else {
            Log.e(TAG, "could not launch game package=" + pkg
                    + " - is the APK installed on this instance?");
        }

        // Do not linger in the recents/foreground task stack.
        finish();
        overridePendingTransition(0, 0);
    }

    /**
     * Try the declared activity first, then fall back to the package's launch
     * intent, then to whatever activity the package reports as its entry point.
     */
    private boolean launchGame(String pkg, String activityClass) {
        // 1. Explicit component.
        Intent intent = new Intent(Intent.ACTION_MAIN);
        intent.addCategory(Intent.CATEGORY_LAUNCHER);
        intent.setComponent(new ComponentName(pkg, activityClass));
        if (startActivitySafely(intent)) {
            return true;
        }

        // 2. The package's own declared launcher activity.
        Intent launchIntent = getPackageManager().getLaunchIntentForPackage(pkg);
        if (launchIntent != null) {
            launchIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            if (startActivitySafely(launchIntent)) {
                return true;
            }
        }

        // 3. Any activity in the package that declares MAIN/LAUNCHER.
        return startAnyLauncherActivityIn(pkg);
    }

    private boolean startAnyLauncherActivityIn(String pkg) {
        try {
            PackageManager pm = getPackageManager();
            Intent query = new Intent(Intent.ACTION_MAIN);
            query.addCategory(Intent.CATEGORY_LAUNCHER);
            query.setPackage(pkg);
            android.content.pm.ResolveInfo info = pm.resolveActivity(query, 0);
            if (info != null && info.activityInfo != null) {
                Intent i = new Intent(Intent.ACTION_MAIN);
                i.addCategory(Intent.CATEGORY_LAUNCHER);
                i.setComponent(new ComponentName(
                        info.activityInfo.packageName, info.activityInfo.name));
                return startActivitySafely(i);
            }
        } catch (Exception e) {
            Log.e(TAG, "activity lookup failed", e);
        }
        return false;
    }

    private boolean startActivitySafely(Intent intent) {
        try {
            // A launcher activity is not in a task of its own, so the game must
            // be started into a new task or it inherits this empty activity's.
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK
                    | Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED);
            startActivity(intent);
            return true;
        } catch (Exception e) {
            Log.w(TAG, "startActivity failed: " + intent.getComponent(), e);
            return false;
        }
    }

    /**
     * When the system re-launches HOME after the game is stopped, forward again
     * rather than showing an empty screen.
     */
    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        onCreate(null);
    }
}
