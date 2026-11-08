package com.pcremote.app;

import android.Manifest;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothClass;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothSocket;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.os.Build;
import android.os.SystemClock;
import androidx.core.content.ContextCompat;
import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.UUID;

/**
 * Bluetooth clasico (RFCOMM) hacia el PC, como el escaner de "PC Remote": busca dispositivos cercanos, empareja
 * y abre un canal serie con lineas de texto en los dos sentidos (solo botones). Tambien da la IP WiFi del movil.
 */
@CapacitorPlugin(name = "BtPad", permissions = {
    // Android 12+: buscar y conectar; Android 11 o menos: la busqueda exige la ubicacion
    @Permission(alias = "bt", strings = { Manifest.permission.BLUETOOTH_CONNECT, Manifest.permission.BLUETOOTH_SCAN }),
    @Permission(alias = "loc", strings = { Manifest.permission.ACCESS_FINE_LOCATION })
})
public class BtPad extends Plugin {
    private BluetoothSocket socket;
    private OutputStream out;
    private volatile int gen = 0;            // cada open()/close() lo cambia: un intento viejo se da cuenta y se retira
    private BroadcastReceiver receiver;
    private PluginCall pairCall;
    private String pairAddr;

    // ---------------- permisos ----------------
    private String alias() { return Build.VERSION.SDK_INT >= 31 ? "bt" : "loc"; }

    /** true si ya se puede seguir; si no, pide el permiso y vuelve a esta misma llamada tras la respuesta. */
    private boolean ready(PluginCall call, boolean scanning) {
        if (Build.VERSION.SDK_INT < 31 && !scanning) return true;
        if (getPermissionState(alias()) == PermissionState.GRANTED) return true;
        requestPermissionForAlias(alias(), call, "permDone");
        return false;
    }

    @PermissionCallback
    private void permDone(PluginCall call) {
        if (getPermissionState(alias()) != PermissionState.GRANTED) {
            call.reject(Build.VERSION.SDK_INT >= 31 ? "Permiso de Bluetooth denegado" : "Para buscar dispositivos Android pide el permiso de ubicación");
            return;
        }
        switch (call.getMethodName()) {
            case "devices": devices(call); break;
            case "scan": scan(call); break;
            case "pair": pair(call); break;
            case "open": open(call); break;
            default: call.reject("permiso inesperado");
        }
    }

    private BluetoothAdapter adapter(PluginCall call) {
        BluetoothAdapter ad = BluetoothAdapter.getDefaultAdapter();
        if (ad == null) call.reject("Este móvil no tiene Bluetooth");
        return ad;
    }

    private static boolean isComputer(BluetoothClass c) {
        return c != null && c.getMajorDeviceClass() == BluetoothClass.Device.Major.COMPUTER;
    }

    private JSObject describe(BluetoothDevice d, String fallbackName, BluetoothClass cls) {
        JSObject o = new JSObject();
        String name = fallbackName;
        try { if (d.getName() != null) name = d.getName(); } catch (SecurityException ignored) { }
        o.put("name", name);
        o.put("address", d.getAddress());
        try { o.put("paired", d.getBondState() == BluetoothDevice.BOND_BONDED); } catch (SecurityException e) { o.put("paired", false); }
        BluetoothClass bc = cls;
        try { if (bc == null) bc = d.getBluetoothClass(); } catch (SecurityException ignored) { }
        o.put("computer", isComputer(bc));
        return o;
    }

    // ---------------- dispositivos emparejados ----------------
    @PluginMethod
    public void devices(PluginCall call) {
        if (!ready(call, false)) return;
        BluetoothAdapter ad = adapter(call);
        if (ad == null) return;
        JSObject r = new JSObject();
        r.put("enabled", ad.isEnabled());
        JSArray list = new JSArray();
        try {
            for (BluetoothDevice d : ad.getBondedDevices()) list.put(describe(d, null, null));
        } catch (SecurityException e) { call.reject("Permiso de Bluetooth denegado"); return; }
        r.put("devices", list);
        call.resolve(r);
    }

    // ---------------- escaner ----------------
    private void listen() {
        if (receiver != null) return;
        IntentFilter f = new IntentFilter();
        f.addAction(BluetoothDevice.ACTION_FOUND);
        f.addAction(BluetoothAdapter.ACTION_DISCOVERY_FINISHED);
        f.addAction(BluetoothDevice.ACTION_BOND_STATE_CHANGED);
        receiver = new BroadcastReceiver() {
            @Override
            @SuppressWarnings("deprecation")
            public void onReceive(Context ctx, Intent i) {
                String act = i.getAction();
                if (act == null) return;
                if (BluetoothAdapter.ACTION_DISCOVERY_FINISHED.equals(act)) {
                    notifyListeners("scanEnd", new JSObject());
                    return;
                }
                BluetoothDevice d = Build.VERSION.SDK_INT >= 33
                    ? i.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE, BluetoothDevice.class)
                    : (BluetoothDevice) i.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE);
                if (d == null) return;
                if (BluetoothDevice.ACTION_FOUND.equals(act)) {
                    notifyListeners("found", describe(d, i.getStringExtra(BluetoothDevice.EXTRA_NAME),
                        (BluetoothClass) i.getParcelableExtra(BluetoothDevice.EXTRA_CLASS)));
                } else if (BluetoothDevice.ACTION_BOND_STATE_CHANGED.equals(act) && pairCall != null && d.getAddress().equals(pairAddr)) {
                    int st = i.getIntExtra(BluetoothDevice.EXTRA_BOND_STATE, BluetoothDevice.ERROR);
                    if (st == BluetoothDevice.BOND_BONDED) { PluginCall c = pairCall; pairCall = null; c.resolve(); }
                    else if (st == BluetoothDevice.BOND_NONE) { PluginCall c = pairCall; pairCall = null; c.reject("No se pudo emparejar (¿rechazado en el PC o en el móvil?)"); }
                }
            }
        };
        ContextCompat.registerReceiver(getContext(), receiver, f, ContextCompat.RECEIVER_EXPORTED);
    }

    @PluginMethod
    public void scan(PluginCall call) {
        if (!ready(call, true)) return;
        BluetoothAdapter ad = adapter(call);
        if (ad == null) return;
        if (!ad.isEnabled()) { call.reject("Activa el Bluetooth del móvil"); return; }
        try {
            listen();
            if (ad.isDiscovering()) ad.cancelDiscovery();
            if (!ad.startDiscovery()) { call.reject("No se pudo empezar a buscar"); return; }
        } catch (SecurityException e) { call.reject("Permiso de Bluetooth denegado"); return; }
        call.resolve();
    }

    @PluginMethod
    public void stopScan(PluginCall call) {
        try {
            BluetoothAdapter ad = BluetoothAdapter.getDefaultAdapter();
            if (ad != null && ad.isDiscovering()) ad.cancelDiscovery();
        } catch (SecurityException ignored) { }
        call.resolve();
    }

    @PluginMethod
    public void pair(PluginCall call) {
        if (!ready(call, false)) return;
        BluetoothAdapter ad = adapter(call);
        if (ad == null) return;
        String address = call.getString("address");
        try {
            BluetoothDevice d = ad.getRemoteDevice(address);
            if (d.getBondState() == BluetoothDevice.BOND_BONDED) { call.resolve(); return; }
            ad.cancelDiscovery();
            listen();
            pairCall = call;
            pairAddr = address;
            if (!d.createBond()) { pairCall = null; call.reject("No se pudo empezar el emparejamiento"); }
        } catch (SecurityException e) { pairCall = null; call.reject("Permiso de Bluetooth denegado"); }
        catch (IllegalArgumentException e) { call.reject("Dirección Bluetooth no válida"); }
    }

    // ---------------- conexion ----------------
    private void status(String text) {
        JSObject o = new JSObject();
        o.put("text", text);
        notifyListeners("status", o);
    }

    /** Metodo 0: SDP seguro; 1: SDP inseguro; 2 en adelante: canal RFCOMM fijo (m - 1), por si el SDP del PC no responde. */
    private BluetoothSocket make(BluetoothDevice dev, UUID uuid, int m) throws Exception {
        if (m == 0) return dev.createRfcommSocketToServiceRecord(uuid);
        if (m == 1) return dev.createInsecureRfcommSocketToServiceRecord(uuid);
        return (BluetoothSocket) dev.getClass().getMethod("createRfcommSocket", int.class).invoke(dev, m - 1);
    }

    /** Manda el saludo con el PIN y espera la primera linea del PC (btok o auth). null si no contesta en 6 s. */
    private String handshake(BluetoothSocket s, BufferedReader in, String hello) {
        final BluetoothSocket fs = s;
        Thread dog = new Thread(() -> {
            try { Thread.sleep(6000); } catch (InterruptedException e) { return; }
            try { fs.close(); } catch (Exception ignored) { }
        });
        dog.start();
        try {
            OutputStream o = s.getOutputStream();
            o.write((hello + "\n").getBytes(StandardCharsets.UTF_8));
            o.flush();
            return in.readLine();
        } catch (Exception e) {
            return null;
        } finally {
            dog.interrupt();
        }
    }

    @PluginMethod
    public void open(PluginCall call) {
        if (!ready(call, false)) return;
        final String address = call.getString("address");
        final String uuidText = call.getString("uuid");
        final String hello = call.getString("hello", "");
        closeQuietly();
        final int mine = ++gen;
        new Thread(() -> {
            String lastErr = "no se pudo abrir el canal";
            try {
                BluetoothAdapter ad = BluetoothAdapter.getDefaultAdapter();
                ad.cancelDiscovery();
                BluetoothDevice dev = ad.getRemoteDevice(address);
                UUID uuid = UUID.fromString(uuidText);
                for (int m = 0; m < 32 && gen == mine; m++) {
                    status(m == 0 ? "Conectando con el PC…" : m == 1 ? "Probando otro método…" : "Buscando el canal del PC… " + (m - 1) + "/30");
                    long t0 = SystemClock.elapsedRealtime();
                    BluetoothSocket s = null;
                    try {
                        s = make(dev, uuid, m);
                        s.connect();
                        BufferedReader in = new BufferedReader(new InputStreamReader(s.getInputStream(), StandardCharsets.UTF_8));
                        String first = handshake(s, in, hello);
                        if (first != null && first.contains("\"auth\"")) {
                            s.close();
                            call.reject("PIN incorrecto: míralo en el panel del PC");
                            return;
                        }
                        if (first == null || !first.contains("\"btok\"")) {   // otro servicio del PC (o ninguno): se sigue probando
                            lastErr = "el PC no respondió como PC Remote";
                            try { s.close(); } catch (Exception ignored) { }
                            continue;
                        }
                        if (gen != mine) { s.close(); return; }
                        socket = s;
                        out = s.getOutputStream();
                        call.resolve();
                        String line;
                        while (gen == mine && (line = in.readLine()) != null) {
                            JSObject o = new JSObject();
                            o.put("data", line);
                            notifyListeners("line", o);
                        }
                        if (gen == mine) { closeQuietly(); notifyListeners("closed", new JSObject()); }
                        return;
                    } catch (NoSuchMethodException | SecurityException e) {
                        lastErr = "este móvil no deja probar canales fijos";
                        break;
                    } catch (Exception e) {
                        lastErr = e.getMessage() == null ? e.toString() : e.getMessage();
                        if (s != null) try { s.close(); } catch (Exception ignored) { }
                        // un fallo lento es que el PC no se alcanza (apagado, lejos, sin emparejar): no tiene sentido seguir
                        if (SystemClock.elapsedRealtime() - t0 > 7000) { lastErr = "el PC no responde por Bluetooth (¿está cerca, con el Bluetooth activo y emparejado?)"; break; }
                    }
                }
            } catch (Exception e) {
                lastErr = e.getMessage() == null ? e.toString() : e.getMessage();
            }
            if (gen == mine) call.reject("No se pudo conectar: " + lastErr);
        }, "btpad").start();
    }

    @PluginMethod
    public void write(PluginCall call) {
        String data = call.getString("data", "");
        try {
            synchronized (this) {
                if (out == null) { call.reject("sin conexión"); return; }
                out.write((data + "\n").getBytes(StandardCharsets.UTF_8));
                out.flush();
            }
            call.resolve();
        } catch (Exception e) {
            closeQuietly();
            call.reject("sin conexión");
        }
    }

    @PluginMethod
    public void close(PluginCall call) {
        gen++;
        closeQuietly();
        call.resolve();
    }

    private void closeQuietly() {
        try { if (socket != null) socket.close(); } catch (Exception ignored) { }
        socket = null;
        out = null;
    }

    @Override
    protected void handleOnDestroy() {
        gen++;
        closeQuietly();
        try {
            BluetoothAdapter ad = BluetoothAdapter.getDefaultAdapter();
            if (ad != null && ad.isDiscovering()) ad.cancelDiscovery();
        } catch (SecurityException ignored) { }
        if (receiver != null) {
            try { getContext().unregisterReceiver(receiver); } catch (Exception ignored) { }
            receiver = null;
        }
    }

    // ---------------- IP del movil en la WiFi (para buscar el PC en su red) ----------------
    @PluginMethod
    public void localIps(PluginCall call) {
        JSArray ips = new JSArray();
        try {
            for (NetworkInterface ni : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                String n = ni.getName();
                if (!ni.isUp() || ni.isLoopback()) continue;
                if (!(n.startsWith("wlan") || n.startsWith("eth") || n.startsWith("swlan") || n.startsWith("ap"))) continue;   // sin datos moviles
                for (InetAddress a : Collections.list(ni.getInetAddresses())) {
                    if (a instanceof Inet4Address && !a.isLoopbackAddress()) ips.put(a.getHostAddress());
                }
            }
        } catch (Exception ignored) { }
        JSObject r = new JSObject();
        r.put("ips", ips);
        call.resolve(r);
    }
}
