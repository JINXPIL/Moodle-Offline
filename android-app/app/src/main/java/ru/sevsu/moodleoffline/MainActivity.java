package ru.sevsu.moodleoffline;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.net.Uri;
import android.view.ViewGroup;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebView;
import android.widget.FrameLayout;
import android.widget.TextView;
import android.widget.Toast;

/** Окно программы: страница локального сервера (тот же интерфейс, что и на компьютере) в WebView. */
public class MainActivity extends Activity {
    private WebView web;
    private TextView status;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private String loaded;
    private long startedAt;
    private static final int REQ_FILE = 61;
    private ValueCallback<Uri[]> chooser;   // ответ странице на «Выберите файл»

    private final Runnable poll = new Runnable() {
        @Override
        public void run() {
            String u = ServerState.url;
            if (u != null) {
                status.setVisibility(View.GONE);
                if (!u.equals(loaded)) {
                    loaded = u;
                    web.loadUrl(u);
                }
            } else if (loaded != null) {
                // программу остановили кнопкой «Остановить» — закрываем окно
                loaded = null;
                finishAndRemoveTask();
                return;
            } else if (ServerState.error != null) {
                status.setVisibility(View.VISIBLE);
                status.setText("Не удалось запустить программу:\n\n" + ServerState.error);
            } else if (!ServerState.running && System.currentTimeMillis() - startedAt > 4000) {
                startedAt = System.currentTimeMillis();
                startServer(false);
            }
            handler.postDelayed(this, 400);
        }
    };

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        Bridge.current = this;
        FrameLayout root = new FrameLayout(this);
        web = new WebView(this);
        root.addView(web, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        status = new TextView(this);
        status.setPadding(48, 96, 48, 48);
        status.setTextSize(16);
        status.setTextIsSelectable(true);
        status.setText("Запускаю программу…");
        root.addView(status, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        setContentView(root);
        Web.configure(web, this);
        web.setVerticalScrollBarEnabled(false);   // системная полоска справа не тянется пальцем — вместо неё свой ползунок в ui/index.html (fastScroll); во втором окне (ViewerActivity) его нет, там системная остаётся
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 5);
        }
        startedAt = System.currentTimeMillis();
        startServer(getIntent().getBooleanExtra("selftest", false));
    }

    private void startServer(boolean selftest) {
        Intent i = new Intent(this, ServerService.class);
        if (selftest) i.putExtra("selftest", true);
        if (Build.VERSION.SDK_INT >= 26) startForegroundService(i);
        else startService(i);
    }

    /** Системный выбор файла для поля «Выберите файл» на странице (архив переноса данных). */
    boolean chooseFile(ValueCallback<Uri[]> cb) {
        if (chooser != null) chooser.onReceiveValue(null);
        chooser = cb;
        try {
            Intent i = new Intent(Intent.ACTION_GET_CONTENT);
            i.addCategory(Intent.CATEGORY_OPENABLE);
            i.setType("*/*");   // архивы у разных «Файлов» называются по-разному — не сужаем
            startActivityForResult(Intent.createChooser(i, "Выберите файл"), REQ_FILE);
        } catch (Throwable t) {
            chooser = null;
            cb.onReceiveValue(null);
            Toast.makeText(this, "Не нашёл приложение «Файлы»", Toast.LENGTH_SHORT).show();
        }
        return true;
    }

    @Override
    protected void onActivityResult(int req, int res, Intent data) {
        if (req == REQ_FILE) {
            ValueCallback<Uri[]> cb = chooser;
            chooser = null;
            if (cb != null) cb.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(res, data));
            return;
        }
        super.onActivityResult(req, res, data);
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
    }

    @Override
    protected void onResume() {
        super.onResume();
        Bridge.current = this;
        handler.removeCallbacks(poll);
        handler.post(poll);
        web.onResume();
    }

    @Override
    protected void onPause() {
        web.onPause();
        handler.removeCallbacks(poll);
        super.onPause();
    }

    @Override
    public void onBackPressed() {
        // Сначала пусть страница закроет то, что открыто поверх (просмотр файла, читалку, окно настроек, курс); если закрывать нечего — сворачиваем.
        if (web == null || ServerState.url == null) {
            moveTaskToBack(true);
            return;
        }
        web.evaluateJavascript("(function(){try{return !!(window.moBack && window.moBack());}catch(e){return false;}})()", new ValueCallback<String>() {
            @Override
            public void onReceiveValue(String value) {
                if (!"true".equals(value)) moveTaskToBack(true);   // служба продолжает работать
            }
        });
    }

    @Override
    protected void onDestroy() {
        if (Bridge.current == this) Bridge.current = null;
        web.destroy();
        super.onDestroy();
    }
}
