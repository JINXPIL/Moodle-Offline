package ru.sevsu.moodleoffline;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;

/** После включения телефона (и после обновления приложения) запускает слежение, если пользователь это включил. */
public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        try {
            if (!Bridge.getAutostart()) return;
            Intent s = new Intent(context, ServerService.class);
            if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(s);
            else context.startService(s);
        } catch (Throwable ignored) {
        }
    }
}
