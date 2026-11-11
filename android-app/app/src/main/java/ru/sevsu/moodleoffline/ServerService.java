package ru.sevsu.moodleoffline;

import android.app.Notification;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.graphics.drawable.Icon;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.util.Log;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;

import java.io.File;

/**
 * Служба переднего плана: внутри неё работает Python-программа (локальный сервер окна, таймер «жив ли Moodle», автообновление).
 * Пока служба жива, слежение идёт и с закрытым окном. Постоянное уведомление обязательно — так Android разрешает работу в фоне.
 */
public class ServerService extends Service {
    static final String ACTION_STOP = "ru.sevsu.moodleoffline.STOP";
    private static final int NOTE_ID = 1;
    private static final String TAG = "MoodleOffline";

    private Thread worker;

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            stopPython();
            return START_NOT_STICKY;
        }
        goForeground("Moodle Offline работает", "Слежение за Moodle и автообновление по вашему таймеру");
        boolean selftest = intent != null && intent.getBooleanExtra("selftest", false);
        synchronized (ServerState.class) {
            if (worker == null || !worker.isAlive()) {
                ServerState.url = null;
                ServerState.error = null;
                startWorker(selftest);
            }
        }
        return START_STICKY;
    }

    private void goForeground(String title, String text) {
        Intent open = new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_NEW_TASK);
        PendingIntent openPi = PendingIntent.getActivity(this, 0, open, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Intent stop = new Intent(this, ServerService.class).setAction(ACTION_STOP);
        PendingIntent stopPi = PendingIntent.getService(this, 1, stop, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Notification.Builder b = Build.VERSION.SDK_INT >= 26 ? new Notification.Builder(this, Bridge.CH_SERVICE) : new Notification.Builder(this);
        b.setSmallIcon(R.drawable.ic_notify)
                .setContentTitle(title)
                .setContentText(text)
                .setOngoing(true)
                .setContentIntent(openPi)
                .addAction(new Notification.Action.Builder(Icon.createWithResource(this, R.drawable.ic_notify), "Остановить", stopPi).build());
        Notification n = b.build();
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(NOTE_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        } else {
            startForeground(NOTE_ID, n);
        }
    }

    private void startWorker(final boolean selftest) {
        ServerState.running = true;
        final Context ctx = getApplicationContext();
        final ServerService self = this;
        worker = new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    File ui = Bridge.copyUi(ctx);
                    PyObject main = Python.getInstance().getModule("apk_main");
                    main.callAttr("run", self, ctx.getFilesDir().getAbsolutePath(), ui.getAbsolutePath(), selftest);
                } catch (Throwable t) {
                    Log.e(TAG, "Python остановился с ошибкой", t);
                    ServerState.error = Log.getStackTraceString(t);
                } finally {
                    ServerState.running = false;
                    ServerState.url = null;
                }
                if (ServerState.error == null) {
                    // программу остановили штатно — службе больше нечего делать
                    new Handler(Looper.getMainLooper()).post(new Runnable() {
                        @Override
                        public void run() {
                            stopForeground(true);
                            stopSelf();
                        }
                    });
                }
            }
        }, "moodle-python");
        worker.start();
    }

    /** Вызывается из Python, когда локальный сервер готов. */
    public void onServerReady(String url) {
        ServerState.url = url;
    }

    private void stopPython() {
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    Python.getInstance().getModule("apk_main").callAttr("stop");
                } catch (Throwable t) {
                    Log.e(TAG, "stop", t);
                }
            }
        }, "moodle-stop").start();
    }

    @Override
    public void onDestroy() {
        Bridge.setWakeLock(false);
        super.onDestroy();
    }
}
