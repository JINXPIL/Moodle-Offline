package ru.sevsu.moodleoffline;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.Intent;
import android.graphics.Bitmap;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.os.Message;
import android.webkit.DownloadListener;
import android.webkit.JsPromptResult;
import android.webkit.JsResult;
import android.webkit.URLUtil;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.Toast;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/** Общая настройка WebView: ссылки, всплывающие окна, диалоги JavaScript и скачивание файлов в приложения телефона. */
final class Web {
    private static final Handler UI = new Handler(Looper.getMainLooper());

    private Web() {}

    static boolean isLocal(String url) {
        return url != null && (url.startsWith("http://127.0.0.1:") || url.startsWith("http://localhost:"));
    }

    /**
     * Куда вести ссылку. Возвращает true, если ссылку обработали мы (и окну грузить её не надо).
     * Чужие адреса (сайт Moodle и т. п.) уходят в браузер телефона; свои страницы из нового окна открываются в ViewerActivity.
     */
    static boolean route(Activity a, String url, boolean newWindow) {
        if (url == null) return true;
        if (!isLocal(url)) {
            if (url.startsWith("http://") || url.startsWith("https://")) {
                try {
                    Intent i = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
                    i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                    a.startActivity(i);
                } catch (Throwable t) {
                    Toast.makeText(a, "Не нашёл приложение для открытия ссылки", Toast.LENGTH_SHORT).show();
                }
            }
            return true;
        }
        if (newWindow) {
            Intent i = new Intent(a, ViewerActivity.class);
            i.putExtra("url", url);
            a.startActivity(i);
            return true;
        }
        return false;
    }

    static void configure(final WebView w, final Activity a) {
        WebSettings s = w.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        s.setSupportMultipleWindows(true);
        s.setJavaScriptCanOpenWindowsAutomatically(true);
        s.setMediaPlaybackRequiresUserGesture(true);

        w.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
                return route(a, r.getUrl().toString(), false);
            }
        });

        w.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onCreateWindow(WebView view, boolean isDialog, boolean isUserGesture, Message resultMsg) {
                WebView temp = new WebView(view.getContext());
                temp.setWebViewClient(new WebViewClient() {
                    private boolean done;

                    private void go(final WebView v, String url) {
                        if (done || url == null || "about:blank".equals(url)) return;
                        done = true;
                        route(a, url, true);
                        v.stopLoading();
                        UI.postDelayed(new Runnable() {
                            @Override
                            public void run() {
                                v.destroy();
                            }
                        }, 1000);
                    }

                    @Override
                    public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
                        go(v, r.getUrl().toString());
                        return true;
                    }

                    @Override
                    public void onPageStarted(WebView v, String url, Bitmap favicon) {
                        go(v, url);
                    }
                });
                WebView.WebViewTransport t = (WebView.WebViewTransport) resultMsg.obj;
                t.setWebView(temp);
                resultMsg.sendToTarget();
                return true;
            }

            @Override
            public boolean onJsAlert(WebView v, String url, String message, final JsResult result) {
                try {
                    new AlertDialog.Builder(a)
                            .setMessage(message)
                            .setPositiveButton("OK", new android.content.DialogInterface.OnClickListener() {
                                @Override
                                public void onClick(android.content.DialogInterface d, int which) {
                                    result.confirm();
                                }
                            })
                            .setOnCancelListener(new android.content.DialogInterface.OnCancelListener() {
                                @Override
                                public void onCancel(android.content.DialogInterface d) {
                                    result.cancel();
                                }
                            })
                            .show();
                } catch (Throwable t) {
                    result.cancel();
                }
                return true;
            }

            @Override
            public boolean onJsConfirm(WebView v, String url, String message, final JsResult result) {
                try {
                    new AlertDialog.Builder(a)
                            .setMessage(message)
                            .setPositiveButton("OK", new android.content.DialogInterface.OnClickListener() {
                                @Override
                                public void onClick(android.content.DialogInterface d, int which) {
                                    result.confirm();
                                }
                            })
                            .setNegativeButton("Отмена", new android.content.DialogInterface.OnClickListener() {
                                @Override
                                public void onClick(android.content.DialogInterface d, int which) {
                                    result.cancel();
                                }
                            })
                            .setOnCancelListener(new android.content.DialogInterface.OnCancelListener() {
                                @Override
                                public void onCancel(android.content.DialogInterface d) {
                                    result.cancel();
                                }
                            })
                            .show();
                } catch (Throwable t) {
                    result.cancel();
                }
                return true;
            }

            @Override
            public boolean onJsPrompt(WebView v, String url, String message, String defaultValue, final JsPromptResult result) {
                try {
                    final EditText input = new EditText(a);
                    input.setText(defaultValue == null ? "" : defaultValue);
                    new AlertDialog.Builder(a)
                            .setMessage(message)
                            .setView(input)
                            .setPositiveButton("OK", new android.content.DialogInterface.OnClickListener() {
                                @Override
                                public void onClick(android.content.DialogInterface d, int which) {
                                    result.confirm(input.getText().toString());
                                }
                            })
                            .setNegativeButton("Отмена", new android.content.DialogInterface.OnClickListener() {
                                @Override
                                public void onClick(android.content.DialogInterface d, int which) {
                                    result.cancel();
                                }
                            })
                            .setOnCancelListener(new android.content.DialogInterface.OnCancelListener() {
                                @Override
                                public void onCancel(android.content.DialogInterface d) {
                                    result.cancel();
                                }
                            })
                            .show();
                } catch (Throwable t) {
                    result.cancel();
                }
                return true;
            }
        });

        w.setDownloadListener(new DownloadListener() {
            @Override
            public void onDownloadStart(String url, String userAgent, String contentDisposition, String mimeType, long contentLength) {
                download(a, url, contentDisposition, mimeType);
            }
        });
    }

    /** WebView не умеет показывать PDF, Word и т. п.: забираем файл у локального сервера и отдаём приложению телефона. */
    static void download(final Context c, final String url, final String contentDisposition, final String mimeType) {
        if (!isLocal(url)) {
            try {
                Intent i = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
                i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                c.startActivity(i);
            } catch (Throwable ignored) {
            }
            return;
        }
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    String name = URLUtil.guessFileName(url, contentDisposition, mimeType).replaceAll("[\\\\/:*?\"<>|]", "_");
                    File dir = new File(c.getCacheDir(), "open");
                    dir.mkdirs();
                    final File out = new File(dir, name);
                    HttpURLConnection con = (HttpURLConnection) new URL(url).openConnection();
                    con.setConnectTimeout(5000);
                    con.setReadTimeout(60000);
                    try (InputStream in = con.getInputStream(); OutputStream os = new FileOutputStream(out)) {
                        byte[] buf = new byte[65536];
                        int r;
                        while ((r = in.read(buf)) > 0) os.write(buf, 0, r);
                    } finally {
                        con.disconnect();
                    }
                    UI.post(new Runnable() {
                        @Override
                        public void run() {
                            if (!Bridge.openFile(out.getAbsolutePath())) {
                                Toast.makeText(c, "Не нашёл приложение, которое откроет этот файл", Toast.LENGTH_LONG).show();
                            }
                        }
                    });
                } catch (Throwable t) {
                    UI.post(new Runnable() {
                        @Override
                        public void run() {
                            Toast.makeText(c, "Не удалось открыть файл", Toast.LENGTH_LONG).show();
                        }
                    });
                }
            }
        }, "open-file").start();
    }
}
