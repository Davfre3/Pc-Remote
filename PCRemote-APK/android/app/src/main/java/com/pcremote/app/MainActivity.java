package com.pcremote.app;

import android.os.Build;
import android.os.Bundle;
import android.view.KeyEvent;
import android.view.WindowManager;
import android.view.View;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.core.view.WindowInsetsControllerCompat;
import com.getcapacitor.BridgeActivity;

/** Pantalla completa, la pantalla no se apaga mientras juegas y el mando ocupa tambien la zona del notch. */
public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(BtPad.class);
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        WindowCompat.setDecorFitsSystemWindows(getWindow(), false);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            getWindow().getAttributes().layoutInDisplayCutoutMode =
                WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES;
        }
        // A pantalla completa el teclado no redimensiona la ventana solo: se deja sitio abajo para que no tape los campos
        View content = findViewById(android.R.id.content);
        ViewCompat.setOnApplyWindowInsetsListener(content, (v, insets) -> {
            Insets ime = insets.getInsets(WindowInsetsCompat.Type.ime());
            v.setPadding(0, 0, 0, ime.bottom);
            return insets;
        });
        hideBars();
    }

    /** Lo activa el mando (BtPad.volumeKeys) solo mientras se juega: en los menus el volumen funciona normal. */
    static volatile boolean volumeKeys = false;

    @Override
    public boolean dispatchKeyEvent(KeyEvent ev) {
        int k = ev.getKeyCode();
        if (volumeKeys && (k == KeyEvent.KEYCODE_VOLUME_UP || k == KeyEvent.KEYCODE_VOLUME_DOWN)) {
            int a = ev.getAction();
            // la auto-repeticion de mantener pulsado no es una pulsacion nueva
            if (bridge != null && ev.getRepeatCount() == 0 && (a == KeyEvent.ACTION_DOWN || a == KeyEvent.ACTION_UP)) {
                String js = "window.onVolKey&&onVolKey('" + (k == KeyEvent.KEYCODE_VOLUME_UP ? "up" : "down") + "',"
                    + (a == KeyEvent.ACTION_DOWN ? 1 : 0) + ")";
                bridge.getWebView().post(() -> bridge.getWebView().evaluateJavascript(js, null));
            }
            return true;   // el volumen del movil no cambia mientras hacen de botones
        }
        return super.dispatchKeyEvent(ev);
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) hideBars();
    }

    private void hideBars() {
        WindowInsetsControllerCompat c = WindowCompat.getInsetsController(getWindow(), getWindow().getDecorView());
        c.setSystemBarsBehavior(WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
        c.hide(WindowInsetsCompat.Type.systemBars());
    }
}
