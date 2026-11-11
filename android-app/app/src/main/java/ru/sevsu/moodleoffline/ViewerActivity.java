package ru.sevsu.moodleoffline;

import android.app.Activity;
import android.os.Bundle;
import android.view.ViewGroup;
import android.webkit.WebView;

/** Второе окно: страницы, которые программа открывает «в новой вкладке» (просмотр курса, расписание, календарь, файлы). */
public class ViewerActivity extends Activity {
    private WebView web;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        web = new WebView(this);
        setContentView(web, new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        Web.configure(web, this);
        String url = getIntent().getStringExtra("url");
        if (url != null && Web.isLocal(url)) web.loadUrl(url);
        else finish();
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) web.goBack();
        else finish();
    }

    @Override
    protected void onDestroy() {
        if (web != null) {
            web.destroy();
            web = null;
        }
        super.onDestroy();
    }
}
