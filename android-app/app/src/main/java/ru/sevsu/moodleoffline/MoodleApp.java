package ru.sevsu.moodleoffline;

import android.app.Application;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.os.Build;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

public class MoodleApp extends Application {
    @Override
    public void onCreate() {
        super.onCreate();
        Bridge.app = this;
        makeChannels();
        if (!Python.isStarted()) {
            Python.start(new AndroidPlatform(this));
        }
    }

    private void makeChannels() {
        if (Build.VERSION.SDK_INT < 26) return;
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        NotificationChannel service = new NotificationChannel(Bridge.CH_SERVICE, "Работа в фоне", NotificationManager.IMPORTANCE_LOW);
        service.setDescription("Постоянное уведомление, пока программа следит за Moodle и обновляет файлы");
        nm.createNotificationChannel(service);
        NotificationChannel news = new NotificationChannel(Bridge.CH_NEWS, "Что нового и сроки", NotificationManager.IMPORTANCE_DEFAULT);
        news.setDescription("Новые материалы, оценки, сроки сдачи, «Moodle не отвечает»");
        nm.createNotificationChannel(news);
    }
}
