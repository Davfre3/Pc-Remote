package com.pcremote.app;

import android.Manifest;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothSocket;
import android.os.Build;
import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.UUID;

/** Bluetooth clasico (RFCOMM) hacia el PC: lineas de texto en las dos direcciones, solo botones. */
@CapacitorPlugin(name = "BtPad", permissions = {
    @Permission(alias = "bt", strings = { Manifest.permission.BLUETOOTH_CONNECT })
})
public class BtPad extends Plugin {
    private BluetoothSocket socket;
    private OutputStream out;

    private boolean allowed() {
        return Build.VERSION.SDK_INT < 31 || getPermissionState("bt") == com.getcapacitor.PermissionState.GRANTED;
    }

    @PluginMethod
    public void devices(PluginCall call) {
        if (!allowed()) { requestPermissionForAlias("bt", call, "devicesPerm"); return; }
        listDevices(call);
    }

    @PermissionCallback
    private void devicesPerm(PluginCall call) {
        if (allowed()) listDevices(call); else call.reject("Permiso de Bluetooth denegado");
    }

    private void listDevices(PluginCall call) {
        BluetoothAdapter ad = BluetoothAdapter.getDefaultAdapter();
        if (ad == null) { call.reject("Este móvil no tiene Bluetooth"); return; }
        JSObject r = new JSObject();
        r.put("enabled", ad.isEnabled());
        JSArray list = new JSArray();
        try {
            for (BluetoothDevice d : ad.getBondedDevices()) {
                JSObject o = new JSObject();
                o.put("name", d.getName());
                o.put("address", d.getAddress());
                list.put(o);
            }
        } catch (SecurityException e) { call.reject("Permiso de Bluetooth denegado"); return; }
        r.put("devices", list);
        call.resolve(r);
    }

    @PluginMethod
    public void open(PluginCall call) {
        if (!allowed()) { requestPermissionForAlias("bt", call, "openPerm"); return; }
        doOpen(call);
    }

    @PermissionCallback
    private void openPerm(PluginCall call) {
        if (allowed()) doOpen(call); else call.reject("Permiso de Bluetooth denegado");
    }

    private void doOpen(PluginCall call) {
        final String address = call.getString("address");
        final String uuid = call.getString("uuid");
        closeQuietly();
        new Thread(() -> {
            try {
                BluetoothAdapter ad = BluetoothAdapter.getDefaultAdapter();
                ad.cancelDiscovery();
                BluetoothDevice dev = ad.getRemoteDevice(address);
                BluetoothSocket s = dev.createRfcommSocketToServiceRecord(UUID.fromString(uuid));
                s.connect();
                socket = s;
                out = s.getOutputStream();
                call.resolve();
                BufferedReader in = new BufferedReader(new InputStreamReader(s.getInputStream(), StandardCharsets.UTF_8));
                String line;
                while ((line = in.readLine()) != null) {
                    JSObject o = new JSObject();
                    o.put("data", line);
                    notifyListeners("line", o);
                }
            } catch (Exception e) {
                if (socket == null) { call.reject("No se pudo conectar: " + e.getMessage()); return; }
            }
            closeQuietly();
            notifyListeners("closed", new JSObject());
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
        closeQuietly();
        call.resolve();
    }

    private void closeQuietly() {
        try { if (socket != null) socket.close(); } catch (Exception ignored) { }
        socket = null;
        out = null;
    }
}
