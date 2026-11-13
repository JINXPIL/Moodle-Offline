package ru.sevsu.moodleoffline;

import android.Manifest;
import android.app.Activity;
import android.app.Notification;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.os.PowerManager;
import android.provider.DocumentsContract;
import android.provider.Settings;
import android.webkit.MimeTypeMap;

import androidx.core.content.FileProvider;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;

/**
 * Мост между Python-программой и Android. Python вызывает эти статические методы через Chaquopy (from java import jclass).
 * Ни один метод не бросает исключений наружу: нет разрешения или приложения — просто false / пустая строка.
 */
public final class Bridge {
    static final String CH_SERVICE = "service";
    static final String CH_NEWS = "news";

    static volatile Context app;          // задаётся в MoodleApp.onCreate
    static volatile Activity current;     // видимое окно (для запроса разрешений)

    private static PowerManager.WakeLock wakeLock;
    private static int noteId = 100;

    private Bridge() {}

    // ── открыть ссылку / файл ────────────────────────────────────────────────
    public static boolean openUrl(String url) {
        try {
            if (url == null || !(url.startsWith("http://") || url.startsWith("https://"))) return false;
            Intent i = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            app.startActivity(i);
            return true;
        } catch (Throwable t) {
            return false;
        }
    }

    public static boolean openFile(String path) {
        try {
            File f = new File(path);
            if (!f.isFile()) return false;
            Uri uri = FileProvider.getUriForFile(app, app.getPackageName() + ".files", f);
            Intent i = new Intent(Intent.ACTION_VIEW);
            i.setDataAndType(uri, mimeOf(f.getName()));
            i.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
            app.startActivity(i);
            return true;
        } catch (Throwable t) {
            return false;
        }
    }

    // ── открыть ПАПКУ в приложении «Файлы» (как «Открыть папку» на компьютере) ──────────────────────────
    /** Адрес папки в системном провайдере общей памяти (content://com.android.externalstorage.documents/…).
     *  null — если папка не в общей памяти (например, во внутренней памяти приложения): приложение «Файлы» её не откроет. */
    static Uri folderUri(String path) {
        try {
            String root = Environment.getExternalStorageDirectory().getCanonicalPath();
            String p = new File(path).getCanonicalPath();
            if (p.equals(root)) return DocumentsContract.buildDocumentUri("com.android.externalstorage.documents", "primary:");
            if (!p.startsWith(root + "/")) return null;
            return DocumentsContract.buildDocumentUri("com.android.externalstorage.documents", "primary:" + p.substring(root.length() + 1));
        } catch (Throwable t) {
            return null;
        }
    }

    /** Способы открыть папку, от самого общепринятого: «Файлы» от Google/AOSP понимают первые два, многие файловые менеджеры — третий. */
    static Intent[] folderIntents(Uri uri) {
        String dir = DocumentsContract.Document.MIME_TYPE_DIR;
        Intent[] list = new Intent[] {
            new Intent(Intent.ACTION_VIEW).setDataAndType(uri, dir),
            new Intent("android.provider.action.BROWSE").setDataAndType(uri, dir),
            new Intent(Intent.ACTION_VIEW).setDataAndType(uri, "resource/folder"),
        };
        for (Intent i : list) i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_GRANT_READ_URI_PERMISSION);
        return list;
    }

    /** Есть ли на телефоне приложение, которое откроет эту папку (ничего не запускает). */
    public static boolean canOpenFolder(String path) {
        try {
            Uri uri = folderUri(path);
            if (uri == null || !new File(path).isDirectory()) return false;
            PackageManager pm = app.getPackageManager();
            for (Intent i : folderIntents(uri)) {
                if (i.resolveActivity(pm) != null) return true;
            }
            return false;
        } catch (Throwable t) {
            return false;
        }
    }

    public static boolean openFolder(String path) {
        try {
            Uri uri = folderUri(path);
            if (uri == null || !new File(path).isDirectory()) return false;
            for (Intent i : folderIntents(uri)) {
                try {
                    app.startActivity(i);
                    return true;
                } catch (ActivityNotFoundException | SecurityException e) {
                    // это приложение не подошло — пробуем следующий способ
                }
            }
            return false;
        } catch (Throwable t) {
            return false;
        }
    }

    // «Поделиться» файлом: системное окно выбора приложения (мессенджер, почта…)
    public static boolean shareFile(String path) {
        try {
            File f = new File(path);
            if (!f.isFile()) return false;
            Uri uri = FileProvider.getUriForFile(app, app.getPackageName() + ".files", f);
            Intent send = new Intent(Intent.ACTION_SEND);
            send.setType(mimeOf(f.getName()));
            send.putExtra(Intent.EXTRA_STREAM, uri);
            send.setClipData(ClipData.newRawUri("", uri));
            send.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            Intent chooser = Intent.createChooser(send, "Поделиться расписанием");
            chooser.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_GRANT_READ_URI_PERMISSION);
            app.startActivity(chooser);
            return true;
        } catch (Throwable t) {
            return false;
        }
    }

    static String mimeOf(String name) {
        int dot = name.lastIndexOf('.');
        if (dot >= 0) {
            String m = MimeTypeMap.getSingleton().getMimeTypeFromExtension(name.substring(dot + 1).toLowerCase());
            if (m != null) return m;
        }
        return "*/*";
    }

    // ── уведомления ──────────────────────────────────────────────────────────
    public static boolean notify(String title, String text) {
        try {
            Context c = app;
            if (c == null) return false;
            if (Build.VERSION.SDK_INT >= 33
                    && c.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                return false;
            }
            NotificationManager nm = (NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE);
            if (!nm.areNotificationsEnabled()) return false;
            Intent open = new Intent(c, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_NEW_TASK);
            PendingIntent pi = PendingIntent.getActivity(c, 0, open, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
            Notification.Builder b = Build.VERSION.SDK_INT >= 26 ? new Notification.Builder(c, CH_NEWS) : new Notification.Builder(c);
            b.setSmallIcon(R.drawable.ic_notify)
                    .setContentTitle(title)
                    .setContentText(text)
                    .setStyle(new Notification.BigTextStyle().bigText(text))
                    .setContentIntent(pi)
                    .setAutoCancel(true);
            synchronized (Bridge.class) {
                noteId++;
                nm.notify(noteId, b.build());
            }
            return true;
        } catch (Throwable t) {
            return false;
        }
    }

    // ── не давать телефону уснуть ────────────────────────────────────────────
    public static synchronized boolean setWakeLock(boolean on) {
        try {
            if (on) {
                if (wakeLock == null) {
                    PowerManager pm = (PowerManager) app.getSystemService(Context.POWER_SERVICE);
                    wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "MoodleOffline:watch");
                    wakeLock.setReferenceCounted(false);
                }
                if (!wakeLock.isHeld()) wakeLock.acquire();
            } else if (wakeLock != null && wakeLock.isHeld()) {
                wakeLock.release();
            }
            return true;
        } catch (Throwable t) {
            return false;
        }
    }

    public static boolean isBatteryExempt() {
        try {
            PowerManager pm = (PowerManager) app.getSystemService(Context.POWER_SERVICE);
            return pm.isIgnoringBatteryOptimizations(app.getPackageName());
        } catch (Throwable t) {
            return false;
        }
    }

    /** Показывает системное окно «не ограничивать работу приложения в фоне». */
    public static boolean requestBatteryExemption() {
        try {
            Intent i = new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:" + app.getPackageName()));
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            app.startActivity(i);
            return true;
        } catch (Throwable t) {
            return false;
        }
    }

    // ── память телефона ──────────────────────────────────────────────────────
    public static boolean hasAllFilesAccess() {
        try {
            if (Build.VERSION.SDK_INT >= 30) return Environment.isExternalStorageManager();
            return app.checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE) == PackageManager.PERMISSION_GRANTED;
        } catch (Throwable t) {
            return false;
        }
    }

    public static boolean requestAllFilesAccess() {
        try {
            if (Build.VERSION.SDK_INT >= 30) {
                Intent i = new Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION, Uri.parse("package:" + app.getPackageName()));
                i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                app.startActivity(i);
                return true;
            }
            final Activity a = current;
            if (a == null) return false;
            a.runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    a.requestPermissions(new String[]{Manifest.permission.WRITE_EXTERNAL_STORAGE, Manifest.permission.READ_EXTERNAL_STORAGE}, 7);
                }
            });
            return true;
        } catch (Throwable t) {
            return false;
        }
    }

    /** Общая память телефона (то, что видно в приложении «Файлы»). */
    public static String sharedRoot() {
        try {
            return Environment.getExternalStorageDirectory().getAbsolutePath();
        } catch (Throwable t) {
            return "";
        }
    }

    /** Куда класть материалы по умолчанию: «Документы/имя», если есть доступ ко всем файлам, иначе папка приложения. */
    public static String materialsDir(String name) {
        try {
            if (hasAllFilesAccess()) {
                return new File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOCUMENTS), name).getAbsolutePath();
            }
            File ext = app.getExternalFilesDir(null);
            return new File(ext != null ? ext : app.getFilesDir(), name).getAbsolutePath();
        } catch (Throwable t) {
            return new File(app.getFilesDir(), name).getAbsolutePath();
        }
    }

    // ── автозапуск при включении телефона ────────────────────────────────────
    static SharedPreferences prefs() {
        return app.getSharedPreferences("moodle_offline", Context.MODE_PRIVATE);
    }

    public static boolean getAutostart() {
        try {
            return prefs().getBoolean("autostart", false);
        } catch (Throwable t) {
            return false;
        }
    }

    public static boolean setAutostart(boolean on) {
        try {
            prefs().edit().putBoolean("autostart", on).apply();
            return true;
        } catch (Throwable t) {
            return false;
        }
    }

    // ── окно программы лежит в assets: копируем рядом с данными, откуда его читает Python ──
    static File copyUi(Context c) throws Exception {
        File dir = new File(c.getFilesDir(), "ui");
        dir.mkdirs();
        String[] names = c.getAssets().list("ui");
        if (names != null) {
            for (String n : names) {
                try (InputStream in = c.getAssets().open("ui/" + n); OutputStream out = new FileOutputStream(new File(dir, n))) {
                    byte[] buf = new byte[65536];
                    int r;
                    while ((r = in.read(buf)) > 0) out.write(buf, 0, r);
                }
            }
        }
        // кэш «открыть файл» чистим: он нужен только на время открытия
        File cache = new File(c.getCacheDir(), "open");
        File[] old = cache.listFiles();
        if (old != null) {
            long limit = System.currentTimeMillis() - 24L * 3600 * 1000;
            for (File f : old) if (f.lastModified() < limit) f.delete();
        }
        return dir;
    }
}
